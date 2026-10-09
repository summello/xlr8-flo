"""PROJ-011/015 schedule contracts exercised through the production app."""

import asyncio
from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.audit import AuditWriter
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.org.schemas import MasterCreate, OrgUnitCreate
from flo.modules.org.service import OrgService
from flo.modules.projects.models import ProjectRepository
from flo.modules.projects.schedule import Schedule
from flo.modules.projects.schemas import PhaseCreate, ProjectCreate
from flo.modules.projects.service import ProjectService
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import admin_id, connection_url, create, settings
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db


@pytest.fixture
def schedule_db(bootstrap_db, monkeypatch):
    conn = bootstrap_db
    a = create(conn)
    b = create(conn, "SECOND", "second@example.test")
    monkeypatch.setenv("DATABASE_URL", connection_url(conn))
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", "A" * 43)
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_MEMORY_COST_KIB", "8192")
    from flo.api.origin_auth import require_origin_secret

    app.dependency_overrides[require_origin_secret] = lambda: None
    app.middleware_stack = None
    with correlation_context("schedule-seed"):
        roots = []
        for tenant, email in ((a, "admin@example.test"), (b, "second@example.test")):
            scope = Scope(tenant.org_id)
            actor = admin_id(conn, email)
            org = OrgService(conn, scope, actor)
            bu = org.create_unit(OrgUnitCreate(code="BU", name="BU", kind="bu"))
            for kind, code in (("department", "D"), ("ledger_account", "L")):
                org.create_master(
                    kind,
                    MasterCreate(
                        code=code,
                        name=code,
                        effective_from=date(2000, 1, 1),
                        attributes={"account_type": "expense"} if kind == "ledger_account" else {},
                    ),
                )
            svc = ProjectService(conn, scope, actor, settings(conn))
            root = svc.create(
                ProjectCreate(
                    bu_id=bu.id,
                    name="Root",
                    department_code="D",
                    ledger_account_code="L",
                    currency="USD",
                    planned_start=date(2026, 1, 1),
                    planned_end=date(2026, 12, 31),
                )
            )
            roots.append(root)
    yield conn, a.org_id, b.org_id, roots
    app.dependency_overrides.clear()
    app.middleware_stack = None


async def send(browser, method, path, payload=None, key=None):
    return await browser.request(
        method,
        "/api/v1/projects/" + path,
        json=payload,
        headers=headers(browser, key or uuid4().hex),
    )


def test_schedule_happy_path_project_patch_variance_and_audit(schedule_db, monkeypatch):
    conn, org, _, (root, _) = schedule_db
    monkeypatch.setattr("flo.modules.projects.schedule.today", lambda: date(2027, 1, 10))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            path = str(root.id)
            created = await send(browser, "POST", path + "/phases", {"name": "Plan"}, "phase-once")
            assert created.status_code == 201, created.text
            phase = created.json()
            assert phase["sequence"] == 1 and phase["schedule_variance_days"] is None
            replay = await send(browser, "POST", path + "/phases", {"name": "Plan"}, "phase-once")
            assert replay.json() == phase
            result = await send(
                browser,
                "PATCH",
                path + "/phases/" + phase["id"],
                {"planned_end": "2026-12-31", "percent_complete": 45, "status": "done"},
            )
            assert result.status_code == 200, result.text
            assert result.json()["schedule_variance_days"] == 10
            assert result.json()["name"] == "Plan"
            assert (await send(browser, "POST", path + "/phases", {"name": "Build"})).json()[
                "sequence"
            ] == 2
            collision = await send(browser, "POST", path + "/phases", {"name": "X", "sequence": 1})
            assert collision.status_code == 409
            assert collision.json()["checks"]["problem"] == "sequence_taken"
            milestone = await send(
                browser,
                "POST",
                path + "/milestones",
                {"name": "Launch", "due_date": "2026-12-31", "phase_id": phase["id"]},
            )
            assert milestone.status_code == 201, milestone.text
            mid = milestone.json()["id"]
            for completed in ("2027-01-10", None):
                updated = await send(
                    browser, "PATCH", path + "/milestones/" + mid, {"completed_on": completed}
                )
                assert updated.status_code == 200, updated.text
                assert updated.json()["completed_on"] == completed
            assert [
                p["sequence"] for p in (await send(browser, "GET", path + "/phases")).json()
            ] == [1, 2]
            assert (await send(browser, "GET", path + "/milestones")).json()[0]["id"] == mid
            result = await browser.patch(
                "/api/v1/projects/" + path,
                headers=headers(browser) | {"If-Match": "1"},
                json={
                    "health": "at_risk",
                    "percent_complete": 30,
                    "actual_start": "2026-01-01",
                    "actual_end": "2027-01-03",
                    "owner_id": str(root.owner_id),
                    "sponsor_id": str(root.owner_id),
                },
            )
            assert result.status_code == 200, result.text
            assert result.json()["schedule_variance_days"] == 3
            assert (await send(browser, "GET", path)).json() == result.json()

    asyncio.run(scenario())
    with tenant_transaction(conn, Scope(org)):
        actions = [
            r[0]
            for r in conn.execute(
                "SELECT action FROM audit_log WHERE action LIKE 'project.%' "
                "ORDER BY occurred_at, id"
            ).fetchall()
        ]
        assert actions.count("project.phase.create") == 2
        assert actions.count("project.phase.update") == 1
        assert actions.count("project.milestone.create") == 1
        assert actions.count("project.milestone.update") == 2
        assert actions.count("project.update") == 1
        before, after = conn.execute(
            "SELECT before, after FROM audit_log WHERE action='project.update' AND target_id=%s",
            (root.id,),
        ).fetchone()
        assert before["health"] == "unknown" and before["percent_complete"] == 0
        assert after["health"] == "at_risk" and after["percent_complete"] == 30
        assert after["owner_id"] == after["sponsor_id"] == str(root.owner_id)
        assert after["actual_end"] == "2027-01-03"


@pytest.mark.parametrize("kind", ["phases", "milestones"])
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
def test_schedule_foreign_tenant_routes(schedule_db, kind, method):
    conn, _, foreign_org, (root, foreign) = schedule_db
    with correlation_context("foreign-schedule"), tenant_transaction(conn, Scope(foreign_org)):
        row_id = uuid4()
        columns, values = (
            ("name, sequence", "'foreign', 1")
            if kind == "phases"
            else ("name, due_date", "'foreign', '2026-06-01'")
        )
        conn.execute(
            f"INSERT INTO project_{kind[:-1]} (id, org_id, project_id, {columns}) "
            f"VALUES (%s,%s,%s,{values})",
            (row_id, foreign_org, foreign.id),
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            payload = {"name": "X"} if kind == "phases" else {"name": "X", "due_date": "2026-06-01"}
            suffix = "/" + str(row_id) if method == "PATCH" else ""
            result = await send(
                browser,
                method,
                f"{foreign.id}/{kind}" + suffix,
                payload if method != "GET" else None,
            )
            assert result.status_code == 404, result.text
            if method == "PATCH":
                assert (
                    await send(browser, method, f"{root.id}/{kind}" + suffix, payload)
                ).status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status", ["draft", "approval_pending", "active", "deferred", "completed", "abandoned"]
)
def test_closed_project_guard(schedule_db, status):
    conn, _, _, (root, _) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rows = []
            for kind, payload in (
                ("phases", {"name": "X"}),
                ("milestones", {"name": "X", "due_date": "2026-06-01"}),
            ):
                row = await send(browser, "POST", f"{root.id}/{kind}", payload)
                assert row.status_code == 201, row.text
                rows.append((kind, payload, row.json()["id"]))
            conn.execute("UPDATE project SET status=%s WHERE id=%s", (status, root.id))
            for kind, payload, row_id in rows:
                for method, suffix in (("POST", ""), ("PATCH", "/" + row_id)):
                    result = await send(browser, method, f"{root.id}/{kind}" + suffix, payload)
                    assert result.status_code == (
                        409
                        if status in ("completed", "abandoned")
                        else 201
                        if method == "POST"
                        else 200
                    ), result.text
                    if result.status_code == 409:
                        assert result.json()["checks"]["problem"] == "project_closed"
                assert (await send(browser, "GET", f"{root.id}/{kind}")).status_code == 200

    asyncio.run(scenario())


def test_schedule_validation_and_child_guard(schedule_db):
    conn, org, _, (root, foreign) = schedule_db
    with correlation_context("child-seed"):
        svc = ProjectService(conn, Scope(org), root.owner_id, settings(conn))

        def child(parent):
            return svc.create(
                ProjectCreate(
                    bu_id=root.bu_id,
                    parent_id=parent,
                    name="Child",
                    department_code="D",
                    ledger_account_code="L",
                    currency="USD",
                )
            )

        direct = child(root.id)
        grandchild = child(direct.id)
        other = child(None)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for candidate, status in (
                (direct, 201),
                (direct, 409),
                (grandchild, 422),
                (other, 422),
                (foreign, 404),
            ):
                result = await send(
                    browser,
                    "POST",
                    f"{root.id}/phases",
                    {"name": "X", "sub_project_id": str(candidate.id)},
                )
                assert result.status_code == status, result.text
                if status == 422:
                    assert result.json()["checks"]["problem"] == "invalid_sub_project"
            other_phase = (
                await send(browser, "POST", f"{other.id}/phases", {"name": "Other"})
            ).json()
            for payload in (
                {"due_date": "2025-12-31"},
                {"due_date": "2027-01-01"},
                {"completed_on": (date.today() + timedelta(days=1)).isoformat()},
                {"phase_id": other_phase["id"]},
            ):
                result = await send(
                    browser,
                    "POST",
                    f"{root.id}/milestones",
                    {"name": "X", "due_date": "2026-06-01"} | payload,
                )
                assert result.status_code == 422, result.text
            for payload in (
                {"percent_complete": -1},
                {"percent_complete": 101},
                {"planned_start": "2026-02-01", "planned_end": "2026-01-01"},
                {"actual_start": "2026-02-01", "actual_end": "2026-01-01"},
            ):
                assert (
                    await send(browser, "POST", f"{root.id}/phases", {"name": "X"} | payload)
                ).status_code == 422
            for payload in (
                {"health": None},
                {"health": "invalid"},
                {"percent_complete": None},
                {"percent_complete": -1},
                {"percent_complete": 101},
                {"actual_start": "2026-02-01", "actual_end": "2026-01-01"},
            ):
                result = await browser.patch(
                    f"/api/v1/projects/{root.id}",
                    json=payload,
                    headers=headers(browser) | {"If-Match": "1"},
                )
                assert result.status_code == 422, result.text

    asyncio.run(scenario())


def test_database_checks_composite_fk_and_audit_rollback(schedule_db):
    conn, org, _, (root, _) = schedule_db
    scope = Scope(org)
    with correlation_context("database-plants"):
        schedule = Schedule(ProjectRepository(conn, scope), root.owner_id)
        phase = schedule.write(root.id, PhaseCreate(name="X"))
        with tenant_transaction(conn, scope):
            for assignment in (
                "planned_start='2026-02-01', planned_end='2026-01-01'",
                "actual_start='2026-02-01', actual_end='2026-01-01'",
                "percent_complete=-1",
                "percent_complete=101",
                "status='bad'",
            ):
                with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                    conn.execute(f"UPDATE project_phase SET {assignment} WHERE id=%s", (phase.id,))
            for assignment in (
                "actual_start='2026-02-01', actual_end='2026-01-01'",
                "percent_complete=101",
                "health='bad'",
            ):
                with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                    conn.execute(f"UPDATE project SET {assignment} WHERE id=%s", (root.id,))
            other_id = uuid4()
            conn.execute(
                "INSERT INTO project (id,org_id,bu_id,number,name,owner_id,department_code,"
                "ledger_account_code,currency,created_by) VALUES (%s,%s,%s,'OTHER','X',%s,"
                "'D','L','USD',%s)",
                (other_id, org, root.bu_id, root.owner_id, root.owner_id),
            )
            with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
                conn.execute(
                    "INSERT INTO project_milestone (id,org_id,project_id,phase_id,name,due_date) "
                    "VALUES (%s,%s,%s,%s,'X','2026-06-01')",
                    (uuid4(), org, other_id, phase.id),
                )
        with patch.object(AuditWriter, "write", side_effect=RuntimeError("planted audit failure")):
            with pytest.raises(RuntimeError, match="planted audit failure"):
                schedule.write(root.id, PhaseCreate(name="Rolled back"))
        assert len(schedule.phases(root.id)) == 1


def test_read_only_grant_and_absent_scope(schedule_db):
    conn, org, _, (root, _) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rows = []
            for kind, payload in (
                ("phases", {"name": "X"}),
                ("milestones", {"name": "X", "due_date": "2026-06-01"}),
            ):
                response = await send(browser, "POST", f"{root.id}/{kind}", payload)
                rows.append((kind, payload, response.json()["id"]))
            # Replace the administrator grant with a member role containing project.read only.
            with tenant_transaction(conn, Scope(org)):
                from flo.modules.identity.service import IdentityAuthorizationService

                with correlation_context("read-only-grant"):
                    identity = IdentityAuthorizationService(conn, Scope(org), root.owner_id)
                    reader = identity.create_role("schedule-reader", "Schedule reader")
                    identity.grant_permission(reader.id, "project.read")
                conn.execute(
                    "UPDATE user_role SET role_id=(SELECT id FROM role WHERE org_id=%s "
                    "AND code='schedule-reader') WHERE user_id=%s",
                    (org, root.owner_id),
                )
            for kind, payload, row_id in rows:
                assert (await send(browser, "GET", f"{root.id}/{kind}")).status_code == 200
                for method, suffix in (("POST", ""), ("PATCH", "/" + row_id)):
                    result = await send(browser, method, f"{root.id}/{kind}" + suffix, payload)
                    assert result.status_code == 403, result.text
            with tenant_transaction(conn, Scope(org)):
                conn.execute("DELETE FROM user_role WHERE user_id=%s", (root.owner_id,))
            for kind, payload, row_id in rows:
                for method, suffix in (("GET", ""), ("POST", ""), ("PATCH", "/" + row_id)):
                    result = await send(
                        browser,
                        method,
                        f"{root.id}/{kind}" + suffix,
                        payload if method != "GET" else None,
                    )
                    assert result.status_code == 404, result.text

    asyncio.run(scenario())


def test_patch_validates_merged_dates_window_phase_and_child(schedule_db):
    conn, org, _, (root, foreign) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            phase = (
                await send(
                    browser,
                    "POST",
                    f"{root.id}/phases",
                    {"name": "X", "planned_start": "2026-06-01", "actual_start": "2026-06-01"},
                )
            ).json()
            for payload, status in (
                ({"planned_end": "2026-01-01"}, 422),
                ({"actual_end": "2026-01-01"}, 422),
                ({"sub_project_id": str(root.id)}, 422),
                ({"sub_project_id": str(foreign.id)}, 404),
                ({"percent_complete": None}, 422),
                ({"percent_complete": 101}, 422),
                ({"sequence": None}, 422),
                ({"status": None}, 422),
                ({"name": None}, 422),
            ):
                result = await send(browser, "PATCH", f"{root.id}/phases/{phase['id']}", payload)
                assert result.status_code == status, result.text
            milestone = (
                await send(
                    browser,
                    "POST",
                    f"{root.id}/milestones",
                    {"name": "X", "due_date": "2026-06-01"},
                )
            ).json()
            for payload in (
                {"due_date": "2025-01-01"},
                {"due_date": "2027-01-01"},
                {"completed_on": (date.today() + timedelta(days=1)).isoformat()},
                {"name": None},
                {"due_date": None},
            ):
                result = await send(
                    browser, "PATCH", f"{root.id}/milestones/{milestone['id']}", payload
                )
                assert result.status_code == 422, result.text
            with tenant_transaction(conn, Scope(org)):
                conn.execute("UPDATE project SET planned_start=NULL WHERE id=%s", (root.id,))
            assert (
                await send(
                    browser,
                    "PATCH",
                    f"{root.id}/milestones/{milestone['id']}",
                    {"due_date": "2025-01-01"},
                )
            ).status_code == 200
            with tenant_transaction(conn, Scope(org)):
                conn.execute(
                    "UPDATE project SET planned_start='2026-01-01', planned_end=NULL WHERE id=%s",
                    (root.id,),
                )
            assert (
                await send(
                    browser,
                    "PATCH",
                    f"{root.id}/milestones/{milestone['id']}",
                    {"due_date": "2027-01-01"},
                )
            ).status_code == 200

    asyncio.run(scenario())


def test_post_requires_idempotency_and_mfa(schedule_db):
    _, _, _, (root, _) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser, enroll=False)
            for kind, payload in (
                ("phases", {"name": "X"}),
                ("milestones", {"name": "X", "due_date": "2026-06-01"}),
            ):
                response = await send(browser, "POST", f"{root.id}/{kind}", payload)
                assert response.status_code == 403
                assert response.headers["www-authenticate"] == "mfa-enroll"
        async with client(app) as browser:
            await login(browser)
            for kind, payload in (
                ("phases", {"name": "X"}),
                ("milestones", {"name": "X", "due_date": "2026-06-01"}),
            ):
                response = await browser.post(
                    f"/api/v1/projects/{root.id}/{kind}",
                    json=payload,
                    headers={k: v for k, v in headers(browser).items() if k != "Idempotency-Key"},
                )
                assert response.status_code == 400, response.text

    asyncio.run(scenario())
