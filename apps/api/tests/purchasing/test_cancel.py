"""Cancel or reduce a PO releases the unused commitment exactly once (E13-S07, PO-013, FIN-011)."""

import asyncio
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget import reconcile
from flo.modules.budget.service import CONSUMED_BY_ACTUAL, record_actual
from flo.modules.purchasing.cancel import cancel, reduce
from flo.modules.purchasing.issue import issue
from flo.modules.purchasing.schemas import (
    PoCancelCreate,
    PoChangeCreate,
    PoChangeLineEdit,
    PoChangePatch,
    PoReduceCreate,
)
from flo.modules.requisitions.service import reservation_entry
from tests.approvals.test_engine import graph
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.isolation.test_real_sessions import client, login
from tests.purchasing.test_changes import (
    changes,
    decide_change,
    entries,
    issued,
    line,
    open_change,
    publish_change,
    submit,
)
from tests.purchasing.test_issue import approve, run, totals
from tests.purchasing.test_po_approval import decide_po
from tests.purchasing.test_po_approval import submit as submit_po
from tests.purchasing.test_purchase_orders import _race, create, pos, ready, send
from tests.requisitions.test_approval_effects import drift, grant, reviewer
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import connection_url
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_cancel_supersede import supersede
from tests.sourcing.test_rfq import refused, sql
from tests.vendors.test_vendors import regrant


def cancel_po(db, po, version=None, flag=False, connection=None, actor=None, notify=True):
    if version is None:
        version = sql(db, "SELECT version FROM purchase_order WHERE id=%s", (po.id,))[0][0]
    body = PoCancelCreate(
        reason="vendor withdrew", cancel_requisition_lines=flag, notify_vendor=notify
    )
    with correlation_context("cancel"):
        return cancel(
            connection or db[0], Scope(db[1]), po.id, actor or db[3], f'"{version}"', body
        )


def problem(call):
    with pytest.raises(ProblemError) as error:
        call()
    return error.value.code, error.value.checks.get("problem")


def new(db, before):
    return entries(db)[len(before) :]


def releases(db, before):
    """Commitment releases posted since `before` (a rereserve also releases a reservation)."""
    return [e for e in new(db, before) if e[0] == "release" and e[2].startswith("po:")]


def clean(db):
    reconcile.reconcile_org(db[0], Scope(db[1]))
    assert reconcile.reconciliation_status(db[0], Scope(db[1])).drift_rows == 0
    assert drift(db) == []


def remaining(db):
    return sql(db, "SELECT coalesce(sum(remaining), 0) FROM commitment_remaining")[0][0]


def counters(db):
    return sql(db, "SELECT ordered_qty, issued_qty, cancelled_qty FROM requisition_line") + sql(
        db, "SELECT ordered_qty FROM award_line"
    )


def balances(db):
    return sql(
        db, "SELECT project_id, reserved, committed FROM project_balance ORDER BY project_id"
    )


def reservation(db):
    [(entry,)] = sql(db, "SELECT reservation_entry_id FROM requisition")
    return sql(
        db, "SELECT remaining FROM reservation_remaining WHERE reservation_entry_id=%s", (entry,)
    )[0][0]


# -- after issue -----------------------------------------------------------------------------


def test_cancel_after_issue_releases_the_remainder_once_and_re_reserves_it(lifecycle_db):
    db, _ = lifecycle_db
    po, row = issued(db)
    before, held = entries(db), reservation(db)
    reserved, committed = totals(db)
    cancelled = cancel_po(db, po)
    assert cancelled.status == "cancelled" and cancelled.version == po.version + 1
    assert cancelled.notice is None and cancelled.released_currency == "USD"
    [release] = releases(db, before)
    assert release[1] == -committed and release[2].startswith(f"po:{po.id}:cancel:")
    assert cancelled.released_amount == format(committed, "f")
    # decision 209: reserved again as the requisition's next funding round (plant 2)
    assert reservation(db) == held + committed
    after_reserved, after_committed = totals(db)
    assert (after_reserved - reserved, committed - after_committed) == (committed, committed)
    assert counters(db) == [(Decimal(0), Decimal(0), Decimal(0)), (Decimal(0),)]
    assert remaining(db) == 0
    clean(db)
    assert sql(db, "SELECT status FROM requisition WHERE id=%s", (row.id,)) == [
        ("open_for_purchase",)
    ]
    # a retry, with the old version or the new one, posts nothing more (FIN-011)
    rows = entries(db)
    assert cancel_po(db, po, version=po.version).status == "cancelled"
    assert cancel_po(db, po, version=cancelled.version).status == "cancelled"
    assert entries(db) == rows
    assert [
        k[0] for k in sql(db, "SELECT action FROM audit_log WHERE action='purchase_order.cancel'")
    ] == ["purchase_order.cancel"]


def test_cancel_after_a_partial_release_releases_only_the_remainder(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    publish_change(db)
    change = submit(db, open_change(db, po, lines=[line(quantity="3")]))  # releases a quarter
    decide_change(db, change)
    [(current,)] = sql(db, "SELECT id FROM purchase_order WHERE status='issued'")
    new_po = pos(db).get(current)
    left = remaining(db)
    before = entries(db)
    cancelled = cancel_po(db, new_po)
    # plant 1: releasing the original amount fails release_exceeds_commitment on this entry
    [release] = releases(db, before)
    assert -release[1] == left and remaining(db) == 0
    [first, second] = sql(
        db,
        "SELECT amount FROM ledger_entry WHERE entry_type='release' "
        "AND source_type='purchase_order' ORDER BY id",
    )
    assert cancelled.released_amount == format(-(first[0] + second[0]), "f")  # both releases
    clean(db)


def test_cancel_after_a_change_order_increase_releases_both_commitment_entries(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    publish_change(db)
    decide_change(db, submit(db, open_change(db, po, lines=[line(quantity="5")])))
    [(current,)] = sql(db, "SELECT id FROM purchase_order WHERE status='issued'")
    assert len(sql(db, "SELECT 1 FROM ledger_entry WHERE entry_type='commitment'")) == 2
    before = entries(db)
    cancel_po(db, pos(db).get(current))
    released = releases(db, before)
    assert len(released) == 2 and len({e[2] for e in released}) == 2  # no key collision
    assert remaining(db) == 0
    clean(db)


def test_cancel_with_requisition_lines_returns_the_funds_to_main_and_blocks_the_quantity(
    lifecycle_db,
):
    db, _ = lifecycle_db
    po, row = issued(db)
    award = sql(db, "SELECT award_id FROM purchase_order")[0][0]
    [(main,)] = sql(db, "SELECT project_id FROM requisition")
    balance = lambda: dict(  # noqa: E731
        (p, (r, c)) for p, r, c in balances(db)
    )
    sub_before = {p: v for p, v in balance().items() if p != main}
    held = reservation(db)
    reserved, committed = totals(db)
    cancelled = cancel_po(db, po, flag=True)
    assert cancelled.status == "cancelled"
    # plant 3: skipping the rereserve takes the amount out of the reservation the open lines need
    assert reservation(db) == held
    assert {p: v for p, v in balance().items() if p != main} == {
        p: (r, c - committed) for p, (r, c) in sub_before.items()
    }
    assert sql(db, "SELECT cancelled_qty, ordered_qty, issued_qty FROM requisition_line") == [
        (Decimal(4), Decimal(0), Decimal(0))
    ]
    clean(db)
    # the quantity cannot be ordered again: 6 are orderable, 7 are not (plant 4)
    assert main and row
    awarded = pos(db).awarded(award, None).lines[0]
    assert awarded.open_qty == "6.0000" and awarded.awarded_qty == "10.0000"


def test_a_cancelled_quantity_is_refused_with_409_not_a_database_error(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    run(db, po)
    cancel_po(db, pos(db).get(po.id), flag=True)
    before = counters(db)
    with pytest.raises(ProblemError) as error:
        create(db, rfq, a, award_id, "7")
    assert error.value.code == ErrorCode.CONFLICT and "cancelled" in error.value.detail
    assert counters(db) == before
    ok = create(db, rfq, a, award_id, "6")
    assert ok.status == "draft"


def test_cancel_lines_refuses_a_requisition_that_is_not_open_for_purchase(lifecycle_db):
    db, _ = lifecycle_db
    po, row = issued(db)
    sql(db, "UPDATE requisition SET status='sourced' WHERE id=%s RETURNING 1", (row.id,))
    rows, state = entries(db), counters(db)
    with pytest.raises(ProblemError) as error:
        cancel_po(db, po, flag=True)
    assert error.value.detail.startswith("Lines can be cancelled")
    assert entries(db) == rows and counters(db) == state
    assert cancel_po(db, po).status == "cancelled"  # without the flag the same PO cancels


def test_cancel_without_a_live_reservation_leaves_the_funds_on_the_commitment_project(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    sql(db, "UPDATE requisition SET reservation_entry_id=NULL RETURNING 1")
    before = entries(db)
    committed = totals(db)[1]
    cancel_po(db, po)
    assert [e[0] for e in new(db, before)] == ["release"]  # no rereserve, no funding round
    assert totals(db)[1] == committed - committed
    clean(db)


def test_cancel_with_the_flag_and_no_reservation_still_raises_cancelled_qty(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    sql(db, "UPDATE requisition SET reservation_entry_id=NULL RETURNING 1")
    before = entries(db)
    cancel_po(db, po, flag=True)
    assert [e[0] for e in new(db, before)] == ["release"]
    assert sql(db, "SELECT cancelled_qty FROM requisition_line") == [(Decimal(4),)]
    clean(db)


# -- before issue ----------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["draft", "approved"])
def test_cancel_before_issue_posts_nothing_and_returns_the_counters(lifecycle_db, status):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    if status == "approved":
        approve(db, po)
    rows = entries(db)
    cancelled = cancel_po(db, pos(db).get(po.id), flag=True)
    assert cancelled.status == "cancelled" and "never issued" in cancelled.notice
    assert entries(db) == rows
    assert counters(db) == [(Decimal(0), Decimal(0), Decimal(0)), (Decimal(0),)]
    assert cancelled.released_amount is None


def test_cancel_of_a_po_pending_approval_withdraws_it_and_only_the_creator_may(lifecycle_db):
    db, _ = lifecycle_db
    workflow = definition(db, document_type="po")
    draft(db, workflow, graph())
    svc(db).publish(workflow.id, 1, True)
    grant(db, "purchase_order.approve")
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    submit_po(db, po)
    other = reviewer(db)
    assert refused(lambda: cancel_po(db, po, actor=other), ErrorCode.FORBIDDEN)
    assert sql(db, "SELECT status FROM approval_instance WHERE document_type='po'") == [
        ("running",)
    ]
    stale = refused(lambda: cancel_po(db, po, version=1))
    assert stale.checks["problem"] == "stale_version"
    cancelled = cancel_po(db, po, version=2)
    assert cancelled.status == "cancelled" and cancelled.version == 4  # the withdraw moved it by 1
    assert sql(db, "SELECT status FROM approval_instance WHERE document_type='po'") == [
        ("withdrawn",)
    ]
    assert counters(db) == [(Decimal(0), Decimal(0), Decimal(0)), (Decimal(0),)]
    # the approval is over: a late decision finds a closed task
    assert refused(lambda: decide_po(db, po)).checks["problem"] == "task_closed"


def test_cancel_is_refused_while_a_change_order_is_open(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    open_change(db, po, lines=[line(quantity="3")])
    rows, state = entries(db), counters(db)
    assert problem(lambda: cancel_po(db, po)) == (ErrorCode.CONFLICT, "change_in_progress")
    assert entries(db) == rows and counters(db) == state


def test_cancel_is_refused_when_the_award_is_not_approved(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    sql(db, "UPDATE award SET status='superseded' RETURNING 1")
    rows = entries(db)
    assert problem(lambda: cancel_po(db, po)) == (ErrorCode.CONFLICT, "invalid_transition")
    assert entries(db) == rows


# -- history ---------------------------------------------------------------------------------


def test_released_amount_counts_releases_and_excludes_a_release_posted_by_an_actual(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    [(entry_id, project, amount, department, ledger)] = sql(
        db,
        "SELECT id, project_id, amount, department_code, ledger_account_code FROM ledger_entry "
        "WHERE entry_type='commitment'",
    )
    assert pos(db).get(po.id).released_amount is None
    half = amount / 2
    with correlation_context("actual"):
        record_actual(
            db[0], Scope(db[1]), project_id=project, amount=half, currency="USD",
            source_type="purchase_order", source_id=po.id, effective_date=date.today(),
            actor_id=db[3], department_code=department, ledger_account_code=ledger,
            idempotency_key="actual-1", from_commitment_entry_id=entry_id,
        )  # fmt: skip
    assert sql(
        db,
        "SELECT reason FROM ledger_entry "
        "WHERE entry_type='release' AND source_type='purchase_order'",
    ) == [(CONSUMED_BY_ACTUAL,)]
    assert pos(db).get(po.id).released_amount is None  # spend is not a release
    cancelled = cancel_po(db, pos(db).get(po.id))
    assert cancelled.released_amount == format(amount - half, "f")


# -- reduce ----------------------------------------------------------------------------------


def reduce_po(db, po, qty="3", version=None, connection=None, **edit):
    if version is None:
        version = sql(db, "SELECT version FROM purchase_order WHERE id=%s", (po.id,))[0][0]
    body = PoReduceCreate(
        reason="vendor short", lines=[PoChangeLineEdit(line_no=1, quantity=qty, **edit)]
    )
    with correlation_context("reduce"):
        return reduce(connection or db[0], Scope(db[1]), po.id, db[3], f'"{version}"', body)


def test_reduce_opens_one_change_that_releases_only_when_approved(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    publish_change(db)
    rows = entries(db)
    change = reduce_po(db, po)
    assert change.status == "approval_pending" and change.material
    assert entries(db) == rows and remaining(db) > 0
    decide_change(db, change)
    assert len(releases(db, rows)) == 1
    assert sql(db, "SELECT ordered_qty FROM award_line") == [(Decimal(3),)]
    clean(db)


def test_reduce_refuses_a_rise_a_price_edit_and_a_stale_version(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    publish_change(db)
    for call in (
        lambda: reduce_po(db, po, qty="5"),
        lambda: reduce_po(db, po, unit_price="1"),
    ):
        with pytest.raises(ProblemError) as error:
            call()
        assert error.value.code == ErrorCode.VALIDATION_FAILED
    assert refused(lambda: reduce_po(db, po, version=9)).checks["problem"] == "stale_version"
    assert sql(db, "SELECT count(*) FROM po_change") == [(0,)]


# -- routes ----------------------------------------------------------------------------------


def test_cancel_and_reduce_routes_authorize_and_need_if_match(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["purchase_order.view"])
            payload = {"reason": "x"}
            assert (await send(browser, "POST", f"/{po.id}/cancel", payload)).status_code == 403
            regrant(db, ["purchase_order.view", "purchase_order.amend"])
            missing = await send(browser, "POST", f"/{po.id}/cancel", payload)
            assert missing.status_code == 400
            stale = await send(browser, "POST", f"/{po.id}/cancel", payload, {"If-Match": '"9"'})
            assert stale.status_code == 409
            lines = {"reason": "x", "lines": [{"line_no": 1, "quantity": "9"}]}
            rise = await send(browser, "POST", f"/{po.id}/reduce", lines, {"If-Match": '"2"'})
            assert rise.status_code == 422, rise.text
            done = await send(
                browser, "POST", f"/{po.id}/cancel", payload,
                {"If-Match": '"2"', "Idempotency-Key": "k1"},
            )  # fmt: skip
            assert done.status_code == 200 and done.headers["ETag"] == '"3"', done.text
            assert done.json()["status"] == "cancelled" and done.json()["released_amount"]
            replay = await send(
                browser, "POST", f"/{po.id}/cancel", payload,
                {"If-Match": '"2"', "Idempotency-Key": "k1"},
            )  # fmt: skip
            assert replay.status_code == 200 and replay.json() == done.json()

    asyncio.run(scenario())


# -- concurrency (real Postgres) -------------------------------------------------------------


def test_cancel_and_issue_of_one_approved_po_have_one_winner(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    version = sql(db, "SELECT version FROM purchase_order")[0][0]
    body = PoCancelCreate(reason="x")

    def work(conn, n):
        with correlation_context("cancel-issue"):
            if n == 0:
                return issue(conn, Scope(db[1]), po.id, db[3], f'"{version}"').status
            return cancel(conn, Scope(db[1]), po.id, db[3], f'"{version}"', body).status

    outcome = _race(db, work)
    status = sql(db, "SELECT status FROM purchase_order")[0][0]
    if status == "cancelled":  # cancel won: issue is refused with nothing posted
        assert outcome in (["cancelled", "invalid_transition"], ["cancelled", "stale_version"])
        assert remaining(db) == 0 and sql(
            db, "SELECT count(*) FROM ledger_entry WHERE entry_type='commitment'"
        ) == [(0,)]
    else:  # issue won first, then cancel ran on the issued order or lost on the version
        assert status == "issued" and "issued" in outcome
    assert counters(db)[0][0] in (Decimal(0), Decimal(4))
    clean(db)


def test_cancel_and_a_change_order_apply_on_one_award_do_not_deadlock(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    with correlation_context("change"):
        changes(db).create(po.id, PoChangeCreate())
        change = changes(db).update(po.id, 1, PoChangePatch(terms="Net 60"), '"1"')
    body = PoCancelCreate(reason="x")

    def work(conn, n):
        with correlation_context("cancel-change"):
            if n == 0:
                return changes(db, conn).apply(po.id, 1, f'"{change.version}"').status
            return cancel(conn, Scope(db[1]), po.id, db[3], '"2"', body).status

    outcome = _race(db, work)
    assert len(outcome) == 2  # both finished: no deadlock
    clean(db)


def test_cancel_and_a_reduce_that_auto_approves_on_one_po_do_not_deadlock(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    workflow = definition(db, document_type="po_change")
    draft(db, workflow, {"nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
                         "edges": [{"from": "start", "to": "end"}]})  # fmt: skip
    svc(db).publish(workflow.id, 1, True)
    body = PoCancelCreate(reason="x")
    request = PoReduceCreate(reason="r", lines=[PoChangeLineEdit(line_no=1, quantity="3")])

    def work(conn, n):
        with correlation_context("cancel-reduce"):
            if n == 0:
                return reduce(conn, Scope(db[1]), po.id, db[3], '"2"', request).status
            return cancel(conn, Scope(db[1]), po.id, db[3], '"2"', body).status

    outcome = _race(db, work)
    assert len(outcome) == 2
    assert sql(
        db,
        "SELECT status FROM purchase_order ORDER BY revision DESC LIMIT 1",
    )[0][0] in ("cancelled", "issued")
    clean(db)


def test_supersede_probes_for_a_po_before_it_waits_on_any_lock(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    create(db, rfq, a, award_id, "2")
    with psycopg.connect(connection_url(db[0]), autocommit=True) as holder:
        holder.execute("BEGIN")
        holder.execute("SELECT set_config('app.org_id', %s, true)", (str(db[1]),))
        holder.execute("SELECT id FROM award WHERE id = %s FOR UPDATE", (award_id,))
        db[0].execute("SET lock_timeout = '1s'")
        try:
            error = refused(lambda: supersede(db, rfq))
        finally:
            db[0].execute("RESET lock_timeout")
            holder.execute("ROLLBACK")
    assert error.checks["problem"] == "po_exists"


def test_cancel_then_issue_and_issue_then_cancel_each_have_one_winner(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    first, second = create(db, rfq, a, award_id, "2"), create(db, rfq, a, award_id, "3")
    for po in (first, second):
        approve(db, po)
    cancel_po(db, first)  # cancel first: the issue that follows is refused
    assert refused(lambda: run(db, first, version="1")).checks["problem"] in (
        "invalid_transition",
        "stale_version",
    )
    run(db, second)  # issue first: the cancel that follows releases it
    assert cancel_po(db, pos(db).get(second.id)).status == "cancelled"
    assert remaining(db) == 0
    clean(db)


def test_cancel_with_requisition_lines_and_an_award_approval_lock_sequence_do_not_deadlock(
    lifecycle_db,
):
    """An award approval takes the balances, then the requisition (`award_funding(lock=True)`).
    A second award cannot be approved on an `open_for_purchase` requisition, so this races the
    approval's lock sequence itself against a cancel that returns the funds to main."""
    from flo.modules.requisitions.service import award_funding

    db, _ = lifecycle_db
    po, row = issued(db)
    scope, version = Scope(db[1]), po.version
    body = PoCancelCreate(reason="x", cancel_requisition_lines=True)

    def work(conn, n):
        with correlation_context("cancel-award"):
            if n == 0:
                with tenant_transaction(conn, scope):
                    award_funding(conn, scope, [row.id], lock=True)
                    reservation_entry(conn, scope, row.id, lock=True)
                return "locked"
            return cancel(conn, scope, po.id, db[3], f'"{version}"', body).status

    assert _race(db, work) == ["cancelled", "locked"]
    clean(db)
