"""Lines with no priced quote: one derived rule, auto-cancel at award approval (E12-S24)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier

import psycopg
import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.money import quantize_down
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import OutcomeContext
from flo.modules.requisitions import sourcing as req_sourcing
from flo.modules.requisitions.lines import LineService
from flo.modules.requisitions.schemas import LineSourcing
from flo.modules.requisitions.service import source_lines
from flo.modules.sourcing.awards import AwardCreate, AwardPatch, AwardService, RequisitionCheck
from flo.modules.sourcing.comparison import ComparisonRead
from flo.modules.sourcing.schemas import BidLineIn, BidSubmit, ReasonBody, RfqContentPatch
from flo.modules.sourcing.service import no_priced_quote
from tests.requisitions.test_approval_effects import approve as approve_requisition
from tests.requisitions.test_approval_effects import drift, linked, reviewer, state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import connection_url, project, unit
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_award import (
    awards,
    decide_award,
    ledger,
    prepare,
    problem,
    publish_workflow,
    requisition,
    rfq_line_of,
    sub_project,
    submit,
)
from tests.sourcing.test_award import (
    pick as pick_one,
)
from tests.sourcing.test_bids import complete, edit, evidence, key
from tests.sourcing.test_bids import submit as submit_bid
from tests.sourcing.test_comparison import deadline_ago, quoted
from tests.sourcing.test_rfq import draft_rfq, entry, refused, sql
from tests.sourcing.test_rfq_content import invite, vendor
from tests.sourcing.test_rfq_lifecycle import life


def quote_one_line(db, rfq, vendor_id, price):
    """The vendor quotes the first RFQ line and keys every other line as not quoted."""
    bid = key(db, rfq, vendor_id, "USD")
    first, *rest = line_ids(db, rfq)
    rows = [BidLineIn(rfq_line_id=first, quoted=True, unit_price=price, tax="0")]
    rows += [BidLineIn(rfq_line_id=i, quoted=False) for i in rest]
    bid = edit(db, rfq, bid, lines=rows, freight="0", evidence_attachment_id=evidence(db, bid.id))
    return submit_bid(db, rfq, bid)


def close(db, rfq):
    deadline_ago(db, rfq, 1)
    sql(db, "UPDATE rfq SET status='under_evaluation' WHERE id=%s RETURNING 1", (rfq.id,))


def requisition_in(db, bu, amount, qty):
    """`requisition`, but in the given unit: one RFQ carries lines of one unit only."""
    # a unit allows one open ship-to address per period; end the earlier requisition's (it is
    # approved already, so nothing re-reads it)
    sql(
        db,
        "UPDATE org_address SET effective_from=current_date-90, effective_to=current_date-31 "
        "WHERE unit_id=%s RETURNING 1",
        (bu.id,),
    ) if sql(db, "SELECT 1 FROM org_address WHERE unit_id=%s", (bu.id,)) else None
    row = linked(db, project(db, bu), price=amount, available=str(int(amount) * 3))
    approve_requisition(db, row)
    line = sql(db, "SELECT id FROM requisition_line WHERE requisition_id=%s", (row.id,))[0][0]
    sql(db, "UPDATE requisition_line SET quantity=%s WHERE id=%s RETURNING 1", (qty, line))
    return state(db, row.id), line


def rfq_over(db, entries, names=("A", "B"), *, quote_first=True, prices=("80", "90"), closed=True):
    """A published RFQ over `entries`, evaluation open. Vendors quote only the first line, so every
    other line has no priced quote. `quote_first=False`: nobody quotes anything."""
    rfq = draft_rfq(db, *entries)
    life(db).update_content(
        rfq.id,
        RfqContentPatch(
            specifications="Spec",
            delivery_location="Dock",
            delivery_terms="DAP",
            terms="Net 30",
            deadline_at=datetime.now(UTC) + timedelta(days=3),
        ),
        str(rfq.edit_revision),
    )
    for name in names:
        invite(db, rfq, vendor(db, name))
    with correlation_context("publish"):
        rfq = life(db).publish(rfq.id)
    vids = [i.vendor_id for i in rfq.invitees]
    if quote_first:
        for vid, price in zip(vids, prices, strict=False):
            quote_one_line(db, rfq, vid, price)
    if closed:
        close(db, rfq)
    return rfq, vids


def two_requisitions(db):
    """R1 (line L1, 10 units, 1,000) priced by A and B; R2 (line L2, 4 units, 400) priced by
    nobody. Returns the RFQ, R1, R2, the two requisition lines and the vendor ids."""
    publish_workflow(db)
    bu = unit(db, "SRC")
    r1, l1 = requisition_in(db, bu, "1000", "10")
    r2, l2 = requisition_in(db, bu, "400", "4")
    rfq, vids = rfq_over(db, [entry(r1, l1, "10"), entry(r2, l2, "4")])
    return rfq, r1, r2, l1, l2, vids


def unpriced_ids(db, rfq):
    return no_priced_quote(db[0], Scope(db[1]), rfq.id)


def line_ids(db, rfq):
    return [
        r[0] for r in sql(db, "SELECT id FROM rfq_line WHERE rfq_id=%s ORDER BY line_no", (rfq.id,))
    ]


def line_qty(db, line):
    query = "SELECT sourced_qty, cancelled_qty, awarded_qty FROM requisition_line WHERE id=%s"
    return sql(db, query, (line,))[0]


# -- the one rule --------------------------------------------------------------------------


def test_quantize_down_never_rounds_up():
    assert quantize_down(Decimal("0.0066"), "USD") == Decimal("0.00")
    assert quantize_down(Decimal("33.339"), "USD") == Decimal("33.33")
    assert quantize_down(Decimal("66.669"), "USD") == Decimal("66.66")
    assert quantize_down(Decimal("100.00"), "USD") == Decimal("100.00")
    assert quantize_down(Decimal("5.9999"), "KWD") == Decimal("5.999")  # three decimals


def test_the_helper_equals_the_comparison_uncovered_lines_and_both_screens_show_the_signal(
    lifecycle_db,
):
    db, _ = lifecycle_db
    rfq, _, _, l1, l2, _ = two_requisitions(db)
    cmp = awards(db).comparison(rfq.id)
    assert isinstance(cmp, ComparisonRead)
    missing = unpriced_ids(db, rfq)
    assert missing == set(cmp.lowest_cost_supplier.uncovered_lines) and len(missing) == 1
    assert [ln.no_priced_quote for ln in cmp.lines] == [False, True]
    award = awards(db).draft(rfq.id, AwardCreate(basis="manual"))
    assert [(u.line_no, u.quantity) for u in award.unpriced_lines] == [(2, "4.0000")]
    # the unpriced share (400) is in the checks beside the priced requisition's own
    # a manual draft has no award rows yet: only the all-unpriced requisition is in the checks
    assert [(c.award, c.unpriced, c.release) for c in award.checks] == [("0", "400.00", "400.00")]


def test_a_vendor_revision_that_quotes_the_line_removes_it_on_the_next_read(lifecycle_db):
    db, _ = lifecycle_db
    publish_workflow(db)
    row, line = requisition(db, "400", "4")
    rfq, [vid, *_] = rfq_over(
        db, [entry(row, line, "4")], names=("A",), quote_first=False, closed=False
    )
    assert unpriced_ids(db, rfq) == set(line_ids(db, rfq))  # no stored flag: derived
    quoted(db, rfq, vid, "USD", price="10")  # before the deadline, a revision may quote it
    assert unpriced_ids(db, rfq) == set()


def test_an_rfq_nobody_priced_cannot_be_awarded(lifecycle_db):
    db, _ = lifecycle_db
    publish_workflow(db)
    row, line = requisition(db, "400", "4")
    rfq, _ = rfq_over(db, [entry(row, line, "4")], names=("A",), quote_first=False)
    with correlation_context("award"):
        award = awards(db).draft(rfq.id, AwardCreate(basis="manual"))
    assert award.lines == [] and len(award.unpriced_lines) == 1
    awards(db).patch(
        rfq.id,
        AwardPatch(rationale="Why"),
        str(award.version),
    )
    error = refused(lambda: submit(db, rfq), ErrorCode.VALIDATION_FAILED)
    assert [e.field for e in error.errors] == ["lines"] and "at least one" in error.errors[
        0
    ].message


# -- approval, retry, supersede --------------------------------------------------------------


def test_approval_cancels_the_unpriced_slice_releases_its_share_and_completes_the_requisition(
    lifecycle_db, monkeypatch
):
    db, _ = lifecycle_db
    rfq, r1, r2, l1, l2, [a, _b] = two_requisitions(db)
    notified = []
    monkeypatch.setattr(
        req_sourcing,
        "notify_requester_lines_cancelled",
        lambda conn, scope, requisition_id, ids: notified.append((requisition_id, list(ids))),
    )
    base = ledger(db)
    prepare(db, rfq, pick_one(db, rfq, a, "10"))
    award = submit(db, rfq)
    checks = {c.requisition: c for c in awards(db).read(rfq.id).checks}
    assert checks[r2.number].unpriced == "400.00" and checks[r2.number].release == "400.00"
    assert checks[r1.number].unpriced == "0.00" and checks[r1.number].release == "200.00"
    decide_award(db, award.id)
    # R2: the slice moved from sourced to cancelled, nothing more; open quantity is unchanged
    assert line_qty(db, l2) == (Decimal(0), Decimal(4), Decimal(0))
    assert state(db, r2.id).status == "completed"
    assert state(db, r2.id).reservation.remaining == "0.0000"
    assert state(db, r1.id).status == "open_for_purchase"
    entries = ledger(db)[len(base) :]
    keys = [e[3] for e in entries]
    # one posting per requisition: the transfer's two legs under the one key
    assert sum(k.startswith(f"award:{award.id}:return:{r2.id}") for k in keys) == 2
    assert (
        sum(e[2] for e in entries if e[0] == "transfer" and e[2] > 0 and e[1] == r2.project_id)
        == 400
    )
    assert notified == [(r2.id, [l2])]  # once per requisition with cancelled lines
    audits = sql(
        db,
        "SELECT after FROM audit_log WHERE action='requisition.cancel_unpriced.update' "
        "AND target_id=%s",
        (r2.id,),
    )
    [(after,)] = audits
    assert after[str(l2)]["reason"] == "no_priced_quote" and after[str(l2)]["award_id"] == str(
        award.id
    )
    assert drift(db) == []
    # no approved award has a row on a no-priced-quote RFQ line
    rows = sql(db, "SELECT rfq_line_id FROM award_line WHERE award_id=%s", (award.id,))
    assert {r[0] for r in rows}.isdisjoint(unpriced_ids(db, rfq))
    # the requisition shows the reason, on the cancelled slice only
    lines = LineService(db[0], Scope(db[1])).list_lines(r2.id).rows
    assert [(c.rfq_number, c.quantity) for c in lines[0].unpriced_cancellations] == [
        (sql(db, "SELECT number FROM rfq WHERE id=%s", (rfq.id,))[0][0], "4.0000")
    ]
    assert LineService(db[0], Scope(db[1])).list_lines(r1.id).rows[0].unpriced_cancellations == []
    # a cancel is final: re-sourcing the slice is refused by the open-quantity guard
    with pytest.raises(ProblemError) as error:
        source_lines(
            db[0],
            Scope(db[1]),
            [
                LineSourcing(
                    requisition_id=r2.id,
                    requisition_line_id=l2,
                    requisition_version=sql(
                        db, "SELECT version FROM requisition WHERE id=%s", (r2.id,)
                    )[0][0],
                    delta=Decimal(1),
                )
            ],
            db[3],
        )
    # refused (the completed requisition is not sourceable, and the open quantity is 0 anyway)
    assert error.value.checks["problem"] in ("over_open_quantity", "stale_version")
    open_qty = "SELECT quantity-cancelled_qty-sourced_qty FROM requisition_line WHERE id=%s"
    assert sql(db, open_qty, (l2,)) == [(Decimal(0),)]


def test_supersede_restores_quantities_and_funds_and_reopens_the_completed_requisition(
    lifecycle_db,
):
    db, _ = lifecycle_db
    rfq, r1, r2, l1, l2, [a, _b] = two_requisitions(db)
    prepare(db, rfq, pick_one(db, rfq, a, "10"))
    award = submit(db, rfq)
    decide_award(db, award.id)
    assert state(db, r2.id).status == "completed"
    with correlation_context("supersede"):
        awards(db).supersede(rfq.id, ReasonBody(reason="Wrong vendor"))
    assert line_qty(db, l2) == (Decimal(4), Decimal(0), Decimal(0))
    assert line_qty(db, l1)[2] == Decimal(0)
    assert state(db, r2.id).status == "sourced" and state(db, r1.id).status == "sourced"
    assert state(db, r2.id).reservation.remaining == "400.0000"  # funds back, exactly
    assert state(db, r1.id).reservation.remaining == "1000.0000"
    assert drift(db) == []
    reopened = sql(
        db,
        "SELECT count(*) FROM audit_log WHERE action='requisition.reopen_for_purchase' "
        "AND target_id=%s",
        (r2.id,),
    )
    assert reopened == [(1,)]
    assert sql(db, "SELECT status FROM award WHERE id=%s", (award.id,)) == [("superseded",)]


def test_a_retried_approval_and_two_concurrent_ones_cancel_and_post_once(lifecycle_db):
    db, _ = lifecycle_db
    rfq, r1, r2, l1, l2, [a, _b] = two_requisitions(db)
    prepare(db, rfq, pick_one(db, rfq, a, "10"))
    award = submit(db, rfq)
    actor = reviewer(db)
    barrier = Barrier(2)

    def settle(_):
        with psycopg.connect(connection_url(db[0]), autocommit=True) as conn:
            context = OutcomeContext(actor, None, None, lambda *_: True)
            barrier.wait(timeout=10)
            try:
                with correlation_context("race"):
                    AwardService(conn, Scope(db[1]), actor).settle(award.id, "approved", context)
                return "ok"
            except ProblemError as exc:
                return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        outcomes = sorted(pool.map(settle, range(2)))
    assert outcomes in (["ok", "over_awarded_quantity"], ["invalid_transition", "ok"])
    assert line_qty(db, l2) == (Decimal(0), Decimal(4), Decimal(0))
    returns = [e[3] for e in ledger(db) if str(e[3]).startswith(f"award:{award.id}:return")]
    assert len(returns) == len(set(returns)) == 4  # R1 and R2, two legs each, posted once
    assert drift(db) == []


# -- the guards -------------------------------------------------------------------------------


def test_the_unpriced_share_never_offsets_scope():
    over = RequisitionCheck(
        requisition_id="00000000-0000-0000-0000-000000000001",
        requisition="REQ-1",
        scope="100.00",
        funds="500.00",
        award="105.00",
        over_scope_by="5.00",
        over_funds_by="0.00",
        release="400.00",
        main_project="P",
        unpriced="400.00",
    )
    with pytest.raises(ProblemError) as error:
        AwardService.assert_within([over])
    assert error.value.checks["problem"] == "over_scope"


def test_award_submit_refuses_an_all_unpriced_requisition_with_no_sub_project(lifecycle_db):
    db, _ = lifecycle_db
    rfq, r1, r2, _l1, _l2, [a, _b] = two_requisitions(db)
    prepare(db, rfq, pick_one(db, rfq, a, "10"))
    sql(
        db,
        "UPDATE project SET generated_by_requisition_id=NULL WHERE id=%s RETURNING 1",
        (sub_project(db, r2.id),),
    )
    base = ledger(db)
    with correlation_context("award"):
        error = refused(lambda: submit(db, rfq), ErrorCode.VALIDATION_FAILED)
    assert error.checks["problem"] == "standalone_not_supported"
    assert error.checks["requisition"] == r2.number
    assert awards(db).read(rfq.id).status == "draft" and ledger(db) == base


def test_award_submit_revalidates_saved_rows_against_the_current_bids(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, _, _, [a, b] = two_requisitions(db)
    prepare(db, rfq, pick_one(db, rfq, a, "10"))
    bid_b = sql(db, "SELECT id FROM bid WHERE vendor_id=%s AND rfq_id=%s", (b, rfq.id))[0][0]
    # the saved row now points at a bid that is not vendor A's current bid
    sql(
        db,
        "UPDATE award_line SET bid_id=%s WHERE rfq_line_id=%s RETURNING 1",
        (bid_b, rfq_line_of(db, rfq)),
    )
    error = refused(lambda: submit(db, rfq), ErrorCode.VALIDATION_FAILED)
    assert error.checks["problem"] == "cannot_submit"
    assert "revised this bid" in error.errors[0].message
    assert awards(db).read(rfq.id).status == "draft"


def test_bids_cannot_change_while_the_award_awaits_approval_and_can_again_after_withdraw(
    lifecycle_db,
):
    db, _ = lifecycle_db
    publish_workflow(db)
    row, line = requisition(db, "1000", "10")
    rfq, [a, b, c] = rfq_over(
        db, [entry(row, line, "10")], names=("A", "B", "C"), prices=("80", "90"), closed=False
    )
    # C's draft bid was sent before the deadline, so it is on time whenever it is submitted
    draft = key(db, rfq, c, "USD")
    sql(db, "UPDATE bid SET sent_at=now()-interval '2 days' WHERE id=%s RETURNING 1", (draft.id,))
    draft = complete(db, rfq, draft)
    close(db, rfq)
    prepare(db, rfq, pick_one(db, rfq, a, "10"))
    award = submit(db, rfq)
    assert award.status == "approval_pending"
    with correlation_context("bid"):
        assert problem(lambda: submit_bid(db, rfq, draft)) == "award_pending"
        awards(db).withdraw(rfq.id)
        submitted = submit_bid(db, rfq, draft)
    assert submitted.status == "submitted"
    # and a submitted bid is in the snapshot of the next submit: the unpriced set is re-read
    assert unpriced_ids(db, rfq) == set()
    assert BidSubmit().accept_late_reason is None


# -- rounding ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "qty", "slices", "expected"),
    [
        ("0.02", "3", ["1"], "0.00"),  # three slices of 0.02 never release more than 0.02
        ("100", "3", ["1"], "33.33"),
        ("100", "3", ["2"], "66.66"),
        ("100", "3", ["3"], "100.00"),  # the whole line releases exactly its amount
    ],
)
def test_each_unpriced_share_rounds_down(lifecycle_db, amount, qty, slices, expected):
    db, _ = lifecycle_db
    publish_workflow(db)
    row, line = requisition(db, "1", qty)
    sql(db, "UPDATE requisition_line SET amount=%s WHERE id=%s RETURNING 1", (amount, line))
    rfq, _ = rfq_over(db, [entry(row, line, slices[0])], names=("A",), quote_first=False)
    [share] = [s.share for s in awards(db)._slices(rfq.id, "USD")]
    assert share == Decimal(expected)
