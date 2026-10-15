"""Return, edit and resubmit preserve every prior value; material changes restart (E09-S08)."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import decide, get_instance
from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
from flo.modules.org.schemas import MasterCreate
from flo.modules.requisitions import revisions
from flo.modules.requisitions.schemas import LineCreate, LinesReplace
from flo.modules.requisitions.service import RequisitionService
from tests.approvals.test_engine import graph
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.requisitions.test_approval_effects import fund
from tests.requisitions.test_catalogue import record
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_header import org_svc, project, unit
from tests.requisitions.test_lifecycle import command, edit_justification, sql
from tests.requisitions.test_lines import BODY, service
from tests.requisitions.test_lines import lines_db as lines_db
from tests.requisitions.test_submission_check import change, complete


@pytest.fixture
def two_step_db(lines_db):
    """A two-step published workflow and two reviewers (step two excludes step one's decider)."""
    db = lines_db
    from datetime import date

    from flo.api.document_types import register_document_types

    register_document_types()
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = auth.seed_baseline_roles()["approver"]
    for email in ("reviewer@example.test", "second@approver.test"):
        user = IdentityId(uuid4())
        db[0].execute(
            "INSERT INTO identity(id,email,password_hash) VALUES(%s,%s,%s)",
            (user, email, "$argon2id$test-placeholder"),
        )
        db[0].execute(
            "INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)", (user, db[1])
        )
        auth.grant_role(user, role.id, AuthorizationTarget.organization(db[1]))
    org_svc(db).create_master(
        "ledger_account",
        MasterCreate(
            code="L2",
            name="L2",
            effective_from=date(2000, 1, 1),
            attributes={"account_type": "expense"},
        ),
    )
    workflow = definition(db)
    draft(db, workflow, graph(two=True))
    svc(db).publish(workflow.id, 1, True)
    return db


def person(db, email):
    return db[0].execute("SELECT id FROM identity WHERE email=%s", (email,)).fetchone()[0]


def detail(db, id):
    return RequisitionService(db[0], Scope(db[1])).detail(id, lambda p: True)


def decide_open(db, id, email, outcome, mode=None):
    current = detail(db, id)
    instance = get_instance(db[0], Scope(db[1]), current.approval.instance_id)
    task = next(t for t in reversed(instance.tasks) if t.status == "open")
    decide(
        db[0],
        Scope(db[1]),
        task_id=task.id,
        actor_id=person(db, email),
        body=DecisionWrite(
            outcome=outcome,
            reject_mode=mode,
            comment=None if outcome == "approve" else "Changes required",
            document_version=current.version,
        ),
    )


def returned_at_step_two(db, price="60"):
    """Submitted, approved at step one, returned at step two."""
    p = project(db, unit(db, uuid4().hex))
    row, _ = complete(db, p, price=price)
    sql(db, "UPDATE project_balance SET allocated=0 WHERE project_id=%s RETURNING 1", (p.id,))
    fund(db, p, "100")
    command(db, row)
    decide_open(db, row.id, "reviewer@example.test", "approve")
    decide_open(db, row.id, "second@approver.test", "return")
    return p, detail(db, row.id)


def instances(db, id):
    return sql(
        db,
        "SELECT id,status FROM approval_instance WHERE document_id=%s ORDER BY started_at,id",
        (id,),
    )


def releases(db, id):
    return sql(
        db,
        "SELECT amount,reason FROM ledger_entry WHERE source_id=%s AND entry_type='release'",
        (id,),
    )


def test_non_material_resubmit_resumes_at_the_returning_step(two_step_db):
    db = two_step_db
    _, row = returned_at_step_two(db)
    edit_justification(db, row, "Explained after the return")
    found = revisions.change_set(db[0], Scope(db[1]), row.id, None, lambda p: True)
    assert (found.material, found.routing, found.nothing_changed) == (False, "same_step", False)
    assert found.funding.outcome == "unchanged"
    command(db, row, note="Added the missing justification")
    after = detail(db, row.id)
    assert after.status == "approval_pending"
    assert after.reservation.entry_id == row.reservation.entry_id
    assert after.reservation.funding_round == 1 and after.reservation.remaining == "60.0000"
    resumed = get_instance(db[0], Scope(db[1]), after.approval.instance_id)
    assert after.approval.instance_id != row.approval.instance_id
    assert [(s.node_id, s.state.value) for s in resumed.route if s.node_id == "one"] == [
        ("one", "carried")
    ]
    assert [t.node_id for t in resumed.tasks if t.status == "open"] == ["two"]
    assert releases(db, row.id) == []
    assert sql(
        db,
        "SELECT revision_no,kind,status,note FROM requisition_revision "
        "WHERE requisition_id=%s ORDER BY revision_no",
        (row.id,),
    ) == [
        (1, "submission", "applied", None),
        (2, "submission", "applied", "Added the missing justification"),
    ]


def edit_quantity(db, row, quantity="1", price="80"):
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    line = service(db).list_lines(row.id).rows[0]
    service(db).write(
        row.id,
        str(current.version),
        line_id=line.id,
        body=LineCreate(**(BODY | {"quantity": quantity, "unit_price": price})),
    )


def test_material_estimate_change_restarts_and_re_reserves_once(two_step_db):
    db = two_step_db
    p, row = returned_at_step_two(db)
    edit_quantity(db, row)
    found = revisions.change_set(db[0], Scope(db[1]), row.id, None, lambda p: True)
    assert (found.material, found.routing) == (True, "restarts")
    assert found.funding.outcome == "reserved_more"
    estimate = next(r for r in found.rows if r.field == "estimate")
    assert (estimate.was, estimate.now, estimate.delta.direction) == ("60.00", "80.00", "up")
    assert estimate.delta.amount == "20.00" and estimate.delta.percent == "33.3"
    command(db, row)
    after = detail(db, row.id)
    assert after.reservation.funding_round == 2 and after.reservation.remaining == "80.0000"
    assert releases(db, row.id) == [(Decimal(-60), "resubmit")]
    fresh = get_instance(db[0], Scope(db[1]), after.approval.instance_id)
    assert not any(s.state.value == "carried" for s in fresh.route)
    assert [t.node_id for t in fresh.tasks if t.status == "open"] == ["one"]


def test_line_count_and_ledger_account_are_material_without_moving_money(two_step_db):
    db = two_step_db
    _, row = returned_at_step_two(db)
    edit_justification(db, row, "Text only")
    base = sql(db, "SELECT snapshot FROM requisition_revision WHERE revision_no=1", ())[0][0]
    # Split the 60.00 line into two of 30.00: same estimate, different line count.
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    line = service(db).list_lines(row.id).rows[0]
    half = BODY | {"quantity": "1", "unit_price": "30"}
    service(db).write(
        row.id,
        str(current.version),
        replacement=LinesReplace(lines=[{"line_id": line.id, **half}, half]),
    )
    found = revisions.change_set(db[0], Scope(db[1]), row.id, None, lambda p: True)
    assert found.material and found.routing == "restarts" and found.funding.outcome == "unchanged"
    assert revisions.snapshot_digest(base) != revisions.snapshot_digest(
        revisions.live_snapshot(db[0], Scope(db[1]), row.id)
    )
    change(db, "requisition", row.id, "ledger_account_code='L2'")
    command(db, row)
    after = detail(db, row.id)
    fresh = get_instance(db[0], Scope(db[1]), after.approval.instance_id)
    assert [t.node_id for t in fresh.tasks if t.status == "open"] == ["one"]
    assert after.reservation.funding_round == 1 and releases(db, row.id) == []


def test_catalogue_item_is_part_of_the_material_digest(two_step_db):
    db = two_step_db
    p = project(db, unit(db, uuid4().hex))
    row, _ = complete(db, p)
    before = revisions.live_snapshot(db[0], Scope(db[1]), row.id)
    line = service(db).list_lines(row.id).rows[0]
    item = record(db, SimpleNamespace(id=row.bu_id))
    change(db, "requisition_line", line.id, "catalogue_item_id=%s", (item,))
    after = revisions.live_snapshot(db[0], Scope(db[1]), row.id)
    assert revisions.snapshot_digest(before) != revisions.snapshot_digest(after)


def test_resubmit_with_nothing_changed_is_refused_and_writes_nothing(two_step_db):
    db = two_step_db
    _, row = returned_at_step_two(db)
    found = revisions.change_set(db[0], Scope(db[1]), row.id, None, lambda p: True)
    assert found.nothing_changed and found.routing == "not_applicable" and found.rows == []
    with pytest.raises(ProblemError) as error:
        command(db, row)
    assert error.value.code == ErrorCode.CONFLICT
    assert error.value.checks["problem"] == "nothing_changed"
    assert error.value.detail == "Not resubmitted. Nothing has changed."
    assert detail(db, row.id).status == "draft"
    assert len(instances(db, row.id)) == 1
    assert sql(db, "SELECT count(*) FROM requisition_revision", ()) == [(1,)]


def test_revision_one_is_never_overwritten_and_the_database_refuses_updates(two_step_db):
    db = two_step_db
    _, row = returned_at_step_two(db)
    first = sql(db, "SELECT snapshot::text FROM requisition_revision WHERE revision_no=1", ())
    edit_justification(db, row, "Version two text")
    edit_quantity(db, row)
    command(db, row)
    assert (
        sql(db, "SELECT snapshot::text FROM requisition_revision WHERE revision_no=1", ()) == first
    )
    assert [
        r[0] for r in sql(db, "SELECT revision_no FROM requisition_revision ORDER BY 1", ())
    ] == [
        1,
        2,
    ]
    import psycopg

    for statement in (
        "UPDATE requisition_revision SET snapshot='{}'::jsonb WHERE revision_no=1",
        "UPDATE requisition_revision SET status='withdrawn' WHERE revision_no=1",
        "DELETE FROM requisition_revision WHERE revision_no=1",
    ):
        with pytest.raises(psycopg.Error, match="immutable"):
            sql(db, statement + " RETURNING 1")
        db[0].rollback()


def test_rejected_to_draft_resubmits_like_a_first_submission_and_keeps_history(two_step_db):
    db = two_step_db
    p = project(db, unit(db, uuid4().hex))
    row, _ = complete(db, p)
    sql(db, "UPDATE project_balance SET allocated=0 WHERE project_id=%s RETURNING 1", (p.id,))
    fund(db, p, "100")
    command(db, row)
    decide_open(db, row.id, "reviewer@example.test", "reject", "return_to_draft")
    assert detail(db, row.id).reservation.remaining == "0.0000"
    with pytest.raises(ProblemError):
        command(db, row)  # nothing changed since revision 1
    edit_justification(db, row, "Reworded after the rejection")
    command(db, row)
    after = detail(db, row.id)
    assert after.status == "approval_pending" and after.reservation.funding_round == 2
    assert after.reservation.remaining == "100.0000"
    assert [
        t.node_id
        for t in get_instance(db[0], Scope(db[1]), after.approval.instance_id).tasks
        if t.status == "open"
    ] == ["one"]


def test_material_resubmit_falls_back_when_resume_is_refused(two_step_db):
    """Plant 1: resume_from offered on a material change is refused; the code starts fresh."""
    db = two_step_db
    _, row = returned_at_step_two(db)
    edit_quantity(db, row)
    # The latest instance is returned, so resume_from is offered; approvals compares its own
    # digest, refuses (cannot_resume) and the transition starts fresh from step one.
    command(db, row)
    after = detail(db, row.id)
    fresh = get_instance(db[0], Scope(db[1]), after.approval.instance_id)
    assert not any(s.state.value == "carried" for s in fresh.route)
    assert [t.node_id for t in fresh.tasks if t.status == "open"] == ["one"]
