"""Awards: split by line and quantity, justification, the scope and funds guard (E12-S08).

An award line prices a vendor's bid line for the quantity awarded: tax and freight share are
prorated by awarded / quoted quantity and quantized once in the vendor currency, then converted
at the rate frozen on the award. The *budget amount* is the part the org's settings let consume
budget (decision 211); the scope and funds checks and the release use it, never the full figure.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError, conflict
from flo.kernel.money import Money, quantize
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.schemas import OutcomeContext
from flo.modules.approvals.service import require_workflow_for, start
from flo.modules.identity.service import AuthorizationConnection, AuthorizationResolver
from flo.modules.org.service import NumberingService, OrgService, today
from flo.modules.projects.service import generated_for
from flo.modules.requisitions.schemas import LinkAccess, LinkedNode, Tone
from flo.modules.requisitions.service import (
    assert_current_upstream,
    award_funding,
    award_lines,
    mark_awarded,
    mark_unawarded,
    register_link_provider,
    release_unawarded,
    restore_unawarded,
    sourcing_basis,
)
from flo.modules.sourcing.comparison import ComparisonRead, ComparisonService, SealedComparisonRead
from flo.modules.sourcing.evaluation import weighted
from flo.modules.sourcing.schemas import ReasonBody
from flo.modules.sourcing.service import SUPERSEDE_GUARDS

type Conn = psycopg.Connection[tuple[object, ...]]
type Basis = Literal["lowest_cost_supplier", "best_average_cost", "manual"]
LIVE = ("draft", "approval_pending", "approved")


def _decimal(value: object) -> object:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,14}(?:\.[0-9]{1,4})?", value):
        raise ValueError("Enter a quantity as a decimal string with at most 4 decimals.")
    if Decimal(value) <= 0:
        raise ValueError("Enter a quantity above zero.")
    return value


def _text(value: object) -> object:
    return (value.strip() or None) if isinstance(value, str) else value


# -- contracts -----------------------------------------------------------------------------


class AwardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basis: Basis


class AwardPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rationale: str | None = Field(default=None, max_length=2000)

    _strip = field_validator("rationale", mode="before")(lambda cls, v: _text(v))


class AwardLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rfq_line_id: UUID
    vendor_id: UUID
    quantity: str
    justification: str | None = Field(default=None, max_length=500)
    tie_override_reason: str | None = Field(default=None, max_length=500)

    _quantity = field_validator("quantity", mode="before", json_schema_input_type=str)(
        lambda cls, v: _decimal(v)
    )
    _strip = field_validator("justification", "tie_override_reason", mode="before")(
        lambda cls, v: _text(v)
    )


class AwardLinesPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[AwardLineIn] = Field(max_length=500)


class AwardLineRead(BaseModel):
    rfq_line_id: UUID
    line_no: int
    vendor_id: UUID
    bid_id: UUID
    quantity: str
    unit_price: str
    currency: str
    amount_vendor: str
    amount_rfq: str
    budget_amount: str
    not_lowest: bool
    justification: str | None
    tie_override_reason: str | None


class RequisitionCheck(BaseModel):
    requisition_id: UUID
    requisition: str
    scope: str
    funds: str
    award: str
    over_scope_by: str
    over_funds_by: str
    release: str  # scope - award when positive, else 0: what approval returns to main_project
    main_project: str  # number of the requisition's project; '' when it has none


class AwardRead(BaseModel):
    id: UUID
    number: str
    rfq_id: UUID
    basis: Basis
    status: Literal["draft", "approval_pending", "approved", "superseded", "cancelled"]
    rationale: str | None
    version: int
    currency: str
    rates: dict[str, Any]
    evaluator_results: dict[str, Any] | None
    exceptions: list[Any] | None
    created_by: UUID
    created_at: datetime
    submitted_at: datetime | None
    approved_at: datetime | None
    total: str
    budget_total: str
    lines: list[AwardLineRead]
    checks: list[RequisitionCheck]
    justification_required: bool = False


# -- the figures ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    quantity: Decimal
    unit_price: Decimal
    tax: Decimal
    freight_share: Decimal


@dataclass(frozen=True)
class Row:
    rfq_line_id: UUID
    line_no: int
    vendor_id: UUID
    bid_id: UUID
    requisition_id: UUID
    requisition_line_id: UUID
    quantity: Decimal
    unit_price: Decimal
    currency: str
    amount_vendor: Decimal
    amount_rfq: Decimal
    not_lowest: bool
    justification: str | None
    tie_override_reason: str | None


def amounts(
    q: Quote,
    qty: Decimal,
    vendor_ccy: str,
    rate: Decimal,
    rfq_ccy: str,
    *,
    tax_on: bool,
    freight_on: bool,
) -> tuple[Decimal, Decimal, Decimal]:
    """(full in the vendor currency, full in the RFQ currency, budget in the RFQ currency).

    Tax and freight share follow the awarded / quoted quantity; each total is quantized once.
    """
    price, tax, freight = (
        q.unit_price * qty,
        q.tax * qty / q.quantity,
        q.freight_share * qty / q.quantity,
    )
    full = quantize(price + tax + freight, vendor_ccy)
    budget = quantize(price + (tax if tax_on else 0) + (freight if freight_on else 0), vendor_ccy)
    return full, quantize(full * rate, rfq_ccy), quantize(budget * rate, rfq_ccy)


def _problem(problems: list[ProblemFieldError], detail: str, problem: str) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail=detail,
        errors=tuple(problems),
        checks={"problem": problem},
    )


class AwardService(ComparisonService):
    def _audit_award(
        self, action: str, id: UUID, before: dict[str, object] | None, after: dict[str, object]
    ) -> None:
        audit_event(
            cast(AuditConnection, self.connection),
            self.scope,
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type="award",
            target_id=id,
            outcome=Outcome.SUCCESS,
            before=before,
            after=after,
        )

    # -- reads of the pieces ---------------------------------------------------------------

    def _rfq(self, rfq_id: UUID, *, lock: bool = False) -> tuple[str, UUID, str, str]:
        row = self.repo.execute(
            "SELECT status, unit_id, currency, number FROM rfq "
            "WHERE org_id = %(org_id)s AND id = %(id)s" + (" FOR UPDATE" if lock else ""),
            {"id": rfq_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return cast(str, row[0]), cast(UUID, row[1]), cast(str, row[2]).strip(), cast(str, row[3])

    def _award(self, rfq_id: UUID, *, lock: bool = False) -> dict[str, Any]:
        """The RFQ's live award, else its latest; 404 when it has none."""
        cur = self.repo.execute(
            "SELECT id, number, rfq_id, basis, status, rationale, version, rates, "
            "evaluator_results, exceptions, created_by, created_at, submitted_at, approved_at "
            "FROM award WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
            "ORDER BY (status IN ('draft','approval_pending','approved')) DESC, created_at DESC "
            "LIMIT 1" + (" FOR UPDATE" if lock else ""),
            {"rfq": rfq_id},
        )
        row = cur.fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return dict(zip((d.name for d in cur.description or []), row, strict=True))

    def award_by_id(self, award_id: UUID, *, lock: bool = False) -> dict[str, Any]:
        cur = self.repo.execute(
            "SELECT id, number, rfq_id, basis, status, rationale, version, rates, "
            "evaluator_results, exceptions, created_by, created_at, submitted_at, approved_at "
            "FROM award WHERE org_id = %(org_id)s AND id = %(id)s"
            + (" FOR UPDATE" if lock else ""),
            {"id": award_id},
        )
        row = cur.fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return dict(zip((d.name for d in cur.description or []), row, strict=True))

    def _rows(self, award_id: UUID) -> list[Row]:
        rows: list[tuple[Any, ...]] = self.repo.execute(
            "SELECT al.rfq_line_id, rl.line_no, al.vendor_id, al.bid_id, rl.requisition_id, "
            "rl.requisition_line_id, al.quantity, al.unit_price, al.currency, "
            "al.amount_vendor, al.amount_rfq, al.not_lowest, al.justification, "
            "al.tie_override_reason FROM award_line al "
            "JOIN rfq_line rl ON rl.id = al.rfq_line_id "
            "WHERE al.org_id = %(org_id)s AND al.award_id = %(id)s "
            "ORDER BY rl.line_no, al.vendor_id",
            {"id": award_id},
        ).fetchall()
        return [Row(*r) for r in rows]

    def _quotes(self, bid_ids: list[UUID]) -> dict[tuple[UUID, UUID], Quote]:
        """Each quoted bid line with its freight share: the bid freight is split in the bid
        currency, pro rata by exact line price, in whole minor units (as the comparison does)."""
        by_bid: dict[UUID, list[tuple[Any, ...]]] = defaultdict(list)
        found: list[tuple[Any, ...]] = self.repo.execute(
            "SELECT l.bid_id, l.rfq_line_id, l.quantity, l.unit_price, l.tax, b.freight, "
            "b.currency FROM bid_line l JOIN bid b ON b.org_id = l.org_id AND b.id = l.bid_id "
            "JOIN rfq_line rl ON rl.id = l.rfq_line_id "
            "WHERE l.org_id = %(org_id)s AND l.bid_id = ANY(%(ids)s) AND l.quoted "
            "ORDER BY l.bid_id, rl.line_no",
            {"ids": bid_ids},
        ).fetchall()
        for r in found:
            by_bid[cast(UUID, r[0])].append(r)
        out: dict[tuple[UUID, UUID], Quote] = {}
        for bid_id, lines in by_bid.items():
            ratios = [int((r[2] * r[3]).scaleb(8)) for r in lines]
            freight = quantize(lines[0][5], lines[0][6])
            shares = (
                [m.amount for m in Money(freight, lines[0][6]).allocate(ratios)]
                if any(ratios)
                else [Decimal(0)] * len(lines)
            )
            for line, share in zip(lines, shares, strict=True):
                out[(bid_id, line[1])] = Quote(line[2], line[3], line[4], share)
        return out

    def _flags(self, unit_id: UUID) -> tuple[bool, bool]:
        org = OrgService(self.connection, self.scope)
        return (
            org.effective_setting(unit_id, "tax.consumes_budget").value is True,
            org.effective_setting(unit_id, "freight.consumes_budget").value is True,
        )

    def _budget(
        self, rows: list[Row], rates: dict[str, Any], rfq_ccy: str, unit_id: UUID
    ) -> list[Decimal]:
        """The budget amount of each row, recomputed from the bid: settings may have moved."""
        tax_on, freight_on = self._flags(unit_id)
        quotes = self._quotes(sorted({r.bid_id for r in rows}))
        return [
            amounts(
                quotes[(r.bid_id, r.rfq_line_id)],
                r.quantity,
                r.currency,
                Decimal(rates[r.currency]["rate"]),
                rfq_ccy,
                tax_on=tax_on,
                freight_on=freight_on,
            )[2]
            for r in rows
        ]

    def figures(
        self,
        award: dict[str, Any],
        rows: list[Row],
        rfq_ccy: str,
        unit_id: UUID,
        *,
        lock: bool = False,
    ) -> tuple[list[Decimal], list[RequisitionCheck]]:
        """Per row budget amounts, and the scope and funds standing of each requisition.

        scope: the approved amount of the lines awarded, pro rata by the quantity awarded here.
        funds: what the requisition's reservation still holds. `lock` takes the project_balance
        locks first, so the figures are read under them.
        """
        budgets = self._budget(rows, award["rates"], rfq_ccy, unit_id)
        by_requisition: dict[UUID, Decimal] = defaultdict(Decimal)
        for r, b in zip(rows, budgets, strict=True):
            by_requisition[r.requisition_id] += b
        funds = award_funding(self.connection, self.scope, list(by_requisition), lock=lock)
        scope: dict[UUID, Decimal] = defaultdict(Decimal)
        numbers: dict[UUID, str] = {}
        mains: dict[UUID, str] = {}
        awarded: list[tuple[Any, ...]] = self.repo.execute(
            "SELECT rl.requisition_id, q.number, ql.amount, ql.quantity, sum(al.quantity), "
            "coalesce(p.number, '') "
            "FROM award_line al JOIN rfq_line rl ON rl.id = al.rfq_line_id "
            "JOIN requisition q ON q.org_id = rl.org_id AND q.id = rl.requisition_id "
            "JOIN requisition_line ql ON ql.id = rl.requisition_line_id "
            "LEFT JOIN project p ON p.org_id = q.org_id AND p.id = q.project_id "
            "WHERE al.org_id = %(org_id)s AND al.award_id = %(id)s "
            "GROUP BY rl.requisition_id, q.number, rl.requisition_line_id, ql.amount, ql.quantity, "
            "p.number",
            {"id": award["id"]},
        ).fetchall()
        for a in awarded:
            scope[cast(UUID, a[0])] += quantize(a[2] * a[4] / a[3], rfq_ccy)
            numbers[cast(UUID, a[0])] = cast(str, a[1])
            mains[cast(UUID, a[0])] = cast(str, a[5])
        zero = quantize(Decimal(0), rfq_ccy)
        checks = [
            RequisitionCheck(
                requisition_id=i,
                requisition=numbers[i],
                scope=format(scope[i], "f"),
                funds=format(quantize(funds[i], rfq_ccy), "f"),
                award=format(by_requisition[i], "f"),
                over_scope_by=format(max(zero, by_requisition[i] - scope[i]), "f"),
                over_funds_by=format(
                    max(zero, quantize(by_requisition[i] - funds[i], rfq_ccy)), "f"
                ),
                release=format(max(zero, scope[i] - by_requisition[i]), "f"),
                main_project=mains[i],
            )
            for i in sorted(by_requisition, key=lambda i: numbers[i])
        ]
        return budgets, checks

    @staticmethod
    def assert_within(checks: list[RequisitionCheck]) -> None:
        """SRC-016: over scope or over funds is a 409 with the figures; nothing is disabled."""
        for c in checks:
            if Decimal(c.over_scope_by) or Decimal(c.over_funds_by):
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=f"The award is over the approved scope or the reserved funds of "
                    f"{c.requisition}. Change the selection or amend the requisition.",
                    checks={
                        "problem": "over_scope",
                        "requisition": c.requisition,
                        "scope": c.scope,
                        "funds": c.funds,
                        "award": c.award,
                        "over_scope_by": c.over_scope_by,
                        "over_funds_by": c.over_funds_by,
                    },
                )

    def _standing(self, rfq_id: UUID) -> tuple[ComparisonRead, dict[UUID, tuple[set[UUID], UUID]]]:
        """Per RFQ line: the vendors tied at the lowest landed cost, and the earliest of them."""
        cmp = self.comparison(rfq_id)
        if isinstance(cmp, SealedComparisonRead):
            raise conflict("comparison_sealed", "Bids are sealed until the bidding stop time.")
        out: dict[UUID, tuple[set[UUID], UUID]] = {}
        for line in cmp.lines:
            landed = {c.vendor_id: Decimal(c.landed) for c in line.cells if c.landed is not None}
            if line.lowest is not None:
                low = min(landed.values())
                out[line.rfq_line_id] = (
                    {v for v, x in landed.items() if x == low},
                    line.lowest.vendor_id,
                )
        return cmp, out

    # -- the read --------------------------------------------------------------------------

    def read(self, rfq_id: UUID) -> AwardRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            _, unit_id, ccy, _ = self._rfq(rfq_id)
            return self._read(self._award(rfq_id), ccy, unit_id)

    def _read(
        self, award: dict[str, Any], ccy: str, unit_id: UUID, *, lock: bool = False
    ) -> AwardRead:
        rows = self._rows(award["id"])
        budgets, checks = self.figures(award, rows, ccy, unit_id, lock=lock)
        return AwardRead(
            **{k: award[k] for k in AwardRead.model_fields if k in award},
            currency=ccy,
            total=format(quantize(sum((r.amount_rfq for r in rows), Decimal(0)), ccy), "f"),
            budget_total=format(sum(budgets, Decimal(0)), "f"),
            lines=[
                AwardLineRead(
                    rfq_line_id=r.rfq_line_id,
                    line_no=r.line_no,
                    vendor_id=r.vendor_id,
                    bid_id=r.bid_id,
                    quantity=format(r.quantity, "f"),
                    unit_price=format(r.unit_price, "f"),
                    currency=r.currency,
                    amount_vendor=format(quantize(r.amount_vendor, r.currency), "f"),
                    amount_rfq=format(quantize(r.amount_rfq, ccy), "f"),
                    budget_amount=format(b, "f"),
                    not_lowest=r.not_lowest,
                    justification=r.justification,
                    tie_override_reason=r.tie_override_reason,
                )
                for r, b in zip(rows, budgets, strict=True)
            ],
            checks=checks,
            justification_required=OrgService(self.connection, self.scope)
            .effective_setting(unit_id, "sourcing.require_non_lowest_justification")
            .value
            is True,
        )

    # -- writes ----------------------------------------------------------------------------

    def draft(self, rfq_id: UUID, body: AwardCreate) -> AwardRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            status, _, _, _ = self._rfq(rfq_id, lock=True)
            if status != "under_evaluation":
                raise conflict(
                    "invalid_transition",
                    "Only an RFQ under evaluation can be awarded. Reload it and retry.",
                )
            live = self.repo.execute(
                "SELECT 1 FROM award WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                "AND status IN ('draft','approval_pending','approved')",
                {"rfq": rfq_id},
            ).fetchone()
            if live:
                raise conflict("award_exists", "This RFQ already has an award. Reload it.")
            cmp, standing = self._standing(rfq_id)
            rates = {
                v.currency: {
                    "rate": v.conversion.rate,
                    "source": v.conversion.source,
                    "effective_date": v.conversion.effective_date.isoformat(),
                }
                for v in cmp.vendors
            }
            id = uuid4()
            number = NumberingService(self.settings or Settings(), self.scope).allocate(
                self.scope.org_id, "award", today()
            )
            self.repo.execute(
                "INSERT INTO award (id, org_id, number, rfq_id, basis, rates, created_by) "
                "VALUES (%(id)s, %(org_id)s, %(number)s, %(rfq)s, %(basis)s, %(rates)s, %(by)s)",
                {
                    "id": id,
                    "number": number,
                    "rfq": rfq_id,
                    "basis": body.basis,
                    "rates": Jsonb(rates),
                    "by": self.actor_id,
                },
            )
            picks: list[AwardLineIn] = []
            for line in cmp.lines:
                if body.basis == "lowest_cost_supplier" and line.lowest is not None:
                    vendor: UUID | None = line.lowest.vendor_id
                elif body.basis == "best_average_cost":
                    vendor = cmp.best_average_cost.vendor_id
                else:
                    continue
                cell = next(
                    (c for c in line.cells if c.vendor_id == vendor and not c.not_quoted), None
                )
                if cell is not None and vendor is not None:
                    qty = min(Decimal(line.quantity), Decimal(cast(str, cell.quantity)))
                    picks.append(
                        AwardLineIn(
                            rfq_line_id=line.rfq_line_id,
                            vendor_id=vendor,
                            quantity=format(qty, "f"),
                        )
                    )
            self._store(self.award_by_id(id), picks, cmp, standing)
            self._audit_award("award.create", id, None, {"basis": body.basis, "number": number})
        return self.read(rfq_id)

    def _store(
        self,
        award: dict[str, Any],
        picks: list[AwardLineIn],
        cmp: ComparisonRead,
        standing: dict[UUID, tuple[set[UUID], UUID]],
    ) -> None:
        """Replace the award's lines; 422 lists every problem at once (SRC-012, REQ-014)."""
        rfq_id, rates = cast(UUID, award["rfq_id"]), award["rates"]
        _, unit_id, rfq_ccy, _ = self._rfq(rfq_id)
        rfq_qty = {
            cast(UUID, r[0]): (cast(Decimal, r[1]), cast(UUID, r[2]), cast(UUID, r[3]))
            for r in self.repo.execute(
                "SELECT id, quantity, requisition_id, requisition_line_id FROM rfq_line "
                "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s",
                {"rfq": rfq_id},
            ).fetchall()
        }
        bids = {
            cast(UUID, r[0]): cast(UUID, r[1])
            for r in self.repo.execute(
                "SELECT b.vendor_id, b.id FROM bid b WHERE b.org_id = %(org_id)s "
                "AND b.rfq_id = %(rfq)s AND b.status = 'submitted' AND NOT EXISTS ("
                "SELECT 1 FROM bid r WHERE r.org_id = b.org_id AND r.replaces_bid_id = b.id "
                "AND r.status = 'submitted')",
                {"rfq": rfq_id},
            ).fetchall()
        }
        quotes = self._quotes(sorted(bids.values()))
        tax_on, freight_on = self._flags(unit_id)
        problems: list[ProblemFieldError] = []
        totals: dict[UUID, Decimal] = defaultdict(Decimal)
        inserts: list[tuple[Any, ...]] = []
        seen: set[tuple[UUID, UUID]] = set()
        for p in picks:
            field = f"lines/{p.rfq_line_id}/{p.vendor_id}"
            if (p.rfq_line_id, p.vendor_id) in seen:
                problems.append(
                    ProblemFieldError(field=field, message="Give each line and vendor once.")
                )
                continue
            seen.add((p.rfq_line_id, p.vendor_id))
            qty = Decimal(p.quantity)
            bid_id = bids.get(p.vendor_id)
            quote = quotes.get((bid_id, p.rfq_line_id)) if bid_id else None
            if p.rfq_line_id not in rfq_qty:
                problems.append(
                    ProblemFieldError(field=field, message="This line is not on the RFQ.")
                )
            elif quote is None or bid_id is None:
                problems.append(
                    ProblemFieldError(field=field, message="The vendor did not quote this line.")
                )
            elif qty > quote.quantity:
                problems.append(
                    ProblemFieldError(field=field, message=f"The vendor quoted {quote.quantity:f}.")
                )
            elif (
                p.vendor_id not in {v.vendor_id for v in cmp.vendors}
                or (
                    vendor_ccy := next(
                        v.currency for v in cmp.vendors if v.vendor_id == p.vendor_id
                    )
                )
                not in rates
            ):
                problems.append(
                    ProblemFieldError(field=field, message="No rate is frozen for this vendor.")
                )
            else:
                totals[p.rfq_line_id] += qty
                full_v, full_r, _ = amounts(
                    quote, qty, vendor_ccy, Decimal(rates[vendor_ccy]["rate"]), rfq_ccy,
                    tax_on=tax_on, freight_on=freight_on,
                )  # fmt: skip
                tied, _first = standing.get(p.rfq_line_id, (set(), None))
                inserts.append(
                    (
                        p.rfq_line_id, p.vendor_id, bid_id, qty, quote.unit_price, vendor_ccy,
                        full_v, full_r, p.vendor_id not in tied,
                        p.justification, p.tie_override_reason,
                    )
                )  # fmt: skip
        problems += [
            ProblemFieldError(
                field=f"lines/{line_id}",
                message=f"{total:f} awarded, the RFQ line is for {rfq_qty[line_id][0]:f}.",
            )
            for line_id, total in totals.items()
            if total > rfq_qty[line_id][0]
        ]
        if problems:
            raise _problem(problems, "Some award lines cannot be saved.", "invalid_award_lines")
        self.repo.execute(
            "DELETE FROM award_line WHERE org_id = %(org_id)s AND award_id = %(id)s",
            {"id": award["id"]},
        )
        for row in inserts:
            self.connection.execute(
                "INSERT INTO award_line (org_id, award_id, rfq_line_id, vendor_id, bid_id, "
                "quantity, unit_price, currency, amount_vendor, amount_rfq, not_lowest, "
                "justification, tie_override_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s)",
                (self.scope.org_id, award["id"], *row),
            )

    def put_lines(self, rfq_id: UUID, body: AwardLinesPut, if_match: str | None) -> AwardRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._rfq(rfq_id, lock=True)
            award = self._award(rfq_id, lock=True)
            self._check_edit_revision(award["version"], if_match)
            if award["status"] != "draft":
                raise conflict("not_draft", "Only a draft award can change. Reload it and retry.")
            cmp, standing = self._standing(rfq_id)
            self._store(award, body.lines, cmp, standing)
            self._bump(award["id"])
            self._audit_award("award.lines", award["id"], None, {"lines": len(body.lines)})
        return self.read(rfq_id)

    def patch(self, rfq_id: UUID, body: AwardPatch, if_match: str | None) -> AwardRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._rfq(rfq_id, lock=True)
            award = self._award(rfq_id, lock=True)
            self._check_edit_revision(award["version"], if_match)
            if award["status"] != "draft":
                raise conflict("not_draft", "Only a draft award can change. Reload it and retry.")
            self.repo.execute(
                "UPDATE award SET rationale = %(text)s WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": award["id"], "text": body.rationale},
            )
            self._bump(award["id"])
            self._audit_award("award.rationale", award["id"], None, {"rationale": body.rationale})
        return self.read(rfq_id)

    def _bump(self, award_id: UUID) -> None:
        self.repo.execute(
            "UPDATE award SET version = version + 1 WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": award_id},
        )

    def submit(self, rfq_id: UUID) -> AwardRead:
        """Decision 204: a workflow is required, never auto-approved; a 409 writes nothing."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            status, unit_id, ccy, _ = self._rfq(rfq_id, lock=True)
            award = self._award(rfq_id, lock=True)
            if status != "under_evaluation" or award["status"] != "draft":
                raise conflict(
                    "invalid_transition",
                    "Only a draft award on an RFQ under evaluation can be submitted. Reload it.",
                )
            id = cast(UUID, award["id"])
            require_workflow_for(self.connection, self.scope, "award", id, "awards")
            rows = self._rows(id)
            cmp, standing = self._standing(rfq_id)
            setting = OrgService(self.connection, self.scope).effective_setting(
                unit_id, "sourcing.require_non_lowest_justification"
            )
            problems = []
            if not (award["rationale"] or "").strip():
                problems.append(ProblemFieldError(field="rationale", message="Give the rationale."))
            if not rows:
                problems.append(
                    ProblemFieldError(field="lines", message="Award at least one line.")
                )
            for r in rows:
                field = f"lines/{r.rfq_line_id}/{r.vendor_id}"
                if r.not_lowest and setting.value is True and not r.justification:
                    problems.append(
                        ProblemFieldError(
                            field=field, message="Justify choosing other than the lowest price."
                        )
                    )
                _tied, first = standing.get(r.rfq_line_id, (set(), None))
                if not r.not_lowest and r.vendor_id != first and not r.tie_override_reason:
                    problems.append(
                        ProblemFieldError(
                            field=field, message="Give the reason for overriding the tie order."
                        )
                    )
            if problems:
                raise _problem(problems, "The award cannot be submitted yet.", "cannot_submit")
            basis = sourcing_basis(
                self.connection, self.scope, sorted({r.requisition_line_id for r in rows})
            )
            for b in basis:
                if generated_for(self.connection, self.scope, b.requisition_id) is None:
                    raise ProblemError(
                        ErrorCode.VALIDATION_FAILED,
                        detail=f"{b.requisition_number} has no sub-project; award it later.",
                        checks={
                            "problem": "standalone_not_supported",
                            "requisition": b.requisition_number,
                        },
                    )
            # The live version is used: every status write bumps it, so the version an RFQ line
            # recorded is stale once the requisition moved to `sourced`.
            for b in basis:
                assert_current_upstream(
                    self.connection, self.scope, b.requisition_id, b.requisition_version
                )
            _, checks = self.figures(award, rows, ccy, unit_id, lock=True)
            self.assert_within(checks)
            scores = weighted(self.repo, rfq_id)
            exceptions = [
                {"vendor_id": str(r[0]), "bid_id": str(r[1]), "exceptions": r[2]}
                for r in self.repo.execute(
                    "SELECT DISTINCT al.vendor_id, al.bid_id, b.exceptions FROM award_line al "
                    "JOIN bid b ON b.org_id = al.org_id AND b.id = al.bid_id "
                    "WHERE al.org_id = %(org_id)s AND al.award_id = %(id)s "
                    "AND b.exceptions IS NOT NULL ORDER BY al.vendor_id",
                    {"id": id},
                ).fetchall()
            ]
            self.repo.execute(
                "UPDATE award SET status = 'approval_pending', version = version + 1, "
                "submitted_at = now(), evaluator_results = %(results)s, "
                "exceptions = %(exceptions)s "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {
                    "id": id,
                    "results": Jsonb(
                        {
                            "weighted": None
                            if scores is None
                            else {str(k): format(v, "f") for k, v in scores.items()}
                        }
                    ),
                    "exceptions": Jsonb(exceptions),
                },
            )
            start(
                self.connection,
                self.scope,
                document_type="award",
                document_id=id,
                actor_id=cast(UUID, self.actor_id),
                resolver=AuthorizationResolver(
                    cast(AuthorizationConnection, self.connection), self.scope
                ),
            )
            self._audit_award(
                "award.submit", id, {"status": "draft"}, {"status": "approval_pending"}
            )
        return self.read(rfq_id)

    def withdraw(self, rfq_id: UUID) -> AwardRead:
        """One transaction: the approval ends WITHDRAWN and the adapter moves the award to draft."""
        from flo.modules.approvals.service import withdraw

        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._rfq(rfq_id)
            award = self._award(rfq_id)
            if award["status"] != "approval_pending":
                raise conflict(
                    "invalid_transition", "Only an award pending approval can be withdrawn."
                )
            withdraw(
                self.connection,
                self.scope,
                document_type="award",
                document_id=award["id"],
                actor_id=cast(UUID, self.actor_id),
                reason="",
            )
        return self.read(rfq_id)

    def awards(self, rfq_id: UUID) -> list[AwardRead]:
        """Every award of the RFQ, newest first, superseded and cancelled included (WF-005)."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            _, unit_id, ccy, _ = self._rfq(rfq_id)
            ids = [
                cast(UUID, r[0])
                for r in self.repo.execute(
                    "SELECT id FROM award WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s "
                    "ORDER BY created_at DESC, id",
                    {"rfq": rfq_id},
                ).fetchall()
            ]
            return [self._read(self.award_by_id(i), ccy, unit_id) for i in ids]

    def cancel(self, rfq_id: UUID, body: ReasonBody) -> AwardRead:
        """A draft award posted nothing, so nothing reverses; an approval in flight is withdrawn
        first (SRC-017)."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._rfq(rfq_id, lock=True)
            award = self._award(rfq_id, lock=True)
            if award["status"] != "draft":
                raise conflict(
                    "invalid_transition",
                    "Only a draft award can be cancelled. Withdraw one pending approval first.",
                )
            self.repo.execute(
                "UPDATE award SET status = 'cancelled', version = version + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": award["id"]},
            )
            self._audit_award(
                "award.cancel",
                award["id"],
                {"status": "draft"},
                {"status": "cancelled", "reason": body.reason},
            )
        return self.read(rfq_id)

    def supersede(self, rfq_id: UUID, body: ReasonBody) -> AwardRead:
        """Decision 213: undo an approved award exactly; refused once a PO exists (SRC-017).

        Lock order: project_balance rows, the award, the RFQ (the requisitions follow inside
        `award_lines`). The approved check runs under the award lock, before any amount is read,
        so the second of two concurrent supersedes posts nothing (FIN-011).
        """
        actor = cast(UUID, self.actor_id)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._rfq(rfq_id)
            live = self._award(rfq_id)
            id = cast(UUID, live["id"])
            rows = self._rows(id)
            requisitions = sorted({r.requisition_id for r in rows})
            award_funding(self.connection, self.scope, requisitions, lock=True)
            award = self.award_by_id(id, lock=True)
            if award["status"] != "approved":
                raise conflict("invalid_transition", "Only an approved award can be superseded.")
            self._rfq(rfq_id, lock=True)
            if any(probe(self.connection, self.scope, id) for probe in SUPERSEDE_GUARDS):
                raise conflict(
                    "po_exists", "A purchase order was created from this award. It stays."
                )
            award_lines(
                self.connection,
                self.scope,
                [(r.requisition_line_id, -r.quantity) for r in rows],
                actor,
            )
            for requisition_id in requisitions:
                restore_unawarded(self.connection, self.scope, requisition_id, id, actor)
                mark_unawarded(self.connection, self.scope, requisition_id, actor)
            self.repo.execute(
                "UPDATE award SET status = 'superseded', version = version + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": id},
            )
            self.repo.execute(
                "UPDATE rfq SET status = 'under_evaluation', updated_at = now(), "
                "edit_revision = edit_revision + 1 WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": rfq_id},
            )
            self._audit_award(
                "award.supersede",
                id,
                {"status": "approved"},
                {"status": "superseded", "reason": body.reason},
            )
            self._audit(
                "rfq.supersede_award",
                rfq_id,
                {"status": "awarded"},
                {"status": "under_evaluation", "reason": body.reason},
            )
        return self.read(rfq_id)

    # -- the approval outcomes (inside the decision's transaction) ---------------------------

    def settle(self, award_id: UUID, outcome: str, context: OutcomeContext) -> None:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            award = self.award_by_id(award_id)
            if outcome == "approved":
                if award["status"] == "approved":
                    return  # a retried decision posts nothing new (FIN-011)
                self._approved(award, context)
                return
            award = self.award_by_id(award_id, lock=True)
            if award["status"] != "approval_pending":
                raise conflict("invalid_transition", "This award is no longer pending approval.")
            self.repo.execute(
                "UPDATE award SET status = 'draft', version = version + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": award_id},
            )
            self._audit_award(
                f"award.{outcome}", award_id, {"status": "approval_pending"}, {"status": "draft"}
            )

    def _approved(self, award: dict[str, Any], context: OutcomeContext) -> None:
        """Lock order: project_balance rows (id order), requisitions (id order), the award."""
        id, rfq_id = cast(UUID, award["id"]), cast(UUID, award["rfq_id"])
        _, unit_id, ccy, _ = self._rfq(rfq_id)
        rows = self._rows(id)
        _, checks = self.figures(award, rows, ccy, unit_id, lock=True)
        if award["status"] != "approval_pending":
            raise conflict("invalid_transition", "This award is no longer pending approval.")
        self.assert_within(checks)
        award_lines(
            self.connection,
            self.scope,
            [(r.requisition_line_id, r.quantity) for r in rows],
            context.actor_id,
        )
        award = self.award_by_id(id, lock=True)
        if award["status"] != "approval_pending":
            raise conflict("invalid_transition", "This award is no longer pending approval.")
        status, *_ = self._rfq(rfq_id, lock=True)
        if status != "under_evaluation":
            raise conflict("invalid_transition", "This RFQ is no longer under evaluation.")
        for c in checks:
            unused = Decimal(c.scope) - Decimal(c.award)
            if unused > 0:
                release_unawarded(
                    self.connection, self.scope, c.requisition_id, unused, id, context.actor_id,
                    version=cast(int, award["version"]),
                )  # fmt: skip
        self.repo.execute(
            "UPDATE award SET status = 'approved', approved_at = now(), version = version + 1 "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": id},
        )
        self.repo.execute(
            "UPDATE rfq SET status = 'awarded', updated_at = now(), "
            "edit_revision = edit_revision + 1 WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": rfq_id},
        )
        for c in checks:
            mark_awarded(self.connection, self.scope, c.requisition_id, context.actor_id)
        self._audit_award(
            "award.approve", id, {"status": "approval_pending"}, {"status": "approved"}
        )
        audit_event(
            cast(AuditConnection, self.connection),
            self.scope,
            actor=AuditActor(ActorKind.USER, context.actor_id),
            action="rfq.award",
            target_type="rfq",
            target_id=rfq_id,
            outcome=Outcome.SUCCESS,
            before={"status": "under_evaluation"},
            after={"status": "awarded"},
        )

    # -- what the adapter shows --------------------------------------------------------------

    def snapshot_facts(self, award_id: UUID) -> tuple[dict[str, Any], UUID, Money, str, str]:
        award = self.award_by_id(award_id)
        _, unit_id, ccy, number = self._rfq(cast(UUID, award["rfq_id"]))
        rows = self._rows(award_id)
        total = sum((r.amount_rfq for r in rows), Decimal(0))
        material = ";".join(f"{r.rfq_line_id}:{r.vendor_id}:{r.quantity:f}" for r in rows)
        return award, unit_id, Money(total, ccy), number, material

    def evidence(self, award_id: UUID) -> dict[str, Any]:
        """What the approver's panel shows (SRC-013), decimal strings only.

        Holds nothing that moves on approval: `funds` and `over_funds_by` are left out because
        approval releases the remainder; the material digest compares this with a fresh snapshot.
        """
        award = self.award_by_id(award_id)
        rfq_id = cast(UUID, award["rfq_id"])
        _, unit_id, ccy, _ = self._rfq(rfq_id)
        rows = self._rows(award_id)
        budgets, checks = self.figures(award, rows, ccy, unit_id)
        names = {
            cast(UUID, r[0]): cast(str, r[1] or "")
            for r in self.repo.execute(
                "SELECT vendor_id, vendor_snapshot->>'trading_name' FROM rfq_invitee "
                "WHERE org_id = %(org_id)s AND rfq_id = %(rfq)s",
                {"rfq": rfq_id},
            ).fetchall()
        }
        full: dict[UUID, Decimal] = defaultdict(Decimal)
        budget: dict[UUID, Decimal] = defaultdict(Decimal)
        for r, b in zip(rows, budgets, strict=True):
            full[r.vendor_id] += r.amount_rfq
            budget[r.vendor_id] += b
        return {
            "rfq_id": str(rfq_id),
            "basis": award["basis"],
            "currency": ccy,
            "totals_by_vendor": [
                {
                    "vendor_id": str(v),
                    "vendor_name": names.get(v, ""),
                    "budget_total": format(budget[v], "f"),
                    "total": format(quantize(full[v], ccy), "f"),
                }
                for v in sorted(full, key=lambda v: (names.get(v, ""), v))
            ],
            "requisitions": [
                {
                    "requisition_id": str(c.requisition_id),
                    "requisition": c.requisition,
                    "scope": c.scope,
                    "award": c.award,
                    "over_scope_by": c.over_scope_by,
                    "release": c.release,
                    "return_project": c.main_project,
                }
                for c in checks
            ],
            "not_lowest": [
                {
                    "line_no": r.line_no,
                    "vendor_name": names.get(r.vendor_id, ""),
                    "justification": r.justification,
                }
                for r in rows
                if r.not_lowest
            ],
            "tie_overrides": [
                {
                    "line_no": r.line_no,
                    "vendor_name": names.get(r.vendor_id, ""),
                    "tie_override_reason": r.tie_override_reason,
                }
                for r in rows
                if r.tie_override_reason
            ],
            "exceptions": award["exceptions"],
            "rationale": award["rationale"],
            "evaluator_results": award["evaluator_results"],
            # scored bidders include vendors that were not awarded; the invitee snapshot is frozen
            "vendor_names": {str(v): n for v, n in names.items()},
        }


_TONES: dict[str, Tone] = {
    "draft": "neutral",
    "approval_pending": "info",
    "approved": "success",
    "superseded": "neutral",
    "cancelled": "danger",
}


def _award_links(
    conn: Conn, scope: Scope, requisition_id: UUID, read_check: object
) -> list[LinkedNode]:
    """SRC-013: the requisition's Linked Records list every award over one of its lines."""
    rows = (
        AwardService(conn, scope)
        .repo.execute(
            "SELECT a.id, a.number, a.status, r.unit_id, a.created_at, a.rfq_id FROM award a "
            "JOIN rfq r ON r.org_id = a.org_id AND r.id = a.rfq_id WHERE a.org_id = %(org_id)s "
            "AND EXISTS (SELECT 1 FROM rfq_line l WHERE l.org_id = a.org_id "
            "AND l.rfq_id = a.rfq_id AND l.requisition_id = %(requisition_id)s)",
            {"requisition_id": requisition_id},
        )
        .fetchall()
    )
    return [
        LinkedNode(
            key=f"award:{r[0]}",
            kind="award",
            id=str(r[0]),
            label=f"Award {r[1]}",
            state=str(r[2]).replace("_", " ").capitalize(),
            state_tone=_TONES[str(r[2])],
            href=f"/sourcing/rfqs/{r[5]}",
            at=cast(datetime, r[4]),
            relation="awarded_by",
            access=LinkAccess(permission="rfq.view", scope_type="bu", target_id=cast(UUID, r[3])),
        )
        for r in rows
    ]


register_link_provider("award", _award_links)
