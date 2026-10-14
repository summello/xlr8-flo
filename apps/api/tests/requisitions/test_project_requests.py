"""Project Requests: own document type, workflow, permission, sponsors, abandon on cancel (E09-S15).

REQ-004, D-M2-18, D-M2-20, D-M2-21, D-M2-22.
"""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget.service import budget_summary
from flo.modules.identity.service import (
    AuthorizationTarget,
    IdentityAuthorizationService,
    ScopeType,
)
from flo.modules.projects.service import ProjectService
from flo.modules.requisitions.schemas import RequisitionCreate, SubmissionInvalid
from flo.modules.requisitions.service import RequisitionService
from tests.requisitions.test_approval_effects import approve, drift, grant, linked, standalone
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import client, login, project, send, unit
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lifecycle import command, sql
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lines import lines_db as lines_db
from tests.requisitions.test_sub_funding import cancel
from tests.requisitions.test_submission_check import complete


def instance_types(db, row):
    return sql(db, "SELECT document_type FROM approval_instance WHERE document_id=%s", (row.id,))


def test_standalone_submits_as_project_request_and_linked_as_requisition(lifecycle_db):
    db, _ = lifecycle_db
    request = standalone(db)
    assert instance_types(db, request) == [("project_request",)]
    other = linked(db, project(db, unit(db, uuid4().hex)))
    assert instance_types(db, other) == [("requisition",)]


def test_project_request_needs_its_own_published_workflow(lifecycle_db):
    """D-M2-21: the requisition workflow does not route a Project Request."""
    db, _ = lifecycle_db
    row, _ = complete(db)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE workflow_definition SET retired_at=now() WHERE document_type='project_request'"
        )
    with pytest.raises(ProblemError) as error:
        command(db, row)
    assert any(x.field == "routing/workflow" for x in error.value.errors)
    assert sql(db, "SELECT status FROM requisition WHERE id=%s", (row.id,)) == [("draft",)]
    assert linked(db, project(db, unit(db, uuid4().hex))).status == "approval_pending"


def test_submit_without_sponsor_is_refused(lifecycle_db):
    db, _ = lifecycle_db
    row, _ = complete(db)
    sql(db, "DELETE FROM requisition_sponsor WHERE requisition_id=%s RETURNING 1", (row.id,))
    with pytest.raises(SubmissionInvalid) as error:
        command(db, row)
    assert [p.field for p in error.value.problems] == ["required/sponsors"]


def test_workflow_condition_on_bu_routes_a_project_request(lifecycle_db):
    from flo.modules.approvals.conditions import field_catalog

    assert field_catalog("project_request") == {"amount": "money", "bu": "code"}
    assert field_catalog("requisition") == {"amount": "money"}


def test_sponsors_are_for_project_requests_only(header_db):
    db = header_db
    bu = unit(db)
    with pytest.raises(ProblemError) as error:
        RequisitionCreate(title="X", project_id=uuid4(), sponsor_ids=[db[3]])
    assert error.value.code == ErrorCode.VALIDATION_FAILED
    with pytest.raises(ProblemError):
        RequisitionCreate(title="X", bu_id=bu.id, sponsor_ids=[db[3], db[3]])
    p = project(db, bu)
    from tests.requisitions.test_header import draft

    row = draft(db, p=p)
    from flo.modules.requisitions.schemas import RequisitionPatch

    with pytest.raises(ProblemError) as refused:
        RequisitionService(db[0], Scope(db[1]), db[3]).update(
            row.id, RequisitionPatch(sponsor_ids=[db[3]]), "1", lambda _id: True
        )
    assert refused.value.code == ErrorCode.VALIDATION_FAILED
    request = draft(db, bu, sponsor_ids=[db[3]])
    assert RequisitionService(db[0], Scope(db[1])).detail(
        request.id, lambda _id: True
    ).sponsor_ids == [db[3]]


def test_approval_creates_the_project_with_the_sponsors(lifecycle_db):
    db, _ = lifecycle_db
    row = standalone(db, price="60")
    grant(db, "budget.allocate")
    approve(db, row)
    created = sql(db, "SELECT id,sponsor_id FROM project WHERE source_requisition_id=%s", (row.id,))
    assert len(created) == 1 and created[0][1] == db[3]
    assert ProjectService(db[0], Scope(db[1])).sponsors(created[0][0]) == [db[3]]
    assert sql(db, "SELECT user_id FROM project_sponsor WHERE project_id=%s", (created[0][0],)) == [
        (db[3],)
    ]
    assert drift(db) == []


def summary(db):
    return budget_summary(db[0], Scope(db[1]))


def test_cancel_after_approval_abandons_keeps_allocation_and_reports_apart(lifecycle_db):
    db, _ = lifecycle_db
    row = standalone(db, price="60")
    grant(db, "budget.allocate")
    approve(db, row)
    created = sql(db, "SELECT id FROM project WHERE source_requisition_id=%s", (row.id,))[0][0]
    live = summary(db)
    assert [t.allocated for t in live.totals] and live.abandoned == []
    cancel(db, row)
    assert sql(db, "SELECT status FROM project WHERE id=%s", (created,)) == [("abandoned",)]
    assert sql(
        db, "SELECT allocated,reserved FROM project_balance WHERE project_id=%s", (created,)
    ) == [(Decimal(60), Decimal(0))]
    after = summary(db)
    assert all(Decimal(t.allocated) == 0 for t in after.totals)
    assert [(a.currency, Decimal(a.allocated), Decimal(a.reserved)) for a in after.abandoned] == [
        ("USD", Decimal(60), Decimal(0))
    ]
    audit = sql(
        db,
        "SELECT count(*) FROM audit_log WHERE action='project.abandon' AND target_id=%s",
        (created,),
    )
    assert audit == [(1,)]
    assert drift(db) == []
    with pytest.raises(ProblemError):
        cancel(db, row)


def test_cancel_of_an_assigned_request_leaves_the_assigned_project_alone(lifecycle_db):
    from tests.requisitions.test_approval_effects import fund

    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    fund(db, p, "100")
    row = standalone(db)
    approve(db, row, p.id)
    cancel(db, row)
    assert sql(db, "SELECT status FROM project WHERE id=%s", (p.id,)) == [("active",)]
    assert summary(db).abandoned == []


def test_create_needs_project_request_create_on_the_unit(header_db):
    db = header_db
    bu = unit(db)
    foreign = unit(db, "FOREIGN", org=db[2])
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
        auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
        role = auth.create_role("requestor-only", "Requestor only")
        for permission in ("requisition.create", "requisition.view"):
            auth.grant_permission(role.id, permission)
        auth.grant_role(db[3], role.id, AuthorizationTarget(ScopeType.BU, bu.id))
        extra = auth.create_role("request-creator", "Request creator")
        auth.grant_permission(extra.id, "project_request.create")
        auth.grant_permission(extra.id, "requisition.view")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            body = {"title": "Request", "bu_id": str(bu.id), "sponsor_ids": [str(db[3])]}
            denied = await send(browser, "POST", data=body)
            assert denied.status_code == 403, denied.text
            with tenant_transaction(db[0], Scope(db[1])):
                IdentityAuthorizationService(db[0], Scope(db[1]), db[3]).grant_role(
                    db[3], extra.id, AuthorizationTarget(ScopeType.BU, bu.id)
                )
            with tenant_transaction(db[0], Scope(db[1])):
                IdentityAuthorizationService(db[0], Scope(db[1]), db[3]).grant_role(
                    db[3], extra.id, AuthorizationTarget.organization(db[1])
                )
            created = await send(browser, "POST", data=body)
            assert created.status_code == 201, created.text
            assert created.json()["sponsor_ids"] == [str(db[3])]
            listed = await send(browser, "GET", "?kind=project_request")
            assert [r["id"] for r in listed.json()["rows"]] == [created.json()["id"]]
            none = await send(browser, "GET", "?kind=requisition")
            assert none.json()["rows"] == []
            missing = await send(
                browser, "POST", data={"title": "Request", "bu_id": str(foreign.id)}
            )
            assert missing.status_code == 404, missing.text

    asyncio.run(scenario())


def _rows(db):
    with tenant_transaction(db[0], Scope(db[1])):
        return (
            db[0].execute("SELECT * FROM requisition ORDER BY id").fetchall(),
            db[0].execute("SELECT * FROM project ORDER BY id").fetchall(),
        )


def _migration_0054():
    from flo.kernel import migrate
    from tests.kernel.test_migrate import MIGRATIONS

    return next(r for r in migrate.discover_migrations(MIGRATIONS) if r.revision.endswith("_0054"))


def test_migration_0054_committed_down_up_keeps_every_requisition_and_project(header_db):
    from flo.modules.projects.schemas import ProjectCreate
    from tests.requisitions.test_header import draft

    db = header_db
    bu = unit(db)
    ProjectService(db[0], Scope(db[1]), db[3]).create(
        ProjectCreate(
            bu_id=bu.id,
            name="Sponsored",
            currency="USD",
            department_code="D",
            ledger_account_code="L",
            sponsor_id=db[3],
        )
    )
    draft(db, bu, sponsor_ids=[db[3]])
    draft(db, p=project(db, unit(db, "OTHER")))
    before = _rows(db)
    assert len(before[0]) == 2 and len(before[1]) == 2
    assert db[0].autocommit  # the down and up below are committed, not rolled back
    target = _migration_0054()
    target.downgrade(db[0])
    assert db[0].execute("SELECT to_regclass('requisition_sponsor')").fetchone() == (None,)
    assert db[0].execute("SELECT to_regclass('project_sponsor')").fetchone() == (None,)
    target.upgrade(db[0])
    after = _rows(db)
    assert after == before
    with tenant_transaction(db[0], Scope(db[1])):
        # Declared loss: requisition sponsors; project sponsors are rebuilt from sponsor_id.
        assert db[0].execute("SELECT count(*) FROM requisition_sponsor").fetchone() == (0,)
        assert db[0].execute("SELECT user_id FROM project_sponsor").fetchall() == [(db[3],)]


def test_migration_0054_downgrade_refuses_while_project_request_workflows_exist(header_db):
    from tests.approvals.test_workflow_definitions import definition

    db = header_db
    definition(db, document_type="project_request")
    with pytest.raises(RuntimeError, match="project_request workflows exist"):
        _migration_0054().downgrade(db[0])
    assert db[0].execute("SELECT to_regclass('requisition_sponsor')").fetchone() != (None,)


def member(db, email):
    """An active member of the first organization."""
    from flo.kernel.identity import IdentityId

    person = IdentityId(uuid4())
    db[0].execute(
        "INSERT INTO identity(id,email,password_hash) VALUES(%s,%s,%s)",
        (person, email, "$argon2id$test-placeholder"),
    )
    db[0].execute(
        "INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)", (person, db[1])
    )
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = auth.seed_baseline_roles()["approver"]
    auth.grant_role(person, role.id, AuthorizationTarget.organization(db[1]))
    return person


def test_http_create_requires_one_to_ten_sponsors_and_patch_never_empties_them(header_db):
    db = header_db
    bu = unit(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            base = {"title": "Request", "bu_id": str(bu.id)}
            for extra in ({}, {"sponsor_ids": []}, {"sponsor_ids": [str(db[3])] * 2}):
                refused = await send(browser, "POST", data=base | extra)
                assert refused.status_code == 422, refused.text
            made = await send(browser, "POST", data=base | {"sponsor_ids": [str(db[3])]})
            assert made.status_code == 201, made.text
            path = "/" + made.json()["id"]
            for sponsors in ([], None):
                refused = await send(
                    browser, "PATCH", path, {"sponsor_ids": sponsors}, extra={"If-Match": '"1"'}
                )
                assert refused.status_code == 422, refused.text
            # A regular requisition is unaffected by the rule.
            other = project(db, unit(db, "PX"))
            linked_row = await send(
                browser, "POST", data={"title": "Linked", "project_id": str(other.id)}
            )
            assert linked_row.status_code == 201, linked_row.text

    asyncio.run(scenario())


def test_only_active_users_of_the_organization_can_sponsor(header_db):
    from tests.requisitions.test_header import draft

    db = header_db
    bu = unit(db)
    person = member(db, "sponsor@example.test")
    assert draft(db, bu, sponsor_ids=[person]).sponsor_ids == [person]
    db[0].execute(
        "UPDATE identity SET status='deactivated',deactivated_at=now() WHERE id=%s", (person,)
    )
    with pytest.raises(ProblemError) as error:
        draft(db, bu, sponsor_ids=[person])
    assert error.value.code == ErrorCode.VALIDATION_FAILED
    outsider = db[0].execute("SELECT id FROM identity WHERE email='second@example.test'").fetchone()
    with pytest.raises(ProblemError):
        draft(db, bu, sponsor_ids=[outsider[0]])


def test_sponsor_change_is_audited_before_and_after(header_db):
    from flo.modules.requisitions.schemas import RequisitionPatch
    from tests.requisitions.test_header import draft

    db = header_db
    row = draft(db, unit(db), sponsor_ids=[db[3]])
    service = RequisitionService(db[0], Scope(db[1]), db[3])
    query = (
        "SELECT before,after FROM audit_log WHERE target_id=%s AND action='requisition.update' "
        "ORDER BY id DESC LIMIT 1"
    )
    service.update(row.id, RequisitionPatch(sponsor_ids=[db[3]]), "1", lambda _id: True)
    # Same sponsors: no sponsor diff is invented.
    assert "sponsor_ids" not in (db[0].execute(query, (row.id,)).fetchone()[1] or {})
    person = member(db, "second-sponsor@example.test")
    service.update(row.id, RequisitionPatch(sponsor_ids=[person]), "2", lambda _id: True)
    last = db[0].execute(query, (row.id,)).fetchone()
    assert last[0]["sponsor_ids"] == [str(db[3])] and last[1]["sponsor_ids"] == [str(person)]


def test_aggregate_excludes_an_abandoned_request_project_itself_and_as_a_child(lifecycle_db):
    from flo.modules.budget.aggregate import aggregate_balance
    from flo.modules.projects.schemas import ProjectCreate
    from tests.requisitions.test_approval_effects import fund

    db, _ = lifecycle_db
    row = standalone(db, price="60")
    grant(db, "budget.allocate")
    approve(db, row)
    created = sql(db, "SELECT id FROM project WHERE source_requisition_id=%s", (row.id,))[0][0]
    cancel(db, row)
    alone = aggregate_balance(db[0], Scope(db[1]), created)
    assert alone.totals == [] and alone.node_count == 0
    parent = project(db, unit(db, uuid4().hex))
    fund(db, parent, "100")
    child = ProjectService(db[0], Scope(db[1]), db[3]).create(
        ProjectCreate(
            bu_id=parent.bu_id,
            parent_id=parent.id,
            name="Child",
            currency="USD",
            department_code="D",
            ledger_account_code="L",
        )
    )
    sql(db, "UPDATE project_balance SET allocated=40 WHERE project_id=%s RETURNING 1", (child.id,))

    def child_allocated():
        return aggregate_balance(db[0], Scope(db[1]), parent.id).totals[0].descendants.allocated

    assert child_allocated() == "40.0000"
    # An abandoned child that is not a Project Request project still counts.
    sql(db, "UPDATE project SET status='abandoned' WHERE id=%s RETURNING 1", (child.id,))
    assert child_allocated() == "40.0000"
    sql(
        db,
        "UPDATE project SET source_requisition_id=%s WHERE id=%s RETURNING 1",
        (row.id, child.id),
    )
    assert child_allocated() == "0.0000"
    assert aggregate_balance(db[0], Scope(db[1]), parent.id).node_count == 1


def test_two_concurrent_cancels_of_an_approved_project_request_abandon_once(lifecycle_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from tests.requisitions.test_release import releases, stop_on_own_connection

    db, _ = lifecycle_db
    row = standalone(db, price="60")
    grant(db, "budget.allocate")
    approve(db, row)
    created = sql(db, "SELECT id FROM project WHERE source_requisition_id=%s", (row.id,))[0][0]
    version = sql(db, "SELECT version FROM requisition WHERE id=%s", (row.id,))[0][0]
    barrier = Barrier(2)
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda _: stop_on_own_connection(db, row, "cancelled", version, barrier), range(2)
            )
        )
    assert sorted(results) == ["conflict", "success"]
    assert len(releases(db, row.id)) == 1
    assert sql(db, "SELECT status FROM project WHERE id=%s", (created,)) == [("abandoned",)]
    abandons = "SELECT count(*) FROM audit_log WHERE action='project.abandon' AND target_id=%s"
    assert sql(db, abandons, (created,)) == [(1,)]
    assert drift(db) == []


def test_service_refuses_a_sponsorless_project_request(header_db):
    db = header_db
    bu = unit(db)
    service = RequisitionService(db[0], Scope(db[1]), db[3])
    with pytest.raises(ProblemError) as error:
        service.create(RequisitionCreate(title="No sponsor", bu_id=bu.id))
    assert error.value.errors[0].field == "sponsor_ids"
    assert db[0].execute("SELECT count(*) FROM requisition").fetchone() == (0,)
