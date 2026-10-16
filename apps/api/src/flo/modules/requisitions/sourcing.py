"""The open-quantity guard: the only writer of `requisition_line.sourced_qty` (SRC-003, REQ-014)."""

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError, conflict
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.requisitions.lifecycle import read_row
from flo.modules.requisitions.links import assert_current_upstream
from flo.modules.requisitions.models import RequisitionRepository
from flo.modules.requisitions.schemas import LineSourcing, SourcingBasis

type Conn = psycopg.Connection[tuple[object, ...]]

BASIS_SQL = """SELECT l.id, l.requisition_id, r.number, r.version, r.status,
 r.pending_amendment_id IS NOT NULL, r.bu_id, r.currency, r.needed_by, l.line_no, l.description,
 l.uom_code, l.quantity::text, l.sourced_qty::text, l.awarded_qty::text, l.ordered_qty::text,
 l.cancelled_qty::text, (l.quantity - l.cancelled_qty - l.sourced_qty)::text
 FROM requisition_line l JOIN requisition r ON r.org_id = l.org_id AND r.id = l.requisition_id
 WHERE l.org_id = %(org_id)s"""
BASIS_FIELDS = tuple(SourcingBasis.model_fields)


def _basis(rows: Sequence[tuple[object, ...]]) -> list[SourcingBasis]:
    return [SourcingBasis.model_validate(dict(zip(BASIS_FIELDS, r, strict=True))) for r in rows]


def sourcing_basis(conn: Conn, scope: Scope, line_ids: Sequence[UUID]) -> list[SourcingBasis]:
    """The lines asked for that exist in this tenant; a foreign or unknown id is simply absent."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        rows = RequisitionRepository(conn, scope).execute(
            BASIS_SQL + " AND l.id = ANY(%(ids)s) ORDER BY l.id", {"ids": list(line_ids)}
        )
        return _basis(rows.fetchall())


def sourceable_lines(
    conn: Conn,
    scope: Scope,
    *,
    unit_id: UUID | None,
    currency: str | None,
    q: str,
    after: tuple[str, int, UUID] | None,
    limit: int,
) -> list[SourcingBasis]:
    """Approved lines with open quantity, keyset-paged by (requisition number, line number)."""
    where = [
        "r.status = 'sourcing_in_progress'",
        "l.quantity - l.cancelled_qty - l.sourced_qty > 0",
    ]
    params: dict[str, object] = {"limit": limit}
    if unit_id is not None:
        where.append("r.bu_id = %(unit_id)s")
        params["unit_id"] = unit_id
    if currency is not None:
        where.append("r.currency = %(currency)s")
        params["currency"] = currency
    needle = q.strip().casefold()
    if needle:
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(lower(l.description) LIKE %(like)s OR lower(r.number) LIKE %(like)s)")
        params["like"] = "%" + escaped + "%"
    if after is not None:
        where.append("(r.number, l.line_no, l.id) > (%(c_number)s, %(c_line)s, %(c_id)s)")
        params.update(c_number=after[0], c_line=after[1], c_id=after[2])
    with tenant_transaction(cast(RlsSession, conn), scope):
        rows = RequisitionRepository(conn, scope).execute(
            BASIS_SQL
            + " AND "
            + " AND ".join(where)
            + " ORDER BY r.number, l.line_no, l.id LIMIT %(limit)s",
            params,
        )
        return _basis(rows.fetchall())


def source_lines(
    conn: Conn, scope: Scope, changes: Sequence[LineSourcing], actor_id: UUID | None
) -> None:
    """Move `sourced_qty` by each delta, all or nothing, in the caller's transaction.

    Lock order is requisitions then their lines, each by id. Only then is anything validated.
    A positive delta needs a current, approved requisition and must fit the open quantity;
    handing quantity back is always allowed, so a pending amendment cannot trap it in a draft.
    """
    if not changes:
        return
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        requisition_ids = sorted({c.requisition_id for c in changes})
        for requisition_id in requisition_ids:
            read_row(repo, requisition_id, lock=True)  # 404 for a foreign or unknown id
        deltas: dict[UUID, Decimal] = defaultdict(Decimal)
        for c in changes:
            deltas[c.requisition_line_id] += c.delta
        lines = {
            cast(UUID, r[0]): r
            for r in repo.execute(
                "SELECT id, requisition_id, quantity, cancelled_qty, sourced_qty "
                "FROM requisition_line WHERE org_id = %(org_id)s AND id = ANY(%(ids)s) "
                "ORDER BY id FOR UPDATE",
                {"ids": sorted(deltas)},
            ).fetchall()
        }
        for c in changes:
            line = lines.get(c.requisition_line_id)
            if line is None or line[1] != c.requisition_id:
                raise ProblemError(ErrorCode.NOT_FOUND)
        for requisition_id, version in sorted(
            {(c.requisition_id, c.requisition_version) for c in changes if c.delta > 0}
        ):
            assert_current_upstream(conn, scope, requisition_id, version)
        over = []
        for line_id, delta in deltas.items():
            _, _, quantity, cancelled, sourced = lines[line_id]
            open_qty = cast(Decimal, quantity) - cast(Decimal, cancelled) - cast(Decimal, sourced)
            if delta > open_qty:
                over.append((line_id, open_qty, delta))
        if over:
            checks = {"problem": "over_open_quantity"}
            for line_id, open_qty, requested in over:
                checks[f"open.{line_id}"] = format(open_qty, "f")
                checks[f"requested.{line_id}"] = format(requested, "f")
            raise ProblemError(
                ErrorCode.CONFLICT,
                detail="The quantity asked for is more than the open quantity on a requisition "
                "line. Lower it to the open quantity, or amend the requisition first.",
                errors=tuple(
                    ProblemFieldError(
                        field=f"lines/{line_id}",
                        message=f"Open {open_qty:f}, requested {requested:f}.",
                    )
                    for line_id, open_qty, requested in over
                ),
                checks=checks,
            )
        for line_id, delta in deltas.items():
            # The CHECK constraint stays as the backstop; this is the readable failure.
            if cast(Decimal, lines[line_id][4]) + delta < 0:
                raise conflict(
                    "over_return",
                    f"More quantity was returned than the line has sourced ({line_id}). "
                    "Reload the RFQ and retry.",
                )
        audit: dict[UUID, tuple[dict[str, object], dict[str, object]]] = {}
        for line_id, delta in deltas.items():
            _, owner, _, _, sourced = lines[line_id]
            requisition_id = cast(UUID, owner)
            updated = cast(Decimal, sourced) + delta
            repo.execute(
                "UPDATE requisition_line SET sourced_qty = %(sourced)s, updated_at = now() "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": line_id, "sourced": updated},
            )
            before, after = audit.setdefault(requisition_id, ({}, {}))
            before[str(line_id)] = format(cast(Decimal, sourced), "f")
            after[str(line_id)] = format(updated, "f")
        for requisition_id, (before, after) in audit.items():
            audit_event(
                cast(AuditConnection, conn),
                scope,
                actor=AuditActor(ActorKind.USER, actor_id),
                action="requisition.sourcing.update",
                target_type="requisition",
                target_id=requisition_id,
                outcome=Outcome.SUCCESS,
                before=before,
                after=after,
            )
