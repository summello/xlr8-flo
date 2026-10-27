"""Cancel or supersede an RFQ or award, history kept, budget reconciled (E12-S09, SRC-017)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from flo.modules.requisitions.funding import move_funds
from flo.modules.requisitions.lifecycle import read_row
from flo.modules.requisitions.models import RequisitionRepository
from flo.modules.sourcing.awards import AwardCreate
from flo.modules.sourcing.schemas import ReasonBody
from flo.modules.sourcing.service import SUPERSEDE_GUARDS, register_supersede_guard
from tests.isolation.test_real_sessions import client, login
from tests.requisitions.test_approval_effects import drift, state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import connection_url
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_award import (
    awards,
    decide_award,
    ledger,
    pick,
    prepare,
    remaining,
    scene,
    sub_project,
    submit,
)
from tests.sourcing.test_rfq import refused, send, sql
from tests.sourcing.test_rfq_lifecycle import life
from tests.vendors.test_vendors import regrant

WHY = ReasonBody(reason="Changed our mind")
KINDS = ("reservation", "release", "transfer")


def count(db):
    return sql(db, "SELECT count(*) FROM ledger_entry")[0][0]


def cancel_rfq(db, rfq):
    with correlation_context("cancel"):
        return life(db).cancel(rfq.id, WHY)


def approved_award(db, rfq, vendor_id, qty="10"):
    prepare(db, rfq, pick(db, rfq, vendor_id, qty))
    award = submit(db, rfq)
    decide_award(db, award.id)
    return awards(db).read(rfq.id)


def supersede(db, rfq, connection=None):
    with correlation_context("supersede"):
        return awards(db, connection=connection).supersede(rfq.id, WHY)


def rfq_status(db, rfq):
    return sql(db, "SELECT status FROM rfq WHERE id=%s", (rfq.id,))[0][0]


def awarded_qty(db):
    return sql(db, "SELECT awarded_qty, sourced_qty FROM requisition_line")


# -- cancelling an RFQ -----------------------------------------------------------------------


@pytest.mark.parametrize("status", ["draft", "published", "closed", "under_evaluation"])
def test_cancel_rfq_returns_the_quantity_posts_nothing_and_keeps_history(lifecycle_db, status):
    db, _ = lifecycle_db
    rfq, row, _, _ = scene(db, workflow=False)
    sql(db, "UPDATE rfq SET status=%s WHERE id=%s RETURNING 1", (status, rfq.id))
    before, held = count(db), remaining(db, row.id)
    assert state(db, row.id).status == "sourced"
    cancelled = cancel_rfq(db, rfq)
    assert cancelled.status == "cancelled" and rfq_status(db, rfq) == "cancelled"
    assert awarded_qty(db) == [(Decimal(0), Decimal(0))]  # quantity is open again
    assert state(db, row.id).status == "sourcing_in_progress"
    assert count(db) == before and remaining(db, row.id) == held  # decision 212: no money moves
    assert life(db).get(rfq.id).status == "cancelled"  # still readable (WF-005)
    assert len(life(db).versions(rfq.id)) == 1  # the published version stays
    assert sql(db, "SELECT count(*) FROM audit_log WHERE action='rfq.cancel'") == [(1,)]
    told = sql(
        db,
        "SELECT count(*) FROM outbox WHERE idempotency_key LIKE %s",
        (f"rfq:{rfq.id}:cancelled:%",),
    )
    assert told[0][0] == (
        0 if status == "draft" else 2
    )  # one email per invitee contact once published
    error = refused(lambda: cancel_rfq(db, rfq))  # cancelled is final
    assert error.checks["problem"] == "invalid_transition"


def test_cancel_rfq_is_refused_once_awarded(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db)
    approved_award(db, rfq, a)
    assert rfq_status(db, rfq) == "awarded"
    before = count(db)
    assert refused(lambda: cancel_rfq(db, rfq)).checks["problem"] == "invalid_transition"
    assert rfq_status(db, rfq) == "awarded" and count(db) == before


def test_cancel_rfq_needs_a_reason():
    with pytest.raises(ValueError):
        ReasonBody(reason="")
    with pytest.raises(ValueError):
        ReasonBody()  # type: ignore[call-arg]


@pytest.mark.parametrize("pending", [False, True])
def test_cancel_rfq_with_a_live_award_is_refused_and_writes_nothing(lifecycle_db, pending):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db)
    award = prepare(db, rfq, pick(db, rfq, a, "10"))
    if pending:
        award = submit(db, rfq)
    snapshot = (
        award.status,
        sql(db, "SELECT count(*) FROM approval_instance")[0][0],
        awarded_qty(db),
        sql(db, "SELECT count(*) FROM outbox")[0][0],
        count(db),
        sql(db, "SELECT status, edit_revision FROM rfq WHERE id=%s", (rfq.id,)),
    )

    def now():
        return (
            awards(db).read(rfq.id).status,
            sql(db, "SELECT count(*) FROM approval_instance")[0][0],
            awarded_qty(db),
            sql(db, "SELECT count(*) FROM outbox")[0][0],
            count(db),
            sql(db, "SELECT status, edit_revision FROM rfq WHERE id=%s", (rfq.id,)),
        )

    assert refused(lambda: cancel_rfq(db, rfq)).checks["problem"] == "award_in_progress"
    assert now() == snapshot
    if pending:
        with correlation_context("withdraw"):
            awards(db).withdraw(rfq.id)
    with correlation_context("award-cancel"):
        assert awards(db).cancel(rfq.id, WHY).status == "cancelled"
    assert cancel_rfq(db, rfq).status == "cancelled"
    assert state(db, row.id).status == "sourcing_in_progress"


# -- cancelling an award ---------------------------------------------------------------------


def test_cancel_award_only_from_draft(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db)
    prepare(db, rfq, pick(db, rfq, a, "10"))
    submit(db, rfq)
    before = count(db)
    error = refused(lambda: awards(db).cancel(rfq.id, WHY))
    assert error.checks["problem"] == "invalid_transition"
    assert awards(db).read(rfq.id).status == "approval_pending"
    with correlation_context("withdraw"):
        awards(db).withdraw(rfq.id)
    with correlation_context("cancel"):
        done = awards(db).cancel(rfq.id, WHY)
    assert done.status == "cancelled" and count(db) == before  # nothing was posted
    assert refused(lambda: awards(db).cancel(rfq.id, WHY)).checks["problem"] == (
        "invalid_transition"
    )
    assert [x.status for x in awards(db).awards(rfq.id)] == ["cancelled"]


# -- approving after the RFQ left under_evaluation ---------------------------------------------


def test_approving_an_award_on_a_cancelled_rfq_is_refused_and_posts_nothing(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, prices=("90", "99"))
    prepare(db, rfq, pick(db, rfq, a, "10"))
    award = submit(db, rfq)
    sql(db, "UPDATE rfq SET status='cancelled' WHERE id=%s RETURNING 1", (rfq.id,))
    before, held, quantities = count(db), remaining(db, row.id), awarded_qty(db)
    error = refused(lambda: decide_award(db, award.id))
    assert error.checks["problem"] == "invalid_transition"
    assert awards(db).read(rfq.id).status == "approval_pending"
    assert rfq_status(db, rfq) == "cancelled"
    assert count(db) == before and remaining(db, row.id) == held
    assert awarded_qty(db) == quantities == [(Decimal(0), Decimal(10))]


# -- superseding an approved award ---------------------------------------------------------------


def test_supersede_reverses_the_release_and_the_return_exactly(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, amount="1000", qty="10", prices=("90", "99"))
    main, sub = row.project_id, sub_project(db, row.id)
    award = approved_award(db, rfq, a)  # 900 of 1,000: release 100, transfer 100 sub -> main
    assert remaining(db, row.id) == "900.0000"
    base = ledger(db, KINDS)
    first_entry = sql(db, "SELECT reservation_entry_id, funding_round FROM requisition")[0]
    done = supersede(db, rfq)
    assert done.id == award.id and done.status == "superseded"
    assert rfq_status(db, rfq) == "under_evaluation"
    new = ledger(db, KINDS)[len(base) :]
    transfers = [(e[1], e[2]) for e in new if e[0] == "transfer"]
    assert sorted(transfers, key=lambda t: t[1]) == [
        (main, Decimal("-100.0000")),
        (sub, Decimal("100.0000")),
    ]  # one transfer of 100, main -> sub
    assert [e[0] for e in new].count("reservation") == 1
    assert remaining(db, row.id) == "1000.0000"
    entry_id, round_ = sql(db, "SELECT reservation_entry_id, funding_round FROM requisition")[0]
    assert entry_id != first_entry[0] and round_ == first_entry[1] + 1  # a new reservation entry
    assert awarded_qty(db) == [(Decimal(0), Decimal(10))]
    assert state(db, row.id).status == "sourced"
    assert drift(db) == []
    # exactly once (FIN-011): a second supersede is refused and posts nothing
    after = count(db)
    assert refused(lambda: supersede(db, rfq)).checks["problem"] == "invalid_transition"
    assert count(db) == after
    # a new draft award can follow, and both awards stay readable, newest first
    with correlation_context("award"):
        replacement = awards(db).draft(rfq.id, AwardCreate(basis="manual"))
    listed = awards(db).awards(rfq.id)
    assert [x.id for x in listed] == [replacement.id, award.id]
    assert [x.status for x in listed] == ["draft", "superseded"]
    assert listed[1].version > award.version and listed[1].lines[0].quantity == "10.0000"
    assert awards(db).read(rfq.id).id == replacement.id  # the live award first


def test_supersede_of_an_award_that_used_the_whole_scope_posts_no_money(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, amount="1000", qty="10", prices=("100", "110"))
    approved_award(db, rfq, a)
    assert remaining(db, row.id) == "1000.0000"
    before = count(db)
    assert supersede(db, rfq).status == "superseded"
    assert count(db) == before and remaining(db, row.id) == "1000.0000"
    assert awarded_qty(db) == [(Decimal(0), Decimal(10))]
    assert state(db, row.id).status == "sourced" and rfq_status(db, rfq) == "under_evaluation"


@pytest.mark.parametrize("negative_allowed", [False, True])
def test_supersede_is_refused_when_the_main_project_spent_the_funds(lifecycle_db, negative_allowed):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, amount="1000", qty="10", prices=("90", "99"))
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(
        "allow_negative_budget", SettingPut(value=negative_allowed)
    )
    approved_award(db, rfq, a)
    main = row.project_id
    # spend the main project's available balance down to 50, below the 100 that went back to it
    available = sql(
        db,
        "SELECT allocated-reserved-committed-actual FROM project_balance WHERE project_id=%s",
        (main,),
    )[0][0]
    sql(
        db,
        "UPDATE project_balance SET actual=actual+%s WHERE project_id=%s RETURNING 1",
        (available - 50, main),
    )
    before, quantities, held = count(db), awarded_qty(db), remaining(db, row.id)
    error = refused(lambda: supersede(db, rfq), ErrorCode.INSUFFICIENT_BUDGET)
    assert error.code == ErrorCode.INSUFFICIENT_BUDGET
    assert awards(db).read(rfq.id).status == "approved" and rfq_status(db, rfq) == "awarded"
    assert count(db) == before and awarded_qty(db) == quantities and remaining(db, row.id) == held
    floor = sql(
        db,
        "SELECT allocated-reserved-committed-actual FROM project_balance WHERE project_id=%s",
        (main,),
    )[0][0]
    assert floor == Decimal("50.0000")  # the main balance never went negative


def test_move_funds_keeps_the_org_setting_unless_told_otherwise(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, amount="1000", qty="10", prices=("90", "99"))
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(
        "allow_negative_budget", SettingPut(value=True)
    )
    approved_award(db, rfq, a)
    main, sub = row.project_id, sub_project(db, row.id)
    scope = Scope(db[1])
    with tenant_transaction(db[0], scope):
        full = read_row(RequisitionRepository(db[0], scope), row.id)
    over = Decimal("100000")

    def move(key, **extra):
        with tenant_transaction(db[0], scope):
            move_funds(
                db[0], scope, full, source_id=main, target_id=sub, amount=over, key=key,
                reason="Test move of funds", actor_id=db[3], **extra,
            )  # fmt: skip

    refused(lambda: move("explicit-false", allow_negative=False), ErrorCode.INSUFFICIENT_BUDGET)
    move("default")  # the setting is on: the default still posts negative
    assert sql(db, "SELECT allocated-reserved-committed-actual FROM project_balance "
               "WHERE project_id=%s", (main,))[0][0] < 0  # fmt: skip


def test_supersede_is_refused_once_a_po_exists(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db, prices=("90", "99"))
    approved_award(db, rfq, a)
    seen = []

    def probe(conn, scope, award_id):
        seen.append(award_id)
        return True

    register_supersede_guard(probe)
    register_supersede_guard(probe)  # idempotent
    try:
        assert SUPERSEDE_GUARDS.count(probe) == 1
        before = count(db)
        assert refused(lambda: supersede(db, rfq)).checks["problem"] == "po_exists"
        assert len(seen) == 1 and count(db) == before
        assert awards(db).read(rfq.id).status == "approved"
    finally:
        SUPERSEDE_GUARDS.remove(probe)
    assert supersede(db, rfq).status == "superseded"  # an empty list lets it proceed


def test_two_concurrent_supersedes_post_exactly_one_transfer_and_one_reservation(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, _, [a, _b] = scene(db, amount="1000", qty="10", prices=("90", "99"))
    approved_award(db, rfq, a)
    base = ledger(db, KINDS)
    barrier = Barrier(2)

    def attempt(_):
        with (
            psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
            correlation_context("supersede-race"),
        ):
            barrier.wait(timeout=10)
            try:
                awards(db, connection=conn).supersede(rfq.id, WHY)
                return "success"
            except ProblemError as exc:
                return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["invalid_transition", "success"]
    new = ledger(db, KINDS)[len(base) :]
    assert [e[0] for e in new].count("transfer") == 2  # one transfer: its two legs
    assert [e[0] for e in new].count("reservation") == 1
    assert remaining(db, row.id) == "1000.0000" and drift(db) == []
    assert awarded_qty(db) == [(Decimal(0), Decimal(10))]


# -- routes ---------------------------------------------------------------------------------


def test_the_new_routes_enforce_rfq_approve_and_return_the_documents(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, _b] = scene(db, prices=("90", "99"))
    approved_award(db, rfq, a)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            base = f"/rfqs/{rfq.id}"
            regrant(db, ["rfq.view", "rfq.edit"])
            for path in ("/cancel", "/award/cancel", "/award/supersede"):
                refused_ = await send(browser, "POST", base + path, {"reason": "x"})
                assert refused_.status_code == 403, (path, refused_.text)
            assert (await send(browser, "GET", base + "/awards")).status_code == 200
            regrant(db, [])
            assert (await send(browser, "GET", base + "/awards")).status_code == 403
            regrant(db, ["rfq.view", "rfq.approve"])
            blank = await send(browser, "POST", base + "/award/supersede", {"reason": ""})
            assert blank.status_code == 422, blank.text
            done = await send(browser, "POST", base + "/award/supersede", {"reason": "Why"})
            assert done.status_code == 200 and done.json()["status"] == "superseded", done.text
            listed = await send(browser, "GET", base + "/awards")
            assert [x["status"] for x in listed.json()] == ["superseded"]
            cancelled = await send(browser, "POST", base + "/cancel", {"reason": "Why"})
            assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"

    asyncio.run(scenario())
