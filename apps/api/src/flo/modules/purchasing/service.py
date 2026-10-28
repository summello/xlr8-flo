"""Purchase order drafts from approved award lines: one per vendor and requisition (E13-S01).

A draft holds its quantity: every create, lower and delete moves `award_line.ordered_qty` and
`requisition_line.ordered_qty` together, in one transaction, after the award header lock (the
lock supersede takes) and before anything is validated against the open quantity (PO-009).
Value is derived from the lines, never stored; nothing is posted to the ledger here.
"""

import base64
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.authz import GrantedScopes
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError, conflict
from flo.kernel.money import quantize
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.schemas import Outcome as ApprovalOutcome
from flo.modules.approvals.schemas import OutcomeContext
from flo.modules.approvals.service import require_workflow_for, start, withdraw
from flo.modules.identity.service import AuthorizationConnection, AuthorizationResolver
from flo.modules.org.service import NumberingService, OrgService, today
from flo.modules.projects.service import generated_for
from flo.modules.purchasing.lifecycle import parse_if_match, transition
from flo.modules.purchasing.models import PurchaseOrderRepository
from flo.modules.purchasing.pricing import Figures, landed, spread
from flo.modules.purchasing.schemas import (
    AwardedLineRead,
    AwardedLinesRead,
    PoCreate,
    PoLineRead,
    PoListRow,
    PoPage,
    PoPatch,
    PoRead,
    PoStatus,
    PoTab,
    PurchaseOrderTransitionCreate,
)
from flo.modules.requisitions.schemas import LineSourcing, LinkAccess, LinkedNode
from flo.modules.requisitions.service import (
    RequisitionService,
    assert_current_upstream,
    order_lines,
    register_link_provider,
)
from flo.modules.sourcing.service import (
    award_evidence,
    award_line_quotes,
    award_unit,
    awarded_lines,
    lock_award,
    order_award_lines,
    register_supersede_guard,
)
from flo.modules.vendors.schemas import Eligibility
from flo.modules.vendors.service import VendorService

type Conn = psycopg.Connection[tuple[object, ...]]

PAGE = 50
HEADER = (
    "id, number, unit_id, vendor_id, vendor_snapshot, requisition_id, award_id, currency, rate, "
    "bill_to, ship_to_address_id, recipients, requester_id, terms, needed_by, status, version, "
    "created_at, freight_mode, document_freight"
)


def _invalid(field: str, message: str, problem: str) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail=message,
        errors=(ProblemFieldError(field=field, message=message),),
        checks={"problem": problem},
    )


def _not_eligible(result: Eligibility) -> ProblemError:
    reasons = "; ".join(r.text for r in result.reasons)
    return ProblemError(
        ErrorCode.CONFLICT,
        detail=f"The vendor is not eligible: {reasons}",
        checks={"problem": "vendor_not_eligible", "reasons": reasons},
    )


def _fmt(value: Decimal) -> str:
    return format(value, "f")


class PurchaseOrderService:
    def __init__(
        self,
        connection: Conn,
        scope: Scope,
        actor_id: UUID | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.connection, self.scope = connection, scope
        self.actor_id, self.settings = actor_id, settings
        self.repo = PurchaseOrderRepository(connection, scope)

    def _audit(
        self, action: str, id: UUID, before: dict[str, object] | None, after: dict[str, object]
    ) -> None:
        audit_event(
            cast(AuditConnection, self.connection),
            self.scope,
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type="purchase_order",
            target_id=id,
            outcome=Outcome.SUCCESS,
            before=before,
            after=after,
        )

    # -- reads -----------------------------------------------------------------------------

    def _row(self, id: UUID, *, lock: bool = False) -> dict[str, object]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            cur = self.repo.execute(
                f"SELECT {HEADER} FROM purchase_order WHERE org_id = %(org_id)s AND id = %(id)s "
                "AND status <> 'deleted'" + (" FOR UPDATE" if lock else ""),
                {"id": id},
            )
            row = cur.fetchone()
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            return dict(zip((d.name for d in cur.description or []), row, strict=True))

    def unit_of(self, id: UUID) -> UUID:
        """The PO's unit, 404 for an unknown, deleted or foreign id."""
        return cast(UUID, self._row(id)["unit_id"])

    def award_unit(self, award_id: UUID) -> UUID:
        return award_unit(self.connection, self.scope, award_id)

    def _flags(self, unit_id: UUID) -> tuple[bool, bool]:
        org = OrgService(self.connection, self.scope)
        return (
            org.effective_setting(unit_id, "tax.consumes_budget").value is True,
            org.effective_setting(unit_id, "freight.consumes_budget").value is True,
        )

    @staticmethod
    def _budget(
        f: Figures, flags: tuple[bool, bool], rate: dict[str, str], currency: str
    ) -> tuple[Decimal, Decimal]:
        """The line's budget amount in the PO currency and, at the PO rate, the project's."""
        budget = f.budget(tax_on=flags[0], freight_on=flags[1])
        return budget, quantize(budget * Decimal(rate["rate"]), rate["to"])

    def get(self, id: UUID) -> PoRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            h = self._row(id)
            lines = self.repo.execute(
                "SELECT line_no, rfq_line_id, requisition_line_id, description, uom_code, "
                "quantity, unit_price, project_id, department_code, ledger_account_code, "
                "price, tax, freight, budget_amount, budget_amount_project "
                "FROM po_line WHERE org_id = %(org_id)s AND po_id = %(id)s ORDER BY line_no",
                {"id": id},
            ).fetchall()
            flags = self._flags(cast(UUID, h["unit_id"]))
        currency = cast(str, h["currency"]).strip()
        rate = cast(dict[str, str], h["rate"])
        to, factor = rate["to"], Decimal(rate["rate"])
        live = h["status"] in ("draft", "approval_pending", "approved")
        reads: list[PoLineRead] = []
        sums = {
            k: Decimal(0) for k in ("price", "tax", "freight", "total", "budget", "project", "conv")
        }
        for r in lines:
            f = Figures(*(quantize(cast(Decimal, v), currency) for v in r[10:13]))
            # until issue the budget amount follows the unit's settings; E13-S05 freezes it at issue
            budget, project = (
                self._budget(f, flags, rate, currency)
                if live
                else (quantize(cast(Decimal, r[13]), currency), quantize(cast(Decimal, r[14]), to))
            )
            converted = quantize(f.line_total * factor, to)
            for k, v in zip(
                sums,
                (f.price, f.tax, f.freight, f.line_total, budget, project, converted),
                strict=True,
            ):
                sums[k] += v
            reads.append(
                PoLineRead(
                    line_no=cast(int, r[0]),
                    rfq_line_id=cast(UUID, r[1]),
                    requisition_line_id=cast(UUID, r[2]),
                    description=cast(str, r[3]),
                    uom_code=cast(str, r[4]),
                    quantity=_fmt(cast(Decimal, r[5])),
                    unit_price=_fmt(cast(Decimal, r[6])),
                    project_id=cast(UUID, r[7]),
                    department_code=cast(str | None, r[8]),
                    ledger_account_code=cast(str | None, r[9]),
                    price=_fmt(f.price),
                    tax=_fmt(f.tax),
                    freight=_fmt(f.freight),
                    line_total=_fmt(f.line_total),
                    budget_amount=_fmt(budget),
                    budget_amount_project=_fmt(project),
                    value=_fmt(f.line_total),
                    value_converted=_fmt(converted),
                )
            )
        document = cast(Decimal | None, h["document_freight"])
        fields: dict[str, Any] = {
            **h,
            "currency": currency,
            "converted_currency": to,
            "document_freight": None if document is None else _fmt(quantize(document, currency)),
        }
        return PoRead(
            **fields,
            lines=reads,
            price_total=_fmt(sums["price"]),
            tax_total=_fmt(sums["tax"]),
            freight_total=_fmt(sums["freight"]),
            total=_fmt(sums["total"]),
            budget_total=_fmt(sums["budget"]),
            budget_total_project=_fmt(sums["project"]),
            total_converted=_fmt(sums["conv"]),
        )

    def awarded(self, award_id: UUID, vendor_id: UUID | None) -> AwardedLinesRead:
        """Open to order per award line, with the value already on draft POs."""
        found = awarded_lines(self.connection, self.scope, award_id, vendor_id=vendor_id)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            ordered: dict[tuple[UUID, UUID], Decimal] = defaultdict(Decimal)
            for r in self.repo.execute(
                "SELECT l.rfq_line_id, l.vendor_id, l.line_total, p.currency "
                "FROM po_line l JOIN purchase_order p ON p.org_id = l.org_id AND p.id = l.po_id "
                "WHERE l.org_id = %(org_id)s AND l.award_id = %(id)s AND p.status <> 'deleted'",
                {"id": award_id},
            ).fetchall():
                key = (cast(UUID, r[0]), cast(UUID, r[1]))
                ordered[key] += quantize(cast(Decimal, r[2]), str(r[3]).strip())
        return AwardedLinesRead(
            award_id=award_id,
            lines=[
                AwardedLineRead(
                    rfq_line_id=x.rfq_line_id,
                    line_no=x.line_no,
                    vendor_id=x.vendor_id,
                    requisition_id=x.requisition_id,
                    requisition_line_id=x.requisition_line_id,
                    description=x.description,
                    uom_code=x.uom_code,
                    currency=x.currency,
                    unit_price=_fmt(x.unit_price),
                    awarded_qty=_fmt(x.quantity),
                    ordered_qty=_fmt(x.ordered_qty),
                    open_qty=_fmt(x.quantity - x.ordered_qty),
                    ordered_value=_fmt(
                        ordered.get((x.rfq_line_id, x.vendor_id), quantize(Decimal(0), x.currency))
                    ),
                )
                for x in found
            ],
        )

    def list_orders(
        self, granted: GrantedScopes, tab: PoTab, cursor: str | None, page_size: int = PAGE
    ) -> PoPage:
        """POs on the units where the caller holds `purchase_order.view`, newest first."""
        params: dict[str, object] = {
            "org_wide": granted.org_wide,
            "bu_ids": list(granted.bu_ids),
            "tab": None if tab == "all" else tab,
            "limit": page_size + 1,
        }
        where = (
            "p.org_id = %(org_id)s AND p.status <> 'deleted' "
            "AND (%(org_wide)s OR p.unit_id = ANY(%(bu_ids)s::uuid[]))"
        )
        after = ""
        if cursor is not None:
            try:
                at, last = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                params.update(c_at=datetime.fromisoformat(at), c_id=UUID(last))
            except (ValueError, TypeError):
                raise _invalid(
                    "cursor", "The page cursor is not valid.", "invalid_cursor"
                ) from None
            after = " AND (p.created_at, p.id) < (%(c_at)s, %(c_id)s)"
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            counts = {
                str(r[0]): cast(int, r[1])
                for r in self.repo.execute(
                    f"SELECT p.status, count(*) FROM purchase_order p WHERE {where} "
                    "GROUP BY p.status",
                    params,
                ).fetchall()
            }
            rows = self.repo.execute(
                "SELECT p.id, p.number, p.unit_id, p.vendor_id, "
                "p.vendor_snapshot->>'trading_name', p.requisition_id, p.currency, p.status, "
                "p.created_at FROM purchase_order p "
                f"WHERE {where} AND (%(tab)s::text IS NULL OR p.status = %(tab)s){after} "
                "ORDER BY p.created_at DESC, p.id DESC LIMIT %(limit)s",
                params,
            ).fetchall()
        page = rows[:page_size]
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            priced = self.repo.execute(
                "SELECT po_id, line_total FROM po_line "
                "WHERE org_id = %(org_id)s AND po_id = ANY(%(ids)s)",
                {"ids": [r[0] for r in page]},
            ).fetchall()
        totals: dict[UUID, Decimal] = defaultdict(Decimal)
        currencies = {cast(UUID, r[0]): cast(str, r[6]).strip() for r in page}
        for po_id, line_total in priced:  # the sum of the rounded landed lines, as `get`
            totals[cast(UUID, po_id)] += quantize(
                cast(Decimal, line_total), currencies[cast(UUID, po_id)]
            )
        return PoPage(
            rows=[
                PoListRow(
                    id=cast(UUID, r[0]),
                    number=cast(str, r[1]),
                    unit_id=cast(UUID, r[2]),
                    vendor_id=cast(UUID, r[3]),
                    vendor_name=cast(str, r[4]),
                    requisition_id=cast(UUID, r[5]),
                    currency=cast(str, r[6]).strip(),
                    status=cast(PoStatus, r[7]),
                    created_at=cast(datetime, r[8]),
                    total=_fmt(totals.get(cast(UUID, r[0]), Decimal(0))),
                )
                for r in page
            ],
            counts={"all": sum(counts.values()), "draft": counts.get("draft", 0)},
            next_cursor=(
                base64.urlsafe_b64encode(
                    json.dumps([cast(datetime, page[-1][8]).isoformat(), str(page[-1][0])]).encode()
                ).decode()
                if len(rows) > page_size
                else None
            ),
        )

    # -- writes ----------------------------------------------------------------------------

    def _bill_to(self, unit_id: UUID) -> dict[str, object]:
        """The active Bill To of the unit, else of the nearest ancestor (decision 165)."""
        org = OrgService(self.connection, self.scope)
        for unit in org.unit_chain(unit_id):
            found = org.list_addresses(unit, "bill_to", None)
            if found:
                return found[0].model_dump(mode="json")
        raise conflict("bill_to_missing", "No Bill To address is set on this unit or above it.")

    def _ship_to_allowed(self, unit_id: UUID, address_id: UUID) -> bool:
        org = OrgService(self.connection, self.scope)
        return any(
            a.id == address_id
            for unit in org.unit_chain(unit_id)
            for a in org.list_addresses(unit, "ship_to", None)
        )

    def _eligibility(
        self, vendors: VendorService, vendor_id: UUID, unit_id: UUID, body: PoCreate,
        can_override: bool,
    ) -> tuple[Eligibility, str | None]:  # fmt: skip
        """Refuse an ineligible vendor unless overridden; returns the result and the reason."""
        result = vendors.eligibility([vendor_id], unit_id, today()).get(vendor_id)
        if result is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        if result.eligible:
            return result, None
        if not (body.override_reason and can_override):
            raise _not_eligible(result)
        return result, body.override_reason

    def create(self, body: PoCreate, can_override: bool = False) -> PoRead:
        """Lock the award, validate, move both counters, number and insert the draft."""
        actor = cast(UUID, self.actor_id)
        id = uuid4()
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            header = lock_award(self.connection, self.scope, body.award_id)
            vendors = VendorService(self.connection, self.scope, self.actor_id)
            vendor = vendors.get(body.vendor_id)  # 404 for a foreign or unknown vendor
            quantities = {line.rfq_line_id: Decimal(line.quantity) for line in body.lines}
            if len(quantities) != len(body.lines):
                raise _invalid("lines", "A line appears twice.", "duplicate_line")
            have = {
                x.rfq_line_id: x
                for x in awarded_lines(
                    self.connection, self.scope, body.award_id, vendor_id=body.vendor_id
                )
            }
            if not quantities.keys() <= have.keys():
                raise _invalid("lines", "A line was not awarded to this vendor.", "mixed_vendors")
            chosen = [have[line.rfq_line_id] for line in body.lines]
            requisitions = {x.requisition_id for x in chosen}
            if len(requisitions) > 1:
                raise _invalid(
                    "lines", "Lines from two requisitions need two purchase orders.",
                    "mixed_requisitions",
                )  # fmt: skip
            requisition_id = chosen[0].requisition_id
            eligible, override = self._eligibility(
                vendors, body.vendor_id, header.unit_id, body, can_override
            )
            requisition = RequisitionService(self.connection, self.scope, self.actor_id).get(
                requisition_id
            )
            sub = generated_for(self.connection, self.scope, requisition_id)
            if sub is None:
                raise conflict("no_project", "The requisition has no project to draw from.")
            bill_to = self._bill_to(header.unit_id)
            recipients = [r.model_dump() for r in vendors.document_recipients(body.vendor_id)]
            currency = chosen[0].currency
            if currency not in header.rates:
                raise conflict("rate_missing", f"The award holds no rate for {currency}.")
            order_award_lines(
                self.connection,
                self.scope,
                body.award_id,
                [(x.rfq_line_id, x.vendor_id, quantities[x.rfq_line_id]) for x in chosen],
            )
            order_lines(
                self.connection,
                self.scope,
                [
                    LineSourcing(
                        x.requisition_id,
                        x.requisition_line_id,
                        x.requisition_version,
                        quantities[x.rfq_line_id],
                    )
                    for x in chosen
                ],
                self.actor_id,
            )
            number = NumberingService(self.settings or Settings(), self.scope).allocate(
                header.unit_id, "po", today()
            )
            snapshot = {
                "trading_name": vendor.trading_name,
                "legal_name": vendor.legal_name,
                "number": vendor.number,
                "status": vendor.status,
                "eligibility": {
                    "eligible": eligible.eligible,
                    "reasons": [r.text for r in eligible.reasons],
                },
            }
            self.repo.execute(
                "INSERT INTO purchase_order (id, org_id, number, unit_id, vendor_id, "
                "requisition_id, vendor_snapshot, award_id, currency, rate, bill_to, "
                "ship_to_address_id, recipients, requester_id, needed_by, created_by, "
                "requisition_version) VALUES "
                "(%(id)s, %(org_id)s, %(number)s, %(unit)s, %(vendor)s, %(req)s, %(snapshot)s, "
                "%(award)s, %(currency)s, %(rate)s, %(bill)s, %(ship)s, %(recipients)s, "
                "%(requester)s, %(needed)s, %(by)s, %(req_version)s)",
                {
                    "id": id,
                    "number": number,
                    "unit": header.unit_id,
                    "vendor": body.vendor_id,
                    "req": requisition_id,
                    "snapshot": Jsonb(snapshot),
                    "award": body.award_id,
                    "currency": currency,
                    "rate": Jsonb({**header.rates[currency], "to": header.rfq_currency}),
                    "bill": Jsonb(bill_to),
                    "ship": requisition.delivery_address_id,
                    "recipients": Jsonb(recipients),
                    "requester": requisition.requester_id,
                    "needed": requisition.needed_by,
                    "by": actor,
                    "req_version": requisition.version,
                },
            )
            quotes = {(q.rfq_line_id, q.vendor_id): q for q in award_line_quotes(
                self.connection, self.scope, body.award_id
            )}  # fmt: skip
            flags = self._flags(header.unit_id)
            rate = {**header.rates[currency], "to": header.rfq_currency}
            for line_no, x in enumerate(chosen, 1):
                quote = quotes[(x.rfq_line_id, x.vendor_id)]
                f = landed(
                    x.unit_price, quantities[x.rfq_line_id], Decimal(quote.tax),
                    Decimal(quote.freight_share), Decimal(quote.quoted_quantity), currency,
                )  # fmt: skip
                budget, project = self._budget(f, flags, rate, currency)
                self.repo.execute(
                    "INSERT INTO po_line (org_id, po_id, line_no, award_id, rfq_line_id, "
                    "vendor_id, requisition_id, requisition_line_id, description, uom_code, "
                    "quantity, unit_price, project_id, department_code, ledger_account_code, "
                    "price, tax, freight, line_total, budget_amount, budget_amount_project) "
                    "VALUES (%(org_id)s, %(id)s, %(no)s, %(award)s, %(line)s, %(vendor)s, "
                    "%(req)s, %(req_line)s, %(description)s, %(uom)s, %(qty)s, %(price)s, "
                    "%(project)s, %(department)s, %(ledger)s, %(f_price)s, %(f_tax)s, "
                    "%(f_freight)s, %(f_total)s, %(budget)s, %(budget_project)s)",
                    {
                        "id": id,
                        "no": line_no,
                        "award": body.award_id,
                        "line": x.rfq_line_id,
                        "vendor": x.vendor_id,
                        "req": requisition_id,
                        "req_line": x.requisition_line_id,
                        "description": x.description,
                        "uom": x.uom_code,
                        "qty": quantities[x.rfq_line_id],
                        "price": x.unit_price,
                        "project": sub.id,
                        "department": requisition.department_code,
                        "ledger": requisition.ledger_account_code,
                        "f_price": f.price,
                        "f_tax": f.tax,
                        "f_freight": f.freight,
                        "f_total": f.line_total,
                        "budget": budget,
                        "budget_project": project,
                    },
                )
            self._audit(
                "purchase_order.create",
                id,
                None,
                {
                    "number": number,
                    "award_id": str(body.award_id),
                    "lines": {str(x.rfq_line_id): _fmt(quantities[x.rfq_line_id]) for x in chosen},
                },
            )
            if override is not None:
                self._audit(
                    "purchase_order.vendor_override",
                    id,
                    None,
                    {"vendor_id": str(body.vendor_id), "reason": override},
                )
        return self.get(id)

    @staticmethod
    def _check_version(current: int, if_match: str | None) -> None:
        """PATCH and DELETE need `If-Match`; a stale version is a 409 (convention O)."""
        expected = parse_if_match(if_match)
        if expected != current:
            raise conflict("stale_version", "The purchase order changed. Reload it and retry.")

    def _draft(self, id: UUID, if_match: str | None) -> dict[str, object]:
        """Lock the award, then the PO row, in the create order; require a current draft."""
        award_id = cast(UUID, self._row(id)["award_id"])
        lock_award(self.connection, self.scope, award_id)
        row = self._row(id, lock=True)
        if row["status"] != "draft":
            raise conflict("po_not_draft", "Only a draft purchase order can change.")
        self._check_version(cast(int, row["version"]), if_match)
        return row

    def _move(
        self, row: dict[str, object], targets: dict[int, Decimal] | None
    ) -> dict[int, Decimal]:
        """Take each line (None: every line, going away) to its target quantity.

        Moves both counters by the delta and, unless the lines are going, the PO lines too.
        Returns the quantities before the move; an unknown line_no is a 422.
        """
        found = {
            cast(int, r[0]): r
            for r in self.repo.execute(
                "SELECT l.line_no, l.rfq_line_id, l.vendor_id, l.requisition_id, "
                "l.requisition_line_id, l.quantity, q.version FROM po_line l "
                "JOIN requisition q ON q.org_id = l.org_id AND q.id = l.requisition_id "
                "WHERE l.org_id = %(org_id)s AND l.po_id = %(id)s",
                {"id": row["id"]},
            ).fetchall()
        }
        for line_no in targets or ():
            if line_no not in found:
                raise _invalid("lines", f"The order has no line {line_no}.", "unknown_line")
        goal = targets if targets is not None else dict.fromkeys(found, Decimal(0))
        moves = [(n, q - cast(Decimal, found[n][5])) for n, q in goal.items()]
        moves = [(n, d) for n, d in moves if d != 0]
        order_award_lines(
            self.connection,
            self.scope,
            cast(UUID, row["award_id"]),
            [(cast(UUID, found[n][1]), cast(UUID, found[n][2]), d) for n, d in moves],
        )
        order_lines(
            self.connection,
            self.scope,
            [
                LineSourcing(
                    cast(UUID, found[n][3]), cast(UUID, found[n][4]), cast(int, found[n][6]), d
                )
                for n, d in moves
            ],
            self.actor_id,
        )
        for n, d in [] if targets is None else moves:
            self.repo.execute(
                "UPDATE po_line SET quantity = quantity + %(delta)s "
                "WHERE org_id = %(org_id)s AND po_id = %(id)s AND line_no = %(no)s",
                {"id": row["id"], "no": n, "delta": d},
            )
        return {n: cast(Decimal, r[5]) for n, r in found.items()}

    def _reprice(
        self, row: dict[str, object], body: PoPatch, before: dict[int, Decimal]
    ) -> dict[str, object]:
        """Rewrite every line's figures after a draft write; returns the header columns to set.

        A quantity edit rescales that line's tax and freight from the award quote, and any value
        the same PATCH sets explicitly wins over the rescale. `document` mode re-spreads the
        document freight over all lines. The budget columns follow the unit's settings.
        """
        id, currency = cast(UUID, row["id"]), cast(str, row["currency"]).strip()
        mode = cast(str, row["freight_mode"])
        document = cast(Decimal | None, row["document_freight"])
        new_mode = body.freight_mode or mode
        patches = {x.line_no: x for x in body.lines or []}
        lines = self.repo.execute(
            "SELECT line_no, rfq_line_id, vendor_id, quantity, unit_price, tax, freight "
            "FROM po_line WHERE org_id = %(org_id)s AND po_id = %(id)s ORDER BY line_no",
            {"id": id},
        ).fetchall()
        for n in patches:
            if n not in {cast(int, r[0]) for r in lines}:
                raise _invalid("lines", f"The order has no line {n}.", "unknown_line")
        if body.document_freight is not None and new_mode != "document":
            raise _invalid(
                "document_freight", "Document freight needs the document freight mode.",
                "freight_mode_mismatch",
            )  # fmt: skip
        if new_mode != "line" and any(x.freight is not None for x in patches.values()):
            raise _invalid(
                "lines", "Line freight needs the line freight mode.", "freight_mode_mismatch"
            )
        quotes = {
            (q.rfq_line_id, q.vendor_id): q
            for q in award_line_quotes(self.connection, self.scope, cast(UUID, row["award_id"]))
        } if any(x.quantity is not None for x in patches.values()) else {}  # fmt: skip
        figures: list[Figures] = []
        exact: list[Decimal] = []
        for r in lines:
            n, qty, unit_price = cast(int, r[0]), cast(Decimal, r[3]), cast(Decimal, r[4])
            patch = patches.get(n)
            tax, freight = cast(Decimal, r[5]), cast(Decimal, r[6])
            if patch is not None and qty != before.get(n, qty):
                q = quotes[(cast(UUID, r[1]), cast(UUID, r[2]))]
                f = landed(
                    unit_price, qty, Decimal(q.tax), Decimal(q.freight_share),
                    Decimal(q.quoted_quantity), currency,
                )  # fmt: skip
                tax, freight = f.tax, f.freight
            if patch is not None and patch.tax is not None:
                tax = quantize(Decimal(patch.tax), currency)
            figures.append(Figures(quantize(unit_price * qty, currency), tax, freight))
            exact.append(unit_price * qty)
        if mode == "document" and document is not None:  # shares follow the new prices
            shares = spread(document, exact, currency)
            figures = [Figures(f.price, f.tax, s) for f, s in zip(figures, shares, strict=True)]
        if new_mode == "line":
            document = None
            for i, r in enumerate(lines):
                patch = patches.get(cast(int, r[0]))
                if patch is not None and patch.freight is not None:
                    f = figures[i]
                    figures[i] = Figures(f.price, f.tax, quantize(Decimal(patch.freight), currency))
        else:
            if body.document_freight is not None:
                document = quantize(Decimal(body.document_freight), currency)
            elif mode == "line":
                document = sum((f.freight for f in figures), Decimal(0))
            shares = spread(cast(Decimal, document), exact, currency)
            figures = [Figures(f.price, f.tax, s) for f, s in zip(figures, shares, strict=True)]
        rate = cast(dict[str, str], row["rate"])
        flags = self._flags(cast(UUID, row["unit_id"]))
        for r, f in zip(lines, figures, strict=True):
            budget, project = self._budget(f, flags, rate, currency)
            self.repo.execute(
                "UPDATE po_line SET price = %(price)s, tax = %(tax)s, freight = %(freight)s, "
                "line_total = %(total)s, budget_amount = %(budget)s, "
                "budget_amount_project = %(project)s "
                "WHERE org_id = %(org_id)s AND po_id = %(id)s AND line_no = %(no)s",
                {
                    "id": id, "no": r[0], "price": f.price, "tax": f.tax, "freight": f.freight,
                    "total": f.line_total, "budget": budget, "project": project,
                },
            )  # fmt: skip
        return {"freight_mode": new_mode, "document_freight": document}

    def update(self, id: UUID, body: PoPatch, if_match: str | None) -> PoRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self._draft(id, if_match)
            before: dict[str, object] = {}
            after: dict[str, object] = {}
            sets = ["version = version + 1"]
            params: dict[str, object] = {"id": id}
            if "ship_to_address_id" in body.model_fields_set:
                address = body.ship_to_address_id
                if address is None or not self._ship_to_allowed(
                    cast(UUID, row["unit_id"]), address
                ):
                    raise _invalid(
                        "ship_to_address_id", "Pick an active Ship To of this unit or above.",
                        "ship_to_invalid",
                    )  # fmt: skip
                sets.append("ship_to_address_id = %(ship)s")
                params["ship"] = address
            for field in ("terms", "needed_by"):
                if field in body.model_fields_set:
                    sets.append(f"{field} = %({field})s")
                    params[field] = getattr(body, field)
            for field in ("terms", "needed_by", "ship_to_address_id"):
                if field in body.model_fields_set:
                    before[field] = str(row[field]) if row[field] is not None else None
                    value = getattr(body, field)
                    after[field] = str(value) if value is not None else None
            moved: dict[int, Decimal] = {}
            if body.lines is not None:
                if len({x.line_no for x in body.lines}) != len(body.lines):
                    raise _invalid("lines", "A line appears twice.", "duplicate_line")
                resized = [x for x in body.lines if x.quantity is not None]
                goal = {x.line_no: Decimal(cast(str, x.quantity)) for x in resized}
                moved = self._move(row, goal)
                if resized:
                    before["lines"] = {str(x.line_no): _fmt(moved[x.line_no]) for x in resized}
                    after["lines"] = {str(x.line_no): x.quantity for x in resized}
            header = self._reprice(row, body, moved)
            sets += ["freight_mode = %(mode)s", "document_freight = %(document)s"]
            params |= {"mode": header["freight_mode"], "document": header["document_freight"]}
            for key, value in (("freight_mode", header["freight_mode"]),
                               ("document_freight", header["document_freight"])):  # fmt: skip
                if str(row[key]) != str(value):
                    before[key], after[key] = str(row[key]), str(value)
            edits = {
                str(x.line_no): {k: v for k, v in (("tax", x.tax), ("freight", x.freight)) if v}
                for x in body.lines or []
            }
            if any(edits.values()):
                after["pricing"] = {n: e for n, e in edits.items() if e}
            self.repo.execute(
                f"UPDATE purchase_order SET {', '.join(sets)} "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                params,
            )
            self._audit("purchase_order.update", id, before, after)
        return self.get(id)

    def delete(self, id: UUID, if_match: str | None) -> None:
        """Soft delete a draft: the row and number stay, the quantity goes back (E22-S02)."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self._draft(id, if_match)
            self._move(row, None)
            transition(
                self.connection, self.scope, id, "delete", self.actor_id, cast(int, row["version"])
            )

    def close(self, id: UUID, body: PurchaseOrderTransitionCreate, if_match: str | None) -> PoRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._row(id)  # 404 before any version talk
            transition(
                self.connection, self.scope, id, body.action, self.actor_id,
                parse_if_match(if_match), body.reason,
            )  # fmt: skip
        return self.get(id)

    # -- wiring -----------------------------------------------------------------------------------

    # -- approval (E13-S04) ----------------------------------------------------------------

    def creator(self, id: UUID) -> UUID:
        """The buyer who drafted the PO: the approval requester, the only one to submit it."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                "SELECT created_by FROM purchase_order WHERE org_id = %(org_id)s AND id = %(id)s "
                "AND status <> 'deleted'",
                {"id": id},
            ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return cast(UUID, row[0])

    def _creator_only(self, id: UUID) -> None:
        if self.creator(id) != self.actor_id:
            raise ProblemError(
                ErrorCode.FORBIDDEN, detail="Only the buyer who drafted this order can do that."
            )

    def submit(self, id: UUID) -> PoRead:
        """Decision 208: a published workflow is required, never auto-approved; a 409 writes
        nothing."""
        actor = cast(UUID, self.actor_id)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            h = self._row(id, lock=True)
            self._creator_only(id)
            if h["status"] != "draft":
                raise conflict(
                    "invalid_transition", "Only a draft purchase order can be submitted."
                )
            require_workflow_for(self.connection, self.scope, "po", id, "purchase orders")
            version = cast(
                int,
                self.repo.execute(
                    "SELECT requisition_version FROM purchase_order "
                    "WHERE org_id = %(org_id)s AND id = %(id)s",
                    {"id": id},
                ).fetchone()[0],  # type: ignore[index]
            )
            assert_current_upstream(
                self.connection, self.scope, cast(UUID, h["requisition_id"]), version
            )
            # a PO that already carries an override was created for an ineligible vendor
            if cast(dict[str, Any], h["vendor_snapshot"])["eligibility"]["eligible"]:
                found = VendorService(self.connection, self.scope).eligibility(
                    [cast(UUID, h["vendor_id"])], cast(UUID, h["unit_id"]), today()
                )[cast(UUID, h["vendor_id"])]
                if not found.eligible:
                    raise _not_eligible(found)
            transition(self.connection, self.scope, id, "submit", actor, cast(int, h["version"]))
            start(
                self.connection,
                self.scope,
                document_type="po",
                document_id=id,
                actor_id=actor,
                resolver=AuthorizationResolver(
                    cast(AuthorizationConnection, self.connection), self.scope
                ),
            )
        return self.get(id)

    def withdraw(self, id: UUID) -> PoRead:
        """The approval ends WITHDRAWN and the adapter moves the PO back to draft."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            h = self._row(id, lock=True)
            self._creator_only(id)
            if h["status"] != "approval_pending":
                raise conflict(
                    "invalid_transition", "Only a purchase order pending approval can be withdrawn."
                )
            withdraw(
                self.connection,
                self.scope,
                document_type="po",
                document_id=id,
                actor_id=cast(UUID, self.actor_id),
                reason="",
            )
        return self.get(id)

    def settle(self, id: UUID, outcome: ApprovalOutcome, context: OutcomeContext) -> None:
        """Inside the decision's transaction. Nothing is posted: issue commits, not approval."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            h = self._row(id, lock=True)
            if outcome == ApprovalOutcome.APPROVED and h["status"] == "approved":
                return  # a retried decision changes nothing
            # a return for changes also lands in draft: the buyer edits and submits again
            action = {
                ApprovalOutcome.APPROVED: "approve",
                ApprovalOutcome.WITHDRAWN: "withdraw",
            }.get(outcome, "reject")
            transition(
                self.connection, self.scope, id, action, context.actor_id, cast(int, h["version"])
            )

    def evidence(self, po: PoRead) -> dict[str, Any]:
        """What the approver's panel shows (PO-003); decimal strings only."""
        eligibility = (
            VendorService(self.connection, self.scope)
            .eligibility([po.vendor_id], po.unit_id, today())
            .get(po.vendor_id)
        )
        effect: dict[UUID, Decimal] = defaultdict(Decimal)
        for line in po.lines:
            effect[line.project_id] += Decimal(line.budget_amount_project)
        return {
            "award": award_evidence(self.connection, self.scope, po.award_id),
            "budget_effect": [
                {"project_id": str(k), "currency": po.converted_currency, "amount": _fmt(v)}
                for k, v in sorted(effect.items())
            ],
            "vendor_eligibility_now": None
            if eligibility is None
            else {
                "eligible": eligibility.eligible,
                "reasons": [r.text for r in eligibility.reasons],
            },
            # the vendor-visible items (E13-S11) join here when that story lands
            "recipients": po.recipients,
        }


def _po_exists(conn: Conn, scope: Scope, award_id: UUID) -> bool:
    """Supersede probe: any purchase order, even a draft, keeps its award (409 `po_exists`)."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        return (
            PurchaseOrderRepository(conn, scope)
            .execute(
                "SELECT 1 FROM purchase_order WHERE org_id = %(org_id)s AND award_id = %(id)s "
                "AND status <> 'deleted' LIMIT 1",
                {"id": award_id},
            )
            .fetchone()
            is not None
        )


register_supersede_guard(_po_exists)


def _po_links(
    conn: Conn, scope: Scope, requisition_id: UUID, read_check: object
) -> list[LinkedNode]:
    """The requisition's Linked Records list the purchase orders drawn against it."""
    rows = (
        PurchaseOrderRepository(conn, scope)
        .execute(
            "SELECT id, number, status, unit_id, created_at FROM purchase_order "
            "WHERE org_id = %(org_id)s AND requisition_id = %(id)s AND status <> 'deleted'",
            {"id": requisition_id},
        )
        .fetchall()
    )
    return [
        LinkedNode(
            key=f"purchase_order:{r[0]}",
            kind="purchase_order",
            id=str(r[0]),
            label=f"PO {r[1]}",
            state=str(r[2]).capitalize(),
            state_tone="neutral",
            href=f"/purchasing/orders/{r[0]}",
            at=cast(datetime, r[4]),
            relation="ordered_by",
            access=LinkAccess(
                permission="purchase_order.view", scope_type="bu", target_id=cast(UUID, r[3])
            ),
        )
        for r in rows
    ]


register_link_provider("purchase_order", _po_links)
