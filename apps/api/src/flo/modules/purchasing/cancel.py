"""Cancel or reduce a purchase order: release the unused commitment exactly once (E13-S07, PO-013).

Cancel lock order, one transaction: the PO read unlocked, the award, project balances (id order),
the requisition, the approval instance (only for a PO pending approval), the PO row. That is
`ChangeService._lock`'s order with the main project and the instance added. Reduce is a
quantities-only change order through E13-S06, so there is one release path.
"""

from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.errors import conflict
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.service import withdraw
from flo.modules.budget.service import lock_balance, release_commitment, reservation_remaining
from flo.modules.purchasing.changes import ChangeService
from flo.modules.purchasing.lifecycle import parse_if_match, transition
from flo.modules.purchasing.schemas import (
    PoCancelCreate,
    PoCancelRead,
    PoChangeCreate,
    PoChangePatch,
    PoChangeRead,
    PoReduceCreate,
)
from flo.modules.purchasing.service import PurchaseOrderService, _invalid
from flo.modules.requisitions.schemas import LineSourcing
from flo.modules.requisitions.service import (
    cancel_lines,
    issue_lines,
    mark_completed,
    order_lines,
    release_unawarded,
    reopen_for_purchase,
    rereserve,
    reservation_entry,
)
from flo.modules.sourcing.service import lock_award, order_award_lines

type Conn = psycopg.Connection[tuple[object, ...]]

NOT_ISSUED_NOTICE = (
    "Requisition lines were not cancelled: the PO was never issued. Cancel them on the requisition."
)


def notify_vendor_cancelled(conn: Conn, scope: Scope, po_id: UUID) -> None:
    """E13-S09 wires the vendor's cancellation email into the outbox here, in the cancel
    transaction."""


def cancel(
    conn: Conn,
    scope: Scope,
    po_id: UUID,
    actor_id: UUID,
    if_match: str | None,
    body: PoCancelCreate,
) -> PoCancelRead:
    service = PurchaseOrderService(conn, scope, actor_id)
    expected = parse_if_match(if_match)
    notice: str | None = None
    with tenant_transaction(cast(RlsSession, conn), scope):
        h = service._row(po_id)  # 404 before any lock or version talk
        award_id, requisition_id = cast(UUID, h["award_id"]), cast(UUID, h["requisition_id"])
        lock_award(conn, scope, award_id)  # 409 invalid_transition when the award is not approved
        lines = service.repo.execute(
            "SELECT line_no, rfq_line_id, vendor_id, requisition_line_id, quantity, project_id "
            "FROM po_line WHERE org_id = %(org_id)s AND po_id = %(id)s ORDER BY line_no",
            {"id": po_id},
        ).fetchall()
        req = service.repo.execute(
            "SELECT project_id, number FROM requisition WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": requisition_id},
        ).fetchone()
        assert req is not None
        entry = reservation_entry(conn, scope, requisition_id)
        projects = {cast(UUID, r[5]) for r in lines} | {cast(UUID, req[0])}
        if entry is not None:
            projects.add(reservation_remaining(conn, scope, entry).project_id)
        for project_id in sorted(projects):
            lock_balance(conn, scope, project_id)
        entry = reservation_entry(conn, scope, requisition_id, lock=True)
        pending = service._row(po_id)
        withdrew = pending["status"] == "approval_pending"
        if withdrew:
            if expected != pending["version"]:
                raise conflict("stale_version", "The purchase order changed. Reload it and retry.")
            service._creator_only(po_id)
            # the instance before the PO, as `decide` takes them; a closed instance is 409
            # `task_closed` and rolls everything back
            withdraw(
                conn, scope, document_type="po", document_id=po_id, actor_id=actor_id,
                reason=body.reason,
            )  # fmt: skip
        h = service._row(po_id, lock=True)
        status, version = h["status"], cast(int, h["version"])
        if status == "cancelled" and expected in (version, version - 1):
            return PoCancelRead(**service.get(po_id).model_dump())  # a retry: post nothing
        if (withdrew and (status != "draft" or version != expected + 1)) or (
            not withdrew and expected != version
        ):
            raise conflict("stale_version", "The purchase order changed. Reload it and retry.")
        if status in ("draft", "approved"):
            service._move(h, None)  # nothing was issued: both counters fall by every line
            if body.cancel_requisition_lines:
                notice = NOT_ISSUED_NOTICE
        elif status == "issued":
            _cancel_issued(conn, scope, service, h, lines, entry, cast(str, req[1]), actor_id, body)
        else:
            raise conflict("invalid_transition", "That purchase order cannot be cancelled.")
        transition(conn, scope, po_id, "cancel", actor_id, version, body.reason)
        if status == "issued" and body.notify_vendor:
            notify_vendor_cancelled(conn, scope, po_id)
        return PoCancelRead(**service.get(po_id).model_dump(), notice=notice)


def _cancel_issued(
    conn: Conn,
    scope: Scope,
    service: PurchaseOrderService,
    h: dict[str, object],
    lines: list[tuple[object, ...]],
    entry: int | None,
    req_number: str,
    actor: UUID,
    body: PoCancelCreate,
) -> None:
    po_id, award_id = cast(UUID, h["id"]), cast(UUID, h["award_id"])
    requisition_id, version = cast(UUID, h["requisition_id"]), cast(int, h["version"])
    root = service._root(po_id)
    if service.repo.execute(
        "SELECT 1 FROM po_change WHERE org_id = %(org_id)s AND root_po_id = %(root)s "
        "AND status IN ('draft', 'approval_pending')",
        {"root": root},
    ).fetchone():
        raise conflict(
            "change_in_progress", "This purchase order has an open change order. Withdraw it first."
        )
    released = Decimal(0)
    # ponytail: reads the budget module's commitment view directly, as ChangeService._post does;
    # route through a budget service probe if a second module needs open commitments by source.
    for entry_id, remaining in service.repo.execute(
        "SELECT e.id, r.remaining FROM ledger_entry e JOIN commitment_remaining r "
        "ON r.org_id = e.org_id AND r.commitment_entry_id = e.id "
        "WHERE e.org_id = %(org_id)s AND e.source_type = 'purchase_order' "
        "AND e.source_id = %(root)s AND e.entry_type = 'commitment' AND r.remaining > 0 "
        "ORDER BY e.id",
        {"root": root},
    ).fetchall():
        release_commitment(
            conn, scope, commitment_entry_id=cast(int, entry_id), amount=None, actor_id=actor,
            reason="Purchase order cancelled", idempotency_key=f"po:{po_id}:cancel:{entry_id}",
        )  # fmt: skip
        released += cast(Decimal, remaining)
    if entry is not None and released > 0:
        rereserve(
            conn, scope, requisition_id, amount=released, key=f"po_cancel_{po_id}", actor_id=actor
        )
    order_award_lines(
        conn, scope, award_id,
        [(cast(UUID, r[1]), cast(UUID, r[2]), -cast(Decimal, r[4])) for r in lines],
    )  # fmt: skip
    returned = [(cast(UUID, r[3]), cast(Decimal, r[4])) for r in lines]
    issue_lines(conn, scope, [(line, -qty) for line, qty in returned], actor)
    requisition_version = cast(
        int,
        service.repo.execute(
            "SELECT version FROM requisition WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": requisition_id},
        ).fetchone()[0],  # type: ignore[index]
    )
    order_lines(
        conn, scope,
        [LineSourcing(requisition_id, line, requisition_version, -qty) for line, qty in returned],
        actor,
    )  # fmt: skip
    if body.cancel_requisition_lines:
        if entry is not None and released > 0:
            release_unawarded(
                conn, scope, requisition_id, released, None, actor, version=version,
                source=f"po:{po_id}", key=f"po:{po_id}:cancel:return:{requisition_id}",
                reason=f"Purchase order {h['number']} cancelled, unused funds of requisition "
                f"{req_number} returned",
            )  # fmt: skip
        cancel_lines(conn, scope, returned, actor)
    reopen_for_purchase(conn, scope, requisition_id, actor)
    mark_completed(conn, scope, requisition_id, actor)


def reduce(
    conn: Conn,
    scope: Scope,
    po_id: UUID,
    actor_id: UUID,
    if_match: str | None,
    body: PoReduceCreate,
) -> PoChangeRead:
    """A quantities-only change order, created, filled and submitted in one transaction."""
    changes = ChangeService(conn, scope, actor_id)
    expected = parse_if_match(if_match)
    for edit in body.lines:
        if edit.model_fields_set - {"line_no", "quantity", "remove"} or (
            edit.quantity is None and not edit.remove
        ):
            raise _invalid(
                f"lines/{edit.line_no}", "Reduce takes lower quantities only.", "reduce_only"
            )
    with tenant_transaction(cast(RlsSession, conn), scope):
        changes.po._row(po_id)  # 404 before any lock
        # the cancel order: award, balances, requisition, PO; an auto-approved apply re-takes them
        changes._lock(po_id)
        h = changes.po._row(po_id, lock=True)
        if h["status"] != "issued":
            raise conflict("po_not_issued", "Only an issued purchase order can be reduced.")
        if expected != h["version"]:
            raise conflict("stale_version", "The purchase order changed. Reload it and retry.")
        current = {
            cast(int, r[0]): cast(Decimal, r[1])
            for r in changes.repo.execute(
                "SELECT line_no, quantity FROM po_line "
                "WHERE org_id = %(org_id)s AND po_id = %(id)s",
                {"id": po_id},
            ).fetchall()
        }
        for edit in body.lines:
            if edit.quantity is not None and edit.line_no in current:
                if Decimal(edit.quantity) >= current[edit.line_no]:
                    raise _invalid(
                        f"lines/{edit.line_no}/quantity",
                        "Reduce takes lower quantities only.",
                        "reduce_only",
                    )
        created = changes.create(po_id, PoChangeCreate(reason=body.reason))
        drafted = changes.update(
            po_id, created.number, PoChangePatch(lines=body.lines, reason=body.reason),
            str(created.version),
        )  # fmt: skip
        return changes.submit(po_id, created.number, str(drafted.version))
