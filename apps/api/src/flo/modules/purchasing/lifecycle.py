"""The purchase order lifecycle: one transition function, the only writer of `status` (E13-S03).

Callers (delete here; submit, issue, cancel and change orders in E13-S04..S07) pass an action and
the version they read. `partially_delivered`, `delivered` and `accepted` have no action until
goods receipt (M4).
"""

from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.tenancy.context import Scope
from flo.modules.purchasing.models import PurchaseOrderRepository

type Conn = psycopg.Connection[tuple[object, ...]]

TRANSITIONS: dict[tuple[str, str], str] = {
    ("draft", "submit"): "approval_pending",
    ("approval_pending", "approve"): "approved",
    ("approval_pending", "reject"): "draft",
    ("approval_pending", "withdraw"): "draft",
    ("approved", "issue"): "issued",
    ("issued", "supersede"): "superseded",
    ("draft", "cancel"): "cancelled",
    ("approval_pending", "cancel"): "cancelled",
    ("approved", "cancel"): "cancelled",
    ("issued", "cancel"): "cancelled",
    ("issued", "close"): "closed",
    ("draft", "delete"): "deleted",
}


def parse_if_match(if_match: str | None) -> int:
    """The integer in `If-Match` (convention O): 400 when missing or not an integer."""
    if if_match is None:
        raise ProblemError(
            ErrorCode.BAD_REQUEST,
            detail="Supply If-Match with the purchase order version.",
            checks={"problem": "if_match_required"},
        )
    try:
        return int(if_match.strip('"'))
    except ValueError:
        raise ProblemError(
            ErrorCode.BAD_REQUEST,
            detail="If-Match must contain an integer version.",
            checks={"problem": "if_match_invalid"},
        ) from None


def transition(
    conn: Conn,
    scope: Scope,
    po_id: UUID,
    action: str,
    actor_id: UUID | None,
    expected_version: int,
    reason: str | None = None,
) -> str:
    """Lock the PO, check expected_version then the table, write status + version, audit.

    Runs in the caller's tenant transaction and returns the new status."""
    row = (
        PurchaseOrderRepository(conn, scope)
        .execute(
            "SELECT status, version FROM purchase_order "
            "WHERE org_id = %(org_id)s AND id = %(id)s AND status <> 'deleted' FOR UPDATE",
            {"id": po_id},
        )
        .fetchone()
    )
    if row is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    status, current = cast(str, row[0]), cast(int, row[1])
    if current != expected_version:
        raise conflict("stale_version", "The purchase order changed. Reload it and retry.")
    target = TRANSITIONS.get((status, action))
    if target is None:
        raise conflict("invalid_transition", "That purchase order status cannot take this action.")
    PurchaseOrderRepository(conn, scope).execute(
        "UPDATE purchase_order SET status = %(status)s, version = version + 1 "
        "WHERE org_id = %(org_id)s AND id = %(id)s",
        {"id": po_id, "status": target},
    )
    audit_event(
        cast(AuditConnection, conn),
        scope,
        actor=AuditActor(ActorKind.USER, actor_id),
        action=f"purchase_order.{action}",
        target_type="purchase_order",
        target_id=po_id,
        outcome=Outcome.SUCCESS,
        reason=reason,
        before={"status": status},
        after={"status": target},
    )
    return target
