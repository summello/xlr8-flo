"""Bid comparison: landed cost, one rate per currency, both award bases (SRC-009, SRC-010).

The page computes nothing; every figure here is the server's. A bid is never altered: each
converted component (price, tax, freight share) is quantized once in the RFQ currency, so every
total is a sum of quantized components and the breakdown reconciles to the minor unit.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, cast
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.money import Money, quantize
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.service import rate_for, today
from flo.modules.sourcing.evaluation import assert_may_see_bids, weighted
from flo.modules.sourcing.schemas import BidLate
from flo.modules.sourcing.sealing import visible
from flo.modules.sourcing.service import RfqService

IDENTICAL_MEASURES = (
    "landed_total", "difference_from_lowest", "price", "tax", "freight", "lead_time_days",
    "meets_need", "met", "unmet", "score", "exceptions_count",
)  # fmt: skip
STALE_AFTER = timedelta(days=7)  # decision 117
REFRESHABLE = ("published", "closed", "under_evaluation")


def _s(amount: Decimal | None) -> str | None:
    return None if amount is None else format(amount, "f")


# -- contracts -----------------------------------------------------------------------------


class ConversionNote(BaseModel):
    base: str
    quote: str
    rate: str
    source: str
    effective_date: date
    stale: bool


class Marker(BaseModel):
    vendor_id: UUID
    tied: bool


class VendorFigures(BaseModel):
    vendor_id: UUID
    vendor_name: str
    bid_id: UUID
    currency: str
    sent_at: datetime
    rfq_version: int
    on_current_version: bool
    not_quoted: bool
    full: bool
    price: str | None
    tax: str | None
    freight: str | None
    freight_unallocated: str | None  # bid currency; folded into the total once, converted
    freight_unallocated_converted: str | None
    landed_total: str | None
    difference_from_lowest: str | None
    met: int
    met_of: int
    unmet: int
    lead_time_days: int | None
    ready_by: date | None
    meets_need: bool | None
    score: str | None
    exceptions_count: int
    late: BidLate | None
    conversion: ConversionNote


class Cell(BaseModel):
    vendor_id: UUID
    not_quoted: bool
    unit_price: str | None  # as the vendor keyed it, in the bid currency
    unit_price_converted: str | None  # display only: unit_price at the frozen rate, RFQ currency
    quantity: str | None
    price: str | None
    tax: str | None
    freight_share: str | None
    landed: str | None


class LineRow(BaseModel):
    rfq_line_id: UUID
    line_no: int
    description: str
    quantity: str
    needed_by: date | None
    identical: bool
    lowest: Marker | None
    cells: list[Cell]


class BasisVendor(BaseModel):
    vendor_id: UUID
    total: str
    lines: list[UUID]


class Basis(BaseModel):
    vendor_id: UUID | None
    total: str | None
    partial: bool
    within_scope: bool | None
    over_by: str | None
    uncovered_lines: list[UUID] = []
    to_spare: str | None = None
    vendors: list[BasisVendor] = []
    not_lowest_lines: list[UUID] = []
    not_lowest_count: int = 0


class Insight(BaseModel):
    difference: str
    difference_pct: str | None
    dearer_basis: Literal["lowest_cost_supplier", "best_average_cost"]
    lowest_cost_supplier_vendors: int
    best_average_cost_vendors: int


class Markers(BaseModel):
    lowest: Marker | None
    fastest: Marker | None
    highest_score: Marker | None


class ComparisonRead(BaseModel):
    sealed: Literal[False] = False
    currency: str
    comparison_date: date
    rate_date: date
    frozen_at: datetime
    stale_rate: bool
    bids_since_freeze: int
    approved_scope: str
    vendors: list[VendorFigures]
    lines: list[LineRow]
    identical_lines: int
    identical_measures: list[str]
    identical_rows: int
    markers: Markers
    lowest_cost_supplier: Basis
    best_average_cost: Basis
    insight: Insight | None


class SealedComparisonRead(BaseModel):
    sealed: Literal[True] = True


# -- the engine ----------------------------------------------------------------------------


@dataclass(frozen=True)
class LineIn:
    id: UUID
    line_no: int
    description: str
    quantity: Decimal
    needed_by: date | None


@dataclass(frozen=True)
class Quote:
    quantity: Decimal
    unit_price: Decimal
    tax: Decimal


@dataclass(frozen=True)
class BidIn:
    id: UUID
    vendor_id: UUID
    vendor_name: str
    currency: str
    sent_at: datetime
    rfq_version: int
    freight: Decimal
    lead_time_days: int | None
    met: int
    quotes: dict[UUID, Quote]  # only the lines the vendor quoted
    exceptions: str | None = None
    late: BidLate | None = None


@dataclass(frozen=True)
class Rate:
    rate: Decimal
    source: str
    effective_date: date


@dataclass
class _Priced:
    bid: BidIn
    cells: dict[UUID, tuple[Decimal, Decimal, Decimal]] = field(default_factory=dict)
    unallocated: Decimal | None = None
    unallocated_converted: Decimal = Decimal(0)
    units: dict[UUID, Decimal] = field(default_factory=dict)

    def landed(self, line: UUID) -> Decimal:
        return sum(self.cells[line], Decimal(0))

    @property
    def total(self) -> Decimal:
        return sum((self.landed(k) for k in self.cells), self.unallocated_converted)


def _order(p: _Priced) -> tuple[datetime, UUID]:
    return (p.bid.sent_at, p.bid.id)  # ties: the earlier submission, then the lower id


def _pick(
    candidates: Sequence[tuple[Decimal | int, _Priced]], *, high: bool = False
) -> Marker | None:
    if not candidates:
        return None
    best = (max if high else min)(v for v, _ in candidates)
    tied = sorted((p for v, p in candidates if v == best), key=_order)
    return Marker(vendor_id=tied[0].bid.vendor_id, tied=len(tied) > 1)


def _price(bid: BidIn, lines: list[LineIn], currency: str, rate: Rate) -> _Priced:
    """Freight is shared in the bid currency (whole minor units, pro rata by exact line price),
    then every component is converted and quantized once."""
    priced = _Priced(bid)
    prices = {k: q.unit_price * q.quantity for k, q in bid.quotes.items()}
    fq = quantize(bid.freight, bid.currency)
    ratios = [int(prices.get(line.id, Decimal(0)).scaleb(8)) for line in lines]
    if bid.quotes and not any(ratios):  # every quoted price is 0: split equally over quoted lines
        ratios = [int(line.id in bid.quotes) for line in lines]
    shares = (
        [m.amount for m in Money(fq, bid.currency).allocate(ratios)]
        if any(ratios)
        else [Decimal(0)] * len(lines)
    )
    for line, share in zip(lines, shares, strict=True):
        if line.id in bid.quotes:
            priced.cells[line.id] = (
                quantize(prices[line.id] * rate.rate, currency),
                quantize(bid.quotes[line.id].tax * rate.rate, currency),
                quantize(share * rate.rate, currency),
            )
            priced.units[line.id] = quantize(bid.quotes[line.id].unit_price * rate.rate, currency)
    return priced


def compute(
    *,
    currency: str,
    lines: list[LineIn],
    bids: list[BidIn],
    requirements: int,
    rates: dict[str, Rate],
    current_version: int,
    approved_scope: Decimal,
    comparison_date: date,
    rate_date: date,
    frozen_at: datetime,
    bids_since_freeze: int,
    scores: dict[UUID, Decimal] | None = None,
) -> ComparisonRead:
    priced = [_price(b, lines, currency, rates[b.currency]) for b in bids]
    scores = scores or {}
    quoted = [p for p in priced if p.cells]  # a bid quoting no line is in no basis, no marker
    full = [p for p in quoted if len(p.cells) == len(lines)]
    pool = full or quoted
    lowest_total = min((p.total for p in pool), default=None)
    needed = [line.needed_by for line in lines if line.needed_by]
    stale = any(
        r.source != "identity" and comparison_date - r.effective_date > STALE_AFTER
        for r in (rates[b.currency] for b in bids)
    )

    vendors = []
    for p in priced:
        b, rate = p.bid, rates[p.bid.currency]
        ready = (
            None if b.lead_time_days is None else comparison_date + timedelta(days=b.lead_time_days)
        )
        got = bool(p.cells)
        vendors.append(
            VendorFigures(
                vendor_id=b.vendor_id,
                vendor_name=b.vendor_name,
                bid_id=b.id,
                currency=b.currency,
                sent_at=b.sent_at,
                rfq_version=b.rfq_version,
                on_current_version=b.rfq_version == current_version,
                not_quoted=not got,
                full=p in full,
                price=_s(sum((c[0] for c in p.cells.values()), Decimal(0))) if got else None,
                tax=_s(sum((c[1] for c in p.cells.values()), Decimal(0))) if got else None,
                freight=_s(sum((c[2] for c in p.cells.values()), Decimal(0))) if got else None,
                freight_unallocated=_s(p.unallocated),
                freight_unallocated_converted=_s(p.unallocated_converted)
                if p.unallocated is not None
                else None,
                landed_total=_s(p.total) if got else None,
                difference_from_lowest=_s(p.total - lowest_total)
                if got and lowest_total is not None and p in pool
                else None,
                met=b.met,
                met_of=requirements,
                unmet=requirements - b.met,
                lead_time_days=b.lead_time_days,
                ready_by=ready,
                meets_need=None if ready is None or not needed else ready <= min(needed),
                score=_s(scores.get(b.vendor_id)),
                # ponytail: line count of free text; a structured list replaces it
                exceptions_count=sum(1 for x in (b.exceptions or "").splitlines() if x.strip()),
                late=b.late,
                conversion=ConversionNote(
                    base=b.currency,
                    quote=currency,
                    rate=format(rate.rate, "f"),
                    source=rate.source,
                    effective_date=rate.effective_date,
                    stale=rate.source != "identity"
                    and comparison_date - rate.effective_date > STALE_AFTER,
                ),
            )
        )

    rows, won, covered, uncovered = [], [], [], []
    cheapest: dict[UUID, Decimal] = {}
    for line in lines:
        quotes = [(p.landed(line.id), p) for p in quoted if line.id in p.cells]
        lowest = _pick(quotes)
        if quotes:
            cheapest[line.id] = min(v for v, _ in quotes)
        if lowest is None:
            uncovered.append(line.id)
        else:
            covered.append(line)
            won.append(next(p for _, p in quotes if p.bid.vendor_id == lowest.vendor_id))
        cells = [
            Cell(
                vendor_id=p.bid.vendor_id,
                not_quoted=line.id not in p.cells,
                unit_price=_s(p.bid.quotes[line.id].unit_price) if line.id in p.cells else None,
                quantity=_s(p.bid.quotes[line.id].quantity) if line.id in p.cells else None,
                unit_price_converted=_s(p.units[line.id]) if line.id in p.cells else None,
                price=_s(p.cells[line.id][0]) if line.id in p.cells else None,
                tax=_s(p.cells[line.id][1]) if line.id in p.cells else None,
                freight_share=_s(p.cells[line.id][2]) if line.id in p.cells else None,
                landed=_s(p.landed(line.id)) if line.id in p.cells else None,
            )
            for p in priced
        ]
        rows.append(
            LineRow(
                rfq_line_id=line.id,
                line_no=line.line_no,
                description=line.description,
                quantity=cast(str, _s(line.quantity)),
                needed_by=line.needed_by,
                identical=len({(c.not_quoted, c.landed) for c in cells}) <= 1,
                lowest=lowest,
                cells=cells,
            )
        )

    def basis(
        vendor_id: UUID | None,
        total: Decimal | None,
        partial: bool,
        shares: list[tuple[_Priced, Decimal, list[UUID]]],
    ) -> Basis:
        zero = quantize(Decimal(0), currency)
        over = None if total is None else max(zero, total - approved_scope)
        # the selected vendor's line is not lowest only when strictly dearer than the cheapest
        dearer = [k for p, _, ks in shares for k in ks if p.landed(k) > cheapest[k]]
        return Basis(
            vendor_id=vendor_id,
            total=_s(total),
            partial=partial,
            within_scope=None if over is None else over == 0,
            over_by=_s(over),
            to_spare=None if total is None else _s(max(zero, approved_scope - total)),
            vendors=[
                BasisVendor(vendor_id=p.bid.vendor_id, total=cast(str, _s(t)), lines=ks)
                for p, t, ks in shares
            ],
            not_lowest_lines=dearer,
            not_lowest_count=len(dearer),
        )

    lcs_total = (
        sum((w.landed(line.id) for w, line in zip(won, covered, strict=True)), Decimal(0))
        if won
        else None
    )
    wins: dict[UUID, list[UUID]] = {}  # bid id -> the lines it wins, in line order
    for w, line in zip(won, covered, strict=True):
        wins.setdefault(w.bid.id, []).append(line.id)
    lcs_shares = [
        (p, sum((p.landed(k) for k in wins[p.bid.id]), Decimal(0)), wins[p.bid.id])
        for p in sorted(quoted, key=_order)
        if p.bid.id in wins
    ]
    lcs = basis(None, lcs_total, bool(uncovered) and lcs_total is not None, lcs_shares)
    lcs.uncovered_lines = uncovered
    best = _pick([(p.total, p) for p in pool])
    best_priced = next((p for p in pool if best and p.bid.vendor_id == best.vendor_id), None)
    best_id = best_priced.bid.vendor_id if best_priced else None
    best_total = best_priced.total if best_priced else None
    bac = basis(
        best_id,
        best_total,
        bool(best_priced) and not full,
        [
            (
                best_priced,
                best_priced.total,
                [k for k in (ln.id for ln in lines) if k in best_priced.cells],
            )
        ]
        if best_priced
        else [],
    )

    insight = None
    if lcs_total is not None and best_priced is not None:
        low, high = sorted([lcs_total, best_priced.total])
        pct = (high - low) / low * 100 if low else None
        insight = Insight(
            difference=cast(str, _s(high - low)),
            difference_pct=None
            if pct is None
            else _s(pct.quantize(Decimal("0.01"), ROUND_HALF_UP)),
            dearer_basis="lowest_cost_supplier"
            if lcs_total > best_priced.total
            else "best_average_cost",
            lowest_cost_supplier_vendors=len({w.bid.vendor_id for w in won}),
            best_average_cost_vendors=1,
        )

    offered = [v for v in vendors if not v.not_quoted]
    same = (
        [m for m in IDENTICAL_MEASURES if len({getattr(v, m) for v in offered}) == 1]
        if len(offered) > 1
        else []
    )

    return ComparisonRead(
        currency=currency,
        comparison_date=comparison_date,
        rate_date=rate_date,
        frozen_at=frozen_at,
        stale_rate=stale,
        bids_since_freeze=bids_since_freeze,
        approved_scope=cast(str, _s(approved_scope)),
        vendors=vendors,
        lines=rows,
        identical_lines=sum(row.identical for row in rows),
        identical_measures=same,
        identical_rows=len(same),
        markers=Markers(
            lowest=_pick([(p.total, p) for p in full]),
            fastest=_pick(
                [(p.bid.lead_time_days, p) for p in quoted if p.bid.lead_time_days is not None]
            ),
            highest_score=_pick(
                [(scores[p.bid.vendor_id], p) for p in quoted if p.bid.vendor_id in scores],
                high=True,
            ),
        ),
        lowest_cost_supplier=lcs,
        best_average_cost=bac,
        insight=insight,
    )


# -- the service ---------------------------------------------------------------------------


class ComparisonService(RfqService):
    def _head(self, rfq_id: UUID, *, lock: bool = False) -> tuple[Any, ...]:
        row = self.repo.execute(
            "SELECT status, deadline_at, now(), currency, version, "
            "(deadline_at AT TIME ZONE deadline_tz)::date FROM rfq "
            "WHERE org_id = %(org_id)s AND id = %(id)s" + (" FOR UPDATE" if lock else ""),
            {"id": rfq_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return row

    def _all(self, query: str, params: dict[str, object]) -> list[tuple[Any, ...]]:
        return self.repo.execute(query, params).fetchall()

    def _current_bids(self, rfq_id: UUID) -> list[tuple[Any, ...]]:
        return self.repo.execute(
            "SELECT b.id, b.vendor_id, i.vendor_snapshot->>'trading_name', b.currency, b.sent_at, "
            "b.rfq_version, b.freight, b.lead_time_days, b.submitted_at, b.exceptions, "
            "b.late_accepted_at, b.late_reason, p.display_name, p.email FROM bid b "
            "LEFT JOIN identity p ON p.id = b.late_accepted_by "
            "JOIN rfq_invitee i ON i.rfq_id = b.rfq_id AND i.vendor_id = b.vendor_id "
            "WHERE b.org_id = %(org_id)s AND b.rfq_id = %(rfq)s AND b.status = 'submitted' "
            "AND NOT EXISTS (SELECT 1 FROM bid r WHERE r.org_id = b.org_id "
            "AND r.replaces_bid_id = b.id AND r.status = 'submitted') ORDER BY b.sent_at, b.id",
            {"rfq": rfq_id},
        ).fetchall()

    def _take(self, rfq: str, currencies: set[str], on: date) -> dict[str, dict[str, str]]:
        taken = {}
        for ccy in sorted(currencies):
            r = rate_for(self.connection, ccy, rfq, on)
            taken[ccy] = {
                "rate": r.rate,
                "source": r.source,
                "effective_date": r.effective_date.isoformat(),
            }
        return taken

    def _frozen(
        self, rfq_id: UUID, rfq_currency: str, rate_date: date, currencies: set[str]
    ) -> tuple[date, datetime, dict[str, dict[str, str]]]:
        """The cached rates, created on the first unsealed read at the deadline's date and
        topped up (under lock) for a currency a later bid brings. Never moves the date."""
        key = {"rfq": rfq_id}
        lock = (
            "SELECT rate_date, frozen_at, rates FROM rfq_comparison "
            "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s FOR UPDATE"
        )
        row = self.repo.execute(lock, key).fetchone()
        if row is None:  # a deterministic cache: the same figures without it
            self.repo.execute(
                "INSERT INTO rfq_comparison (org_id, rfq_id, rate_date, frozen_at, rates) "
                "VALUES (%(org_id)s, %(rfq)s, %(date)s, now(), %(rates)s) "
                "ON CONFLICT (org_id, rfq_id) DO NOTHING",
                {
                    **key,
                    "date": rate_date,
                    "rates": Jsonb(self._take(rfq_currency, currencies, rate_date)),
                },
            )
            row = self.repo.execute(lock, key).fetchone()
        assert row is not None
        rates = cast(dict[str, dict[str, str]], row[2])
        missing = currencies - rates.keys()
        if missing:
            rates = {**rates, **self._take(rfq_currency, missing, cast(date, row[0]))}
            self.repo.execute(
                "UPDATE rfq_comparison SET rates = %(rates)s, row_version = row_version + 1 "
                "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s",
                {**key, "rates": Jsonb(rates)},
            )
        return cast(date, row[0]), cast(datetime, row[1]), rates

    def _build(self, rfq_id: UUID, head: tuple[Any, ...]) -> ComparisonRead:
        currency, version = cast(str, head[3]).strip(), cast(int, head[4])
        bid_rows = self._current_bids(rfq_id)
        rate_date, frozen_at, raw = self._frozen(
            rfq_id, currency, cast(date, head[5]), {cast(str, r[3]) for r in bid_rows}
        )
        rates = {
            k: Rate(Decimal(v["rate"]), v["source"], date.fromisoformat(v["effective_date"]))
            for k, v in raw.items()
        }
        lines = [
            LineIn(r[0], r[1], r[2], r[3], r[4])
            for r in self._all(
                "SELECT id, line_no, description, quantity, needed_by FROM rfq_line "
                "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s ORDER BY line_no",
                {"rfq": rfq_id},
            )
        ]
        ids = [r[0] for r in bid_rows]
        quotes: dict[UUID, dict[UUID, Quote]] = {i: {} for i in ids}
        for r in self._all(
            "SELECT bid_id, rfq_line_id, quantity, unit_price, tax FROM bid_line "
            "WHERE org_id = %(org_id)s AND bid_id = ANY(%(ids)s) AND quoted",
            {"ids": ids},
        ):
            quotes[r[0]][r[1]] = Quote(r[2], r[3], r[4])
        met = dict(
            self._all(
                "SELECT a.bid_id, count(*) FROM bid_requirement a "
                "JOIN rfq_requirement q ON q.id = a.requirement_id AND NOT q.waived "
                "WHERE a.org_id = %(org_id)s AND a.bid_id = ANY(%(ids)s) AND a.met "
                "GROUP BY a.bid_id",
                {"ids": ids},
            )
        )
        requirements = cast(
            int,
            self.repo.execute(
                "SELECT count(*) FROM rfq_requirement "
                "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s AND NOT waived",
                {"rfq": rfq_id},
            ).fetchone()[0],  # type: ignore[index]
        )
        scope = sum(
            (
                quantize(cast(Decimal, r[0]) * cast(Decimal, r[1]) / cast(Decimal, r[2]), currency)
                for r in self._all(
                    "SELECT q.amount, l.quantity, q.quantity FROM rfq_line l "
                    "JOIN requisition_line q ON q.id = l.requisition_line_id "
                    "WHERE l.org_id = %(org_id)s AND l.rfq_id = %(rfq)s",
                    {"rfq": rfq_id},
                )
            ),
            Decimal(0),
        )
        return compute(
            currency=currency,
            lines=lines,
            bids=[
                BidIn(
                    r[0],
                    r[1],
                    r[2],
                    r[3],
                    r[4],
                    r[5],
                    r[6],
                    r[7],
                    met.get(r[0], 0),
                    quotes[r[0]],
                    r[9],
                    BidLate(accepted_by_name=r[12], accepted_by_email=r[13], at=r[10], reason=r[11])
                    if r[10] is not None
                    else None,
                )
                for r in bid_rows
            ],
            requirements=requirements,
            rates=rates,
            current_version=version,
            approved_scope=scope,
            comparison_date=today(),
            rate_date=rate_date,
            frozen_at=frozen_at,
            bids_since_freeze=sum(1 for r in bid_rows if r[8] > frozen_at),
            scores=weighted(self.repo, rfq_id),
        )

    def comparison(self, rfq_id: UUID) -> ComparisonRead | SealedComparisonRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            head = self._head(rfq_id)
            if not visible(head[1], head[2]):
                return SealedComparisonRead()
            assert_may_see_bids(self.repo, rfq_id, self.actor_id)
            return self._build(rfq_id, head)

    def refresh_rate(self, rfq_id: UUID) -> ComparisonRead:
        """Re-freeze every currency at today's rate (decision 117). The RFQ row is locked first,
        then the comparison row."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            head = self._head(rfq_id, lock=True)
            status = head[0]
            if status == "cancelled":
                raise conflict("rfq_cancelled", "This RFQ is cancelled. Its rates cannot change.")
            if status == "awarded":
                raise conflict("rfq_awarded", "This RFQ is awarded. Its figures are fixed.")
            if status == "draft" or not visible(head[1], head[2]):
                raise conflict(
                    "comparison_sealed", "Rates can be refreshed once the bidding stop time passes."
                )
            assert status in REFRESHABLE
            assert_may_see_bids(self.repo, rfq_id, self.actor_id)
            currency, on = cast(str, head[3]).strip(), today()
            currencies = {cast(str, r[3]) for r in self._current_bids(rfq_id)}
            taken = self._take(currency, currencies, on)
            old = self.repo.execute(
                "SELECT rate_date FROM rfq_comparison "
                "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s FOR UPDATE",
                {"rfq": rfq_id},
            ).fetchone()
            self.repo.execute(
                "INSERT INTO rfq_comparison (org_id, rfq_id, rate_date, frozen_at, rates) "
                "VALUES (%(org_id)s, %(rfq)s, %(date)s, now(), %(rates)s) "
                "ON CONFLICT (org_id, rfq_id) DO UPDATE SET rate_date = EXCLUDED.rate_date, "
                "frozen_at = EXCLUDED.frozen_at, rates = EXCLUDED.rates, "
                "row_version = rfq_comparison.row_version + 1",
                {"rfq": rfq_id, "date": on, "rates": Jsonb(taken)},
            )
            self._audit(
                "rfq.comparison.refresh_rate",
                rfq_id,
                {"rate_date": cast(date, old[0]).isoformat() if old else None},
                {"rate_date": on.isoformat()},
            )
            return self._build(rfq_id, head)
