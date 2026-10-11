"""PROJ-016 risk contracts through the production application and real Postgres."""

import asyncio
import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.audit import AuditWriter
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.projects.schemas import RiskCreate
from flo.modules.projects.service import ProjectService
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db
from tests.projects.test_schedule import schedule_db as schedule_db


def payload(root, **changes):
    return {
        "title": " Risk ",
        "likelihood": 3,
        "impact": 4,
        "owner_id": str(root.owner_id),
    } | changes


async def send(browser, method, root, data=None, risk=None, version="1", query="", key=None):
    path = f"/api/v1/projects/{root.id}/risks" + ("/" + str(risk) if risk else "") + query
    return await browser.request(
        method,
        path,
        json=data,
        headers=headers(browser, key or uuid4().hex)
        | ({"If-Match": version} if version is not None else {}),
    )


def test_risk_create_update_close_reopen_audit_and_idempotency(schedule_db, monkeypatch):
    conn, org, _, (root, _) = schedule_db
    monkeypatch.setattr("flo.modules.projects.risks.today", lambda: date(2026, 6, 1))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            data = payload(root, due_date="2026-05-31", description=" <script>plain</script> ")
            created = await send(browser, "POST", root, data, key="risk-once")
            assert created.status_code == 201, created.text
            row = created.json()
            assert row["title"] == "Risk" and row["score"] == 12 and row["overdue"]
            assert row["description"] == "<script>plain</script>"
            assert (await send(browser, "POST", root, data, key="risk-once")).json() == row
            rid = row["id"]
            for version in (None, "bad"):
                result = await send(browser, "PATCH", root, {}, rid, version)
                assert result.status_code == 400
                if version is None:
                    assert result.json()["checks"]["problem"] == "if_match_required"
            for reason in (None, "", "   "):
                result = await send(
                    browser, "PATCH", root, {"status": "closed", "closed_reason": reason}, rid
                )
                assert result.status_code == 422
                assert result.json()["errors"][0]["field"] == "closed_reason"
            closed = await send(
                browser, "PATCH", root, {"status": "closed", "closed_reason": " resolved "}, rid
            )
            assert closed.status_code == 200, closed.text
            assert closed.json()["closed_reason"] == "resolved"
            assert not closed.json()["overdue"] and closed.json()["version"] == 2
            assert closed.json()["updated_at"] >= row["updated_at"]
            stale = await send(browser, "PATCH", root, {}, rid)
            assert stale.status_code == 409
            assert stale.json()["checks"]["problem"] == "stale_version"
            for n, status in enumerate(("accepted", "mitigating", "open"), 2):
                result = await send(
                    browser,
                    "PATCH",
                    root,
                    {"status": status, "closed_reason": "discard"},
                    rid,
                    str(n),
                )
                assert result.status_code == 200, result.text
                assert result.json()["closed_reason"] is None
                assert result.json()["overdue"] == (status != "accepted")
            assert (await send(browser, "GET", root)).json()["rows"][0]["version"] == 5

    asyncio.run(scenario())
    with tenant_transaction(conn, Scope(org)):
        rows = conn.execute(
            "SELECT action,before,after FROM audit_log "
            "WHERE target_type='project_risk' ORDER BY occurred_at,id"
        ).fetchall()
        assert len(rows) == 5 and rows[0][0] == "project.risk.create"
        assert rows[1][1]["status"] == "open" and rows[1][2]["status"] == "closed"
        assert rows[2][1]["status"] == "closed" and rows[2][2]["status"] == "accepted"


def test_risk_order_filters_cursor_and_overdue(schedule_db, monkeypatch):
    _, _, _, (root, _) = schedule_db
    monkeypatch.setattr("flo.modules.projects.risks.today", lambda: date(2026, 6, 1))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for score, due, status in (
                (5, None, "open"),
                (5, "2026-06-01", "open"),
                (5, "2026-05-31", "mitigating"),
                (5, None, "accepted"),
                (1, "2026-05-31", "accepted"),
            ):
                response = await send(
                    browser,
                    "POST",
                    root,
                    payload(root, likelihood=score, impact=1, due_date=due, status=status),
                )
                assert response.status_code == 201, response.text
            all_rows = (await send(browser, "GET", root)).json()["rows"]
            assert [r["score"] for r in all_rows] == [5, 5, 5, 5, 1]
            assert [r["due_date"] for r in all_rows[:4]] == ["2026-05-31", "2026-06-01", None, None]
            assert [r["overdue"] for r in all_rows] == [True, False, False, False, False]
            assert [r["id"] for r in all_rows[2:4]] == sorted(r["id"] for r in all_rows[2:4])
            paged = []
            cursor = None
            while True:
                query = "?page_size=1" + ("&cursor=" + cursor if cursor else "")
                result = await send(browser, "GET", root, query=query)
                assert result.status_code == 200, result.text
                paged += result.json()["rows"]
                cursor = result.json()["next_cursor"]
                if cursor is None:
                    break
            assert paged == all_rows
            for malformed in (
                {"score": 5, "due_date": None, "id": 123},
                {"score": True, "due_date": None, "id": str(uuid4())},
                {"score": 5, "due_date": 123, "id": str(uuid4())},
                {"score": 5, "due_date": "bad", "id": str(uuid4())},
            ):
                encoded = base64.urlsafe_b64encode(json.dumps(malformed).encode()).decode()
                result = await send(browser, "GET", root, query="?cursor=" + encoded)
                assert result.status_code == 422, result.text
                assert result.json()["errors"][0]["field"] == "cursor"
            filtered = await send(browser, "GET", root, query="?min_score=5&status=open")
            assert len(filtered.json()["rows"]) == 2
            for query in (
                "?cursor=bad",
                "?cursor=W10=",
                "?min_score=0",
                "?min_score=26",
                "?page_size=0",
                "?page_size=51",
                "?status=bad",
            ):
                assert (await send(browser, "GET", root, query=query)).status_code == 422

    asyncio.run(scenario())


def test_risk_validation_owner_and_null_guards(schedule_db):
    _, _, _, (root, foreign) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rid = (await send(browser, "POST", root, payload(root))).json()["id"]
            invalid = [
                {field: value} for field in ("likelihood", "impact") for value in (0, 6, 1.5, True)
            ]
            invalid += [
                {"owner_id": str(foreign.owner_id)},
                {"owner_id": str(uuid4())},
                {"title": " "},
                {"title": "x" * 201},
                {"description": "x" * 4001},
                {"mitigation": "x" * 4001},
                {"status": "bad"},
            ]
            for changes in invalid:
                assert (
                    await send(browser, "POST", root, payload(root, **changes))
                ).status_code == 422
                assert (await send(browser, "PATCH", root, changes, rid)).status_code == 422
            for field in ("title", "owner_id", "likelihood", "impact", "status"):
                assert (await send(browser, "PATCH", root, {field: None}, rid)).status_code == 422
            assert (
                await send(browser, "POST", root, payload(root, owner_id=None))
            ).status_code == 422

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
def test_risk_foreign_tenant_routes(schedule_db, method):
    conn, _, foreign_org, (root, foreign) = schedule_db
    with correlation_context("foreign-risk"):
        risk = ProjectService(conn, Scope(foreign_org), foreign.owner_id).risks.write(
            foreign.id, RiskCreate.model_validate(payload(foreign))
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            result = await send(
                browser,
                method,
                foreign,
                payload(root) if method != "GET" else None,
                risk.id if method == "PATCH" else None,
            )
            assert result.status_code == 404, result.text
            if method == "PATCH":
                assert (await send(browser, method, root, {}, risk.id)).status_code == 404
                assert (await send(browser, method, root, {}, uuid4())).status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status", ["draft", "approval_pending", "active", "deferred", "completed", "abandoned"]
)
def test_risk_project_closed_guard(schedule_db, status):
    conn, _, _, (root, _) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rid = (await send(browser, "POST", root, payload(root))).json()["id"]
            conn.execute("UPDATE project SET status=%s WHERE id=%s", (status, root.id))
            for method, risk in (("POST", None), ("PATCH", rid)):
                result = await send(browser, method, root, payload(root), risk)
                expected = (
                    409 if status in ("completed", "abandoned") else (201 if risk is None else 200)
                )
                assert result.status_code == expected, result.text
                if expected == 409:
                    assert result.json()["checks"]["problem"] == "project_closed"
            assert (await send(browser, "GET", root)).status_code == 200

    asyncio.run(scenario())


def test_risk_database_checks_and_audit_rollback(schedule_db):
    conn, org, foreign_org, (root, foreign) = schedule_db
    service = ProjectService(conn, Scope(org), root.owner_id).risks
    with correlation_context("risk-checks"):
        risk = service.write(root.id, RiskCreate.model_validate(payload(root)))
        with tenant_transaction(conn, Scope(org)):
            for likelihood in (0, 6):
                with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                    conn.execute(
                        "INSERT INTO project_risk(id,org_id,project_id,title,likelihood,impact,"
                        "owner_id,created_by) VALUES (%s,%s,%s,'Invalid',%s,1,%s,%s)",
                        (uuid4(), org, root.id, likelihood, root.owner_id, root.owner_id),
                    )
            for assignment in (
                "likelihood=0",
                "likelihood=6",
                "impact=0",
                "impact=6",
                "title=''",
                "title=repeat('x',201)",
                "description=repeat('x',4001)",
                "mitigation=repeat('x',4001)",
                "status='bad'",
                "status='closed',closed_reason=NULL",
                "status='closed',closed_reason='   '",
            ):
                with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                    conn.execute(f"UPDATE project_risk SET {assignment} WHERE id=%s", (risk.id,))
            with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
                conn.execute(
                    "UPDATE project_risk SET project_id=%s WHERE id=%s", (foreign.id, risk.id)
                )
            with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
                conn.execute(
                    "UPDATE project_risk SET org_id=%s WHERE id=%s", (foreign_org, risk.id)
                )
            with pytest.raises(psycopg.errors.GeneratedAlways), conn.transaction():
                conn.execute("UPDATE project_risk SET score=1 WHERE id=%s", (risk.id,))
            assert conn.execute(
                "SELECT bool_and(score=likelihood*impact) FROM project_risk"
            ).fetchone() == (True,)
        with patch.object(AuditWriter, "write", side_effect=RuntimeError("planted audit failure")):
            with pytest.raises(RuntimeError, match="planted audit failure"):
                service.write(root.id, RiskCreate.model_validate(payload(root)))
        assert len(service.list(root.id).rows) == 1


def test_risk_read_only_and_no_scope(schedule_db):
    conn, org, _, (root, _) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rid = (await send(browser, "POST", root, payload(root))).json()["id"]
            from flo.modules.identity.service import IdentityAuthorizationService

            with correlation_context("risk-reader"), tenant_transaction(conn, Scope(org)):
                identity = IdentityAuthorizationService(conn, Scope(org), root.owner_id)
                role = identity.create_role("risk-reader", "Risk reader")
                identity.grant_permission(role.id, "project.read")
                conn.execute(
                    "UPDATE user_role SET role_id=%s WHERE user_id=%s", (role.id, root.owner_id)
                )
            assert (await send(browser, "GET", root)).status_code == 200
            for method, risk in (("POST", None), ("PATCH", rid)):
                assert (await send(browser, method, root, payload(root), risk)).status_code == 403
            conn.execute("DELETE FROM user_role WHERE user_id=%s", (root.owner_id,))
            for method, risk in (("GET", None), ("POST", None), ("PATCH", rid)):
                assert (
                    await send(
                        browser, method, root, payload(root) if method != "GET" else None, risk
                    )
                ).status_code == 404

    asyncio.run(scenario())


def test_risk_concurrent_patch_same_version(schedule_db):
    conn, org, _, (root, _) = schedule_db
    barrier = Barrier(2)

    async def seed():
        async with client(app) as browser:
            await login(browser)
            rid = (await send(browser, "POST", root, payload(root))).json()["id"]
            cookies = dict(browser.cookies)
            request_headers = headers(browser) | {"If-Match": "1"}
            return rid, cookies, request_headers

    rid, cookies, request_headers = asyncio.run(seed())
    # Both requests lock the same row only after reaching the read, on independent connections.
    from flo.modules.projects.risks import Risks

    original = Risks._row

    def synchronized(self, project_id, risk_id, lock=False):
        if lock:
            barrier.wait(timeout=10)
        return original(self, project_id, risk_id, lock)

    def worker(title):
        async def scenario():
            async with client(app) as browser:
                browser.cookies.update(cookies)
                return await browser.patch(
                    f"/api/v1/projects/{root.id}/risks/{rid}",
                    json={"title": title},
                    headers=request_headers,
                )

        return asyncio.run(scenario())

    with patch.object(Risks, "_row", synchronized), ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(worker, ("First", "Second")))
    assert sorted(r.status_code for r in responses) == [200, 409]
    with tenant_transaction(conn, Scope(org)):
        assert conn.execute("SELECT version FROM project_risk WHERE id=%s", (rid,)).fetchone() == (
            2,
        )


def test_no_risk_delete_route():
    assert not [
        route
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1/projects/{id}/risks")
        and "DELETE" in getattr(route, "methods", set())
    ]


def test_risk_rls_rejects_cross_tenant_write(schedule_db):
    from psycopg import sql

    conn, org, foreign_org, (root, foreign) = schedule_db
    with correlation_context("rls-risk"):
        risk = ProjectService(conn, Scope(org), root.owner_id).risks.write(
            root.id, RiskCreate.model_validate(payload(root))
        )
    role = "risk_rls_" + uuid4().hex
    with conn.transaction():
        conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        conn.execute(
            sql.SQL("GRANT SELECT, UPDATE ON project_risk TO {}").format(sql.Identifier(role))
        )
        with tenant_transaction(conn, Scope(org)):
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            assert conn.execute("SELECT id FROM project_risk").fetchall() == [(risk.id,)]
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(
                    "UPDATE project_risk SET org_id=%s,project_id=%s WHERE id=%s",
                    (foreign_org, foreign.id, risk.id),
                )
            conn.execute("SET LOCAL ROLE NONE")
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_risk_update_row_lock_blocks_competing_writer(schedule_db):
    """A competing connection cannot modify the version snapshot before commit."""
    from flo.modules.projects.risks import Risks
    from tests.org.test_bootstrap import connection_url

    conn, org, _, (root, _) = schedule_db
    with correlation_context("risk-lock"):
        service = ProjectService(conn, Scope(org), root.owner_id).risks
        risk = service.write(root.id, RiskCreate.model_validate(payload(root)))
    with psycopg.connect(connection_url(conn), autocommit=True) as other:
        with tenant_transaction(conn, Scope(org)):
            Risks(service.repo, root.owner_id)._row(root.id, risk.id, True)
            with tenant_transaction(other, Scope(org)):
                other.execute("SET LOCAL lock_timeout='100ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable), other.transaction():
                    other.execute(
                        "UPDATE project_risk SET title='Competing' WHERE id=%s", (risk.id,)
                    )


def test_risk_belongs_to_requested_project(schedule_db):
    from flo.modules.projects.schemas import ProjectCreate
    from tests.org.test_bootstrap import settings

    conn, org, _, (root, _) = schedule_db
    with correlation_context("other-project-risk"):
        other = ProjectService(conn, Scope(org), root.owner_id, settings(conn)).create(
            ProjectCreate(
                bu_id=root.bu_id,
                name="Other project",
                department_code="D",
                ledger_account_code="L",
                currency="USD",
            )
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rid = (await send(browser, "POST", root, payload(root))).json()["id"]
            response = await send(browser, "PATCH", other, {"title": "Wrong project"}, rid)
            assert response.status_code == 404, response.text
            assert (await send(browser, "GET", root)).json()["rows"][0]["title"] == "Risk"

    asyncio.run(scenario())
