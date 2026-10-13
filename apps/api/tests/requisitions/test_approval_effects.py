"""Final approval: sub-project, standalone allocation and reservation, one transaction (E09-S06)."""

from decimal import Decimal
from uuid import uuid4

import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import (
    DecisionWrite,
    Outcome,
    OutcomeContext,
    WorkflowVersionWrite,
)
from flo.modules.approvals.service import decide, get_instance
from flo.modules.budget.schemas import AllocationCreate
from flo.modules.budget.service import allocate
from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService, today
from flo.modules.requisitions.schemas import RequisitionCreate
from flo.modules.requisitions.service import RequisitionAdapter, RequisitionService
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_header import project, unit
from tests.requisitions.test_lifecycle import command, sql
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lines import lines_db as lines_db
from tests.requisitions.test_submission_check import change, complete


def fund(db, p, amount="100"):
    """Allocate through the ledger (so balances reconcile), then activate."""
    allocate(
        db[0],
        Scope(db[1]),
        p.id,
        AllocationCreate(
            amount=amount, currency="USD", effective_date=today(), reason="Seed funding for test"
        ),
        db[3],
        idempotency_key=uuid4().hex,
    )
    change(db, "project", p.id, "status='active'")


def linked(db, p, price="60", available="100"):
    """A submitted, project-linked requisition (reserved at submit)."""
    row, _ = complete(db, p, price=price)
    sql(db, "UPDATE project_balance SET allocated=0 WHERE project_id=%s RETURNING 1", (p.id,))
    fund(db, p, available)
    command(db, row)
    return RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)


def standalone(db, price="60"):
    row, _ = complete(db, price=price)
    command(db, row)
    return RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)


def reviewer(db):
    return (
        db[0].execute("SELECT id FROM identity WHERE email='reviewer@example.test'").fetchone()[0]
    )


def approve(db, row, assign=None):
    current = RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)
    instance = get_instance(db[0], Scope(db[1]), current.approval.instance_id)
    decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[-1].id,
        actor_id=reviewer(db),
        body=DecisionWrite(
            outcome="approve", document_version=current.version, assign_project_id=assign
        ),
    )


def grant(db, permission):
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = auth.create_role(f"r-{uuid4().hex[:8]}", "Extra role")
    auth.grant_permission(role.id, permission)
    auth.grant_role(reviewer(db), role.id, AuthorizationTarget.organization(db[1]))


def drift(db):
    return sql(
        db,
        "SELECT b.project_id,v.bucket FROM project_balance b CROSS JOIN LATERAL (VALUES "
        "('allocated',b.allocated),('reserved',b.reserved),('committed',b.committed),"
        "('actual',b.actual)) v(bucket,amount) "
        "WHERE v.amount <> COALESCE((SELECT sum(amount) FROM ledger_entry e "
        "WHERE e.project_id=b.project_id AND e.bucket::text=v.bucket),0)",
    )


def state(db, id):
    return RequisitionService(db[0], Scope(db[1])).detail(id, lambda p: True)


def test_project_linked_approval_generates_active_sub_project(lifecycle_db):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    row = linked(db, p)
    before = sql(db, "SELECT reserved,allocated FROM project_balance WHERE project_id=%s", (p.id,))
    approve(db, row)
    after = state(db, row.id)
    assert after.status == "sourcing_in_progress" and after.reservation.remaining == "60.0000"
    sub = sql(
        db,
        "SELECT parent_id,status,owner_id,bu_id,currency,name FROM project "
        "WHERE generated_by_requisition_id=%s",
        (row.id,),
    )
    assert sub == [(p.id, "active", db[3], p.bu_id, "USD", "Request")]
    # The submit-time reservation stands: no new ledger row, parent unchanged.
    assert sql(db, "SELECT count(*) FROM ledger_entry WHERE source_id=%s", (row.id,)) == [(1,)]
    assert (
        sql(db, "SELECT reserved,allocated FROM project_balance WHERE project_id=%s", (p.id,))
        == before
    )
    child = sql(db, "SELECT id FROM project WHERE generated_by_requisition_id=%s", (row.id,))[0][0]
    assert sql(
        db, "SELECT allocated,reserved FROM project_balance WHERE project_id=%s", (child,)
    ) == [(Decimal(0), Decimal(0))]
    events = sql(
        db,
        "SELECT decision_id FROM requisition_status_event "
        "WHERE requisition_id=%s AND to_status='sourcing_in_progress'",
        (row.id,),
    )
    assert len(events) == 1 and events[0][0] is not None
    assert sql(
        db,
        "SELECT reason FROM audit_log WHERE target_id=%s AND action='project.create'",
        (child,),
    ) == [(f"Generated from requisition {row.number}",)]
    assert sql(
        db,
        "SELECT count(*) FROM audit_log WHERE target_id=%s AND action='requisition.approve'",
        (row.id,),
    ) == [(1,)]
    assert sql(db, "SELECT count(*) FROM project_status_event WHERE project_id=%s", (child,)) == [
        (0,)
    ]
    assert drift(db) == []
    # REQ-003: the generated sub-project takes no requisition.
    with pytest.raises(ProblemError) as error:
        RequisitionService(db[0], Scope(db[1]), db[3]).create(
            RequisitionCreate(title="Again", project_id=child)
        )
    assert error.value.checks["problem"] == "requisition_generated_project"


def test_deferred_parent_blocks_approval_and_rolls_everything_back(lifecycle_db):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    row = linked(db, p)
    change(db, "project", p.id, "status='deferred'")
    counts = "SELECT (SELECT count(*) FROM project),(SELECT count(*) FROM ledger_entry)"
    projects_before = sql(db, counts)
    with pytest.raises(ProblemError) as error:
        approve(db, row)
    assert error.value.code == ErrorCode.CONFLICT
    assert error.value.checks["problem"] == "project_not_active"
    assert "deferred" in error.value.detail
    after = state(db, row.id)
    assert after.status == "approval_pending" and after.approval.status == "running"
    assert sql(db, counts) == projects_before
    assert sql(db, "SELECT count(*) FROM approval_decision") == [(0,)]
    assert drift(db) == []


def test_standalone_new_project_allocates_and_reserves(lifecycle_db):
    db, _ = lifecycle_db
    grant(db, "budget.allocate")
    row = standalone(db)
    approve(db, row)
    after = state(db, row.id)
    assert after.status == "sourcing_in_progress" and after.reservation.funding_round == 1
    new = sql(
        db,
        "SELECT id,status,parent_id,generated_by_requisition_id,owner_id FROM project "
        "WHERE id=(SELECT project_id FROM requisition WHERE id=%s)",
        (row.id,),
    )[0]
    assert new[1:] == ("active", None, None, db[3])
    ledger = sql(
        db,
        "SELECT entry_type,bucket,amount FROM ledger_entry WHERE project_id=%s ORDER BY id",
        (new[0],),
    )
    assert [(t, b, a) for t, b, a in ledger] == [
        ("allocation", "allocated", Decimal(60)),
        ("reservation", "reserved", Decimal(60)),
    ]
    assert sql(db, "SELECT standalone FROM requisition WHERE id=%s", (row.id,)) == [(True,)]
    created = sql(
        db,
        "SELECT reason,after FROM audit_log WHERE target_id=%s AND action='project.create'",
        (new[0],),
    )
    assert len(created) == 1
    assert created[0][0] == f"Standalone requisition {row.number} approved"
    assert created[0][1]["source"] == "requisition"
    assert created[0][1]["requisition_id"] == str(row.id)
    assert created[0][1]["generated_by_requisition_id"] is None
    ids = {
        t: i
        for i, t in sql(db, "SELECT id,entry_type FROM ledger_entry WHERE project_id=%s", (new[0],))
    }
    approved = sql(
        db,
        "SELECT after FROM audit_log WHERE target_id=%s AND action='requisition.approve'",
        (row.id,),
    )
    assert len(approved) == 1
    assert approved[0][0]["allocation_entry_id"] == ids["allocation"]
    assert approved[0][0]["reservation_entry_id"] == ids["reservation"]
    assert approved[0][0]["created_project_id"] == str(new[0])
    assert drift(db) == []
    # Plant 4: the new project is not marked generated, so it takes further requisitions.
    again = RequisitionService(db[0], Scope(db[1]), db[3]).create(
        RequisitionCreate(title="Second", project_id=new[0])
    )
    assert again.project_id == new[0]


@pytest.mark.parametrize("mode", ["roll_up", "roll_down"])
def test_standalone_new_project_in_every_funding_mode(lifecycle_db, mode):
    db, _ = lifecycle_db
    OrgService(db[0], Scope(db[1]), db[3]).set_setting("funding_mode", SettingPut(value=mode))
    grant(db, "budget.allocate")
    row = standalone(db)
    approve(db, row)
    assert state(db, row.id).status == "sourcing_in_progress"
    assert drift(db) == []


def test_standalone_without_budget_allocate_is_403_and_writes_nothing(lifecycle_db):
    db, _ = lifecycle_db
    row = standalone(db)
    before = sql(db, "SELECT (SELECT count(*) FROM project),(SELECT count(*) FROM ledger_entry)")
    with pytest.raises(ProblemError) as error:
        approve(db, row)
    assert error.value.code == ErrorCode.FORBIDDEN
    assert "Budget Allocate" in error.value.detail
    assert state(db, row.id).status == "approval_pending"
    assert (
        sql(db, "SELECT (SELECT count(*) FROM project),(SELECT count(*) FROM ledger_entry)")
        == before
    )


def test_standalone_assigned_reserves_on_project_and_generates_sub_project(lifecycle_db):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    fund(db, p, "100")
    row = standalone(db)
    approve(db, row, p.id)
    after = state(db, row.id)
    assert after.status == "sourcing_in_progress" and after.project_id == p.id
    assert sql(db, "SELECT reserved FROM project_balance WHERE project_id=%s", (p.id,)) == [
        (Decimal(60),)
    ]
    assert sql(
        db, "SELECT parent_id,status FROM project WHERE generated_by_requisition_id=%s", (row.id,)
    ) == [(p.id, "active")]
    assert drift(db) == []


def test_standalone_assigned_insufficient_funds_409_nothing_written(lifecycle_db):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    fund(db, p, "50")
    row = standalone(db)
    with pytest.raises(ProblemError) as error:
        approve(db, row, p.id)
    assert error.value.code == ErrorCode.INSUFFICIENT_BUDGET
    assert "needs 60.00 USD; 50.00 USD is available." in error.value.detail
    assert state(db, row.id).status == "approval_pending"
    assert sql(db, "SELECT count(*) FROM ledger_entry WHERE source_id=%s", (row.id,)) == [(0,)]
    assert sql(
        db, "SELECT count(*) FROM project WHERE generated_by_requisition_id=%s", (row.id,)
    ) == [(0,)]


def test_assignment_needs_requisition_approve_on_that_project(lifecycle_db):
    db, user = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    fund(db, p, "100")
    row = standalone(db)
    context = OutcomeContext(
        user, None, p.id, lambda permission, target: permission != "requisition.approve"
    )
    with pytest.raises(ProblemError) as error:
        with tenant_transaction(db[0], Scope(db[1])):
            RequisitionAdapter().on_outcome(db[0], Scope(db[1]), row.id, Outcome.APPROVED, context)
    assert error.value.code == ErrorCode.FORBIDDEN
    assert state(db, row.id).status == "approval_pending"
    # An unknown or foreign project is 404, not a permission answer.
    context = OutcomeContext(user, None, uuid4(), lambda permission, target: True)
    with pytest.raises(ProblemError) as error:
        with tenant_transaction(db[0], Scope(db[1])):
            RequisitionAdapter().on_outcome(db[0], Scope(db[1]), row.id, Outcome.APPROVED, context)
    assert error.value.code == ErrorCode.NOT_FOUND


def test_project_linked_with_assignment_is_422(lifecycle_db):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    other = project(db, unit(db, uuid4().hex))
    row = linked(db, p)
    with pytest.raises(ProblemError) as error:
        approve(db, row, other.id)
    assert error.value.code == ErrorCode.VALIDATION_FAILED
    assert error.value.errors[0].field == "assign_project_id"
    assert state(db, row.id).status == "approval_pending"


def test_second_decision_on_an_approved_requisition_is_refused(lifecycle_db):
    db, _ = lifecycle_db
    grant(db, "budget.allocate")
    row = standalone(db)
    approve(db, row)
    snapshot = sql(db, "SELECT (SELECT count(*) FROM project),(SELECT count(*) FROM ledger_entry)")
    with pytest.raises(ProblemError):
        approve(db, row)
    assert (
        sql(db, "SELECT (SELECT count(*) FROM project),(SELECT count(*) FROM ledger_entry)")
        == snapshot
    )
    # The adapter itself refuses a second approval of a document that already moved on.
    context = OutcomeContext(db[3], None, None, lambda permission, target: True)
    with pytest.raises(ProblemError) as error:
        with tenant_transaction(db[0], Scope(db[1])):
            RequisitionAdapter().on_outcome(db[0], Scope(db[1]), row.id, Outcome.APPROVED, context)
    assert error.value.checks["problem"] == "invalid_transition"


def test_auto_approval_at_submit_runs_the_same_effects(lifecycle_db):
    from tests.approvals.test_workflow_definitions import svc as workflows

    db, _ = lifecycle_db
    definition_id = sql(db, "SELECT id FROM workflow_definition")[0][0]
    nodes = [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}]
    version = workflows(db).create_version(
        definition_id,
        WorkflowVersionWrite(graph={"nodes": nodes, "edges": [{"from": "start", "to": "end"}]}),
    )
    workflows(db).publish(definition_id, version.version, True)
    p = project(db, unit(db, uuid4().hex))
    row = linked(db, p)
    after = state(db, row.id)
    assert after.status == "sourcing_in_progress"
    assert sql(
        db, "SELECT parent_id,status FROM project WHERE generated_by_requisition_id=%s", (row.id,)
    ) == [(p.id, "active")]
    assert sql(db, "SELECT count(*) FROM ledger_entry WHERE source_id=%s", (row.id,)) == [(1,)]
    # The status event carries no decision for an automatic approval.
    assert sql(
        db,
        "SELECT decision_id FROM requisition_status_event "
        "WHERE requisition_id=%s AND to_status='sourcing_in_progress'",
        (row.id,),
    ) == [(None,)]
    assert drift(db) == []


def footprint(db):
    return sql(
        db,
        "SELECT (SELECT count(*) FROM project),(SELECT count(*) FROM ledger_entry),"
        "(SELECT count(*) FROM requisition_status_event),(SELECT count(*) FROM approval_decision),"
        "(SELECT count(*) FROM project_balance)",
    )


def refused(db, row, problem, code=ErrorCode.CONFLICT, assign=None):
    before = footprint(db)
    with pytest.raises(ProblemError) as error:
        approve(db, row, assign)
    assert error.value.code == code
    assert error.value.checks["problem"] == problem
    assert footprint(db) == before
    assert state(db, row.id).status == "approval_pending"
    assert drift(db) == []
    return error.value


def test_assigned_project_that_is_itself_generated_is_refused(lifecycle_db):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    approve(db, linked(db, p))
    child = sql(db, "SELECT id FROM project WHERE parent_id=%s", (p.id,))[0][0]
    row = standalone(db)
    refused(db, row, "project_generated", assign=child)


def test_assigned_project_in_another_currency_is_refused(lifecycle_db):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    fund(db, p, "100")
    change(db, "project", p.id, "currency='EUR'")
    row = standalone(db)
    refused(db, row, "currency_mismatch", assign=p.id)


def test_assigned_project_at_the_depth_limit_is_refused(lifecycle_db):
    from flo.modules.projects.schemas import ProjectCreate
    from flo.modules.projects.service import ProjectService
    from tests.requisitions.test_header import settings

    db, _ = lifecycle_db
    # roll_up lets the deepest project be funded directly, without funding its ancestors.
    OrgService(db[0], Scope(db[1]), db[3]).set_setting("funding_mode", SettingPut(value="roll_up"))
    deepest = project(db, unit(db, uuid4().hex))
    for _ in range(4):
        deepest = ProjectService(db[0], Scope(db[1]), db[3], settings(db[0])).create(
            ProjectCreate(
                bu_id=deepest.bu_id,
                parent_id=deepest.id,
                name="Level",
                currency="USD",
                department_code="D",
                ledger_account_code="L",
            )
        )
    fund(db, deepest, "100")
    row = standalone(db)
    refused(db, row, "project_depth_exceeded", assign=deepest.id)


def test_reservation_mismatch_is_an_invariant_breach_and_rolls_back(lifecycle_db, caplog):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    row = linked(db, p)
    change(db, "requisition", row.id, "estimate=70")
    with caplog.at_level("ERROR"):
        refused(db, row, "reservation_mismatch")
    assert "reservation mismatch" in caplog.text


def test_missing_reservation_pointer_is_the_same_invariant_breach(lifecycle_db, caplog):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    row = linked(db, p)
    change(db, "requisition", row.id, "reservation_entry_id=NULL")
    with caplog.at_level("ERROR"):
        refused(db, row, "reservation_mismatch")
    assert "reservation mismatch" in caplog.text


def test_budget_evidence_required_is_satisfied_by_the_requisition_reference(lifecycle_db):
    db, _ = lifecycle_db
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(
        "budget.evidence_required", SettingPut(value=True)
    )
    grant(db, "budget.allocate")
    row = standalone(db)
    approve(db, row)
    assert state(db, row.id).status == "sourcing_in_progress"
    assert sql(
        db,
        "SELECT ref FROM ledger_evidence WHERE entry_id=(SELECT id FROM ledger_entry "
        "WHERE entry_type='allocation' AND project_id=(SELECT project_id FROM requisition "
        "WHERE id=%s))",
        (row.id,),
    ) == [(f"requisition:{row.number}",)]
    assert drift(db) == []


def test_keyed_final_approval_replays_through_the_decision_route(lifecycle_db):
    import asyncio

    from flo.api.health import app
    from flo.modules.org.service import OrgService as Org
    from tests.requisitions.test_header import client, headers, login

    db, _ = lifecycle_db
    Org(db[0], Scope(db[1]), db[3]).set_setting(
        "approval.separation_of_duty", SettingPut(value=False)
    )
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    approver = auth.seed_baseline_roles()["approver"]
    auth.grant_role(db[3], approver.id, AuthorizationTarget.organization(db[1]))
    allocator = auth.create_role("allocator", "Allocator")
    auth.grant_permission(allocator.id, "budget.allocate")
    auth.grant_role(db[3], allocator.id, AuthorizationTarget.organization(db[1]))
    row = standalone(db)
    current = state(db, row.id)
    task = get_instance(db[0], Scope(db[1]), current.approval.instance_id).tasks[-1].id
    key = uuid4().hex

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            responses = []
            for _ in range(2):
                responses.append(
                    await browser.post(
                        f"/api/v1/approvals/tasks/{task}/decisions",
                        json={"outcome": "approve", "document_version": current.version},
                        headers=headers(browser, key),
                    )
                )
            return responses

    first, second = asyncio.run(scenario())
    assert first.status_code == 200, first.text
    assert second.status_code == 200 and second.json() == first.json()
    after = state(db, row.id)
    assert after.status == "sourcing_in_progress"
    assert sql(db, "SELECT count(*) FROM approval_decision") == [(1,)]
    assert sql(
        db,
        "SELECT entry_type,count(*) FROM ledger_entry WHERE project_id=%s GROUP BY entry_type "
        "ORDER BY entry_type",
        (after.project_id,),
    ) == [("allocation", 1), ("reservation", 1)]
    assert sql(db, "SELECT count(*) FROM project WHERE id=%s", (after.project_id,)) == [(1,)]
    assert drift(db) == []
