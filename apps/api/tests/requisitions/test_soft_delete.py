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


def test_requisition_with_a_live_reservation_is_not_deletable(header_db):
    db = header_db
    bu = unit(db)
    row = draft(db, p=project(db, bu))
    with tenant_transaction(db[0], Scope(db[1])):
        entry = (
            db[0]
            .execute(
                "INSERT INTO ledger_entry(org_id,bu_id,project_id,entry_type,bucket,amount,"
                "currency,"
                "source_type,source_id,effective_date,actor_id,idempotency_key) "
                "SELECT org_id,bu_id,project_id,'reserve','reserved',1,'USD','requisition',id,"
                "now()::date,requester_id,'k-' || id FROM requisition WHERE id=%s RETURNING id",
                (row.id,),
            )
            .fetchone()[0]
        )
        db[0].execute("UPDATE requisition SET reservation_entry_id=%s WHERE id=%s", (entry, row.id))
    with pytest.raises(ProblemError) as error:
        svc(db).delete(row.id)
    assert error.value.checks["problem"] == "reservation_held"
    assert status_of(db, "requisition", row.id) == "draft"


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


def test_project_delete_only_when_an_empty_draft(header_db):
    db = header_db
    bu = unit(db)
    empty, parent, busy = project(db, bu), project(db, bu), project(db, bu)
    child = draft(db, p=busy)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE project SET parent_id=%s,root_id=%s,depth=2 WHERE id=%s",
            (parent.id, parent.id, project(db, bu).id),
        )
    active = project(db, bu)
    db[0].execute("UPDATE project SET status='active' WHERE id=%s", (active.id,))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert (await call(browser, "DELETE", f"/projects/{empty.id}")).status_code == 204
            assert (await call(browser, "GET", f"/projects/{empty.id}")).status_code == 404
            ids = {r["id"] for r in (await call(browser, "GET", "/projects")).json()["rows"]}
            assert str(empty.id) not in ids and str(busy.id) in ids
            for blocked, problem in (
                (busy, "not_empty"),
                (parent, "not_empty"),
                (active, "not_draft"),
            ):
                result = await call(browser, "DELETE", f"/projects/{blocked.id}")
                assert result.status_code == 409, result.text
                assert result.json()["checks"]["problem"] == problem

    asyncio.run(scenario())
    assert audit_actions(db, empty.id)[-1] == "project.delete"
    assert status_of(db, "project", busy.id) == "draft" and child.project_id == busy.id


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
    migration = load_migration(ROOT / "migrations" / "20261014_0062_soft_delete.py", "soft_delete")
    migration.downgrade(db[0])
    assert status_of(db, "requisition", req.id) == "withdrawn"
    assert status_of(db, "project", proj.id) == "abandoned"
    with pytest.raises(psycopg.errors.CheckViolation):
        db[0].execute("UPDATE requisition SET status='deleted' WHERE id=%s", (req.id,))
    migration.upgrade(db[0])
    db[0].execute("UPDATE requisition SET status='deleted' WHERE id=%s", (req.id,))
