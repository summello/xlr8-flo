"""M2 exit criterion in one path: conditional two-level approval to sub-project (E09-S14)."""

from decimal import Decimal
from uuid import uuid4

import pytest

from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import decide, get_instance
from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
from flo.modules.requisitions import approval
from tests.approvals.test_workflow_definitions import draft, svc
from tests.requisitions.test_approval_effects import drift, linked, state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_header import project, unit
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lifecycle import sql
from tests.requisitions.test_lines import lines_db as lines_db


def step(node, name, user):
    return {
        "id": node,
        "kind": "approval",
        "name": name,
        "approvers": {"type": "users", "user_ids": [str(user)]},
        "mode": "any",
    }


def two_level_graph(a, b, threshold="1000"):
    edges = [
        {"from": "start", "to": "condition"},
        {
            "from": "condition",
            "to": "a",
            "when": {
                "field": "amount",
                "op": "gt",
                "value": {"amount": threshold, "currency": "USD"},
            },
        },
        {"from": "condition", "to": "a_low", "default": True},
        {"from": "a", "to": "b"},
        {"from": "b", "to": "end"},
        {"from": "a_low", "to": "end"},
    ]
    return {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "condition", "kind": "condition", "name": "Estimate"},
            step("a", "Level 1", a),
            step("b", "Level 2", b),
            step("a_low", "Level 1", a),
            {"id": "end", "kind": "end"},
        ],
        "edges": edges,
    }


def setup(db, user_a, threshold="1000"):
    """Second approver B, then workflow v2 (conditional, two levels) replaces the fixture's."""
    b = IdentityId(uuid4())
    db[0].execute(
        "INSERT INTO identity(id,email,password_hash) VALUES(%s,%s,%s)",
        (b, f"second-{b}@example.test", "$argon2id$test-placeholder"),
    )
    db[0].execute("INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)", (b, db[1]))
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    auth.grant_role(
        b, auth.seed_baseline_roles()["approver"].id, AuthorizationTarget.organization(db[1])
    )
    workflow = svc(db).list(None, None).rows[0]
    draft(db, workflow, two_level_graph(user_a, b, threshold))
    svc(db).publish(workflow.id, 2, True)
    return b


def instance_of(db, row):
    current = state(db, row.id)
    return current, get_instance(db[0], Scope(db[1]), current.approval.instance_id)


def open_task(instance):
    open_ = [t for t in instance.tasks if t.status == "open"]
    assert len(open_) == 1
    return open_[0]


def approve_as(db, row, user):
    current, instance = instance_of(db, row)
    decide(
        db[0],
        Scope(db[1]),
        task_id=open_task(instance).id,
        actor_id=user,
        body=DecisionWrite(outcome="approve", document_version=current.version),
    )


def reservations(db, project_id):
    return sql(
        db,
        "SELECT amount FROM ledger_entry WHERE project_id=%s AND entry_type='reservation'",
        (project_id,),
    )


def subprojects(db, row):
    return sql(
        db,
        "SELECT id,parent_id FROM project WHERE generated_by_requisition_id=%s",
        (row.id,),
    )


def submitted(db, price):
    p = project(db, unit(db, uuid4().hex))
    return p, linked(db, p, price=price, available="2000")


def test_high_estimate_walks_two_levels_then_generates_sub_project(lifecycle_db):
    """WF-002, WF-003, REQ-002, ACC-001."""
    db, a = lifecycle_db
    b = setup(db, a)
    p, row = submitted(db, "1500")
    current, instance = instance_of(db, row)
    assert current.status == "approval_pending"
    assert reservations(db, p.id) == [(Decimal(1500),)]
    # The instance records steps as they open; the whole conditional route is the preview.
    workflow = svc(db).list(None, None).rows[0]
    for amount, labels in (("1500", ["Level 1", "Level 2"]), ("300", ["Level 1"])):
        preview = svc(db).route_preview(workflow.id, 2, amount, "USD", p.bu_id)
        assert [s.label for s in preview.steps] == labels
    assert [s.label for s in instance.route] == ["Level 1"]
    assert open_task(instance).label == "Level 1"

    approve_as(db, row, a)
    current, instance = instance_of(db, row)
    assert current.status == "approval_pending"
    assert subprojects(db, row) == []
    assert reservations(db, p.id) == [(Decimal(1500),)]
    assert [s.label for s in instance.route] == ["Level 1", "Level 2"]
    assert open_task(instance).label == "Level 2"
    assert open_task(instance).approver_emails == [f"second-{b}@example.test"]

    approve_as(db, row, b)
    assert state(db, row.id).status == "sourcing_in_progress"
    assert subprojects(db, row) == [(subprojects(db, row)[0][0], p.id)]
    child = subprojects(db, row)[0][0]
    assert sql(
        db, "SELECT allocated,reserved FROM project_balance WHERE project_id=%s", (child,)
    ) == [(Decimal(1500), Decimal(1500))]
    # E09-S16: the reservation moved to the sub-project through a main -> sub transfer.
    assert reservations(db, child) == [(Decimal(1500),)]
    assert sql(
        db,
        "SELECT project_id,amount FROM ledger_entry WHERE entry_type='transfer' ORDER BY amount",
    ) == [(p.id, Decimal(-1500)), (child, Decimal(1500))]
    assert sql(
        db, "SELECT allocated,reserved FROM project_balance WHERE project_id=%s", (p.id,)
    ) == [(Decimal(500), Decimal(0))]
    assert drift(db) == []


def test_low_estimate_takes_one_level(lifecycle_db):
    """WF-002: the condition routes 300 to a single step."""
    db, a = lifecycle_db
    setup(db, a)
    p, row = submitted(db, "300")
    _, instance = instance_of(db, row)
    assert [s.label for s in instance.route] == ["Level 1"]
    approve_as(db, row, a)
    assert state(db, row.id).status == "sourcing_in_progress"
    assert [parent for _, parent in subprojects(db, row)] == [p.id]
    assert reservations(db, p.id) == [(Decimal(300),)]
    assert drift(db) == []


def test_sub_project_failure_rolls_back_final_approval(lifecycle_db, monkeypatch):
    """WF-003: a failing sub-project insert leaves the decision, status and ledger untouched."""
    db, a = lifecycle_db
    b = setup(db, a)
    p, row = submitted(db, "1500")
    approve_as(db, row, a)

    def boom(*args, **kwargs):
        raise RuntimeError("sub-project insert failed")

    monkeypatch.setattr(approval, "_generate", boom)
    counts = (
        "SELECT (SELECT count(*) FROM approval_decision),(SELECT count(*) FROM project),"
        "(SELECT count(*) FROM ledger_entry)"
    )
    before = sql(db, counts)
    with pytest.raises(RuntimeError):
        approve_as(db, row, b)
    current, instance = instance_of(db, row)
    assert current.status == "approval_pending" and current.approval.status == "running"
    assert sql(db, counts) == before
    assert open_task(instance).label == "Level 2"
    assert subprojects(db, row) == []
    assert reservations(db, p.id) == [(Decimal(1500),)]
    assert drift(db) == []
