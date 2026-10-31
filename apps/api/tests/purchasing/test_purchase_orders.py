"""PO drafts from approved award lines: guard, numbering, figures, authorization (E13-S01)."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier, Event
from uuid import UUID, uuid4

import psycopg

from flo.api.health import app
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.org.schemas import OrgAddressCreate
from flo.modules.org.service import NumberingService, OrgService
from flo.modules.purchasing.schemas import PoCreate, PoLineIn, PoLinePatch, PoPatch
from flo.modules.purchasing.service import PurchaseOrderService
from flo.modules.requisitions import amendments
from flo.modules.requisitions.schemas import AmendmentSubmit
from tests.isolation.test_real_sessions import client, headers, login
from tests.requisitions.test_amendments import amend, opened, propose
from tests.requisitions.test_approval_effects import state
from tests.requisitions.test_expired_codes import add_code, expire
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import connection_url
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_award import awards, requisition, rfq_line_of, scene, sub_project
from tests.sourcing.test_cancel_supersede import WHY, approved_award, supersede
from tests.sourcing.test_rfq import refused, sql
from tests.vendors.test_vendors import regrant


def pos(db, connection=None, actor=None):
    return PurchaseOrderService(connection or db[0], Scope(db[1]), actor or db[3], Settings())


def address(db, unit_id, kind="bill_to"):
    return OrgService(db[0], Scope(db[1]), db[3]).create_address(
        unit_id,
        OrgAddressCreate(
            kind=kind, line1="1 Main St", city="Springfield", country="US",
            effective_from=date(2020, 1, 1),
        ),
    )  # fmt: skip


def ready(db, qty="10", awarded="10"):
    """An approved award of `awarded` units to the first vendor, a Bill To on its unit."""
    rfq, row, _, [a, b] = scene(db, qty=qty, prices=("90", "99"))
    award = approved_award(db, rfq, a, awarded)
    unit_id = sql(db, "SELECT unit_id FROM rfq WHERE id=%s", (rfq.id,))[0][0]
    address(db, unit_id)
    return rfq, row, a, b, award.id, unit_id


def body(db, rfq, vendor, award_id, qty, **extra):
    line = PoLineIn(rfq_line_id=rfq_line_of(db, rfq), quantity=qty)
    return PoCreate(award_id=award_id, vendor_id=vendor, lines=[line], **extra)


def create(db, rfq, vendor, award_id, qty, connection=None, **extra):
    with correlation_context("po"):
        return pos(db, connection).create(body(db, rfq, vendor, award_id, qty, **extra), True)


def counters(db):
    return (
        sql(db, "SELECT ordered_qty FROM award_line")[0][0],
        sql(db, "SELECT ordered_qty FROM requisition_line")[0][0],
    )


# -- creation ----------------------------------------------------------------------------


def test_create_copies_the_requisition_numbers_and_holds_the_quantity(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, a, _, award_id, unit_id = ready(db)
    ledger = sql(db, "SELECT count(*) FROM ledger_entry")
    po = create(db, rfq, a, award_id, "4")
    assert po.status == "draft" and po.version == 1 and po.unit_id == unit_id
    assert po.number.startswith("PO") and po.currency == "USD"
    assert po.requisition_id == row.id and po.requester_id == row.requester_id
    assert po.needed_by == row.needed_by and po.bill_to["city"] == "Springfield"
    assert po.ship_to_address_id == row.delivery_address_id
    assert po.vendor_snapshot["trading_name"] and po.recipients is not None
    [line] = po.lines
    # D-M2-26: the line draws on the requisition's sub-project, never the main project
    assert line.project_id == sub_project(db, row.id) != row.project_id
    assert line.department_code == row.department_code
    assert line.ledger_account_code == row.ledger_account_code
    assert counters(db) == (Decimal(4), Decimal(4))
    assert sql(db, "SELECT count(*) FROM ledger_entry") == ledger  # nothing is posted
    [open_line] = pos(db).awarded(award_id, a).lines
    assert open_line.open_qty == "6.0000" and open_line.ordered_qty == "4.0000"
    assert sql(db, "SELECT count(*) FROM audit_log WHERE action='purchase_order.create'") == [(1,)]


def test_the_guard_refuses_over_open_and_a_delete_returns_the_quantity(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    first = create(db, rfq, a, award_id, "4")
    error = refused(lambda: create(db, rfq, a, award_id, "7"))
    assert error.checks["problem"] == "over_open_quantity"
    assert counters(db) == (Decimal(4), Decimal(4))  # nothing moved
    second = create(db, rfq, a, award_id, "6")  # exactly the open quantity
    assert counters(db) == (Decimal(10), Decimal(10))
    assert second.number != first.number
    with correlation_context("po"):
        pos(db).delete(first.id, '"1"')
    assert counters(db) == (Decimal(6), Decimal(6))  # only the deleted draft's quantity returns
    assert sql(db, "SELECT status FROM purchase_order WHERE id=%s", (first.id,)) == [("deleted",)]
    refused(lambda: pos(db).get(first.id), ErrorCode.NOT_FOUND)
    assert [r.id for r in pos(db).list_orders(_all(db), "all", None).rows] == [second.id]
    # a deleted number is kept and not reused
    third = create(db, rfq, a, award_id, "4")
    assert third.number not in (first.number, second.number)


def _all(db):
    from flo.kernel.authz import GrantedScopes

    return GrantedScopes(db[3], True, frozenset(), frozenset())


def test_patch_moves_both_counters_by_the_delta_and_checks_the_version(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, unit_id = ready(db)
    po = create(db, rfq, a, award_id, "4")
    org = OrgService(db[0], Scope(db[1]), db[3])
    ship = (org.list_addresses(unit_id, "ship_to", None) or [address(db, unit_id, "ship_to")])[0]

    def patch(version, **fields):
        with correlation_context("po"):
            return pos(db).update(po.id, PoPatch(**fields), version)

    up = patch('"1"', lines=[PoLinePatch(line_no=1, quantity="9")], terms="Net 45")
    assert up.lines[0].quantity == "9.0000" and up.terms == "Net 45" and up.version == 2
    assert counters(db) == (Decimal(9), Decimal(9))
    down = patch('"2"', lines=[PoLinePatch(line_no=1, quantity="2")], ship_to_address_id=ship.id)
    assert counters(db) == (Decimal(2), Decimal(2)) and down.ship_to_address_id == ship.id
    # an omitted `lines` leaves the quantity alone
    assert patch('"3"', terms=None).lines[0].quantity == "2.0000"
    assert counters(db) == (Decimal(2), Decimal(2))
    assert refused(lambda: patch('"1"', terms="x")).checks["problem"] == "stale_version"
    assert refused(lambda: patch(None, terms="x"), ErrorCode.BAD_REQUEST)
    error = refused(
        lambda: patch('"4"', lines=[PoLinePatch(line_no=7, quantity="1")]),
        ErrorCode.VALIDATION_FAILED,
    )
    assert error.checks["problem"] == "unknown_line"
    over = refused(lambda: patch('"4"', lines=[PoLinePatch(line_no=1, quantity="11")]))
    assert over.checks["problem"] == "over_open_quantity"
    foreign_ship = refused(
        lambda: patch('"4"', ship_to_address_id=uuid4()), ErrorCode.VALIDATION_FAILED
    )
    assert foreign_ship.checks["problem"] == "ship_to_invalid"
    assert counters(db) == (Decimal(2), Decimal(2))


def test_numbers_are_per_unit_and_a_replay_never_reuses_one(lifecycle_db):
    db, _ = lifecycle_db
    one, two = (
        OrgService(db[0], Scope(db[1]), db[3]).create_unit(
            __import__("flo.modules.org.schemas", fromlist=["OrgUnitCreate"]).OrgUnitCreate(
                code=code, name=code, kind="bu"
            )
        )
        for code in ("PN1", "PN2")
    )
    numbering = NumberingService(Settings(), Scope(db[1]))
    day = date.today()
    first = [numbering.allocate(one.id, "po", day) for _ in range(2)]
    other = numbering.allocate(two.id, "po", day)
    assert first[0] != first[1] and other == first[0]  # each unit counts from its own start


def test_ordered_value_is_derived_from_the_award_rate(lifecycle_db, monkeypatch):
    from tests.sourcing import test_award
    from tests.sourcing.test_comparison import deadline_ago, fx, rate_date_of

    db, _ = lifecycle_db
    real = test_award.quoted
    monkeypatch.setattr(
        test_award, "quoted", lambda db, rfq, vid, currency, **kw: real(db, rfq, vid, "EUR", **kw)
    )
    rfq, _, _, [a, _b] = scene(db, prices=("7.5", "9"))  # bids in EUR
    # five days back, so the award's rate day is always earlier than today's: one day back is the
    # same business day as today on a weekend, and the two rates collided on fx_rate_pkey
    deadline_ago(db, rfq, 5)
    fx(db, rate_date_of(db, rfq), USD="1.1")  # 1 EUR = 1.1 USD, frozen on the award
    award_id = approved_award(db, rfq, a, "5").id
    fx(db, datetime.now(UTC).date(), USD="1.2")  # today's rate moves on; the award's must not
    address(db, sql(db, "SELECT unit_id FROM rfq WHERE id=%s", (rfq.id,))[0][0])
    assert sql(db, "SELECT rates->'EUR'->>'rate' FROM award")[0][0].startswith("1.1")
    # vendor EUR at the award's rate; 2 x 7.5000 and 3 x 7.5000 (hand computed)
    first = create(db, rfq, a, award_id, "2")
    second = create(db, rfq, a, award_id, "3")
    assert first.currency == "EUR" and first.converted_currency == "USD"
    assert (first.lines[0].value, first.lines[0].value_converted) == ("15.00", "16.50")
    assert (first.total, first.total_converted) == ("15.00", "16.50")
    assert (second.total, second.total_converted) == ("22.50", "24.75")
    assert pos(db).awarded(award_id, a).lines[0].ordered_value == "37.50"  # 15.00 + 22.50
    assert sql(db, "SELECT count(*) FROM ledger_entry WHERE entry_type::text='commitment'") == [
        (0,)
    ]
    columns = sql(
        db,
        "SELECT column_name FROM information_schema.columns "
        "WHERE column_name LIKE '%%ordered_amount%%' OR column_name LIKE '%%ordered_value%%'",
    )
    assert columns == []  # no stored amount (decision 1)


def test_the_list_total_rounds_in_the_po_currency_like_the_detail(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "3")
    for currency, price, total, digits in (("KWD", "1.234", "3.702", 3), ("JPY", "1.5", "5", 0)):
        sql(db, "UPDATE purchase_order SET currency=%s RETURNING 1", (currency,))
        sql(db, "UPDATE po_line SET unit_price=%s RETURNING 1", (price,))
        sql(  # the stored landed line, rounded in the PO currency
            db,
            f"UPDATE po_line SET price=round(quantity*unit_price, {digits}), "
            f"line_total=round(quantity*unit_price, {digits}), tax=0, freight=0 RETURNING 1",
        )
        listed = pos(db).list_orders(_all(db), "all", None).rows[0]
        assert listed.total == pos(db).get(po.id).total == total, currency


# -- the award, the vendor, the bill to ---------------------------------------------------


def test_mixed_vendors_and_an_unapproved_award_are_refused_nothing_written(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, b, award_id, _ = ready(db)
    error = refused(lambda: create(db, rfq, b, award_id, "1"), ErrorCode.VALIDATION_FAILED)
    assert error.checks["problem"] == "mixed_vendors"
    assert counters(db) == (Decimal(0), Decimal(0))
    sql(db, "UPDATE award SET status='cancelled' RETURNING 1")
    assert refused(lambda: create(db, rfq, a, award_id, "1")).checks["problem"] == (
        "invalid_transition"
    )
    assert sql(db, "SELECT count(*) FROM purchase_order") == [(0,)]


def test_lines_from_two_requisitions_are_refused(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    other, other_line = requisition(db)
    first = rfq_line_of(db, rfq)
    second = uuid4()
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("CREATE TEMP TABLE t_rl AS SELECT * FROM rfq_line WHERE id=%s", (first,))
        db[0].execute(
            "UPDATE t_rl SET id=%s, line_no=2, requisition_id=%s, requisition_line_id=%s",
            (second, other.id, other_line),
        )
        db[0].execute("INSERT INTO rfq_line SELECT * FROM t_rl")
        db[0].execute(
            "CREATE TEMP TABLE t_bl AS SELECT * FROM bid_line WHERE rfq_line_id=%s", (first,)
        )
        db[0].execute("UPDATE t_bl SET rfq_line_id=%s", (second,))
        db[0].execute("ALTER TABLE bid_line DISABLE TRIGGER USER")  # a submitted bid is immutable
        db[0].execute("INSERT INTO bid_line SELECT * FROM t_bl")
        db[0].execute("ALTER TABLE bid_line ENABLE TRIGGER USER")
        db[0].execute(
            "CREATE TEMP TABLE t_al AS SELECT * FROM award_line WHERE rfq_line_id=%s", (first,)
        )
        db[0].execute("UPDATE t_al SET rfq_line_id=%s", (second,))
        db[0].execute("INSERT INTO award_line SELECT * FROM t_al")
    both = PoCreate(
        award_id=award_id,
        vendor_id=a,
        lines=[
            PoLineIn(rfq_line_id=first, quantity="1"),
            PoLineIn(rfq_line_id=second, quantity="1"),
        ],
    )
    error = refused(lambda: pos(db).create(both, True), ErrorCode.VALIDATION_FAILED)
    assert error.checks["problem"] == "mixed_requisitions"
    assert sql(db, "SELECT count(*) FROM purchase_order") == [(0,)]
    assert counters_all(db) == [Decimal(0), Decimal(0)]


def counters_all(db):
    return [r[0] for r in sql(db, "SELECT ordered_qty FROM award_line")]


def test_an_ineligible_vendor_needs_an_override_and_a_reason(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    sql(db, "UPDATE vendor SET status='suspended' WHERE id=%s RETURNING 1", (a,))
    error = refused(lambda: create(db, rfq, a, award_id, "1"))
    assert error.checks["problem"] == "vendor_not_eligible"
    with correlation_context("po"):
        denied = refused(
            lambda: pos(db).create(body(db, rfq, a, award_id, "1", override_reason="Urgent"), False)
        )
    assert denied.checks["problem"] == "vendor_not_eligible"
    overridden = create(db, rfq, a, award_id, "1", override_reason="Urgent")
    assert overridden.status == "draft"
    frozen = overridden.vendor_snapshot  # the same shape as the RFQ invitee snapshot
    assert set(frozen) == {"trading_name", "legal_name", "number", "status", "eligibility"}
    assert frozen["eligibility"]["eligible"] is False and frozen["eligibility"]["reasons"]
    assert sql(
        db, "SELECT count(*) FROM audit_log WHERE action='purchase_order.vendor_override'"
    ) == [(1,)]


def test_bill_to_comes_from_the_nearest_ancestor_and_is_missing_when_none(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, unit_id = ready(db)
    sql(db, "DELETE FROM org_address RETURNING 1")  # the unit itself holds none
    refused(lambda: create(db, rfq, a, award_id, "1"))  # none anywhere on the chain
    error = refused(lambda: create(db, rfq, a, award_id, "1"))
    assert error.checks["problem"] == "bill_to_missing" and counters(db) == (0, 0)
    org = OrgService(db[0], Scope(db[1]), db[3])
    from flo.modules.org.schemas import OrgUnitCreate

    top = org.create_unit(OrgUnitCreate(code="TOPX", name="Top", kind="bu"))
    address(db, top.id)
    sql(db, "UPDATE org_unit SET parent_id=%s WHERE id=%s RETURNING 1", (top.id, unit_id))
    po = create(db, rfq, a, award_id, "1")  # the address is only on the top-level unit
    assert po.bill_to["unit_id"] == str(top.id)


# -- the upstream guard (WF-001, D-M2-25) ---------------------------------------------------


def amended(db, row):
    """A pending material amendment on an awarded requisition. The service opens amendments only
    while sourcing, so the status is stepped back for the open and restored after."""
    status = sql(db, "SELECT status FROM requisition WHERE id=%s", (row.id,))[0][0]
    sql(
        db,
        "UPDATE requisition SET status='sourcing_in_progress' WHERE id=%s RETURNING 1",
        (row.id,),
    )
    amend(db, state(db, row.id), "100")
    sql(db, "UPDATE requisition SET status=%s WHERE id=%s RETURNING 1", (status, row.id))
    assert sql(db, "SELECT pending_amendment_id FROM requisition")[0][0] is not None


def nothing_written(db):
    assert sql(db, "SELECT count(*) FROM purchase_order") == [(0,)]
    assert counters(db) == (Decimal(0), Decimal(0))


def test_a_pending_amendment_refuses_the_po_and_writes_nothing(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, a, _, award_id, _ = ready(db)
    amended(db, row)
    assert refused(lambda: create(db, rfq, a, award_id, "1")).checks["problem"] == "stale_version"
    nothing_written(db)


def test_a_requisition_no_longer_approved_refuses_the_po(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, a, _, award_id, _ = ready(db)
    sql(db, "UPDATE requisition SET status='cancelled' WHERE id=%s RETURNING 1", (row.id,))
    assert refused(lambda: create(db, rfq, a, award_id, "1")).checks["problem"] == "stale_version"
    nothing_written(db)


def test_an_expired_code_refuses_the_po_and_writes_nothing(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    expire(db, "department", "D")
    add_code(db, "department", "D2")
    assert refused(lambda: create(db, rfq, a, award_id, "1")).checks["problem"] == "code_expired"
    nothing_written(db)


def test_a_po_is_refused_for_an_unapproved_requisition_even_when_lowering_is_allowed(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    amended(db, row)
    with correlation_context("po"):
        pos(db).update(po.id, PoPatch(lines=[PoLinePatch(line_no=1, quantity="2")]), '"1"')
    assert counters(db) == (Decimal(2), Decimal(2))  # handing quantity back is always allowed
    refused(
        lambda: pos(db).update(po.id, PoPatch(lines=[PoLinePatch(line_no=1, quantity="3")]), '"2"')
    )  # raising it again is not
    assert counters(db) == (Decimal(2), Decimal(2))


# -- concurrency (real Postgres) ----------------------------------------------------------


def _race(db, work):
    barrier = Barrier(2)

    def attempt(n):
        with psycopg.connect(connection_url(db[0]), autocommit=True) as conn:
            barrier.wait(timeout=10)
            try:
                return work(conn, n)
            except ProblemError as exc:
                return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        return sorted(pool.map(attempt, range(2)))


def test_two_pos_for_the_last_five_units_create_exactly_one(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    create(db, rfq, a, award_id, "5")

    request = body(db, rfq, a, award_id, "5")  # built here: `db[0]` is not thread-safe

    def work(conn, _):
        with correlation_context("po-race"):
            return pos(db, conn).create(request, True).status

    assert _race(db, work) == ["draft", "over_open_quantity"]
    assert counters(db) == (Decimal(10), Decimal(10))
    assert sql(db, "SELECT count(*) FROM purchase_order") == [(2,)]


def test_a_create_and_a_supersede_of_one_award_cannot_both_win(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)

    request = body(db, rfq, a, award_id, "3")  # built here: `db[0]` is not thread-safe

    def work(conn, n):
        with correlation_context("po-supersede-race"):
            if n == 0:
                return pos(db, conn).create(request, True).status
            return awards(db, connection=conn).supersede(rfq.id, WHY).status

    outcome = _race(db, work)
    made = sql(db, "SELECT count(*) FROM purchase_order")[0][0]
    status = sql(db, "SELECT status FROM award WHERE id=%s", (award_id,))[0][0]
    if made:  # the PO won: supersede is refused and the award stays
        assert outcome == ["draft", "po_exists"] and status == "approved"
    else:  # supersede won: the create is refused with nothing written
        assert outcome == ["invalid_transition", "superseded"] and status == "superseded"
        assert counters_all(db) == [] or counters_all(db) == [Decimal(0)]


def test_supersede_is_refused_once_a_draft_po_exists(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "2")
    assert refused(lambda: supersede(db, rfq)).checks["problem"] == "po_exists"
    with correlation_context("po"):
        pos(db).delete(po.id, '"1"')
    assert supersede(db, rfq).status == "superseded"  # a deleted draft no longer blocks


# -- routes -------------------------------------------------------------------------------

BASE = "/api/v1/purchase-orders"


async def send(browser, method, suffix="", data=None, extra=None):
    return await browser.request(
        method, BASE + suffix, json=data, headers=headers(browser, uuid4().hex) | (extra or {})
    )


def test_authorization_per_role_and_a_replayed_delete(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, unit_id = ready(db)
    po = create(db, rfq, a, award_id, "2")
    payload = {
        "award_id": str(award_id),
        "vendor_id": str(a),
        "lines": [{"rfq_line_id": str(rfq_line_of(db, rfq)), "quantity": "1"}],
    }

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["purchase_order.view"])  # the auditor's read-only set
            assert (await send(browser, "GET", f"/{po.id}")).status_code == 200
            assert (
                await send(browser, "GET", f"/awarded-lines?award_id={award_id}")
            ).status_code == 200
            assert (await send(browser, "GET")).status_code == 200
            assert (await send(browser, "POST", "", payload)).status_code == 403
            patched = await send(browser, "PATCH", f"/{po.id}", {"terms": "x"}, {"If-Match": '"1"'})
            assert patched.status_code == 403, patched.text  # plant 4: PATCH needs create
            gone = await send(browser, "DELETE", f"/{po.id}", None, {"If-Match": '"1"'})
            assert gone.status_code == 403
            regrant(db, ["rfq.view", "rfq.create"])  # sourcing specialist: neither code
            for method, suffix in (("GET", f"/{po.id}"), ("GET", ""), ("POST", "")):
                response = await send(
                    browser, method, suffix, payload if method == "POST" else None
                )
                assert response.status_code == 403, (method, suffix)
            regrant(db, ["purchase_order.view", "purchase_order.create"])
            created = await send(browser, "POST", "", payload)
            assert created.status_code == 201 and created.headers["ETag"] == '"1"', created.text
            key = {"Idempotency-Key": "delete-once", "If-Match": '"1"'}
            first = await browser.request(
                "DELETE", f"{BASE}/{created.json()['id']}", headers=headers(browser) | key
            )
            replay = await browser.request(
                "DELETE", f"{BASE}/{created.json()['id']}", headers=headers(browser) | key
            )
            assert first.status_code == 204 and replay.status_code == 204
            assert replay.content == first.content  # the stored response
            assert counters(db) == (Decimal(2), Decimal(2))  # the replay moved nothing again

    asyncio.run(scenario())


def test_the_list_shows_only_permitted_units_and_a_unit_scoped_viewer_gets_200(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, unit_id = ready(db)
    po = create(db, rfq, a, award_id, "2")
    other = OrgService(db[0], Scope(db[1]), db[3]).create_unit(
        __import__("flo.modules.org.schemas", fromlist=["OrgUnitCreate"]).OrgUnitCreate(
            code="OTHER", name="Other", kind="bu"
        )
    )
    from flo.modules.identity.models import AuthorizationTarget, ScopeType

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["purchase_order.view"], AuthorizationTarget(ScopeType.BU, other.id))
            mine = await send(browser, "GET")
            assert mine.status_code == 200 and mine.json()["rows"] == []  # unit B only: not unit A
            assert mine.json()["counts"] == {"all": 0, "draft": 0}
            regrant(db, ["purchase_order.view"], AuthorizationTarget(ScopeType.BU, unit_id))
            seen = await send(browser, "GET", "?tab=draft")
            assert [r["id"] for r in seen.json()["rows"]] == [str(po.id)]
            regrant(
                db,
                ["purchase_order.create"],
                AuthorizationTarget(ScopeType.BU, other.id),
                keep=True,
            )
            wrong = await send(browser, "PATCH", f"/{po.id}", {"terms": "x"}, {"If-Match": '"1"'})
            assert wrong.status_code == 403, wrong.text  # create on unit B is not create on unit A

    asyncio.run(scenario())


def test_purchase_order_routes_conceal_foreign_tenants(lifecycle_db):
    from tests.requisitions.test_header import unit

    db, _ = lifecycle_db
    rfq, row, a, _, award_id, _ = ready(db)
    foreign_unit, foreign_rfq, foreign_award, foreign_po = (
        unit(db, "FBU", org=db[2]),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    with tenant_transaction(db[0], Scope(db[2])):  # real rows, owned by the other tenant
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign_rfq, db[2], foreign_unit.id, db[3]),
        )
        db[0].execute(
            "INSERT INTO award (id,org_id,number,rfq_id,basis,status,rates,created_by) "
            "VALUES (%s,%s,'AWD-X',%s,'manual','approved','{}',%s)",
            (foreign_award, db[2], foreign_rfq, db[3]),
        )
        db[0].execute(
            "INSERT INTO purchase_order (id,org_id,number,unit_id,vendor_id,requisition_id,"
            "vendor_snapshot,award_id,currency,rate,bill_to,recipients,requester_id,created_by,"
            "requisition_version) "
            "VALUES (%s,%s,'PO-X',%s,%s,%s,'{}',%s,'USD','{}','{}','[]',%s,%s,1)",
            (foreign_po, db[2], foreign_unit.id, a, row.id, foreign_award, db[3], db[3]),
        )
    payload = {
        "award_id": str(foreign_award),
        "vendor_id": str(a),
        "lines": [{"rfq_line_id": str(rfq_line_of(db, rfq)), "quantity": "1"}],
    }

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for id in (foreign_po, uuid4()):
                for method, suffix, data in (
                    ("GET", f"/{id}", None),
                    ("PATCH", f"/{id}", {"terms": "x"}),
                    ("DELETE", f"/{id}", None),
                    ("POST", f"/{id}/transitions", {"action": "close", "reason": "x"}),
                    ("POST", f"/{id}/submit", None),
                    ("POST", f"/{id}/withdraw", None),
                    ("POST", f"/{id}/issue", None),
                    ("POST", f"/{id}/cancel", {"reason": "x"}),
                    ("POST", f"/{id}/reduce", {"reason": "x", "lines": []}),
                    ("POST", f"/{id}/changes", {}),
                    ("GET", f"/{id}/changes", None),
                    ("GET", f"/{id}/changes/1", None),
                    ("PATCH", f"/{id}/changes/1", {"terms": "x"}),
                    ("POST", f"/{id}/changes/1/submit", None),
                    ("POST", f"/{id}/changes/1/withdraw", None),
                    ("POST", f"/{id}/changes/1/apply", None),
                ):
                    response = await send(browser, method, suffix, data, {"If-Match": '"1"'})
                    assert response.status_code == 404, (method, response.text)
            for award in (foreign_award, uuid4()):
                assert (
                    await send(browser, "POST", "", payload | {"award_id": str(award)})
                ).status_code == 404
                lines = await send(browser, "GET", f"/awarded-lines?award_id={award}")
                assert lines.status_code == 404
            listed = await send(browser, "GET")
            assert str(foreign_po) not in listed.text

    asyncio.run(scenario())
    assert UUID(str(award_id))


def test_a_create_waits_on_the_requisition_lock_and_is_refused_by_the_amendment_that_won(
    lifecycle_db,
):
    """Deterministic: the amendment submit holds the requisition row, uncommitted, until the
    create is seen waiting on a lock. Without the header lock in `order_lines` the create would
    read the requisition before the amendment lands and commit a PO against it."""
    db, _ = lifecycle_db
    rfq, row, a, _, award_id, _ = ready(db)
    sql(
        db,
        "UPDATE requisition SET status='sourcing_in_progress' WHERE id=%s RETURNING 1",
        (row.id,),
    )
    draft = opened(db, state(db, row.id))
    # left in `sourcing_in_progress`, where a submit is allowed, like a requisition part-awarded
    draft = propose(db, row, draft.revision_no, draft.version, price="100")
    request = body(db, rfq, a, award_id, "3")  # built here: `db[0]` is not thread-safe
    url, held, waiting = connection_url(db[0]), Event(), Event()

    def amendment():
        with psycopg.connect(url, autocommit=True) as conn, correlation_context("amend-hold"):
            with conn.transaction():
                amendments.submit(
                    conn, Scope(db[1]), row.id, draft.revision_no, AmendmentSubmit(note=None),
                    str(draft.version), db[3],
                )  # fmt: skip
                held.set()
                with psycopg.connect(url, autocommit=True) as watcher:
                    for _ in range(100):  # until the create blocks, at most 5 s
                        blocked = watcher.execute(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE wait_event_type='Lock' AND datname=current_database()"
                        ).fetchone()
                        if blocked and blocked[0]:
                            break
                        time.sleep(0.05)
                waiting.set()
        return "submitted"

    def create_po():
        assert held.wait(10)
        with psycopg.connect(url, autocommit=True) as conn, correlation_context("po-held"):
            try:
                return pos(db, conn).create(request, True).status
            except ProblemError as exc:
                return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        amended_, created = pool.submit(amendment), pool.submit(create_po)
        assert amended_.result(timeout=30) == "submitted" and waiting.is_set()
        outcome = created.result(timeout=30)
    assert sql(db, "SELECT pending_amendment_id FROM requisition")[0][0] is not None
    assert outcome == "stale_version"  # the create waited, then saw the amendment
    nothing_written(db)
