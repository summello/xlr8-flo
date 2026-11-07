"""Issued POs are immutable; numbered change orders with diff and budget effect (E13-S06)."""

import asyncio
import typing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import (
    DecisionWrite,
    DelegationCreate,
    DocumentType,
    Outcome,
    OutcomeContext,
)
from flo.modules.approvals.service import decide, get_instance
from flo.modules.budget import reconcile
from flo.modules.purchasing.approval import PoChangeAdapter
from flo.modules.purchasing.changes import ChangeService
from flo.modules.purchasing.schemas import (
    PoChangeAdd,
    PoChangeCreate,
    PoChangeLineEdit,
    PoChangePatch,
    PoPatch,
)
from tests.approvals.test_engine import graph
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.isolation.test_real_sessions import client, login
from tests.purchasing.test_issue import approve, run, totals
from tests.purchasing.test_purchase_orders import _race, create, pos, ready, send
from tests.requisitions.test_approval_effects import grant, reviewer
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import refused, sql
from tests.vendors.test_vendors import regrant


def changes(db, connection=None):
    return ChangeService(connection or db[0], Scope(db[1]), db[3])


def issued(db, qty="4", awarded="10"):
    rfq, row, a, _, award_id, _ = ready(db, awarded, awarded)
    po = create(db, rfq, a, award_id, qty)
    approve(db, po)
    run(db, po)
    return pos(db).get(po.id), row


def open_change(db, po, version=None, **edit):
    """A draft change order on the issued `po`, edited by `edit`; returns it as read."""
    with correlation_context("change"):
        change = changes(db).create(po.id, PoChangeCreate(reason="vendor asked"))
        return changes(db).update(po.id, change.number, PoChangePatch(**edit), '"1"')


def line(**edit):
    return PoChangeLineEdit(line_no=1, **edit)


def publish_change(db):
    workflow = definition(db, document_type="po_change")
    draft(db, workflow, graph())
    svc(db).publish(workflow.id, 1, True)
    grant(db, "purchase_order.approve")


def decide_change(db, change, outcome="approve"):
    [(instance_id,)] = sql(
        db,
        "SELECT id FROM approval_instance WHERE document_type='po_change' AND document_id=%s "
        "ORDER BY started_at DESC LIMIT 1",
        (change.id,),
    )
    instance = get_instance(db[0], Scope(db[1]), instance_id)
    version = sql(db, "SELECT version FROM po_change WHERE id=%s", (change.id,))[0][0]
    with correlation_context("decide"):
        return decide(
            db[0], Scope(db[1]), task_id=instance.tasks[-1].id, actor_id=reviewer(db),
            body=DecisionWrite(
                outcome=outcome, document_version=version, comment="ok",
                reject_mode="return_to_draft" if outcome == "reject" else None,
            ),
        )  # fmt: skip


def submit(db, change):
    with correlation_context("change"):
        return changes(db).submit(change.po_id, change.number, f'"{change.version}"')


def entries(db):
    return sql(
        db, "SELECT entry_type, amount, idempotency_key, source_id FROM ledger_entry ORDER BY id"
    )


def revisions(db):
    return sql(db, "SELECT revision, status, version FROM purchase_order ORDER BY revision")


# -- immutability --------------------------------------------------------------------------


def test_an_issued_po_refuses_every_write_in_the_service_and_the_database(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    error = refused(lambda: pos(db).update(po.id, PoPatch(terms="x"), '"2"'))
    assert error.checks["problem"] == "issued_is_immutable"
    refused(lambda: pos(db).delete(po.id, '"2"'))
    for statement in (  # plant: dropping either trigger lets these through
        "UPDATE purchase_order SET terms='x' WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET needed_by=COALESCE(needed_by, current_date)+1 "
        "WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET currency='EUR' WHERE id=%s RETURNING 1",
        "DELETE FROM purchase_order WHERE id=%s RETURNING 1",
        "DELETE FROM po_line WHERE po_id=%s RETURNING 1",
        "UPDATE po_line SET tax=1 WHERE po_id=%s RETURNING 1",
        "UPDATE po_line SET description='x' WHERE po_id=%s RETURNING 1",
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match="issued_is_immutable"):
            sql(db, statement, (po.id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(
            db,
            "INSERT INTO po_line SELECT org_id, po_id, 9, award_id, rfq_line_id, vendor_id, "
            "requisition_id, requisition_line_id, description, uom_code, quantity, unit_price, "
            "project_id, department_code, ledger_account_code, price, tax, freight, line_total, "
            "budget_amount, budget_amount_project FROM po_line WHERE po_id=%s RETURNING 1",
            (po.id,),
        )
    assert sql(db, "SELECT terms FROM purchase_order WHERE id=%s", (po.id,)) == [(None,)]


def test_status_and_version_cannot_be_rewritten_by_raw_sql(lifecycle_db):
    """Review round 1: only the lifecycle transitions pass the trigger, never a reopen."""
    db, _ = lifecycle_db
    po, _ = issued(db)
    bad = (  # plant: skipping status/version in the trigger lets every one of these through
        "UPDATE purchase_order SET status='draft', version=version+1 WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET status='approved', version=version+1 WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET status='superseded' WHERE id=%s RETURNING 1",  # version kept
        "UPDATE purchase_order SET version=version+1 WHERE id=%s RETURNING 1",  # no transition
        "UPDATE purchase_order SET status='superseded', version=version+1, issued_at=now() "
        "WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET issued_at=now() WHERE id=%s RETURNING 1",  # stamp is one-time
    )
    for statement in bad:
        with pytest.raises(psycopg.errors.CheckViolation, match="issued_is_immutable"):
            sql(db, statement, (po.id,))
    assert sql(db, "SELECT status, version FROM purchase_order WHERE id=%s", (po.id,)) == [
        ("issued", 2)
    ]
    sql(
        db,
        "UPDATE purchase_order SET status='superseded', version=version+1 WHERE id=%s RETURNING 1",
        (po.id,),
    )  # the lifecycle's own transition still passes
    for status in ("draft", "issued", "closed", "cancelled"):  # nothing leaves superseded
        with pytest.raises(psycopg.errors.CheckViolation, match="issued_is_immutable"):
            sql(
                db,
                "UPDATE purchase_order SET status=%s, version=version+1 WHERE id=%s RETURNING 1",
                (status, po.id),
            )


# -- diff, materiality, revisions ----------------------------------------------------------


def test_a_date_change_is_not_material_applies_at_once_and_keeps_the_prior_revision(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    rows, spent = entries(db), totals(db)
    change = open_change(db, po, needed_by=date(2030, 1, 5), terms="Net 45")
    assert change.material is False and change.number == 1
    assert {d["field"] for d in change.diff} == {"needed_by", "terms"}
    assert any(
        d["field"] == "terms" and d["was"] is None and d["now"] == "Net 45" for d in change.diff
    )
    assert change.budget_effect == []
    applied = changes(db).apply(po.id, 1, f'"{change.version}"')
    assert applied.status == "approved" and applied.result_po_id is not None
    assert revisions(db) == [(0, "superseded", 3), (1, "issued", 2)]
    new = pos(db).get(applied.result_po_id)
    assert (new.number, new.revision, new.supersedes_id) == (po.number, 1, po.id)
    assert (new.terms, new.needed_by) == ("Net 45", date(2030, 1, 5))
    old = pos(db).get(po.id)  # the prior revision, readable with its sent snapshot
    assert (old.terms, old.status, old.vendor_snapshot) == (None, "superseded", po.vendor_snapshot)
    assert [x.model_dump() for x in old.lines] == [x.model_dump() for x in po.lines]
    assert (entries(db), totals(db)) == (rows, spent)  # dates post nothing
    assert sql(db, "SELECT count(*) FROM audit_log WHERE action='purchase_order.change_apply'") == [
        (1,)
    ]


def test_the_issue_stamp_is_written_once_and_whole(lifecycle_db):
    """Review round 2: a half stamp, a clear or a repeat is refused; the whole stamp passes once."""
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    sql(db, "UPDATE purchase_order SET status='issued', version=version+1 RETURNING 1")  # unstamped
    assert sql(db, "SELECT issued_at, issued_by FROM purchase_order") == [(None, None)]
    for statement in (  # plant: checking only OLD.issued_at lets the first three through
        "UPDATE purchase_order SET issued_by=%s WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET issued_at=now() WHERE id=%s RETURNING 1",
    ):
        args = (db[3], po.id) if "issued_by=" in statement else (po.id,)
        with pytest.raises(psycopg.errors.CheckViolation, match="issued_is_immutable"):
            sql(db, statement, args)
    assert sql(db, "SELECT issued_at, issued_by FROM purchase_order") == [(None, None)]
    sql(
        db,
        "UPDATE purchase_order SET issued_at=now(), issued_by=%s WHERE id=%s RETURNING 1",
        (db[3], po.id),
    )  # the whole stamp, once
    for statement in (
        "UPDATE purchase_order SET issued_by=NULL WHERE id=%s RETURNING 1",  # clearing
        "UPDATE purchase_order SET issued_at=NULL, issued_by=NULL WHERE id=%s RETURNING 1",
        "UPDATE purchase_order SET issued_at=now() WHERE id=%s RETURNING 1",  # repeating
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match="issued_is_immutable"):
            sql(db, statement, (po.id,))


@pytest.mark.parametrize("action", ["approve", "reject", "withdraw"])
def test_a_stale_change_stays_readable_and_only_its_approval_is_refused(lifecycle_db, action):
    """Review round 2: the adapter reads stored figures; apply re-validates against the award."""
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db, "5", "5")
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    run(db, po)
    po = pos(db).get(po.id)
    publish_change(db)
    change = submit(db, open_change(db, po, lines=[line(quantity="5")]))
    other = create(db, rfq, a, award_id, "1")  # the award's last unit goes elsewhere
    assert other.status == "draft"
    adapter = PoChangeAdapter()
    summary = adapter.summaries(db[0], Scope(db[1]), [change.id])[change.id]
    assert (
        summary.number == f"{po.number} change 1"
        and adapter.snapshot(db[0], Scope(db[1]), change.id).version == change.version
    )  # plant: re-proposing in the adapter raises over_open_quantity
    rows, before = entries(db), revisions(db)
    if action == "approve":
        with pytest.raises(ProblemError) as error:
            decide_change(db, change)
        assert error.value.checks["problem"] == "over_open_quantity"
        assert changes(db).get(po.id, 1).status == "approval_pending"
    elif action == "reject":
        decide_change(db, change, "reject")
        assert changes(db).get(po.id, 1).status == "rejected"
    else:
        with correlation_context("change"):
            done = changes(db).withdraw(po.id, 1, f'"{change.version}"')
        assert done.status == "withdrawn"
    assert (entries(db), revisions(db)) == (rows, before)  # nothing posted, no new revision


def test_change_numbers_run_across_the_revision_chain(lifecycle_db):
    """Review round 1: CO2 on the new revision is number 2, and either revision finds both."""
    db, _ = lifecycle_db
    po, _ = issued(db)
    first = open_change(db, po, terms="Net 45")
    one = changes(db).apply(po.id, 1, f'"{first.version}"')
    revision = one.result_po_id
    with correlation_context("change"):
        second = changes(db).create(revision, PoChangeCreate())
        second = changes(db).update(revision, second.number, PoChangePatch(terms="Net 60"), '"1"')
    assert second.number == 2  # plant: numbering by the revision id restarts at 1 here
    two = changes(db).apply(revision, 2, f'"{second.version}"')
    assert two.status == "approved" and two.result_po_id not in (po.id, revision)
    for any_revision in (po.id, revision, two.result_po_id):
        assert [c.number for c in changes(db).list_changes(any_revision)] == [1, 2]
        assert changes(db).get(any_revision, 2).id == second.id
    assert revisions(db) == [
        (0, "superseded", 3), (1, "superseded", 3), (2, "issued", 2),
    ]  # fmt: skip
    refused(lambda: changes(db).create(revision, PoChangeCreate()))  # a superseded revision


def test_an_increase_beyond_the_open_award_quantity_is_refused_when_drafted(lifecycle_db):
    """Review round 1: the draft raises what the apply would."""
    db, _ = lifecycle_db
    po, _ = issued(db, "4", "5")  # 5 awarded, 4 ordered: one open
    change = changes(db).create(po.id, PoChangeCreate())
    with pytest.raises(ProblemError) as error:
        changes(db).update(po.id, 1, PoChangePatch(lines=[line(quantity="6")]), '"1"')
    assert error.value.checks["problem"] == "over_open_quantity"
    ok = changes(db).update(po.id, 1, PoChangePatch(lines=[line(quantity="5")]), '"1"')
    assert ok.material and ok.version == change.version + 1


def test_a_quantity_change_is_material_and_needs_a_workflow_and_approval(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    change = open_change(db, po, lines=[line(quantity="5")])
    assert change.material is True  # plant 2: marking a quantity change non-material fails here
    [d] = change.diff
    assert (d["line_no"], d["field"], d["was"], d["now"]) == (1, "quantity", "4.0000", "5.0000")
    error = refused(lambda: changes(db).apply(po.id, 1, f'"{change.version}"'))
    assert error.checks["problem"] == "approval_required"
    error = refused(lambda: submit(db, change))
    assert error.checks["problem"] == "workflow_not_set_up"  # never auto-approved
    assert revisions(db) == [(0, "issued", 2)]
    publish_change(db)
    pending = submit(db, change)
    assert pending.status == "approval_pending"
    assert refused(lambda: changes(db).create(po.id, PoChangeCreate())).checks["problem"] == (
        "change_in_progress"
    )
    decide_change(db, pending)
    done = changes(db).get(po.id, 1)
    assert done.status == "approved" and revisions(db)[-1][:2] == (1, "issued")


def test_reject_and_withdraw_end_the_change_and_leave_the_order_alone(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    publish_change(db)
    rows = entries(db)
    first = submit(db, open_change(db, po, lines=[line(quantity="5")]))
    decide_change(db, first, "reject")
    assert changes(db).get(po.id, 1).status == "rejected"
    second = changes(db).create(po.id, PoChangeCreate())
    second = changes(db).update(po.id, 2, PoChangePatch(lines=[line(quantity="5")]), '"1"')
    submitted = submit(db, second)
    with correlation_context("change"):
        assert changes(db).withdraw(po.id, 2, f'"{submitted.version}"').status == "withdrawn"
    assert revisions(db) == [(0, "issued", 2)]
    assert entries(db) == rows  # neither a rejected nor a withdrawn change posts anything


# -- budget effect -------------------------------------------------------------------------


def test_a_decrease_releases_and_re_reserves_and_a_retry_posts_nothing_twice(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    publish_change(db)
    before = entries(db)
    reserved, committed = totals(db)
    change = submit(db, open_change(db, po, lines=[line(unit_price="77.5")]))
    assert change.budget_effect[0]["delta"] == "-50.00"
    decide_change(db, change)
    new = entries(db)[len(before) :]
    own = [e for e in new if e[2].startswith(f"po:{po.id}:co1:")]
    assert [(e[0], e[1]) for e in own] == [("release", Decimal("-50.0000"))]  # one release of 50
    # decision 209: it is reserved again, as the requisition's next funding round (plant 1:
    # releasing without `rereserve` leaves the reservation where it was and fails the totals)
    assert len([e for e in new if e[0] == "reservation"]) == 1
    after_reserved, after_committed = totals(db)
    assert (after_reserved - reserved, committed - after_committed) == (Decimal(50), Decimal(50))
    [release] = own
    assert release[2].startswith(f"po:{po.id}:co1:release:") and release[3] == po.id
    status = reconcile.reconcile_org(db[0], Scope(db[1])) and reconcile.reconciliation_status(
        db[0], Scope(db[1])
    )
    assert status.status == "ok" and status.drift_rows == 0
    # a repeated approval outcome and a repeated apply are replays
    rows = entries(db)
    context = OutcomeContext(db[3], None, None, lambda permission, target: True)
    changes(db).settle(change.id, Outcome.APPROVED, context)
    done = changes(db).get(po.id, 1)
    assert changes(db).apply(po.id, 1, f'"{done.version - 1}"').status == "approved"
    assert entries(db) == rows


def test_an_increase_commits_once_and_moves_the_quantities(lifecycle_db):
    db, _ = lifecycle_db
    po, row = issued(db)
    publish_change(db)
    rows = entries(db)
    change = submit(db, open_change(db, po, lines=[line(quantity="5")]))
    assert change.budget_effect[0]["delta"] == "90.00"
    decide_change(db, change)
    [commit] = [e for e in entries(db)[len(rows) :] if e[0] == "commitment"]
    assert commit[1] == Decimal("90.0000") and commit[2].startswith(f"po:{po.id}:co1:commit:")
    assert sql(db, "SELECT ordered_qty FROM award_line") == [(Decimal("5.0000"),)]
    assert sql(db, "SELECT ordered_qty, issued_qty FROM requisition_line") == [
        (Decimal("5.0000"), Decimal("5.0000"))
    ]
    status = reconcile.reconcile_org(db[0], Scope(db[1])) and reconcile.reconciliation_status(
        db[0], Scope(db[1])
    )
    assert status.drift_rows == 0


def test_a_decrease_reopens_a_completed_requisition_and_the_quantity_is_orderable(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, a, _, award_id, _ = ready(db, "4", "4")
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    run(db, po)
    po = pos(db).get(po.id)
    state = "SELECT status FROM requisition WHERE id=%s"
    assert sql(db, state, (row.id,)) == [("completed",)]
    publish_change(db)
    decide_change(db, submit(db, open_change(db, po, lines=[line(quantity="3")])))
    assert sql(db, state, (row.id,)) == [("open_for_purchase",)]
    assert sql(db, "SELECT ordered_qty FROM award_line") == [(Decimal("3.0000"),)]
    assert sql(db, "SELECT ordered_qty, issued_qty FROM requisition_line") == [
        (Decimal("3.0000"), Decimal("3.0000"))
    ]
    again = create(db, rfq, a, award_id, "1")  # the returned unit can be ordered again
    approve(db, again)
    run(db, again)
    assert sql(db, state, (row.id,)) == [("completed",)]


def test_a_removed_line_and_an_added_line_are_material_and_a_bad_draft_is_422(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    change = changes(db).create(po.id, PoChangeCreate())

    def edit(**patch):
        return lambda: changes(db).update(po.id, 1, PoChangePatch(**patch), f'"{change.version}"')

    for patch, problem in (
        ({"lines": [line(unit_price="91")]}, "price_increase"),
        ({"lines": [line(remove=True)]}, "no_lines"),
        ({"lines": [PoChangeLineEdit(line_no=7, quantity="1")]}, "unknown_line"),
        ({"lines": [line(project_id=uuid4())]}, "use_transfer"),
        ({"lines": [line(ledger_account_code="OTHER")]}, "use_transfer"),
        ({"lines": [line(department_code="OTHER")]}, "use_transfer"),
        ({"add": [PoChangeAdd(rfq_line_id=uuid4(), quantity="1")]}, "mixed_vendors"),
        ({"document_freight": "5"}, "freight_mode_mismatch"),
    ):
        with pytest.raises(ProblemError) as error:
            edit(**patch)()
        assert error.value.checks["problem"] == problem, patch
    assert sql(db, "SELECT version FROM po_change WHERE id=%s", (change.id,)) == [(1,)]


# -- concurrency ---------------------------------------------------------------------------


def test_two_creates_at_once_make_one_draft_and_two_applies_post_once(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)

    def create_one(conn, _):
        with correlation_context("change"):
            return changes(db, conn).create(po.id, PoChangeCreate()).status

    assert _race(db, create_one) == ["change_in_progress", "draft"]
    change = changes(db).update(po.id, 1, PoChangePatch(terms="Net 60"), '"1"')
    rows = entries(db)

    def apply_one(conn, _):
        with correlation_context("change"):
            return changes(db, conn).apply(po.id, 1, f'"{change.version}"').status

    assert _race(db, apply_one) == ["approved", "approved"]  # the loser gets the replay
    assert revisions(db) == [(0, "superseded", 3), (1, "issued", 2)]
    assert entries(db) == rows
    assert sql(db, "SELECT count(*) FROM audit_log WHERE action='purchase_order.change_apply'") == [
        (1,)
    ]


# -- routes, authorization, taxonomy -------------------------------------------------------


def test_change_routes_authorize_per_role_and_need_if_match(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            base = f"/{po.id}/changes"
            regrant(db, ["purchase_order.view"])
            assert (await send(browser, "GET", base)).status_code == 200
            assert (await send(browser, "POST", base, {})).status_code == 403
            regrant(db, ["rfq.view"])
            assert (await send(browser, "GET", base)).status_code == 403
            regrant(db, ["purchase_order.view", "purchase_order.amend"])
            made = await send(browser, "POST", base, {"reason": "late"}, {"Idempotency-Key": "c1"})
            assert made.status_code == 201 and made.headers["ETag"] == '"1"', made.text
            replay = await send(
                browser, "POST", base, {"reason": "late"}, {"Idempotency-Key": "c1"}
            )
            assert replay.status_code == 201 and replay.json() == made.json()
            assert (await send(browser, "POST", base, {})).status_code == 409  # change_in_progress
            assert (await send(browser, "PATCH", f"{base}/1", {"terms": "x"})).status_code == 400
            stale = await send(browser, "PATCH", f"{base}/1", {"terms": "x"}, {"If-Match": '"9"'})
            assert stale.status_code == 409
            patched = await send(browser, "PATCH", f"{base}/1", {"terms": "x"}, {"If-Match": '"1"'})
            assert patched.status_code == 200 and patched.headers["ETag"] == '"2"'
            assert patched.json()["material"] is False
            assert (await send(browser, "POST", f"{base}/1/apply")).status_code == 400
            applied = await send(browser, "POST", f"{base}/1/apply", None, {"If-Match": '"2"'})
            assert applied.status_code == 200 and applied.json()["status"] == "approved"
            listed = await send(browser, "GET", base)
            assert [c["status"] for c in listed.json()] == ["approved"]

    asyncio.run(scenario())


def test_a_delegation_can_cover_all_nine_document_types():
    types = list(typing.get_args(DocumentType))
    assert len(types) == 9 and "po_change" in types
    now = datetime.now(UTC)
    body = DelegationCreate(
        delegate_id=uuid4(), starts_at=now, ends_at=now + timedelta(days=1),
        document_types=types, reason="away",
    )  # fmt: skip
    assert body.document_types == types
