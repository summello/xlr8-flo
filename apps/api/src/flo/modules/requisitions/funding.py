"""Funding effects shared by requisition lifecycle commands."""

from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.tenancy.context import Scope
from flo.modules.budget.service import release_reservation, reservation_remaining


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
