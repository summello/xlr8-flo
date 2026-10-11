"""Real production routes, durable job slices, cancellation and retry plants."""

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from pydantic import ValidationError

from flo.api.health import app
from flo.kernel.config import Settings
from flo.kernel.jobs import JobRunner
from flo.kernel.jobs.runner import TransientJobError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.service import identity_email
from flo.modules.imports.jobs import ImportWorker, handlers
from flo.modules.imports.service import ImportService
from tests.imports.test_validation import send, upload
from tests.isolation.test_real_sessions import client, headers, login
from tests.org.test_bootstrap import connection_url


async def authenticate(browser, probe):
    storage = probe[4]
    if hasattr(storage, "session_cookies"):
        browser.cookies.update(storage.session_cookies)
    else:
        await login(browser)
        storage.session_cookies = dict(browser.cookies)


@contextmanager
def allowed(scope):
    yield SimpleNamespace(check=lambda user, permission, target: SimpleNamespace(allowed=True))


def tick(probe, *, clock=None, resolver=allowed):
    conn, _, _, _, storage, _ = probe
    times = iter([0, 0, 100])
    with psycopg.connect(connection_url(conn)) as runner:
        return JobRunner(
            runner,
            handlers(
                lambda: psycopg.connect(connection_url(conn)),
                resolver,
                storage=storage,
                monotonic=clock or (lambda: 0),
            ),
            random_fraction=lambda: 0,
            monotonic=lambda: next(times),
        ).run(25)


def sliced_clock():
    times = iter([0, 21])
    return lambda: next(times)


def batch_read(conn, org, id):
    return ImportService(conn, Scope(org)).get(UUID(id))


def test_external_key_replay_conflict_header_precedence_and_concurrency(probe):
    conn, org, _, actor, storage, _ = probe

    async def scenario():
        async with client(app) as browser:
            await authenticate(browser, probe)

            async def post(data=b"key,value,kind\nA,private-value,\n", key="same", field="ignored"):
                return await browser.post(
                    "/api/v1/imports",
                    data={"template": "probe", "external_key": field},
                    files={"file": ("data.csv", data, "text/csv")},
                    headers={**headers(browser, uuid4().hex), "X-Import-Key": key},
                )

            first = await post(key=" same ")
            assert first.status_code == 201, first.text
            second = await post()
            assert second.status_code == 200, second.text
            assert second.headers["Idempotent-Replay"] == "true"
            assert first.json()["id"] == second.json()["id"]
            assert first.json()["external_key"] == "same"
            assert len(storage.objects) == 1
            conflict = await post(b"key,value,kind\nB,different,\n")
            assert conflict.status_code == 409
            assert conflict.json()["checks"]["problem"] == "import_key_conflict"
            for key in (" ", "k" * 129):
                assert (await post(key=key)).status_code == 422
            field = await browser.post(
                "/api/v1/imports",
                data={"template": "probe", "external_key": " field "},
                files={"file": ("data.csv", b"key,value,kind\nC,ok,\n", "text/csv")},
                headers=headers(browser, uuid4().hex),
            )
            assert field.status_code == 201
            assert field.json()["external_key"] == "field"
            # Two independent request connections compete before storing the object.
            results = await asyncio.gather(post(key="concurrent"), post(key="concurrent"))
            assert sorted(r.status_code for r in results) == [200, 201]
            assert results[0].json()["id"] == results[1].json()["id"]
            assert len(storage.objects) == 3
            with tenant_transaction(conn, Scope(org)):
                assert conn.execute("SELECT count(*) FROM import_batch").fetchone() == (3,)

    asyncio.run(scenario())


def test_5000_rows_validate_commit_slices_progress_and_single_notification(probe):
    conn, org, _, actor, _, handler = probe

    async def scenario():
        async with client(app) as browser:
            await authenticate(browser, probe)
            id = await upload(browser, [[str(i), "private-row-value", ""] for i in range(5000)])
            response = await send(browser, "POST", id, "validate")
            assert response.status_code == 202, response.text
            assert response.headers["Location"] == f"/api/v1/imports/{id}"
            for done in range(500, 5001, 500):
                assert tick(probe, clock=sliced_clock()).done == 1
                polled = await browser.get(f"/api/v1/imports/{id}")
                assert polled.json()["progress"]["rows_done"] == done
            assert batch_read(conn, org, id).status == "validated"
            response = await send(browser, "POST", id, "commit", {"mode": "partial"})
            assert response.status_code == 202, response.text
            assert response.headers["Location"] == f"/api/v1/imports/{id}"
            for done in range(500, 5001, 500):
                assert tick(probe, clock=sliced_clock()).done == 1
                assert (await browser.get(f"/api/v1/imports/{id}")).json()["progress"][
                    "rows_done"
                ] == done
            batch = batch_read(conn, org, id)
            assert batch.status == "committed"
            assert batch.result.committed == 5000
            assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (5000,)
            with tenant_transaction(conn, Scope(org)):
                assert conn.execute(
                    "SELECT count(*) FROM import_result WHERE batch_id=%s", (id,)
                ).fetchone() == (5000,)
                rows = conn.execute(
                    "SELECT payload FROM outbox WHERE idempotency_key=%s",
                    (f"import:{id}:finished",),
                ).fetchall()
                assert len(rows) == 1
                payload = rows[0][0]
                assert payload["to"] == identity_email(conn, actor)
                assert payload["context"]["counts"]["committed"] == 5000
                assert payload["context"]["link"].endswith(id)
                assert "private-row-value" not in json.dumps(payload)
                # Plant target: dropping the transition guard tries to enqueue a duplicate.
                ImportService(conn, Scope(org)).notify(UUID(id), "committed", "committed")
                assert conn.execute(
                    "SELECT count(*) FROM outbox WHERE idempotency_key=%s",
                    (f"import:{id}:finished",),
                ).fetchone() == (1,)
            assert len(handler.applied) == len(set(handler.applied)) == 5000

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "phase,mode,expected",
    [("validate", None, 0), ("commit", "atomic", 0), ("commit", "partial", 500)],
)
def test_cancel_between_chunks_validation_atomic_partial(probe, monkeypatch, phase, mode, expected):
    conn, org, _, _, _, _ = probe
    original = ImportWorker.control
    requested = False

    def cancel_after_chunk(worker, job, done=None):
        nonlocal requested
        result = original(worker, job, done)
        batch = batch_read(conn, org, str(job.payload["batch_id"]))
        if batch.progress.rows_done >= 500 and not requested:
            requested = True

            # Real HTTP cancellation through middleware while the worker is between chunks.
            async def cancel():
                async with client(app) as browser:
                    await authenticate(browser, probe)
                    response = await send(
                        browser, "POST", str(batch.id), "cancel", key="cancel-once"
                    )
                    assert response.status_code == 202, response.text
                    replay = await send(browser, "POST", str(batch.id), "cancel", key="cancel-once")
                    assert replay.json() == response.json()

            asyncio.run(cancel())
            return original(worker, job)
        return result

    async def prepare():
        async with client(app) as browser:
            await authenticate(browser, probe)
            id = await upload(browser, [[str(i), "ok", ""] for i in range(1000)])
            assert (await send(browser, "POST", id, "validate")).status_code == 202
            return id

    id = asyncio.run(prepare())
    if phase == "commit":
        assert tick(probe).done == 1

        async def commit():
            async with client(app) as browser:
                await authenticate(browser, probe)
                assert (
                    await send(browser, "POST", id, "commit", {"mode": mode})
                ).status_code == 202

        asyncio.run(commit())
    monkeypatch.setattr(ImportWorker, "control", cancel_after_chunk)
    assert tick(probe).done == 1
    batch = batch_read(conn, org, id)
    assert requested and batch.status == "cancelled"
    assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (expected,)
    with tenant_transaction(conn, Scope(org)):
        assert conn.execute(
            "SELECT count(*) FROM import_result WHERE batch_id=%s", (id,)
        ).fetchone() == (expected,)
        if phase == "validate":
            assert conn.execute(
                "SELECT count(*) FROM import_row WHERE batch_id=%s", (id,)
            ).fetchone() == (0,)
        else:
            assert batch.result.committed == expected
            assert len(batch.result.committed_row_numbers) == expected


def test_transient_crash_resume_consults_durable_results(probe, monkeypatch):
    conn, org, _, _, _, handler = probe
    original = ImportWorker.control
    crashed = False

    async def prepare():
        async with client(app) as browser:
            await authenticate(browser, probe)
            id = await upload(browser, [[str(i), "ok", ""] for i in range(1000)])
            await send(browser, "POST", id, "validate")
            return id

    id = asyncio.run(prepare())
    tick(probe)

    async def commit():
        async with client(app) as browser:
            await authenticate(browser, probe)
            await send(browser, "POST", id, "commit", {"mode": "partial"})

    asyncio.run(commit())

    def kill(worker, job, done=None):
        nonlocal crashed
        result = original(worker, job, done)
        if not crashed and batch_read(conn, org, id).progress.rows_done == 500:
            crashed = True
            # Simulate a crash after durable results but before a checkpoint is available.
            with tenant_transaction(conn, Scope(org)):
                conn.execute(
                    "UPDATE import_batch SET progress=jsonb_set(progress,'{rows_done}','0') "
                    "WHERE id=%s",
                    (id,),
                )
            raise TransientJobError("private crash detail")
        return result

    monkeypatch.setattr(ImportWorker, "control", kill)
    assert tick(probe).retried == 1
    assert batch_read(conn, org, id).status == "committing"
    with tenant_transaction(conn, Scope(org)):
        conn.execute("UPDATE job SET run_after=now() WHERE kind='import.commit'")
    assert tick(probe).done == 1
    assert batch_read(conn, org, id).status == "committed"
    assert len(handler.applied) == len(set(handler.applied)) == 1000
    assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (1000,)


def test_permanent_failure_and_retry_exhaustion_notify_once_without_row_data(probe, monkeypatch):
    conn, org, _, _, _, handler = probe

    async def prepare(mode):
        async with client(app) as browser:
            await authenticate(browser, probe)
            id = await upload(browser, [[str(i), "secret-row-value", ""] for i in range(1000)])
            await send(browser, "POST", id, "validate")
            return id

    id = asyncio.run(prepare("atomic"))
    tick(probe)

    async def commit():
        async with client(app) as browser:
            await authenticate(browser, probe)
            await send(browser, "POST", id, "commit", {"mode": "atomic"})

    asyncio.run(commit())
    handler.fail_key = "600"
    assert tick(probe).dead == 1
    batch = batch_read(conn, org, id)
    assert batch.status == "failed" and batch.error_class == "RuntimeError"
    assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (0,)
    with tenant_transaction(conn, Scope(org)):
        rows = conn.execute(
            "SELECT payload FROM outbox WHERE idempotency_key=%s", (f"import:{id}:failed",)
        ).fetchall()
        assert len(rows) == 1
        assert "secret-row-value" not in json.dumps(rows)
        ImportService(conn, Scope(org)).notify(UUID(id), "failed", "failed")
        conn.execute("UPDATE job SET state='queued',run_after=now() WHERE kind='import.commit'")
    assert tick(probe).done == 1
    with tenant_transaction(conn, Scope(org)):
        assert conn.execute(
            "SELECT count(*) FROM outbox WHERE idempotency_key=%s", (f"import:{id}:failed",)
        ).fetchone() == (1,)
    # Exhausted transient failures enter failed and emit one failure event.
    id2 = asyncio.run(prepare("partial"))

    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError("private database hostname")

    monkeypatch.setattr(ImportWorker, "validate", unavailable)
    with tenant_transaction(conn, Scope(org)):
        conn.execute("UPDATE job SET attempts=4 WHERE state='queued'")
    assert tick(probe).dead == 1
    assert batch_read(conn, org, id2).error_class == "OperationalError"


def test_uploader_without_permission_row_check_and_one_resolver_per_run(probe):
    conn, org, _, actor, _, _ = probe
    opened = []
    checked = []

    @contextmanager
    def denied(scope):
        opened.append(scope)

        def check(user, permission, target):
            checked.append(user)
            return SimpleNamespace(allowed=False)

        yield SimpleNamespace(check=check)

    async def prepare():
        async with client(app) as browser:
            await authenticate(browser, probe)
            id = await upload(browser, [[str(i), "ok", ""] for i in range(501)])
            await send(browser, "POST", id, "validate")
            return id

    id = asyncio.run(prepare())
    assert tick(probe, resolver=denied).done == 1
    assert len(opened) == 1 and set(checked) == {actor}
    batch = batch_read(conn, org, id)
    assert batch.status == "failed_validation" and batch.counts["error"] == 501
    assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (0,)


def test_history_and_cancel_foreign_tenant_routes_and_guards(probe):
    conn, org, other, actor, _, _ = probe

    async def scenario():
        async with client(app) as browser:
            await authenticate(browser, probe)
            ids = [await upload(browser, [[str(i), "ok", ""]]) for i in range(3)]
            with tenant_transaction(conn, Scope(org)):
                conn.execute("UPDATE import_batch SET org_id=%s WHERE id=%s", (other, ids[0]))
            assert (await browser.get(f"/api/v1/imports/{ids[0]}")).status_code == 404
            assert (await send(browser, "POST", ids[0], "cancel")).status_code == 404
            first = await browser.get("/api/v1/imports?page_size=1")
            assert first.status_code == 200
            page = first.json()
            row = page["rows"][0]
            assert row["uploader_id"] == str(actor)
            assert len(row["file_sha256"]) == 64 and row["template_version"] == 1
            assert row["counts"]["rows"] == 1
            assert (
                await browser.get("/api/v1/imports?status=committed&cursor=" + page["next_cursor"])
            ).status_code == 422
            import base64

            foreign_cursor = json.loads(base64.urlsafe_b64decode(page["next_cursor"]))
            foreign_cursor["org"] = str(other)
            encoded = base64.urlsafe_b64encode(json.dumps(foreign_cursor).encode()).decode()
            assert (await browser.get("/api/v1/imports?cursor=" + encoded)).status_code == 422
            second = (
                await browser.get("/api/v1/imports?page_size=1&cursor=" + page["next_cursor"])
            ).json()
            assert {row["id"], second["rows"][0]["id"]} == set(ids[1:])
            assert second["next_cursor"] is None
            assert (await browser.get("/api/v1/imports?status=committed")).json()["rows"] == []
            for query in ("page_size=51", "page_size=0", "cursor=broken", "status=wrong"):
                assert (await browser.get("/api/v1/imports?" + query)).status_code == 422
            for status in (
                "uploaded",
                "validated",
                "failed_validation",
                "failed",
                "cancelled",
                "committed",
            ):
                with tenant_transaction(conn, Scope(org)):
                    conn.execute("UPDATE import_batch SET status=%s WHERE id=%s", (status, ids[1]))
                response = await send(browser, "POST", ids[1], "cancel")
                assert response.status_code == 409
                assert response.json()["checks"]["problem"] == "batch_not_cancellable"
            # Wrong permission in a matching organization grant is forbidden.
            conn.execute("DELETE FROM role_permission WHERE permission_code='import.read'")
            assert (await browser.get("/api/v1/imports")).status_code == 403
            conn.execute("DELETE FROM role_permission WHERE permission_code='import.run'")
            assert (await send(browser, "POST", ids[2], "cancel")).status_code == 403
            with tenant_transaction(conn, Scope(org)):
                conn.execute("DELETE FROM user_role WHERE user_id=%s", (actor,))
            assert (await send(browser, "POST", ids[2], "cancel")).status_code == 404

    asyncio.run(scenario())


def test_atomic_limit_and_identity_email(probe, monkeypatch):
    conn, org, _, actor, _, _ = probe
    assert identity_email(conn, actor)
    assert identity_email(conn, uuid4()) is None
    for limit in (499, 50001):
        with pytest.raises(ValidationError):
            Settings(import_atomic_max_rows=limit)
    monkeypatch.setenv("FLO_IMPORT_ATOMIC_MAX_ROWS", "500")

    async def scenario():
        async with client(app) as browser:
            await authenticate(browser, probe)
            id = await upload(browser, [[str(i), "ok", ""] for i in range(501)])
            await send(browser, "POST", id, "validate")
            return id

    id = asyncio.run(scenario())
    tick(probe)

    async def commit():
        async with client(app) as browser:
            await authenticate(browser, probe)
            response = await send(browser, "POST", id, "commit", {"mode": "atomic"})
            assert response.status_code == 422
            assert response.json()["checks"]["problem"] == "atomic_too_large"
            assert (
                await send(browser, "POST", id, "commit", {"mode": "partial"})
            ).status_code == 202

    asyncio.run(commit())


def test_inline_boundary_and_async_duplicate_key_across_slices(probe):
    conn, org, _, _, _, _ = probe

    async def prepare():
        async with client(app) as browser:
            await authenticate(browser, probe)
            small = await upload(browser, [[str(i), "ok", ""] for i in range(500)])
            assert (await send(browser, "POST", small, "validate")).status_code == 200
            assert (
                await send(browser, "POST", small, "commit", {"mode": "atomic"})
            ).status_code == 200
            rows = [[f"new-{i}", "ok", ""] for i in range(501)]
            rows[-1][0] = rows[0][0]
            big = await upload(browser, rows)
            assert (await send(browser, "POST", big, "validate")).status_code == 202
            for suffix, body in (("validate", None), ("commit", {"mode": "partial"})):
                response = await send(browser, "POST", big, suffix, body)
                assert response.status_code == 409
            return big

    id = asyncio.run(prepare())
    tick(probe, clock=sliced_clock())
    tick(probe)
    batch = batch_read(conn, org, id)
    assert batch.status == "failed_validation" and batch.counts["error"] == 1

    async def commit():
        async with client(app) as browser:
            await authenticate(browser, probe)
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"})
            ).status_code == 409
            assert (
                await send(browser, "POST", id, "commit", {"mode": "partial"})
            ).status_code == 202

    asyncio.run(commit())
    assert tick(probe).done == 1
    report = batch_read(conn, org, id).result
    assert report.committed == 500 and report.skipped_row_numbers == [502]


def test_async_atomic_success_and_permission_revoked_before_commit(probe):
    conn, org, _, _, _, _ = probe

    async def prepare():
        async with client(app) as browser:
            await authenticate(browser, probe)
            id = await upload(browser, [[str(i), "ok", ""] for i in range(501)])
            assert (await send(browser, "POST", id, "validate")).status_code == 202
            return id

    id = asyncio.run(prepare())
    tick(probe)

    async def start_commit(id):
        async with client(app) as browser:
            await authenticate(browser, probe)
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"})
            ).status_code == 202

    asyncio.run(start_commit(id))
    assert tick(probe).done == 1
    assert batch_read(conn, org, id).result.committed == 501
    id2 = asyncio.run(prepare())
    tick(probe)
    asyncio.run(start_commit(id2))

    @contextmanager
    def revoked(scope):
        yield SimpleNamespace(check=lambda *args: SimpleNamespace(allowed=False))

    assert tick(probe, resolver=revoked).dead == 1
    assert batch_read(conn, org, id2).status == "failed"
    assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (501,)
    with tenant_transaction(conn, Scope(org)):
        assert conn.execute(
            "SELECT count(*) FROM import_result WHERE batch_id=%s", (id2,)
        ).fetchone() == (0,)


def test_unique_index_loser_replays_and_removes_losing_object(probe, monkeypatch):
    conn, org, _, actor, storage, _ = probe
    winner = uuid4()
    put = storage.put
    fired = False

    def racing_put(key, data, *, content_type):
        nonlocal fired
        if not fired:
            fired = True
            from hashlib import sha256

            with psycopg.connect(connection_url(conn)) as competitor:
                with tenant_transaction(competitor, Scope(org)):
                    competitor.execute(
                        "INSERT INTO import_batch(id,org_id,template,template_version,uploader_id,"
                        "file_name,file_sha256,file_size,mapping,counts,external_key) "
                        "VALUES(%s,%s,'probe',1,%s,'data.csv',%s,%s,'{}',"
                        '\'{"headers":["key","value","kind"],"rows":1}\',\'race\')',
                        (winner, org, actor, sha256(data).hexdigest(), len(data)),
                    )
            put(str(winner), data, content_type=content_type)
        return put(key, data, content_type=content_type)

    monkeypatch.setattr(storage, "put", racing_put)

    async def scenario():
        async with client(app) as browser:
            await authenticate(browser, probe)
            response = await browser.post(
                "/api/v1/imports",
                data={"template": "probe"},
                files={"file": ("data.csv", b"key,value,kind\nA,ok,\n", "text/csv")},
                headers={**headers(browser, uuid4().hex), "X-Import-Key": "race"},
            )
            assert response.status_code == 200, response.text
            assert response.headers["Idempotent-Replay"] == "true"
            assert response.json()["id"] == str(winner)

    asyncio.run(scenario())
    assert set(storage.objects) == {str(winner)}


def test_progress_schema_rejects_negative_counters():
    from flo.modules.imports.schemas import ImportProgress

    for done, total in ((-1, 0), (0, -1)):
        with pytest.raises(ValidationError):
            ImportProgress(phase="validating", rows_done=done, rows_total=total)


def test_late_atomic_cancel_cannot_transition_to_committed(probe, monkeypatch):
    conn, org, _, _, _, _ = probe
    original = ImportWorker.save_report

    async def prepare():
        async with client(app) as browser:
            await authenticate(browser, probe)
            id = await upload(browser, [[str(i), "ok", ""] for i in range(501)])
            await send(browser, "POST", id, "validate")
            return id

    id = asyncio.run(prepare())
    tick(probe)

    async def commit():
        async with client(app) as browser:
            await authenticate(browser, probe)
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"})
            ).status_code == 202

    asyncio.run(commit())

    def late_cancel(worker, service, batch, report, done):
        async def cancel():
            async with client(app) as browser:
                await authenticate(browser, probe)
                assert (await send(browser, "POST", id, "cancel")).status_code == 202

        asyncio.run(cancel())
        original(worker, service, batch, report, done)

    monkeypatch.setattr(ImportWorker, "save_report", late_cancel)
    assert tick(probe).done == 1
    assert batch_read(conn, org, id).status == "cancelled"
    assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (0,)
    with tenant_transaction(conn, Scope(org)):
        assert conn.execute(
            "SELECT count(*) FROM import_result WHERE batch_id=%s", (id,)
        ).fetchone() == (0,)
