"""Funding effects shared by requisition lifecycle commands."""

from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget.schemas import LedgerEntry
from flo.modules.budget.service import lock_balance, release_reservation, reservation_remaining
from flo.modules.requisitions.models import RequisitionRepository


def release_live(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    requisition_row: dict[str, object],
    reason: str,
    *,
    actor_id: UUID,
) -> int | None:
    entry_id = cast(int | None, requisition_row["reservation_entry_id"])
    if entry_id is None or reservation_remaining(conn, scope, entry_id).remaining == 0:
        return None
    entry = release_reservation(
        conn,
        scope,
        reservation_entry_id=entry_id,
        amount=None,
        actor_id=actor_id,
        reason=reason.replace("_", " "),
        idempotency_key=f"req:{requisition_row['id']}:release:{requisition_row['funding_round']}:{reason}",
    )
    return entry.id if entry else None


def reduce_to(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    requisition_id: UUID,
    *,
    new_amount: Decimal,
    version: int,
    actor_id: UUID,
) -> LedgerEntry | None:
    """Release `remaining - new_amount` from the live reservation, once per version.

    Returns the release entry (None when nothing was released).

    One transaction holds project_balance, then the requisition, through the remaining read and
    the release; the same order as every other stop. No M2 route
    calls this; M3's partial award and line cancel do.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        fields = "reservation_entry_id,funding_round"
        sql = f"SELECT {fields} FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s"
        before = repo.execute(sql, {"id": requisition_id}).fetchone()
        if before is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        if before[0] is not None:
            lock_balance(
                conn, scope, reservation_remaining(conn, scope, cast(int, before[0])).project_id
            )
        row = repo.execute(sql + " FOR UPDATE", {"id": requisition_id}).fetchone()
        assert row is not None
        if row[0] is None:
            raise ProblemError(
                ErrorCode.CONFLICT,
                detail="There is no live reservation to reduce.",
                checks={"problem": "release_exceeds_reservation"},
            )
        remaining = reservation_remaining(conn, scope, cast(int, row[0])).remaining
        if new_amount < 0 or new_amount > remaining:
            raise ProblemError(
                ErrorCode.CONFLICT,
                detail="The reduced amount exceeds the remaining reservation.",
                checks={"problem": "release_exceeds_reservation"},
            )
        if new_amount == remaining:
            return None
        entry = release_reservation(
            conn,
            scope,
            reservation_entry_id=cast(int, row[0]),
            amount=remaining - new_amount,
            actor_id=actor_id,
            reason="reduced",
            idempotency_key=f"req:{requisition_id}:release:{row[1]}:reduce:{version}",
        )
        return entry
