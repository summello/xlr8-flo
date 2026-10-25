"""The comparison on the real stack: freeze, rate, refresh, roles, tenants (SRC-009, SRC-010)."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.config import Settings
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.sourcing import comparison as engine
from flo.modules.sourcing.comparison import ComparisonService
from flo.modules.sourcing.schemas import BidLineIn, BidReplace, BidSubmit
from tests.isolation.test_real_sessions import client, login
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_header import unit
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_bids import (
    as_role,
    bids,
    complete,
    edit,
    evidence,
    key,
    live,
    rfq_line,
    second_user,
    submit,
)
from tests.sourcing.test_rfq import refused, send, sql
from tests.vendors.test_vendors import regrant

PRICE = "98765.4321"
FIGURES = ("98765.4321", "98,765.4321", "296296.296", "77777.7777")


def cmp(db):
    return ComparisonService(db[0], Scope(db[1]), db[3], Settings())


def utc_today() -> date:
    return datetime.now(UTC).date()


def effective(day: date) -> date:
    """The business day `rate_for` reads for `day`."""
    return day - timedelta(days=day.weekday() - 4) if day.weekday() >= 5 else day


def fx(db, day, **quotes):
    for quote, rate in quotes.items():
        sql(
            db,
            "INSERT INTO fx_rate (quote, rate, effective_date, fetched_at) "
            "VALUES (%s, %s, %s, now()) RETURNING 1",
            (quote, rate, effective(day)),
        )


def deadline_ago(db, rfq, days):
    sql(
        db,
        f"UPDATE rfq SET deadline_at=now()-interval '{days} days' WHERE id=%s RETURNING 1",
        (rfq.id,),
    )


def rate_date_of(db, rfq) -> date:
    query = "SELECT (deadline_at AT TIME ZONE deadline_tz)::date FROM rfq WHERE id=%s"
    return sql(db, query, (rfq.id,))[0][0]


def quoted(db, rfq, vendor_id, currency="USD", price="10", freight="0", tax="0"):
    bid = key(db, rfq, vendor_id, currency)
    line = rfq_line(db, rfq)
    bid = edit(
        db,
        rfq,
        bid,
        lines=[BidLineIn(rfq_line_id=line, quoted=True, unit_price=price, tax=tax)],
        freight=freight,
        evidence_attachment_id=evidence(db, bid.id),
    )
    return submit(db, rfq, bid)


def frozen_row(db, rfq):
    return sql(
        db,
        "SELECT rate_date, frozen_at, rates, row_version FROM rfq_comparison WHERE rfq_id=%s",
        (rfq.id,),
    )


def test_rates_freeze_at_the_deadline_date_then_refresh_moves_them_and_is_audited(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [eur, usd] = live(db, qty="4", names=("E", "U"))
    quoted(db, rfq, eur, "EUR", price="10")  # 4 x 10 EUR
    quoted(db, rfq, usd, "USD", price="3")
    deadline_ago(db, rfq, 10)
    then = rate_date_of(db, rfq)
    fx(db, then, USD="1.25")
    fx(db, utc_today(), USD="1.40")
    before = sql(db, "SELECT * FROM bid_line ORDER BY bid_id, rfq_line_id")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["rfq.view"])  # an Auditor's first read freezes the same rates
            first = await send(browser, "GET", f"/rfqs/{rfq.id}/comparison")
            assert first.status_code == 200, first.text
            [row] = frozen_row(db, rfq)
            assert row[0] == then and row[2]["EUR"]["rate"] == "1.25000000"
            assert row[2]["EUR"]["source"] == "ECB" and set(row[2]) == {"EUR", "USD"}
            body = first.json()
            by_ccy = {v["currency"]: v for v in body["vendors"]}
            assert by_ccy["EUR"]["landed_total"] == "50.00"  # 40 x 1.25
            assert by_ccy["EUR"]["conversion"]["rate"] == "1.25000000"
            assert by_ccy["EUR"]["conversion"]["effective_date"] == effective(then).isoformat()
            assert body["stale_rate"] is True and body["rate_date"] == then.isoformat()
            # a later read, by anyone, keeps the frozen rates even though today's differ
            regrant(db, ["rfq.view", "rfq.edit"])
            again = await send(browser, "GET", f"/rfqs/{rfq.id}/comparison")
            assert again.json()["vendors"] == body["vendors"] and frozen_row(db, rfq) == [row]
            refreshed = await send(browser, "POST", f"/rfqs/{rfq.id}/comparison/refresh-rate")
            assert refreshed.status_code == 200, refreshed.text
            out = {v["currency"]: v for v in refreshed.json()["vendors"]}
            assert (
                out["EUR"]["landed_total"] == "56.00"
                and out["EUR"]["conversion"]["rate"] == "1.40000000"
            )
            assert refreshed.json()["stale_rate"] is False
            return refreshed.json()["rate_date"]

    moved = asyncio.run(scenario())
    [row] = frozen_row(db, rfq)
    assert row[0] == utc_today() and moved == utc_today().isoformat() and row[3] == 2
    audit = sql(
        db, "SELECT before, after FROM audit_log WHERE action='rfq.comparison.refresh_rate'"
    )
    assert audit == [({"rate_date": then.isoformat()}, {"rate_date": utc_today().isoformat()})]
    assert sql(db, "SELECT * FROM bid_line ORDER BY bid_id, rfq_line_id") == before  # SRC-010


def test_a_bid_after_the_freeze_joins_at_the_frozen_date_and_the_rest_stays(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [eur, gbp] = live(db, qty="4", names=("E", "G"))
    quoted(db, rfq, eur, "EUR", price="10")
    deadline_ago(db, rfq, 3)
    then = rate_date_of(db, rfq)
    fx(db, then, USD="1.25", GBP="0.80")
    first = cmp(db).comparison(rfq.id)
    [frozen] = frozen_row(db, rfq)
    assert first.bids_since_freeze == 0 and set(frozen[2]) == {"EUR"}
    with correlation_context("late"):  # sent an hour ago, keyed after the stop time
        bid = key(db, rfq, gbp, "GBP")
        line = rfq_line(db, rfq)
        bid = edit(
            db, rfq, bid,
            lines=[BidLineIn(rfq_line_id=line, quoted=True, unit_price="8", tax="0")],
            evidence_attachment_id=evidence(db, bid.id),
        )  # fmt: skip
        sql(db, "UPDATE bid SET sent_at=now()-interval '5 days' WHERE id=%s RETURNING 1", (bid.id,))
        bids(db).submit(
            rfq.id, bid.id, BidSubmit(accept_late_reason="Courier"), str(bid.row_version)
        )
    second = cmp(db).comparison(rfq.id)
    [after] = frozen_row(db, rfq)
    assert second.bids_since_freeze == 1 and len(second.vendors) == 2
    assert after[0] == frozen[0] and after[1] == frozen[1]  # date and frozen_at untouched
    assert after[2]["EUR"] == frozen[2]["EUR"]
    assert Decimal(after[2]["GBP"]["rate"]) == Decimal("1.5625")  # 1.25 / 0.80 at the frozen date
    gb = next(v for v in second.vendors if v.currency == "GBP")
    assert gb.landed_total == "50.00"  # 4 x 8 GBP x 1.5625


def test_a_replaced_bid_counts_once_at_the_replacements_figures(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db, qty="4")
    first = quoted(db, rfq, vid, "USD", price="10")
    with correlation_context("replace"):
        draft = bids(db).replace(
            rfq.id, first.id, BidReplace(reason="Re-quote", sent_at=datetime.now(UTC))
        )
    line = rfq_line(db, rfq)
    draft = edit(
        db, rfq, draft,
        lines=[BidLineIn(rfq_line_id=line, quoted=True, unit_price="9", tax="0")],
        evidence_attachment_id=evidence(db, draft.id),
    )  # fmt: skip
    submit(db, rfq, draft)
    deadline_ago(db, rfq, 1)
    result = cmp(db).comparison(rfq.id)
    [only] = result.vendors
    assert only.bid_id == draft.id and only.landed_total == "36.00"  # 4 x 9
    assert sql(db, "SELECT count(*) FROM bid WHERE status='submitted'")[0][0] == 2


def test_an_amended_rfq_prices_the_bid_at_the_quantity_it_answered(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db, qty="4")
    quoted(db, rfq, vid, "USD", price="10")
    sql(db, "UPDATE rfq SET version=2 WHERE id=%s RETURNING 1", (rfq.id,))
    sql(db, "UPDATE rfq_line SET quantity=9 WHERE rfq_id=%s RETURNING 1", (rfq.id,))
    deadline_ago(db, rfq, 1)
    [v] = (result := cmp(db).comparison(rfq.id)).vendors
    assert v.landed_total == "40.00" and v.rfq_version == 1 and not v.on_current_version
    assert result.lines[0].quantity == "9.0000" and result.lines[0].cells[0].quantity == "4.0000"


def test_approved_scope_of_a_third_matches_the_hand_computed_figure(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db, qty="1")
    sql(db, "UPDATE requisition_line SET quantity=3, amount=100 RETURNING 1")
    quoted(db, rfq, vid, "USD", price="10")
    deadline_ago(db, rfq, 1)
    assert cmp(db).comparison(rfq.id).approved_scope == "33.33"  # 100 x 1 / 3 at 2 decimals


def test_no_current_bid_is_an_empty_comparison_not_an_error(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, _ = live(db)
    deadline_ago(db, rfq, 1)
    result = cmp(db).comparison(rfq.id)
    assert result.vendors == [] and result.insight is None and result.bids_since_freeze == 0
    assert result.best_average_cost.total is None


def test_the_sealed_shape_carries_no_figure_before_the_deadline(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    bid = key(db, rfq, vid, "USD")
    bid = complete(db, rfq, bid, price=PRICE)
    submit(db, rfq, edit(db, rfq, bid, freight="77777.7777"))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for kind, grant in [
                ("role", "auditor"),
                ("role", "sourcing-specialist"),
                ("role", "organization-administrator"),
                ("permissions", ["rfq.view"]),
            ]:
                as_role(db, grant) if kind == "role" else regrant(db, grant)
                response = await send(browser, "GET", f"/rfqs/{rfq.id}/comparison")
                assert response.status_code == 200, (grant, response.text)
                assert response.json() == {"sealed": True}
                assert not any(f in response.text for f in FIGURES)
            assert sql(db, "SELECT count(*) FROM rfq_comparison")[0][0] == 0  # nothing frozen

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "problem"),
    [
        ("draft", "comparison_sealed"),
        ("cancelled", "rfq_cancelled"),
        ("awarded", "rfq_awarded"),
    ],
)
def test_refresh_is_refused_in_the_states_that_cannot_take_it(lifecycle_db, status, problem):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    quoted(db, rfq, vid, "USD")
    deadline_ago(db, rfq, 1)
    sql(db, "UPDATE rfq SET status=%s WHERE id=%s RETURNING 1", (status, rfq.id))
    assert refused(lambda: cmp(db).refresh_rate(rfq.id)).checks["problem"] == problem


def test_refresh_is_refused_before_the_deadline_and_allowed_after_it(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    quoted(db, rfq, vid, "USD")
    assert refused(lambda: cmp(db).refresh_rate(rfq.id)).checks["problem"] == "comparison_sealed"
    deadline_ago(db, rfq, 1)  # published, past the stop time, close job not yet run
    assert sql(db, "SELECT status FROM rfq")[0][0] == "published"
    assert not frozen_row(db, rfq)  # no row yet: refresh creates it at today's date
    result = cmp(db).refresh_rate(rfq.id)
    assert result.rate_date == utc_today() and frozen_row(db, rfq)[0][0] == utc_today()
    for status in ("closed", "under_evaluation"):
        sql(db, "UPDATE rfq SET status=%s WHERE id=%s RETURNING 1", (status, rfq.id))
        cmp(db).refresh_rate(rfq.id)


@pytest.mark.parametrize(
    ("permissions", "get", "post"),
    [
        (["rfq.view"], 200, 403),  # an Auditor reads and never acts
        (["rfq.view", "rfq.edit"], 200, 200),
        (["rfq.edit"], 403, 200),
        (["rfq.approve"], 403, 403),
    ],
)
def test_authorization_per_role_over_both_routes(lifecycle_db, permissions, get, post):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    quoted(db, rfq, vid, "USD")
    deadline_ago(db, rfq, 1)
    regrant(db, permissions)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            read = await send(browser, "GET", f"/rfqs/{rfq.id}/comparison")
            act = await send(browser, "POST", f"/rfqs/{rfq.id}/comparison/refresh-rate")
            return read.status_code, act.status_code

    assert asyncio.run(scenario()) == (get, post)


def test_baseline_auditor_reads_but_cannot_refresh(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    quoted(db, rfq, vid, "USD")
    deadline_ago(db, rfq, 1)
    as_role(db, "auditor")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            read = await send(browser, "GET", f"/rfqs/{rfq.id}/comparison")
            act = await send(browser, "POST", f"/rfqs/{rfq.id}/comparison/refresh-rate")
            return read.status_code, act.status_code

    assert asyncio.run(scenario()) == (200, 403)


def test_comparison_routes_conceal_foreign_tenants(lifecycle_db):
    db, _ = lifecycle_db
    foreign_unit = unit(db, "FBU", org=db[2])
    foreign_rfq = uuid4()
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign_rfq, db[2], foreign_unit.id, second_user(db)),
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, suffix in [
                ("GET", f"/rfqs/{foreign_rfq}/comparison"),
                ("POST", f"/rfqs/{foreign_rfq}/comparison/refresh-rate"),
                ("GET", f"/rfqs/{uuid4()}/comparison"),
            ]:
                response = await send(browser, method, suffix)
                assert response.status_code == 404, (method, suffix, response.text)

    asyncio.run(scenario())


def test_the_stale_rate_clock_is_the_comparison_modules_today(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    quoted(db, rfq, vid, "EUR", price="1")
    deadline_ago(db, rfq, 1)
    fx(db, rate_date_of(db, rfq), USD="1.25")
    assert cmp(db).comparison(rfq.id).stale_rate is False
    monkeypatch.setattr(engine, "today", lambda: utc_today() + timedelta(days=8))
    stale = cmp(db).comparison(rfq.id)
    assert stale.stale_rate is True and Decimal(stale.vendors[0].conversion.rate) == Decimal("1.25")
