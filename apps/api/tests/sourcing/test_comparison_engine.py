"""The comparison engine against hand-computed figures (SRC-009, SRC-010). No database."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from uuid import UUID

from flo.modules.sourcing.comparison import BidIn, LineIn, Quote, Rate, compute

TODAY = date(2026, 10, 26)
T0 = datetime(2026, 10, 20, 9, tzinfo=UTC)
L1, L2, L3, L4 = (UUID(int=i) for i in (1, 2, 3, 4))
LINES = [
    LineIn(L1, 1, "One", D("10"), None),
    LineIn(L2, 2, "Two", D("5"), date(2026, 11, 10)),
    LineIn(L3, 3, "Three", D("2"), date(2026, 11, 20)),
    LineIn(L4, 4, "Four", D("1"), None),
]
RATES = {
    "USD": Rate(D(1), "identity", TODAY),
    "EUR": Rate(D("1.10"), "ECB", date(2026, 10, 24)),
}


def bid(n, ccy, freight, quotes, hours=0, lead=None, version=1, met=0):
    return BidIn(
        id=UUID(int=100 + n),
        vendor_id=UUID(int=200 + n),
        vendor_name=f"V{n}",
        currency=ccy,
        sent_at=T0 + timedelta(hours=hours),
        rfq_version=version,
        freight=D(freight),
        lead_time_days=lead,
        met=met,
        quotes={k: Quote(D(q), D(p), D(t)) for k, (q, p, t) in quotes.items()},
    )


def run(bids, lines=LINES, rates=RATES, scope="400.00", version=1, **extra):
    return compute(
        currency="USD",
        lines=lines,
        bids=bids,
        requirements=2,
        rates=rates,
        current_version=version,
        approved_scope=D(scope),
        comparison_date=TODAY,
        rate_date=date(2026, 10, 24),
        frozen_at=T0,
        bids_since_freeze=0,
        **extra,
    )


A = bid(
    1,
    "USD",
    "100",
    {L1: (10, 10, 5), L2: (5, 20, 0), L3: (2, 50, 10), L4: (1, 100, 0)},
    0,
    10,
    met=2,
)
B = bid(  # EUR, freight 30.00 splits 6.86 / 7.29 / 7.71 / 8.14 (residual to the earliest)
    2, "EUR", "30", {L1: (10, 8, 4), L2: (5, 17, 0), L3: (2, 45, 0), L4: (1, 95, 0)}, 1, 5, met=1
)
C = bid(  # a zero-price line takes no freight; the others absorb it
    3, "USD", "50", {L1: (10, 9, 2), L2: (5, 0, 0), L3: (2, 40, 0)}, 2, None
)


def vendor(result, n):
    return next(v for v in result.vendors if v.bid_id == UUID(int=100 + n))


def test_three_vendors_four_lines_every_figure():
    r = run([A, B, C])
    a, b, c = (vendor(r, n) for n in (1, 2, 3))
    cells = {
        n: dict(zip((x.id for x in LINES), (r_.cells[n - 1] for r_ in r.lines), strict=True))
        for n in (1, 2, 3)
    }
    assert [row.cells[0].landed for row in r.lines] == ["130.00", "125.00", "135.00", "125.00"]
    assert [row.cells[1].landed for row in r.lines] == ["99.95", "101.52", "107.48", "113.45"]
    assert [row.cells[2].landed for row in r.lines] == ["118.48", "0.00", "103.52", None]
    assert [row.cells[1].freight_share for row in r.lines] == ["7.55", "8.02", "8.48", "8.95"]
    assert cells[3][L2].freight_share == "0.00" and cells[3][L4].not_quoted
    # a breakdown that reconciles to the minor unit
    assert (a.price, a.tax, a.freight, a.landed_total) == ("400.00", "15.00", "100.00", "515.00")
    assert (b.price, b.tax, b.freight, b.landed_total) == ("385.00", "4.40", "33.00", "422.40")
    assert (c.price, c.tax, c.freight, c.landed_total) == ("170.00", "2.00", "50.00", "222.00")
    for v in (a, b, c):
        assert D(v.price) + D(v.tax) + D(v.freight) == D(v.landed_total)
    assert (a.full, b.full, c.full) == (True, True, False)
    # lowest is across full bids only: C is cheaper but partial
    assert r.markers.lowest.vendor_id == b.vendor_id and not r.markers.lowest.tied
    assert (a.difference_from_lowest, b.difference_from_lowest) == ("92.60", "0.00")
    assert c.difference_from_lowest is None  # partial while a full bid exists
    # ... but a partial bid is the lowest on a line
    assert [row.lowest.vendor_id for row in r.lines] == [
        b.vendor_id, c.vendor_id, c.vendor_id, b.vendor_id,
    ]  # fmt: skip
    # both bases
    assert r.lowest_cost_supplier.total == "316.92" and not r.lowest_cost_supplier.partial
    assert r.best_average_cost.total == "422.40" and r.best_average_cost.vendor_id == b.vendor_id
    assert not r.best_average_cost.partial
    assert r.approved_scope == "400.00"
    assert (r.lowest_cost_supplier.within_scope, r.lowest_cost_supplier.over_by) == (True, "0.00")
    assert (r.best_average_cost.within_scope, r.best_average_cost.over_by) == (False, "22.40")
    assert r.insight.difference == "105.48" and r.insight.difference_pct == "33.28"
    assert r.insight.dearer_basis == "best_average_cost"
    assert (r.insight.lowest_cost_supplier_vendors, r.insight.best_average_cost_vendors) == (2, 1)
    # lead time counts from the comparison date; needed_by is the earliest line's
    assert r.markers.fastest.vendor_id == b.vendor_id
    assert (a.ready_by, a.meets_need) == (date(2026, 11, 5), True)
    assert (b.ready_by, b.meets_need) == (date(2026, 10, 31), True)
    assert (c.ready_by, c.meets_need) == (None, None)  # no lead time: never fastest
    assert (a.met, a.met_of, b.met) == (2, 2, 1)
    assert b.conversion.rate == "1.10" and b.conversion.source == "ECB"
    assert b.conversion.effective_date == date(2026, 10, 24) and not r.stale_rate
    assert r.markers.highest_score is None and a.score is None
    # the vendor's own paper is untouched
    assert cells[2][L1].unit_price == "8" and B.freight == D("30")
    # converted unit price: unit x rate, minor unit of the RFQ currency; null when not quoted
    assert [row.cells[1].unit_price_converted for row in r.lines] == [
        "8.80", "18.70", "49.50", "104.50",
    ]  # fmt: skip
    assert cells[1][L1].unit_price_converted == "10.00"
    assert cells[3][L4].unit_price_converted is None


def test_row_is_identical_only_when_every_vendor_agrees():
    same = {L1: (1, 5, 0)}
    r = run([bid(1, "USD", "0", same), bid(2, "USD", "0", same, 1)], lines=LINES[:1])
    assert r.lines[0].identical
    r = run([bid(1, "USD", "0", same), bid(2, "USD", "0", {L1: (1, 6, 0)}, 1)], lines=LINES[:1])
    assert not r.lines[0].identical


def test_tie_goes_to_the_earlier_submission_then_the_lower_id():
    quotes = {L1: (1, 10, 0)}
    late, early = bid(1, "USD", "0", quotes, hours=5), bid(2, "USD", "0", quotes, hours=1)
    r = run([late, early], lines=LINES[:1])
    assert r.markers.lowest.tied and r.markers.lowest.vendor_id == early.vendor_id
    assert r.lines[0].lowest.tied and r.lines[0].lowest.vendor_id == early.vendor_id
    assert r.best_average_cost.vendor_id == early.vendor_id
    same_time = [bid(2, "USD", "0", quotes), bid(1, "USD", "0", quotes)]
    assert run(same_time, lines=LINES[:1]).markers.lowest.vendor_id == UUID(int=201)


def test_every_line_at_zero_price_folds_the_bid_currency_freight_in_once():
    zero = {L1: (1, 0, 0), L2: (1, 0, 0)}
    usd = bid(1, "USD", "10.0050", zero)  # sub-minor freight: quantize(10.0050, USD) = 10.01
    eur = bid(2, "EUR", "12.345", zero, 1)  # 12.35 EUR x 1.10 = 13.585 -> 13.59 USD
    r = run([usd, eur], lines=LINES[:2])
    u, e = vendor(r, 1), vendor(r, 2)
    assert (u.freight_unallocated, u.landed_total, u.freight) == ("10.01", "10.01", "0.00")
    assert (e.freight_unallocated, e.freight_unallocated_converted) == ("12.35", "13.59")
    assert e.landed_total == "13.59"
    assert [c.freight_share for c in r.lines[0].cells] == ["0.00", "0.00"]


def test_freight_shares_sum_back_exactly_in_the_bid_currency():
    r = run([bid(1, "USD", "10.0050", {L1: (1, 1, 0), L2: (1, 2, 0)})], lines=LINES[:2])
    assert [row.cells[0].freight_share for row in r.lines] == ["3.34", "6.67"]  # 10.01 in all
    assert vendor(r, 1).freight == "10.01"


def test_a_sub_minor_line_price_still_takes_a_positive_ratio():
    tiny = {L1: (1, "0.0010", 0), L2: (1, 1000, 0)}
    r = run([bid(1, "USD", "10", tiny)], lines=LINES[:2])
    # a zero ratio would leave the residual cent to L2: 0.00 / 10.00
    assert [row.cells[0].freight_share for row in r.lines] == ["0.01", "9.99"]


def test_no_full_bid_falls_back_to_the_lowest_partial_and_says_so():
    p1 = bid(1, "USD", "0", {L1: (10, 10, 0), L2: (5, 20, 0)})  # 200
    p2 = bid(2, "USD", "0", {L1: (10, 9, 0), L2: (5, 19, 0)}, 1)  # 185
    r = run([p1, p2], lines=LINES[:3], scope="190.00")
    assert r.markers.lowest is None  # lowest is for full bids only
    assert r.best_average_cost.vendor_id == p2.vendor_id and r.best_average_cost.partial
    assert r.best_average_cost.total == "185.00" and r.best_average_cost.within_scope
    assert (vendor(r, 1).difference_from_lowest, vendor(r, 2).difference_from_lowest) == (
        "15.00",
        "0.00",
    )
    assert r.lowest_cost_supplier.uncovered_lines == [L3] and r.lowest_cost_supplier.partial


def test_a_bid_quoting_nothing_is_in_no_basis_and_no_marker():
    nothing = bid(1, "USD", "5", {}, hours=0, lead=1)
    real = bid(2, "USD", "0", {L1: (1, 10, 0)}, hours=1, lead=9)
    r = run([nothing, real], lines=LINES[:1])
    n = vendor(r, 1)
    assert n.not_quoted and n.landed_total is None and n.freight_unallocated is None
    assert r.markers.fastest.vendor_id == real.vendor_id  # lead 1 never counts
    assert r.best_average_cost.vendor_id == real.vendor_id
    assert [c.not_quoted for c in r.lines[0].cells] == [True, False]


def test_empty_comparison_has_the_documented_shape():
    r = run([])
    assert r.vendors == [] and r.insight is None
    assert (r.markers.lowest, r.markers.fastest, r.markers.highest_score) == (None, None, None)
    for basis in (r.lowest_cost_supplier, r.best_average_cost):
        assert (basis.vendor_id, basis.total, basis.partial) == (None, None, False)
        assert (basis.within_scope, basis.over_by) == (None, None)
    assert r.lowest_cost_supplier.uncovered_lines == [x.id for x in LINES]
    assert all(row.lowest is None and row.cells == [] for row in r.lines)


def test_an_amended_rfq_flags_the_bid_on_an_older_version():
    r = run([bid(1, "USD", "0", {L1: (4, 10, 0)}, version=1)], lines=LINES[:1], version=2)
    v = vendor(r, 1)
    assert not v.on_current_version and v.rfq_version == 1
    assert r.lines[0].cells[0].quantity == "4"  # priced at the quantity the vendor answered
    assert v.landed_total == "40.00"


def test_difference_pct_is_null_when_the_lower_total_is_zero():
    free = bid(1, "USD", "0", {L1: (1, 0, 0)})
    pricey = bid(2, "USD", "0", {L1: (1, 10, 0)}, 1)
    r = run([free, pricey], lines=LINES[:1])
    assert r.lowest_cost_supplier.total == "0.00" and r.best_average_cost.total == "0.00"
    assert r.insight.difference == "0.00" and r.insight.difference_pct is None


def test_a_rate_eight_days_old_is_stale_and_seven_is_not():
    old = {**RATES, "EUR": Rate(D("1.10"), "ECB", TODAY - timedelta(days=8))}
    r = run([B], rates=old)
    assert r.stale_rate and vendor(r, 2).conversion.stale
    fresh = {**RATES, "EUR": Rate(D("1.10"), "ECB", TODAY - timedelta(days=7))}
    assert not run([B], rates=fresh).stale_rate
    assert not run([A]).stale_rate  # an identity rate never goes stale


def selection_example(scope):
    x = bid(1, "USD", "0", {L1: (1, 10, 0), L2: (1, 20, 0), L3: (1, 30, 0)}, 0)
    y = bid(2, "USD", "0", {L1: (1, 10, 0), L2: (1, 25, 0), L3: (1, 20, 0)}, 1)
    z = bid(3, "USD", "0", {L1: (1, 12, 0), L2: (1, 15, 0)}, 2)  # partial
    return run([x, y, z], lines=LINES[:3], scope=scope)


def test_selection_totals_sum_to_the_basis_total_and_amount_to_spare_is_hand_computed():
    r = selection_example("50.00")
    lcs, bac = r.lowest_cost_supplier, r.best_average_cost
    assert [(v.vendor_id, v.total, v.lines) for v in lcs.vendors] == [
        (UUID(int=201), "10.00", [L1]), (UUID(int=202), "20.00", [L3]),
        (UUID(int=203), "15.00", [L2]),
    ]  # fmt: skip
    assert [(v.vendor_id, v.total, v.lines) for v in bac.vendors] == [
        (UUID(int=202), "55.00", [L1, L2, L3])
    ]
    for b in (lcs, bac):
        assert sum(D(v.total) for v in b.vendors) == D(b.total)
    assert (lcs.total, lcs.to_spare, lcs.over_by) == ("45.00", "5.00", "0.00")
    assert (bac.total, bac.to_spare, bac.over_by) == ("55.00", "0.00", "5.00")
    assert selection_example("55.00").best_average_cost.to_spare == "0.00"


def test_not_lowest_lines_are_those_where_another_vendor_is_strictly_cheaper():
    r = selection_example("50.00")
    # Best Average Cost is vendor 2: tied at the lowest on L1, cheapest on L3, dearer on L2
    assert r.best_average_cost.not_lowest_lines == [L2]
    assert r.best_average_cost.not_lowest_count == 1
    assert r.lowest_cost_supplier.not_lowest_lines == []
    assert r.lowest_cost_supplier.not_lowest_count == 0


def test_unmet_and_exceptions_count_and_identical_lines():
    r = run([replace(A, exceptions=None), replace(B, exceptions="  \n")], lines=LINES)
    assert [(v.unmet, v.exceptions_count) for v in r.vendors] == [(0, 0), (1, 0)]
    r = run([replace(A, exceptions="a\n \nb")], lines=LINES)
    assert vendor(r, 1).exceptions_count == 2
    same = {L1: (1, 5, 0)}
    r = run([bid(1, "USD", "0", same), bid(2, "USD", "0", same, 1)], lines=LINES[:1])
    assert r.identical_lines == 1
    r = run([A, B, C])
    assert r.identical_lines == 0


def test_identical_measures_name_what_every_quoting_vendor_shares():
    x = replace(bid(1, "USD", "1", {L1: (1, 10, 1)}, lead=7, met=1), exceptions="a")
    y = bid(2, "USD", "2", {L1: (1, 12, 2)}, 1, lead=7, met=2)
    r = run([x, y], lines=LINES[:1], scores={x.vendor_id: D("1"), y.vendor_id: D("2")})
    # same lead time means the same readiness against the need, so the two go together
    assert r.identical_measures == ["lead_time_days", "meets_need"]
    assert r.identical_rows == 2
    r = run([x], lines=LINES[:1])  # fewer than two quoting vendors: nothing to compare
    assert (r.identical_measures, r.identical_rows) == ([], 0)


def _unit(ccy, unit, qty, rate, rfq="USD"):
    """One vendor, one line; returns the cell."""
    line = LineIn(L1, 1, "One", D(qty), None)
    r = compute(
        currency=rfq,
        lines=[line],
        bids=[bid(1, ccy, "0", {L1: (qty, unit, 0)})],
        requirements=0,
        rates={ccy: rate, rfq: Rate(D(1), "identity", TODAY)},
        current_version=1,
        approved_scope=D("0"),
        comparison_date=TODAY,
        rate_date=TODAY,
        frozen_at=T0,
        bids_since_freeze=0,
    )
    return r.lines[0].cells[0]


def test_converted_unit_price_uses_the_frozen_rate_and_the_rfq_minor_unit():
    # EUR 8.125 x 1.0837 = 8.8050625 -> 8.81 (HALF_UP, 2 places); the keyed price is untouched
    c = _unit("EUR", "8.125", 1, Rate(D("1.0837"), "ECB", TODAY))
    assert (c.unit_price, c.unit_price_converted) == ("8.125", "8.81")
    # JPY RFQ has no minor unit: EUR 8.125 x 150.5 = 1222.8125 -> 1223
    c = _unit("EUR", "8.125", 1, Rate(D("150.5"), "ECB", TODAY), rfq="JPY")
    assert c.unit_price_converted == "1223"


def test_converted_unit_price_rounds_half_up_not_half_even():
    assert _unit("USD", "2.125", 1, Rate(D(1), "identity", TODAY)).unit_price_converted == "2.13"


def test_converted_unit_price_is_not_the_quantized_price_over_quantity():
    # price 10 x 0.0046 = 0.046 -> 0.05; / 10 would give 0.01 where the rule gives 0.00
    c = _unit("USD", "0.0046", 10, Rate(D(1), "identity", TODAY))
    assert c.unit_price_converted == "0.00"
