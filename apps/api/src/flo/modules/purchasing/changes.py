"""Numbered change orders against an issued purchase order (E13-S06, PO-010, PO-011, ACC-005).

An issued PO is never edited. A change order holds the proposed edits (`draft`), the field-level
`diff`, the signed `budget_effect` and `material`, all computed by the server. Applying it writes
a new `purchase_order` revision (same number, `revision + 1`), supersedes the old row and posts
the budget effect, in one transaction. Lock order: award, project balances (id order), the
requisition, the PO row, the change row.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.money import quantize
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.schemas import Outcome as ApprovalOutcome
from flo.modules.approvals.schemas import OutcomeContext
from flo.modules.approvals.service import require_workflow_for, start, withdraw
from flo.modules.budget.service import (
    commit,
    lock_balance,
    release_commitment,
    reservation_remaining,
)
from flo.modules.identity.service import AuthorizationConnection, AuthorizationResolver
from flo.modules.org.service import today
from flo.modules.purchasing.issue import enqueue_transmission
from flo.modules.purchasing.lifecycle import parse_if_match, transition
from flo.modules.purchasing.pdf import freeze
from flo.modules.purchasing.pricing import Figures, landed, spread
from flo.modules.purchasing.schemas import PoChangeCreate, PoChangePatch, PoChangeRead
from flo.modules.purchasing.service import PurchaseOrderService, _fmt, _invalid
from flo.modules.requisitions.schemas import LineSourcing
from flo.modules.requisitions.service import (
    issue_lines,
    mark_completed,
    order_lines,
    reopen_for_purchase,
    rereserve,
    reservation_entry,
)
from flo.modules.sourcing.service import (
    award_line_quotes,
    awarded_lines,
    lock_award,
    order_award_lines,
)

type Conn = psycopg.Connection[tuple[object, ...]]

# A change to any of these is a change to an amount, an item or the line count: it needs approval.
# Dates and terms alone are not material (decision 166).
MATERIAL = {"quantity", "unit_price", "tax", "freight", "document_freight", "line"}
LINE = (
    "line_no, award_id, rfq_line_id, vendor_id, requisition_id, requisition_line_id, description, "
    "uom_code, quantity, unit_price, project_id, department_code, ledger_account_code, price, "
    "tax, freight, line_total, budget_amount, budget_amount_project"
)
CHANGE = (
    "id, po_id, root_po_id, number, version, from_version, status, material, reason, "
    "created_by, created_at, "
    "draft, diff, budget_effect, result_po_id, total, budget_total_project"
)
Group = tuple[UUID, str, str]  # project, department code, ledger account code


@dataclass
class Proposal:
    header: dict[str, Any]
    old: list[dict[str, Any]]
    lines: list[dict[str, Any]]
    diff: list[dict[str, Any]]
    deltas: dict[Group, Decimal]  # signed, in the project currency
    material: bool
    currency: str
    to: str
    total: Decimal = Decimal(0)
    budget_total_project: Decimal = Decimal(0)
    effect: list[dict[str, Any]] = field(default_factory=list)


def _q4(value: Decimal) -> str:
    return _fmt(value.quantize(Decimal("0.0001")))


def _entry(
    line_no: int | None, field_: str, label: str, was: str | None, now: str | None
) -> dict[str, Any]:
    return {"line_no": line_no, "field": field_, "label": label, "was": was, "now": now}


class ChangeService:
    def __init__(self, connection: Conn, scope: Scope, actor_id: UUID | None = None) -> None:
        self.connection, self.scope, self.actor_id = connection, scope, actor_id
        self.po = PurchaseOrderService(connection, scope, actor_id)
        self.repo = self.po.repo

    # -- reads -----------------------------------------------------------------------------

    def _read(
        self, *, id: UUID | None = None, po_id: UUID | None = None, number: int | None = None,
        lock: bool = False,
    ) -> PoChangeRead:  # fmt: skip
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            cur = self.repo.execute(
                f"SELECT {CHANGE} FROM po_change WHERE org_id = %(org_id)s AND "
                + ("id = %(id)s" if id else "root_po_id = %(root)s AND number = %(n)s")
                + (" FOR UPDATE" if lock else ""),
                {"id": id, "root": None if id else self._root(cast(UUID, po_id)), "n": number},
            )
            row = cur.fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        fields: dict[str, Any] = dict(
            zip((d.name for d in cur.description or []), row, strict=True)
        )
        for key in ("total", "budget_total_project"):
            fields[key] = _fmt(fields[key])
        return PoChangeRead(**fields)

    def get(self, po_id: UUID, number: int) -> PoChangeRead:
        self.po.unit_of(po_id)  # 404 for an unknown or foreign order first
        return self._read(po_id=po_id, number=number)

    def get_by_id(self, id: UUID) -> PoChangeRead:
        return self._read(id=id)

    def list_changes(self, po_id: UUID) -> list[PoChangeRead]:
        self.po.unit_of(po_id)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            numbers = [
                cast(int, r[0])
                for r in self.repo.execute(
                    "SELECT number FROM po_change WHERE org_id = %(org_id)s "
                    "AND root_po_id = %(root)s ORDER BY number",
                    {"root": self._root(po_id)},
                ).fetchall()
            ]
        return [self._read(po_id=po_id, number=n) for n in numbers]

    def _audit(self, action: str, ch: PoChangeRead, after: dict[str, object]) -> None:
        audit_event(
            cast(AuditConnection, self.connection),
            self.scope,
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=f"purchase_order.change_{action}",
            target_type="purchase_order",
            target_id=ch.po_id,
            outcome=Outcome.SUCCESS,
            before=None,
            after={"number": ch.number} | after,
        )

    # -- the proposal ----------------------------------------------------------------------

    def propose(self, po_id: UUID, draft: dict[str, Any]) -> Proposal:
        """The draft applied to the PO as it stands: new lines, diff, budget effect, materiality.

        Raises the 422 and 409 the apply would, so a draft that cannot apply is refused early."""
        s = self.po
        h = s._row(po_id)
        currency, rate, mode = (
            cast(str, h["currency"]).strip(),
            cast(dict[str, str], h["rate"]),
            h["freight_mode"],
        )
        old: list[dict[str, Any]] = [
            dict(zip(LINE.split(", "), r, strict=True))
            for r in self.repo.execute(
                f"SELECT {LINE} FROM po_line WHERE org_id = %(org_id)s AND po_id = %(id)s "
                "ORDER BY line_no",
                {"id": po_id},
            ).fetchall()
        ]
        edits: dict[str, dict[str, Any]] = draft.get("lines", {})
        adds: list[dict[str, Any]] = draft.get("add", [])
        if set(edits) - {str(x["line_no"]) for x in old}:
            raise _invalid("lines", "The order has no such line.", "unknown_line")
        award_id, vendor_id = cast(UUID, h["award_id"]), cast(UUID, h["vendor_id"])
        have = {
            x.rfq_line_id: x
            for x in awarded_lines(self.connection, self.scope, award_id, vendor_id=vendor_id)
        }
        quotes = {
            (q.rfq_line_id, q.vendor_id): q
            for q in award_line_quotes(self.connection, self.scope, award_id)
        }
        diff: list[dict[str, Any]] = []
        keep: list[dict[str, Any]] = []
        for x in old:
            n, e = cast(int, x["line_no"]), edits.get(str(x["line_no"]), {})
            if e.get("remove"):
                diff.append(
                    _entry(
                        n,
                        "line",
                        f"Line {n}",
                        f"{x['description']} x {_q4(x['quantity'])}",
                        "Removed",
                    )
                )
                continue
            qty = Decimal(e["quantity"]) if e.get("quantity") is not None else x["quantity"]
            price = Decimal(e["unit_price"]) if e.get("unit_price") is not None else x["unit_price"]
            if price > x["unit_price"]:
                raise _invalid(
                    f"lines/{n}/unit_price",
                    "A price can only go down; a price above the award needs a new award.",
                    "price_increase",
                )
            new = {**x, "quantity": qty, "unit_price": price}
            if qty != x["quantity"] or price != x["unit_price"]:
                q = quotes[(x["rfq_line_id"], x["vendor_id"])]
                f = landed(
                    price,
                    qty,
                    Decimal(q.tax),
                    Decimal(q.freight_share),
                    Decimal(q.quoted_quantity),
                    currency,
                )
                new |= {"price": f.price, "tax": f.tax, "freight": f.freight}
            if e.get("tax") is not None:
                new["tax"] = quantize(Decimal(e["tax"]), currency)
            if e.get("freight") is not None:
                if mode != "line":
                    raise _invalid(
                        f"lines/{n}/freight",
                        "Line freight needs the line freight mode.",
                        "freight_mode_mismatch",
                    )
                new["freight"] = quantize(Decimal(e["freight"]), currency)
            keep.append(new)
        next_no = max((x["line_no"] for x in old), default=0)
        seen: set[UUID] = {x["rfq_line_id"] for x in old}
        for a in adds:
            x_ = have.get(UUID(a["rfq_line_id"]))
            if x_ is None:
                raise _invalid("add", "A line was not awarded to this vendor.", "mixed_vendors")
            if x_.requisition_id != h["requisition_id"]:
                raise _invalid(
                    "add",
                    "A line from another requisition needs its own purchase order.",
                    "invalid",
                )
            if x_.rfq_line_id in seen:
                raise _invalid("add", "A line appears twice.", "duplicate_line")
            seen.add(x_.rfq_line_id)
            qty, q = Decimal(a["quantity"]), quotes[(x_.rfq_line_id, x_.vendor_id)]
            f = landed(
                x_.unit_price,
                qty,
                Decimal(q.tax),
                Decimal(q.freight_share),
                Decimal(q.quoted_quantity),
                currency,
            )
            next_no += 1
            # ponytail: a new line takes the first line's project and codes (one requisition, one
            # sub-project); a PO that mixes groups needs the line's group named in the draft.
            base = old[0]
            keep.append({
                "line_no": next_no, "award_id": award_id, "rfq_line_id": x_.rfq_line_id,
                "vendor_id": x_.vendor_id, "requisition_id": x_.requisition_id,
                "requisition_line_id": x_.requisition_line_id, "description": x_.description,
                "uom_code": x_.uom_code, "quantity": qty, "unit_price": x_.unit_price,
                "project_id": base["project_id"], "department_code": base["department_code"],
                "ledger_account_code": base["ledger_account_code"], "price": f.price,
                "tax": f.tax, "freight": f.freight, "line_total": Decimal(0),
                "budget_amount": Decimal(0), "budget_amount_project": Decimal(0),
            })  # fmt: skip
            diff.append(
                _entry(
                    next_no, "line", f"Line {next_no}", None, f"Added {x_.description} x {_q4(qty)}"
                )
            )
        # the apply moves these against the award; refuse here what it would refuse there
        gone = {x["rfq_line_id"]: x["quantity"] for x in old}
        grew = {
            k["rfq_line_id"]: k["quantity"] - gone.get(k["rfq_line_id"], Decimal(0)) for k in keep
        }
        over = {
            key: (min(have[key].quantity - have[key].ordered_qty, have[key].orderable), delta)
            for key, delta in grew.items()
            if delta > 0
            and key in have
            and delta > min(have[key].quantity - have[key].ordered_qty, have[key].orderable)
        }
        if over:
            checks = {"problem": "over_open_quantity"}
            for key, (open_qty, requested) in over.items():
                checks[f"open.{key}"] = format(open_qty, "f")
                checks[f"requested.{key}"] = format(requested, "f")
            raise ProblemError(
                ErrorCode.CONFLICT,
                detail="The quantity asked for is more than is still open to order on the award.",
                checks=checks,
            )
        if not keep:
            raise _invalid("lines", "A purchase order keeps at least one line.", "no_lines")
        document = cast(Decimal | None, h["document_freight"])
        if mode == "document":
            if draft.get("document_freight") is not None:
                document = quantize(Decimal(draft["document_freight"]), currency)
            shares = spread(
                cast(Decimal, document), [k["unit_price"] * k["quantity"] for k in keep], currency
            )
            for k, share in zip(keep, shares, strict=True):
                k["freight"] = share
        elif draft.get("document_freight") is not None:
            raise _invalid(
                "document_freight",
                "Document freight needs the document freight mode.",
                "freight_mode_mismatch",
            )
        if document != h["document_freight"]:
            diff.append(_entry(None, "document_freight", "Document freight",
                               _fmt(quantize(cast(Decimal, h["document_freight"]), currency)),
                               _fmt(quantize(cast(Decimal, document), currency))))  # fmt: skip
        flags = s._flags(cast(UUID, h["unit_id"]))
        was = {x["line_no"]: x for x in old}
        for k in keep:
            if k["price"] == 0:
                raise _invalid(
                    f"lines/{k['line_no']}/quantity",
                    "This quantity is too small to price.",
                    "zero_line",
                )
            k["line_total"] = Figures(k["price"], k["tax"], k["freight"]).line_total
            before = was.get(k["line_no"])
            if before is None or any(
                k[c] != before[c] for c in ("quantity", "unit_price", "price", "tax", "freight")
            ):
                # decision 167: only a changed line is revalued; the rest keeps its frozen amounts
                k["budget_amount"], k["budget_amount_project"] = s._budget(
                    Figures(k["price"], k["tax"], k["freight"]), flags, rate, currency
                )
            else:
                k["budget_amount"], k["budget_amount_project"] = (
                    before["budget_amount"],
                    before["budget_amount_project"],
                )
            if before is None:
                continue
            for column, label, show in (
                ("quantity", "Quantity", _q4), ("unit_price", "Unit price", _q4),
                ("tax", "Tax", lambda v: _fmt(quantize(v, currency))),
                ("freight", "Freight", lambda v: _fmt(quantize(v, currency))),
            ):  # fmt: skip
                if k[column] != before[column]:
                    diff.append(
                        _entry(k["line_no"], column, label, show(before[column]), show(k[column]))
                    )
        header: dict[str, Any] = {
            "terms": h["terms"],
            "needed_by": h["needed_by"],
            "document_freight": document,
        }
        for key, label in (("terms", "Terms"), ("needed_by", "Needed by")):
            if key in draft:
                value = (
                    draft[key]
                    if key == "terms"
                    else (None if draft[key] is None else date.fromisoformat(draft[key]))
                )
                if value != h[key]:
                    diff.append(
                        _entry(
                            None,
                            key,
                            label,
                            None if h[key] is None else str(h[key]),
                            None if value is None else str(value),
                        )
                    )
                    header[key] = value
        deltas: dict[Group, Decimal] = {}
        for rows, sign in ((old, -1), (keep, 1)):
            for x in rows:
                g = (
                    cast(UUID, x["project_id"]),
                    cast(str, x["department_code"]),
                    cast(str, x["ledger_account_code"]),
                )
                deltas[g] = deltas.get(g, Decimal(0)) + sign * cast(
                    Decimal, x["budget_amount_project"]
                )
        deltas = {
            g: quantize(d, rate["to"]) for g, d in deltas.items() if quantize(d, rate["to"]) != 0
        }
        return Proposal(
            header, old, keep, diff, deltas,
            any(d["field"] in MATERIAL for d in diff), currency, rate["to"],
            sum((k["line_total"] for k in keep), Decimal(0)),
            sum((k["budget_amount_project"] for k in keep), Decimal(0)),
            [
                {"project_id": str(p), "department_code": d, "ledger_account_code": c,
                 "currency": rate["to"], "delta": _fmt(v)}
                for (p, d, c), v in sorted(deltas.items(), key=lambda i: tuple(map(str, i[0])))
            ],
        )  # fmt: skip

    def _store(self, ch: PoChangeRead, draft: dict[str, Any], reason: str | None) -> None:
        proposal = self.propose(ch.po_id, draft)
        self.repo.execute(
            "UPDATE po_change SET draft = %(draft)s, diff = %(diff)s, budget_effect = %(effect)s, "
            "material = %(material)s, reason = %(reason)s, total = %(total)s, "
            "budget_total_project = %(budget)s, version = version + 1 "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {
                "id": ch.id, "draft": Jsonb(draft), "diff": Jsonb(proposal.diff),
                "effect": Jsonb(proposal.effect), "material": proposal.material, "reason": reason,
                "total": proposal.total, "budget": proposal.budget_total_project,
            },
        )  # fmt: skip

    # -- writes ----------------------------------------------------------------------------

    def create(self, po_id: UUID, body: PoChangeCreate) -> PoChangeRead:
        """Lock the PO row, allow one open change per PO, number it and save an empty draft."""
        actor = cast(UUID, self.actor_id)
        id = uuid4()
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            h = self.po._row(po_id, lock=True)
            if h["status"] != "issued":
                raise conflict(
                    "po_not_issued", "Only an issued purchase order takes a change order."
                )
            root = self._root(po_id)  # numbers run across the whole revision chain
            if self.repo.execute(
                "SELECT 1 FROM po_change WHERE org_id = %(org_id)s AND root_po_id = %(root)s "
                "AND status IN ('draft', 'approval_pending')",
                {"root": root},
            ).fetchone():
                raise conflict(
                    "change_in_progress", "This purchase order already has an open change order."
                )
            number = cast(
                int,
                self.repo.execute(
                    "SELECT coalesce(max(number), 0) + 1 FROM po_change "
                    "WHERE org_id = %(org_id)s AND root_po_id = %(root)s",
                    {"root": root},
                ).fetchone()[0],  # type: ignore[index]
            )
            self.repo.execute(
                "INSERT INTO po_change (org_id, id, po_id, root_po_id, number, from_version, "
                "reason, created_by) VALUES (%(org_id)s, %(id)s, %(po)s, %(root)s, %(n)s, "
                "%(rev)s, %(reason)s, %(by)s)",
                {
                    "id": id,
                    "po": po_id,
                    "root": root,
                    "n": number,
                    "rev": h["revision"],
                    "reason": body.reason,
                    "by": actor,
                },
            )
            ch = self._read(id=id)
            self._audit("create", ch, {"reason": body.reason})
        return ch

    def update(
        self, po_id: UUID, number: int, body: PoChangePatch, if_match: str | None
    ) -> PoChangeRead:
        expected = parse_if_match(if_match)
        for line in body.lines or []:
            if line.model_fields_set & {"project_id", "department_code", "ledger_account_code"}:
                raise _invalid(
                    f"lines/{line.line_no}",
                    "A change order cannot move a line to another project, department or "
                    "ledger account. Use a transfer.",
                    "use_transfer",
                )
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.po._row(po_id, lock=True)
            ch = self._read(po_id=po_id, number=number, lock=True)
            if ch.status != "draft":
                raise conflict("invalid_transition", "Only a draft change order can be edited.")
            if ch.version != expected:
                raise conflict("stale_version", "The change order changed. Reload it and retry.")
            draft = dict(ch.draft)
            for key in ("terms", "needed_by"):
                if key in body.model_fields_set:
                    value = getattr(body, key)
                    draft[key] = (
                        None if value is None else (value if key == "terms" else value.isoformat())
                    )
            if body.document_freight is not None:
                draft["document_freight"] = body.document_freight
            if body.lines is not None:
                draft["lines"] = {
                    str(x.line_no): x.model_dump(
                        mode="json", exclude={"line_no"}, exclude_none=True, exclude_defaults=True
                    )
                    for x in body.lines
                }
            if body.add is not None:
                draft["add"] = [x.model_dump(mode="json") for x in body.add]
            self._store(ch, draft, body.reason if "reason" in body.model_fields_set else ch.reason)
            after = self._read(id=ch.id)
            self._audit("update", after, {"diff": after.diff})
        return after

    def submit(self, po_id: UUID, number: int, if_match: str | None) -> PoChangeRead:
        """Material changes only (decision 208: a published `po_change` workflow, never auto)."""
        expected, actor = parse_if_match(if_match), cast(UUID, self.actor_id)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.po._row(po_id, lock=True)
            ch = self._read(po_id=po_id, number=number, lock=True)
            if ch.status != "draft":
                raise conflict("invalid_transition", "Only a draft change order can be submitted.")
            if ch.version != expected:
                raise conflict("stale_version", "The change order changed. Reload it and retry.")
            if not ch.diff:
                raise _invalid("diff", "This change order changes nothing.", "no_changes")
            if not ch.material:
                raise conflict("not_material", "A change that is not material applies directly.")
            require_workflow_for(
                self.connection, self.scope, "po_change", ch.id, "purchase order changes"
            )
            self.repo.execute(
                "UPDATE po_change SET status = 'approval_pending', version = version + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": ch.id},
            )
            start(
                self.connection, self.scope, document_type="po_change", document_id=ch.id,
                actor_id=actor,
                resolver=AuthorizationResolver(
                    cast(AuthorizationConnection, self.connection), self.scope
                ),
            )  # fmt: skip
            after = self._read(id=ch.id)
            self._audit("submit", after, {})
        return after

    def withdraw(self, po_id: UUID, number: int, if_match: str | None) -> PoChangeRead:
        """The creator withdraws; the approval ends and the adapter settles the change."""
        expected, actor = parse_if_match(if_match), cast(UUID, self.actor_id)
        ch = self.get(po_id, number)
        if ch.created_by != actor:
            raise ProblemError(
                ErrorCode.FORBIDDEN, detail="Only the author of this change order can withdraw it."
            )
        if ch.status != "approval_pending":
            raise conflict(
                "invalid_transition", "Only a change order pending approval can be withdrawn."
            )
        if ch.version != expected:
            raise conflict("stale_version", "The change order changed. Reload it and retry.")
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            withdraw(
                self.connection,
                self.scope,
                document_type="po_change",
                document_id=ch.id,
                actor_id=actor,
                reason="",
            )
            after = self._read(id=ch.id)
        return after

    def settle(self, id: UUID, outcome: ApprovalOutcome, context: OutcomeContext) -> None:
        """Inside the decision's transaction: approved applies, anything else ends the change."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            ch = self._read(id=id)
            if outcome == ApprovalOutcome.APPROVED:
                self._lock(ch.po_id)
                ch = self._read(id=id, lock=True)
                if ch.status == "approved":
                    return  # a retried decision changes nothing
                self._apply(ch, context.actor_id)
                return
            status = "withdrawn" if outcome == ApprovalOutcome.WITHDRAWN else "rejected"
            self.repo.execute(
                "UPDATE po_change SET status = %(s)s, version = version + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s AND status = 'approval_pending'",
                {"id": id, "s": status},
            )

    def apply(self, po_id: UUID, number: int, if_match: str | None) -> PoChangeRead:
        """A non-material change applies at once; a retry or the loser of a race gets the result."""
        expected = parse_if_match(if_match)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.po._row(po_id)  # 404 before any lock
            self._read(po_id=po_id, number=number)
            self._lock(po_id)
            ch = self._read(po_id=po_id, number=number, lock=True)
            if ch.status == "approved" and expected in (ch.version, ch.version - 1):
                return ch
            if ch.status != "draft":
                raise conflict("invalid_transition", "Only a draft change order can be applied.")
            if ch.version != expected:
                raise conflict("stale_version", "The change order changed. Reload it and retry.")
            if ch.material:
                raise conflict("approval_required", "A material change needs approval. Submit it.")
            self._apply(ch, cast(UUID, self.actor_id))
            return self._read(id=ch.id)

    # -- apply -----------------------------------------------------------------------------

    def _lock(self, po_id: UUID) -> None:
        """Award, project balances in id order, the requisition, then the PO row."""
        h = self.po._row(po_id)
        lock_award(self.connection, self.scope, cast(UUID, h["award_id"]))
        projects = sorted(
            cast(UUID, r[0])
            for r in self.repo.execute(
                "SELECT DISTINCT project_id FROM po_line "
                "WHERE org_id = %(org_id)s AND po_id = %(id)s",
                {"id": po_id},
            ).fetchall()
        )
        for project_id in projects:
            lock_balance(self.connection, self.scope, project_id)
        reservation_entry(self.connection, self.scope, cast(UUID, h["requisition_id"]), lock=True)
        self.po._row(po_id, lock=True)

    def _root(self, po_id: UUID) -> UUID:
        return self.po._root(po_id)

    def _apply(self, ch: PoChangeRead, actor: UUID) -> None:
        """Under `_lock` and the change's row lock: move the quantities, post the budget effect,
        write the new revision and supersede the old one."""
        h = self.po._row(ch.po_id)
        proposal = self.propose(ch.po_id, ch.draft)
        if not proposal.diff:
            raise _invalid("diff", "This change order changes nothing.", "no_changes")
        requisition_id, award_id = cast(UUID, h["requisition_id"]), cast(UUID, h["award_id"])
        was = {x["rfq_line_id"]: x for x in proposal.old}
        now = {x["rfq_line_id"]: x for x in proposal.lines}
        moves: dict[UUID, tuple[dict[str, Any], Decimal]] = {}
        for key in was.keys() | now.keys():
            delta = (now[key]["quantity"] if key in now else Decimal(0)) - (
                was[key]["quantity"] if key in was else Decimal(0)
            )
            if delta != 0:
                moves[key] = (now.get(key) or was[key], delta)
        version = cast(
            int,
            self.repo.execute(
                "SELECT version FROM requisition WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": requisition_id},
            ).fetchone()[0],  # type: ignore[index]
        )
        order_award_lines(
            self.connection, self.scope, award_id,
            [(k, cast(UUID, m["vendor_id"]), d) for k, (m, d) in moves.items()],
        )  # fmt: skip
        # issued first when it falls (its ceiling is the ordered quantity), last when it rises
        issue_lines(
            self.connection,
            self.scope,
            [(m["requisition_line_id"], d) for m, d in moves.values() if d < 0],
            actor,
        )
        order_lines(
            self.connection, self.scope,
            [
                LineSourcing(requisition_id, m["requisition_line_id"], version, d)
                for m, d in moves.values()
            ],
            actor,
        )  # fmt: skip
        issue_lines(
            self.connection,
            self.scope,
            [(m["requisition_line_id"], d) for m, d in moves.values() if d > 0],
            actor,
        )
        root = self._root(ch.po_id)
        self._post(h, ch, root, proposal, actor)
        if any(d < 0 for _, d in moves.values()):
            reopen_for_purchase(self.connection, self.scope, requisition_id, actor)  # PO-011
        new_id = self._write_revision(h, proposal, actor)
        mark_completed(self.connection, self.scope, requisition_id, actor)
        enqueue_transmission(self.connection, self.scope, new_id, 2)
        self.repo.execute(
            "UPDATE po_change SET status = 'approved', version = version + 1, "
            "result_po_id = %(new)s "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": ch.id, "new": new_id},
        )
        self._audit(
            "apply",
            ch,
            {
                "diff": proposal.diff,
                "budget_effect": proposal.effect,
                "revision": cast(int, h["revision"]) + 1,
            },
        )

    def _post(
        self, h: dict[str, object], ch: PoChangeRead, root: UUID, proposal: Proposal, actor: UUID
    ) -> None:
        """Post the signed effect per group: an increase commits (E13-S05's excess rule), a
        decrease releases the group's open commitments newest first, then re-reserves the total
        on the requisition's sub-project (decision 209)."""
        requisition_id = cast(UUID, h["requisition_id"])
        entry = reservation_entry(self.connection, self.scope, requisition_id)
        stem = f"po:{ch.po_id}:co{ch.number}"
        returned = Decimal(0)
        for (project_id, department, ledger), delta in sorted(
            proposal.deltas.items(), key=lambda i: tuple(map(str, i[0]))
        ):
            group = f"{requisition_id}|{project_id}|{department}|{ledger}"
            if delta > 0:
                held = (
                    reservation_remaining(self.connection, self.scope, entry).remaining
                    if entry is not None
                    else Decimal(0)
                )
                excess = max(delta - held, Decimal(0))
                available = lock_balance(self.connection, self.scope, project_id).available
                if excess > available:
                    raise ProblemError(
                        ErrorCode.CONFLICT,
                        detail="This change needs more than the requisition reserved and "
                        "the project has available.",
                        checks={
                            "problem": "over_reservation",
                            "group_key": group,
                            "requested": format(delta, "f"),
                            "reservation_remaining": format(held, "f"),
                            "excess": format(excess, "f"),
                            "available": format(available, "f"),
                        },
                    )  # fmt: skip
                commit(
                    self.connection, self.scope, project_id=project_id, amount=delta,
                    currency=proposal.to, source_type="purchase_order", source_id=root,
                    effective_date=today(), actor_id=actor, department_code=department,
                    ledger_account_code=ledger, idempotency_key=f"{stem}:commit:{group}",
                    from_reservation_entry_id=entry,
                )  # fmt: skip
                continue
            left = -delta
            # ponytail: reads the budget module's commitment view directly; route through a
            # budget service probe if a second module ever needs open commitments by source.
            for entry_id, remaining in self.repo.execute(
                "SELECT e.id, r.remaining FROM ledger_entry e JOIN commitment_remaining r "
                "ON r.org_id = e.org_id AND r.commitment_entry_id = e.id "
                "WHERE e.org_id = %(org_id)s AND e.source_type = 'purchase_order' "
                "AND e.source_id = %(root)s AND e.entry_type = 'commitment' "
                "AND e.project_id = %(p)s "
                "AND e.department_code = %(d)s AND e.ledger_account_code = %(l)s "
                "AND r.remaining > 0 "
                "ORDER BY e.id DESC",
                {"root": root, "p": project_id, "d": department, "l": ledger},
            ).fetchall():
                take = min(left, cast(Decimal, remaining))
                release_commitment(
                    self.connection, self.scope, commitment_entry_id=cast(int, entry_id),
                    amount=take, actor_id=actor, reason=f"Purchase order change {ch.number}",
                    idempotency_key=f"{stem}:release:{group}:{entry_id}",
                )  # fmt: skip
                left -= take
                if left == 0:
                    break
            if left != 0:
                raise conflict(
                    "release_exceeds_commitment", "The change releases more than is committed."
                )
            returned += -delta
        if returned > 0 and entry is not None:
            rereserve(
                self.connection,
                self.scope,
                requisition_id,
                amount=returned,
                key=f"po_change_{ch.number}",
                actor_id=actor,
            )

    def _write_revision(self, h: dict[str, object], proposal: Proposal, actor: UUID) -> UUID:
        """Insert the new revision `approved`, supersede the old, issue the new through the
        lifecycle (version + 1); the vendor-sent snapshot of the old row is untouched."""
        old_id, new_id = cast(UUID, h["id"]), uuid4()
        header = proposal.header
        self.repo.execute(
            "INSERT INTO purchase_order (id, org_id, number, unit_id, vendor_id, requisition_id, "
            "vendor_snapshot, award_id, currency, rate, bill_to, ship_to_address_id, recipients, "
            "requester_id, terms, needed_by, status, version, created_by, created_at, "
            "requisition_version, freight_mode, document_freight, revision, supersedes_id) "
            "SELECT %(new)s, org_id, number, unit_id, vendor_id, requisition_id, vendor_snapshot, "
            "award_id, currency, rate, bill_to, ship_to_address_id, recipients, requester_id, "
            "%(terms)s, %(needed)s, 'approved', 1, created_by, now(), requisition_version, "
            "freight_mode, %(doc)s, revision + 1, id FROM purchase_order "
            "WHERE org_id = %(org_id)s AND id = %(old)s",
            {
                "new": new_id,
                "old": old_id,
                "terms": header["terms"],
                "needed": header["needed_by"],
                "doc": header["document_freight"],
            },
        )
        for k in proposal.lines:
            self.repo.execute(
                f"INSERT INTO po_line (org_id, po_id, {LINE}) VALUES (%(org_id)s, %(po)s, "
                + ", ".join(f"%({c})s" for c in LINE.split(", "))
                + ")",
                {"po": new_id} | {c: k[c] for c in LINE.split(", ")},
            )
        transition(self.connection, self.scope, old_id, "supersede", actor, cast(int, h["version"]))
        transition(self.connection, self.scope, new_id, "issue", actor, 1)
        self.repo.execute(
            "UPDATE purchase_order SET issued_at = now(), issued_by = %(actor)s "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": new_id, "actor": actor},
        )
        freeze(self.connection, self.scope, new_id)  # E13-S08: before the send reads the snapshot
        return new_id
