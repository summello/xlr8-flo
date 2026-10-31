"""Issue: freeze the budget amounts and convert the reservation into a commitment, once (E13-S05).

One transaction. Lock order: the award, project balances (id order), the requisition, then the PO
row. Approval posts nothing; this is the only place a PO touches the ledger (claude-plan
section 14 #5).
"""

from collections import defaultdict
from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.money import quantize
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget.service import commit, lock_balance, reservation_remaining
from flo.modules.org.service import today
from flo.modules.purchasing import transmission
from flo.modules.purchasing.lifecycle import parse_if_match, transition
from flo.modules.purchasing.pdf import freeze
from flo.modules.purchasing.pricing import Figures
from flo.modules.purchasing.schemas import PoRead
from flo.modules.purchasing.service import PurchaseOrderService
from flo.modules.requisitions.service import (
    assert_current_upstream,
    issue_lines,
    mark_completed,
    reservation_entry,
)
from flo.modules.sourcing.service import lock_award
from flo.modules.vendors.service import VendorService

type Conn = psycopg.Connection[tuple[object, ...]]


def enqueue_transmission(conn: Conn, scope: Scope, po_id: UUID, version: int) -> None:
    """The send goes into the outbox in the issue transaction (E13-S09)."""
    transmission.enqueue(conn, scope, po_id, version)


def notify_requester_issued(conn: Conn, scope: Scope, po_id: UUID) -> None:
    """E17-S02 wires the requester's in-app notification here, in the issue transaction."""


def issue(conn: Conn, scope: Scope, po_id: UUID, actor_id: UUID, if_match: str | None) -> PoRead:
    service = PurchaseOrderService(conn, scope, actor_id)
    expected = parse_if_match(if_match)
    with tenant_transaction(cast(RlsSession, conn), scope):
        # 404 before any lock or version talk; the award first, as ChangeService._lock and cancel
        # do (E13-S07: issue's `UPDATE purchase_order` key-shares the award row)
        lock_award(conn, scope, cast(UUID, service._row(po_id)["award_id"]))
        lines = service.repo.execute(
            "SELECT line_no, requisition_id, requisition_line_id, quantity, project_id, "
            "department_code, ledger_account_code, price, tax, freight FROM po_line "
            "WHERE org_id = %(org_id)s AND po_id = %(id)s ORDER BY line_no",
            {"id": po_id},
        ).fetchall()
        for project_id in sorted({cast(UUID, r[4]) for r in lines}):
            lock_balance(conn, scope, project_id)
        requisitions = sorted({cast(UUID, r[1]) for r in lines})
        entries = {r: reservation_entry(conn, scope, r, lock=True) for r in requisitions}
        h = service._row(po_id, lock=True)
        version = cast(int, h["version"])
        if h["status"] == "issued" and expected in (version, version - 1):
            return service.get(po_id)  # a retry or the loser of a race: same result (PO-012)
        if h["status"] != "approved":
            raise conflict("invalid_transition", "Only an approved purchase order can be issued.")
        if expected != version:
            raise conflict("stale_version", "The purchase order changed. Reload it and retry.")
        recipients = h["recipients"] or [
            r.model_dump()
            for r in VendorService(conn, scope, actor_id).document_recipients(
                cast(UUID, h["vendor_id"])
            )
        ]
        if not recipients:
            raise conflict(*transmission.NO_RECIPIENTS)
        if not h["recipients"]:
            service.repo.execute(
                "UPDATE purchase_order SET recipients = %(r)s "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": po_id, "r": Jsonb(recipients)},
            )
        required = cast(
            int,
            service.repo.execute(
                "SELECT requisition_version FROM purchase_order "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": po_id},
            ).fetchone()[0],  # type: ignore[index]
        )
        for requisition_id in requisitions:
            assert_current_upstream(conn, scope, requisition_id, required)
        for r in lines:
            if r[5] is None or r[6] is None:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="A purchase order line has no department or ledger account code.",
                    checks={"problem": "missing_accounting_code", "line_no": str(r[0])},
                )

        # freeze the budget amounts under the locks; the read path now reads these columns
        currency = cast(str, h["currency"]).strip()
        rate = cast(dict[str, str], h["rate"])
        flags = service._flags(cast(UUID, h["unit_id"]))
        groups: dict[tuple[UUID, UUID, str, str], Decimal] = defaultdict(Decimal)
        for r in lines:
            f = Figures(*(quantize(cast(Decimal, v), currency) for v in r[7:10]))
            budget, project = service._budget(f, flags, rate, currency)
            service.repo.execute(
                "UPDATE po_line SET budget_amount = %(b)s, budget_amount_project = %(p)s "
                "WHERE org_id = %(org_id)s AND po_id = %(id)s AND line_no = %(no)s",
                {"id": po_id, "no": r[0], "b": budget, "p": project},
            )
            groups[(cast(UUID, r[1]), cast(UUID, r[4]), cast(str, r[5]), cast(str, r[6]))] += (
                project
            )
        if any(amount <= 0 for amount in groups.values()):
            raise conflict("zero_value_line", "A purchase order line has no value to commit.")

        for key in sorted(groups, key=lambda k: tuple(map(str, k))):
            requisition_id, project_id, department, ledger = key
            amount = groups[key]
            entry = entries[requisition_id]
            held = (
                reservation_remaining(conn, scope, entry).remaining
                if entry is not None
                else Decimal(0)
            )
            excess = max(amount - held, Decimal(0))
            available = lock_balance(conn, scope, project_id).available
            if excess > available:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="This purchase order needs more than the requisition reserved and "
                    "the project has available.",
                    checks={
                        "problem": "over_reservation",
                        "group_key": f"{requisition_id}|{project_id}|{department}|{ledger}",
                        "requested": format(amount, "f"),
                        "reservation_remaining": format(held, "f"),
                        "excess": format(excess, "f"),
                        "available": format(available, "f"),
                    },
                )
            commit(
                conn,
                scope,
                project_id=project_id,
                amount=amount,
                currency=rate["to"],
                source_type="purchase_order",
                source_id=po_id,
                effective_date=today(),
                actor_id=actor_id,
                department_code=department,
                ledger_account_code=ledger,
                idempotency_key=f"po:{po_id}:v{version}:commit:{requisition_id}|{project_id}|"
                f"{department}|{ledger}",
                from_reservation_entry_id=entry,
            )

        issue_lines(conn, scope, [(cast(UUID, r[2]), cast(Decimal, r[3])) for r in lines], actor_id)
        transition(conn, scope, po_id, "issue", actor_id, version)
        service.repo.execute(
            "UPDATE purchase_order SET issued_at = now(), issued_by = %(actor)s "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": po_id, "actor": actor_id},
        )
        freeze(conn, scope, po_id)  # E13-S08: before the send reads the snapshot
        for requisition_id in requisitions:
            mark_completed(conn, scope, requisition_id, actor_id)
        enqueue_transmission(conn, scope, po_id, version + 1)
        notify_requester_issued(conn, scope, po_id)
    return service.get(po_id)
