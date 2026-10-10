"""Atomic rollback, partial recovery, re-planning and state-machine contracts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from tests.imports.test_validation import send, upload
from tests.isolation.test_real_sessions import client, headers, login
from tests.org.test_bootstrap import connection_url


def assert_problem(response, label):
    assert response.status_code == 409, response.text
    assert response.json()["type"].endswith("/conflict")
    assert response.json()["checks"]["problem"] == label


def test_atomic_clean_and_idempotency(probe):
    conn, org, _, _, _, _ = probe

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [[str(i), "ok", ""] for i in range(100)])
            assert_problem(
                await send(browser, "POST", id, "commit", {"mode": "atomic"}), "not_validated"
            )
            missing_key = await browser.post(
                f"/api/v1/imports/{id}/validate",
                headers={
                    k: v for k, v in headers(browser, "unused").items() if k != "Idempotency-Key"
                },
            )
            assert missing_key.status_code == 400
            await send(browser, "POST", id, "validate")
            result = await send(browser, "POST", id, "commit", {"mode": "atomic"}, "commit-once")
            assert result.status_code == 200, result.text
            report = result.json()["result"]
            assert report == {
                "mode": "atomic",
                "committed": 100,
                "skipped_errors": 0,
                "unchanged": 0,
                "committed_row_numbers": list(range(2, 102)),
                "skipped_row_numbers": [],
                "recovery": None,
            }
            assert (
                await send(browser, "POST", id, "commit", {"mode": "atomic"}, "commit-once")
            ).json() == result.json()
            with tenant_transaction(conn, Scope(org)):
                assert conn.execute(
                    "SELECT count(*) FROM import_result WHERE batch_id=%s", (id,)
                ).fetchone() == (100,)
                assert conn.execute(
                    "SELECT count(*) FROM audit_log WHERE action='import.commit'"
                ).fetchone() == (1,)
                after = conn.execute(
                    "SELECT after FROM audit_log WHERE action='import.commit'"
                ).fetchone()[0]
                assert after == {"committed": 100, "skipped_errors": 0, "unchanged": 0}
            assert_problem(await send(browser, "POST", id, "validate"), "batch_not_editable")
            assert_problem(
                await send(browser, "POST", id, "commit", {"mode": "atomic"}), "batch_not_editable"
            )
            assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (100,)

    asyncio.run(scenario())


def test_atomic_row_50_rolls_back_everything_and_safe_failure(probe):
    conn, org, _, _, _, handler = probe
    handler.fail_key = "50"

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [[str(i), "private-row-text", ""] for i in range(1, 101)])
            await send(browser, "POST", id, "validate")
            response = await send(browser, "POST", id, "commit", {"mode": "atomic"})
            assert response.status_code == 500
            assert "private-row" not in response.text
            assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (0,)
            with tenant_transaction(conn, Scope(org)):
                assert conn.execute(
                    "SELECT status,result FROM import_batch WHERE id=%s", (id,)
                ).fetchone() == ("failed", None)
                assert conn.execute(
                    "SELECT count(*) FROM import_result WHERE batch_id=%s", (id,)
                ).fetchone() == (0,)
                assert conn.execute(
                    "SELECT count(*) FROM audit_log WHERE action='import.commit'"
                ).fetchone() == (0,)

    asyncio.run(scenario())


def test_partial_errors_savepoints_recovery_and_corrected_reupload(probe):
    conn, _, _, _, _, handler = probe
    handler.fail_key = "F"

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rows = [["A", "ok", ""], ["B", "warn", ""], ["C", "ok", "bad-code"], ["F", "ok", ""]]
            id = await upload(browser, rows)
            await send(browser, "POST", id, "validate")
            assert_problem(
                await send(browser, "POST", id, "commit", {"mode": "atomic"}), "has_errors"
            )
            response = await send(browser, "POST", id, "commit", {"mode": "partial"})
            assert response.status_code == 200, response.text
            report = response.json()["result"]
            assert report["committed_row_numbers"] == [2, 3]
            assert report["skipped_row_numbers"] == [4, 5]
            assert report["committed"] == report["skipped_errors"] == 2
            assert report["recovery"] == (
                "Fix rows 4 and 5 in the file and upload it again. "
                "Rows that were committed will be skipped as unchanged."
            )
            assert conn.execute("SELECT key FROM import_probe ORDER BY key").fetchall() == [
                ("A",),
                ("B",),
            ]
            handler.fail_key = None
            rows[2][2] = ""
            corrected = await upload(browser, rows)
            validated = await send(browser, "POST", corrected, "validate")
            assert validated.json()["counts"]["skip"] == 2
            preview = (await send(browser, "GET", corrected, "preview?kind=skip")).json()
            assert [row["row_no"] for row in preview["rows"]] == [2, 3]
            final = await send(browser, "POST", corrected, "commit", {"mode": "partial"})
            assert final.json()["result"]["committed_row_numbers"] == [4, 5]
            assert final.json()["result"]["unchanged"] == 2
            assert final.json()["result"]["recovery"] is None
            assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (4,)

    asyncio.run(scenario())


@pytest.mark.parametrize("drift", ["value", "classification", "permission"])
def test_stale_validation_replans(probe, drift):
    conn, org, _, _, _, _ = probe
    conn.execute("INSERT INTO import_probe VALUES(%s,%s,'A','before')", (uuid4(), org))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [["A", "after", ""]])
            await send(browser, "POST", id, "validate")
            if drift == "permission":
                conn.execute("DELETE FROM role_permission WHERE permission_code='project.update'")
            elif drift == "classification":
                conn.execute("DELETE FROM import_probe")
            else:
                conn.execute("UPDATE import_probe SET value='drift'")
            response = await send(browser, "POST", id, "commit", {"mode": "atomic"})
            assert_problem(response, "stale_validation")
            batch = await browser.get(f"/api/v1/imports/{id}")
            assert batch.json()["status"] == "validated"
            with tenant_transaction(conn, Scope(org)):
                assert conn.execute(
                    "SELECT count(*) FROM import_result WHERE batch_id=%s", (id,)
                ).fetchone() == (0,)

    asyncio.run(scenario())


def test_target_permission_errors_cannot_apply(probe):
    conn, org, _, _, _, handler = probe
    conn.execute("INSERT INTO import_probe VALUES(%s,%s,'B','before')", (uuid4(), org))
    conn.execute("DELETE FROM role_permission WHERE permission_code='project.create'")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [["A", "ok", ""], ["B", "after", ""]])
            result = await send(browser, "POST", id, "validate")
            assert result.status_code == 200
            assert result.json()["status"] == "failed_validation"
            preview = (await send(browser, "GET", id, "preview")).json()["rows"]
            assert preview[0]["action"] == "error"
            assert preview[0]["issues"][0]["code"] == "permission_denied"
            assert_problem(
                await send(browser, "POST", id, "commit", {"mode": "atomic"}), "has_errors"
            )
            partial = await send(browser, "POST", id, "commit", {"mode": "partial"})
            assert partial.status_code == 200, partial.text
            assert partial.json()["result"]["committed_row_numbers"] == [3]
            assert partial.json()["result"]["skipped_row_numbers"] == [2]
            assert handler.applied == ["B"]
            assert conn.execute("SELECT count(*) FROM import_probe").fetchone() == (1,)

    asyncio.run(scenario())


def test_competing_advisory_lock_waits(probe):
    conn, org, _, _, _, _ = probe
    locked = Event()
    release = Event()

    def hold():
        with psycopg.connect(connection_url(conn)) as competitor:
            competitor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"{org}:probe",)
            )
            locked.set()
            assert release.wait(15)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [["A", "ok", ""]])
            await send(browser, "POST", id, "validate")
            with ThreadPoolExecutor() as pool:
                holder = pool.submit(hold)
                assert locked.wait(5)
                pending = asyncio.create_task(
                    send(browser, "POST", id, "commit", {"mode": "atomic"})
                )
                try:
                    for _ in range(100):
                        waiting = conn.execute(
                            "SELECT count(*) FROM pg_locks "
                            "WHERE locktype='advisory' AND NOT granted"
                        ).fetchone()[0]
                        if waiting:
                            break
                        await asyncio.sleep(0.02)
                    assert waiting
                    assert not pending.done()
                finally:
                    release.set()
                assert (await pending).status_code == 200
                holder.result()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status",
    [
        "uploaded",
        "validating",
        "validated",
        "failed_validation",
        "committing",
        "committed",
        "failed",
        "cancelled",
    ],
)
def test_status_guards(probe, status):
    conn, org, _, _, _, _ = probe

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [["A", "ok", ""]])
            await send(browser, "POST", id, "validate")
            with tenant_transaction(conn, Scope(org)):
                conn.execute("UPDATE import_batch SET status=%s WHERE id=%s", (status, id))
            response = await send(browser, "POST", id, "validate")
            if status in {"uploaded", "validated", "failed_validation"}:
                assert response.status_code == 200
            else:
                assert_problem(response, "batch_not_editable")
            with tenant_transaction(conn, Scope(org)):
                conn.execute("UPDATE import_batch SET status=%s WHERE id=%s", (status, id))
            response = await send(browser, "POST", id, "commit", {"mode": "atomic"})
            if status == "validated":
                assert response.status_code == 200
            else:
                assert_problem(
                    response,
                    "not_validated"
                    if status in {"uploaded", "validating"}
                    else "has_errors"
                    if status == "failed_validation"
                    else "batch_not_editable",
                )

    asyncio.run(scenario())


def test_partial_nothing_to_commit(probe):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [["A", "ok", "bad-code"]])
            await send(browser, "POST", id, "validate")
            assert_problem(
                await send(browser, "POST", id, "commit", {"mode": "partial"}), "nothing_to_commit"
            )

    asyncio.run(scenario())


def test_recovery_limits_row_list():
    from flo.modules.imports.commit import recovery_text

    text = recovery_text(list(range(2, 27)))
    assert text.startswith("Fix rows 2, 3, 4")
    assert "20, 21 and 5 more in the file" in text
    assert "22," not in text
    assert "Rows that were committed will be skipped as unchanged." in text


def test_partial_failed_row_restores_apply_scratch(probe, monkeypatch):
    _, _, _, _, _, handler = probe
    original = handler.apply_row
    handler.fail_key = "F"

    def apply(context, plan, values):
        previous = context.scratch.setdefault("created", [])
        if values["key"] == "B":
            assert previous == ["A"]
        previous.append(values["key"])
        return original(context, plan, values)

    monkeypatch.setattr(handler, "apply_row", apply)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            id = await upload(browser, [["A", "ok", ""], ["F", "ok", ""], ["B", "ok", ""]])
            await send(browser, "POST", id, "validate")
            response = await send(browser, "POST", id, "commit", {"mode": "partial"})
            assert response.status_code == 200, response.text
            assert response.json()["result"]["committed_row_numbers"] == [2, 4]
            assert response.json()["result"]["skipped_row_numbers"] == [3]

    asyncio.run(scenario())
