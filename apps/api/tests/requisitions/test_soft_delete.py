"""E22-S02 (DATA-002): soft delete of drafts, through production middleware and Postgres."""

import asyncio
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import WorkflowVersionWrite
from tests.approvals.test_workflow_definitions import GRAPH
from tests.approvals.test_workflow_definitions import definition as workflow
from tests.approvals.test_workflow_definitions import draft as workflow_draft
from tests.approvals.test_workflow_definitions import send as workflow_send
from tests.approvals.test_workflow_definitions import svc as workflow_svc
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.authz.conftest import ROOT, load_migration
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import draft, project, svc, unit
from tests.requisitions.test_header import header_db as header_db


async def call(browser, method, path, **kwargs):
    return await browser.request(
        method, "/api/v1" + path, headers=headers(browser, uuid4().hex), **kwargs
    )


def status_of(db, table, id):
    return db[0].execute(f"SELECT status FROM {table} WHERE id=%s", (id,)).fetchone()[0]


def audit_actions(db, id):
    return [
        r[0]
        for r in db[0]
        .execute("SELECT action FROM audit_log WHERE target_id=%s ORDER BY id", (id,))
        .fetchall()
    ]


def test_requisition_and_project_request_delete_from_draft_only(header_db):
    db = header_db
    bu = unit(db)
    linked = draft(db, p=project(db, bu))
    request = draft(db, bu)
    kept = draft(db, p=project(db, bu))
    pending = draft(db, p=project(db, bu))
    db[0].execute("UPDATE requisition SET status='approval_pending' WHERE id=%s", (pending.id,))
    numbers = {linked.number, request.number}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for row in (linked, request):
                path = f"/requisitions/{row.id}"
                assert (await call(browser, "DELETE", path)).status_code == 204
                assert (await call(browser, "GET", path)).status_code == 404
                assert (await call(browser, "PATCH", path, json={"title": "x"})).status_code == 404
                assert (await call(browser, "DELETE", path)).status_code == 404
            blocked = await call(browser, "DELETE", f"/requisitions/{pending.id}")
            assert blocked.status_code == 409
            assert blocked.json()["checks"]["problem"] == "not_draft"
            listing = (await call(browser, "GET", "/requisitions")).json()
            ids = {r["id"] for r in listing["rows"]}
            assert ids == {str(kept.id), str(pending.id)} and listing["total"] == 2

    asyncio.run(scenario())
    assert status_of(db, "requisition", pending.id) == "approval_pending"
    assert audit_actions(db, linked.id)[-1] == "requisition.delete"
    assert audit_actions(db, request.id)[-1] == "project_request.delete"
    with pytest.raises(ProblemError):
        svc(db).get(linked.id)
    # The row, and so its number, is kept: a new draft never takes a deleted one's number.
    assert status_of(db, "requisition", linked.id) == "deleted"
    assert {draft(db, p=project(db, bu)).number}.isdisjoint(numbers)


def test_deleting_a_returned_draft_releases_its_reservation_once(header_db):
    from decimal import Decimal

    from flo.modules.budget.service import reservation_remaining, reserve
    from flo.modules.org.service import today

    db = header_db
    p = project(db, unit(db))
    row = draft(db, p=p)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE project_balance SET allocated=100 WHERE project_id=%s", (p.id,))
        db[0].execute("UPDATE project SET status='active' WHERE id=%s", (p.id,))
    entry = reserve(
        db[0],
        Scope(db[1]),
        project_id=p.id,
        amount=Decimal(60),
        currency="USD",
        source_type="requisition",
        source_id=row.id,
        effective_date=today(),
        actor_id=db[3],
        department_code="D",
        ledger_account_code="L",
        idempotency_key=uuid4().hex,
    )
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE requisition SET reservation_entry_id=%s,funding_round=1 WHERE id=%s",
            (entry.id, row.id),
        )
    svc(db).delete(row.id)
    assert status_of(db, "requisition", row.id) == "deleted"
    assert reservation_remaining(db[0], Scope(db[1]), entry.id).remaining == 0
    with pytest.raises(ProblemError):
        svc(db).delete(row.id)
    releases = db[0].execute(
        "SELECT count(*) FROM ledger_entry WHERE entry_type='release' AND source_id=%s", (row.id,)
    ).fetchone()
    assert releases == (1,)


def test_foreign_tenant_delete_routes_are_404(header_db):
    db = header_db
    foreign_bu = unit(db, org=db[2])
    foreign_req = draft(db, foreign_bu, org=db[2])
    foreign_project = project(db, foreign_bu, org=db[2])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for path in (f"/requisitions/{foreign_req.id}", f"/projects/{foreign_project.id}"):
                assert (await call(browser, "DELETE", path)).status_code == 404

    asyncio.run(scenario())
    assert status_of(db, "requisition", foreign_req.id) == "draft"
    assert status_of(db, "project", foreign_project.id) == "draft"


def test_every_draft_project_deletes_and_keeps_its_number_and_reference(header_db):
    db = header_db
    bu = unit(db)
    plain, parent, active = project(db, bu), project(db, bu), project(db, bu)
    db[0].execute("UPDATE project SET external_ref='ERP-9' WHERE id=%s", (plain.id,))
    child = draft(db, p=parent)
    db[0].execute("UPDATE project SET status='active' WHERE id=%s", (active.id,))
    number = db[0].execute("SELECT number FROM project WHERE id=%s", (plain.id,)).fetchone()

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for deletable in (plain, parent):
                path = f"/projects/{deletable.id}"
                assert (await call(browser, "DELETE", path)).status_code == 204
                assert (await call(browser, "GET", path)).status_code == 404
            ids = {r["id"] for r in (await call(browser, "GET", "/projects")).json()["rows"]}
            assert ids.isdisjoint({str(plain.id), str(parent.id)}) and str(active.id) in ids
            blocked = await call(browser, "DELETE", f"/projects/{active.id}")
            assert blocked.status_code == 409
            assert blocked.json()["checks"]["problem"] == "not_draft"

    asyncio.run(scenario())
    assert audit_actions(db, plain.id)[-1] == "project.delete"
    kept = db[0].execute("SELECT number,external_ref FROM project WHERE id=%s", (plain.id,))
    assert kept.fetchone() == (number[0], "ERP-9")
    assert child.project_id == parent.id and status_of(db, "requisition", child.id) == "draft"


def test_delete_routes_replay_with_the_same_key_and_need_one(header_db):
    db = header_db
    bu = unit(db)
    first, second = draft(db, p=project(db, bu)), draft(db, p=project(db, bu))
    proj = project(db, bu)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            path = f"/api/v1/requisitions/{first.id}"
            sent = headers(browser, "k-delete")
            assert (await browser.request("DELETE", path, headers=sent)).status_code == 204
            # The lost-response retry: same key, same request, replays the 204.
            assert (await browser.request("DELETE", path, headers=sent)).status_code == 204
            changed = await browser.request(
                "DELETE", f"/api/v1/requisitions/{second.id}", headers=sent
            )
            assert changed.status_code == 422 and status_of(db, "requisition", second.id) == "draft"
            # A fresh key on the already-deleted draft is the honest 404.
            fresh = headers(browser, "k-fresh")
            assert (await browser.request("DELETE", path, headers=fresh)).status_code == 404
            unkeyed = {k: v for k, v in sent.items() if k != "Idempotency-Key"}
            for route in (path, f"/api/v1/projects/{proj.id}"):
                missing = await browser.request("DELETE", route, headers=unkeyed)
                assert missing.status_code == 400, route
            assert status_of(db, "project", proj.id) == "draft"
            key = headers(browser, "k-project")
            for _ in range(2):
                url = f"/api/v1/projects/{proj.id}"
                response = await browser.request("DELETE", url, headers=key)
                assert response.status_code == 204

    asyncio.run(scenario())
    assert audit_actions(db, first.id).count("requisition.delete") == 1


def test_include_deleted_needs_record_read(header_db):
    from tests.vendors.test_vendors import regrant

    db = header_db
    bu = unit(db)
    row, proj = draft(db, p=project(db, bu)), project(db, bu)
    live = draft(db, p=project(db, bu))
    svc(db).delete(row.id)

    from flo.modules.projects.service import ProjectService

    ProjectService(db[0], Scope(db[1]), db[3]).delete(proj.id)
    paths = (f"/requisitions/{row.id}", f"/projects/{proj.id}")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for path in paths:
                assert (await call(browser, "GET", path)).status_code == 404
                found = await call(browser, "GET", path + "?include_deleted=true")
                assert found.status_code == 200, (path, found.text)
                assert found.json()["status"] == "deleted"
            # Lists never show deleted rows, whatever the flag.
            listing = await call(browser, "GET", "/requisitions?include_deleted=true")
            assert {r["id"] for r in listing.json()["rows"]} == {str(live.id)}
            editor = await call(browser, "GET", f"/requisitions/{live.id}")
            assert editor.json()["can_edit"] is True
            live_project = await call(browser, "GET", f"/projects/{live.project_id}")
            assert live_project.json()["can_edit"] is True
            regrant(db, ["requisition.view", "project.read"])
            for path in paths:
                hidden = await call(browser, "GET", path + "?include_deleted=true")
                assert hidden.status_code == 404
            assert (
                await call(browser, "GET", f"/requisitions/{live.id}?include_deleted=true")
            ).status_code == 404
            plain = await call(browser, "GET", f"/requisitions/{live.id}")
            assert plain.status_code == 200 and plain.json()["can_edit"] is False
            no_edit = await call(browser, "GET", f"/projects/{live.project_id}")
            assert no_edit.status_code == 200 and no_edit.json()["can_edit"] is False

    asyncio.run(scenario())


def test_workflow_draft_delete_keeps_the_version_number(workflow_db):
    db = workflow_db
    live, fresh, foreign = workflow(db), workflow(db, document_type="project"), workflow(db, db[2])
    workflow_draft(db, live)
    workflow_svc(db).publish(live.id, 1)
    workflow_draft(db, live)
    workflow_draft(db, fresh)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert (
                await workflow_send(browser, "DELETE", f"/{live.id}/versions/1")
            ).status_code == 409
            assert (
                await workflow_send(browser, "DELETE", f"/{live.id}/versions/2")
            ).status_code == 204
            assert (
                await workflow_send(browser, "GET", f"/{live.id}/versions/2")
            ).status_code == 404
            seen = await workflow_send(
                browser, "GET", f"/{live.id}/versions/2", params={"include_deleted": "true"}
            )
            assert seen.status_code == 200 and seen.json()["status"] == "deleted"
            from tests.vendors.test_vendors import regrant

            regrant(db, ["workflow.read", "workflow.manage"])
            denied = await workflow_send(
                browser, "GET", f"/{live.id}/versions/2", params={"include_deleted": "true"}
            )
            assert denied.status_code == 404
            regrant(db, ["workflow.read", "workflow.manage", "record.read"])
            assert (
                await workflow_send(browser, "DELETE", f"/{live.id}/versions/2")
            ).status_code == 404
            detail = (await workflow_send(browser, "GET", f"/{live.id}")).json()
            assert [v["version"] for v in detail["versions"]] == [1] and detail[
                "draft_version"
            ] is None
            assert (
                await workflow_send(browser, "DELETE", f"/{fresh.id}/versions/1")
            ).status_code == 204
            assert (
                await workflow_send(browser, "DELETE", f"/{foreign.id}/versions/1")
            ).status_code == 404

    asyncio.run(scenario())
    assert workflow_draft(db, live).version == 3

    def retired(id):
        row = db[0].execute("SELECT retired_at FROM workflow_definition WHERE id=%s", (id,))
        return row.fetchone()[0]

    assert retired(fresh.id) is not None and retired(live.id) is None
    assert "workflow.version.delete" in audit_actions(db, live.id)
    # A deleted version stays immutable.
    with (
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
        tenant_transaction(db[0], Scope(db[1])),
    ):
        db[0].execute(
            "UPDATE workflow_version SET row_version=row_version+1 WHERE status='deleted'"
        )
    assert WorkflowVersionWrite(graph=GRAPH)


def test_soft_delete_migration_down_keeps_rows_then_up_again(header_db):
    db = header_db
    bu = unit(db)
    req, proj = draft(db, p=project(db, bu)), project(db, bu)
    svc(db).delete(req.id)
    from flo.modules.projects.service import ProjectService

    ProjectService(db[0], Scope(db[1]), db[3]).delete(proj.id)
    bcr = uuid4()
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "INSERT INTO budget_change_request(id,org_id,kind,project_id,payload,amount,currency,"
            "requested_by,idempotency_key,status) VALUES(%s,%s,'adjustment',%s,'{}',1,'USD',%s,"
            "'k-bcr','deleted')",
            (bcr, db[1], proj.id, db[3]),
        )
    migration = load_migration(ROOT / "migrations" / "20261022_0071_soft_delete.py", "soft_delete")
    migration.downgrade(db[0])
    assert status_of(db, "budget_change_request", bcr) == "withdrawn"
    assert status_of(db, "requisition", req.id) == "withdrawn"
    assert status_of(db, "project", proj.id) == "abandoned"
    with pytest.raises(psycopg.errors.CheckViolation):
        db[0].execute("UPDATE requisition SET status='deleted' WHERE id=%s", (req.id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        db[0].execute("UPDATE budget_change_request SET status='deleted' WHERE id=%s", (bcr,))
    migration.upgrade(db[0])
    db[0].execute("UPDATE requisition SET status='deleted' WHERE id=%s", (req.id,))
    db[0].execute("UPDATE budget_change_request SET status='deleted' WHERE id=%s", (bcr,))
