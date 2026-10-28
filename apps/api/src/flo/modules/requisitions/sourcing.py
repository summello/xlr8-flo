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
from flo.modules.budget.service import (
    entry_by_idempotency_key,
    lock_balance,
    reservation_remaining,
)
from flo.modules.requisitions.funding import move_funds, reduce_to
from flo.modules.requisitions.lifecycle import read_row, rereserve, write_status
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


def order_lines(
    conn: Conn, scope: Scope, changes: Sequence[LineSourcing], actor_id: UUID | None
) -> None:
    """Move `ordered_qty` by each delta, all or nothing, in the caller's transaction (PO-009).

    Lock order is requisitions then their lines, each by id; only then `assert_current_upstream`
    (its own read is unlocked, so the header lock is what stops a racing amendment). A positive
    delta must fit `awarded_qty - ordered_qty`; handing quantity back is always allowed.
    """
    if not changes:
        return
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        for requisition_id in sorted({c.requisition_id for c in changes}):
            read_row(repo, requisition_id, lock=True)  # 404 for a foreign or unknown id
        deltas: dict[UUID, Decimal] = defaultdict(Decimal)
        for c in changes:
            deltas[c.requisition_line_id] += c.delta
        lines = {
            cast(UUID, r[0]): r
            for r in repo.execute(
                "SELECT id, requisition_id, awarded_qty, ordered_qty FROM requisition_line "
                "WHERE org_id = %(org_id)s AND id = ANY(%(ids)s) ORDER BY id FOR UPDATE",
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
        over = [
            (line_id, cast(Decimal, lines[line_id][2]) - cast(Decimal, lines[line_id][3]), delta)
            for line_id, delta in deltas.items()
            if delta > cast(Decimal, lines[line_id][2]) - cast(Decimal, lines[line_id][3])
        ]
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
            ordered = cast(Decimal, lines[line_id][3])
            if ordered + delta < 0:  # the CHECK constraint stays as the backstop
                raise conflict("over_return", "More quantity was returned than was ordered.")
            repo.execute(
                "UPDATE requisition_line SET ordered_qty = ordered_qty + %(delta)s, "
                "updated_at = now() WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": line_id, "delta": delta},
            )
            audit_event(
                cast(AuditConnection, conn),
                scope,
                actor=AuditActor(ActorKind.USER, actor_id),
                action="requisition.order.update",
                target_type="requisition",
                target_id=cast(UUID, lines[line_id][1]),
                outcome=Outcome.SUCCESS,
                before={str(line_id): format(ordered, "f")},
                after={str(line_id): format(ordered + delta, "f")},
            )


def mark_sourced(conn: Conn, scope: Scope, requisition_id: UUID, actor_id: UUID) -> bool:
    """`sourcing_in_progress` -> `sourced` once nothing is left to source (decision 206).

    Every non-cancelled line is fully sourced and no RFQ holding its lines is still a draft.
    Returns whether the requisition moved; a no-op for any other state. Call in a transaction.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        row = read_row(repo, requisition_id, lock=True)
        if row["status"] != "sourcing_in_progress":
            return False
        params = {"id": requisition_id}
        open_lines = repo.execute(
            "SELECT 1 FROM requisition_line WHERE org_id = %(org_id)s AND requisition_id = %(id)s "
            "AND sourced_qty <> quantity - cancelled_qty LIMIT 1",
            params,
        ).fetchone()
        # ponytail: reads the rfq tables directly; route through a sourcing probe if a second
        # document type ever holds requisition lines.
        on_draft = repo.execute(
            "SELECT 1 FROM rfq_line l JOIN rfq r ON r.org_id = l.org_id AND r.id = l.rfq_id "
            "WHERE l.org_id = %(org_id)s AND l.requisition_id = %(id)s AND r.status = 'draft' "
            "LIMIT 1",
            params,
        ).fetchone()
        if open_lines or on_draft:
            return False
        write_status(conn, scope, row, "sourced", actor_id, None, None)
        return True


def award_lines(
    conn: Conn, scope: Scope, changes: Sequence[tuple[UUID, Decimal]], actor_id: UUID
) -> None:
    """`awarded_qty += qty` for each (requisition line, quantity), all or nothing (SRC-012).

    Requisitions are locked in id order, then their lines, before anything is validated; two
    awards racing for one line serialize here and the loser fails the quantity check.
    """
    wanted: dict[UUID, Decimal] = defaultdict(Decimal)
    for line_id, qty in changes:
        wanted[line_id] += qty
    if not wanted:
        return
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        owners = {
            cast(UUID, r[0]): cast(UUID, r[1])
            for r in repo.execute(
                "SELECT id, requisition_id FROM requisition_line "
                "WHERE org_id = %(org_id)s AND id = ANY(%(ids)s)",
                {"ids": sorted(wanted)},
            ).fetchall()
        }
        if owners.keys() != wanted.keys():
            raise ProblemError(ErrorCode.NOT_FOUND)
        for requisition_id in sorted(set(owners.values())):
            read_row(repo, requisition_id, lock=True)
        lines = repo.execute(
            "SELECT id, requisition_id, quantity, cancelled_qty, awarded_qty FROM requisition_line "
            "WHERE org_id = %(org_id)s AND id = ANY(%(ids)s) ORDER BY id FOR UPDATE",
            {"ids": sorted(wanted)},
        ).fetchall()
        for row_id, owner, quantity_, cancelled_, awarded_ in lines:
            line_id = cast(UUID, row_id)
            quantity, cancelled, awarded = (
                cast(Decimal, quantity_),
                cast(Decimal, cancelled_),
                cast(Decimal, awarded_),
            )
            if awarded + wanted[line_id] > quantity - cancelled:
                raise conflict(
                    "over_awarded_quantity",
                    f"Line {line_id} has {quantity - cancelled - awarded:f} left to award, "
                    f"{wanted[line_id]:f} was asked for. Reload the award and retry.",
                )
            repo.execute(
                "UPDATE requisition_line SET awarded_qty = awarded_qty + %(qty)s, "
                "updated_at = now() WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": line_id, "qty": wanted[line_id]},
            )
            audit_event(
                cast(AuditConnection, conn),
                scope,
                actor=AuditActor(ActorKind.USER, actor_id),
                action="requisition.award.update",
                target_type="requisition",
                target_id=cast(UUID, owner),
                outcome=Outcome.SUCCESS,
                before={str(line_id): format(awarded, "f")},
                after={str(line_id): format(awarded + wanted[line_id], "f")},
            )


def release_unawarded(
    conn: Conn,
    scope: Scope,
    requisition_id: UUID,
    unused: Decimal,
    award_id: UUID,
    actor_id: UUID,
    *,
    version: int,
) -> None:
    """Release `unused` from the live reservation and return it sub-project -> main (decision 205).

    The caller holds the project_balance locks. Both postings carry the award in their key, so a
    retry posts nothing new (FIN-011). `version` is the award's, bumped on every submit.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = read_row(RequisitionRepository(conn, scope), requisition_id)
        if row["reservation_entry_id"] is None:
            raise conflict("release_exceeds_reservation", "There is no live reservation to reduce.")
        held = reservation_remaining(conn, scope, cast(int, row["reservation_entry_id"]))
        reduce_to(
            conn,
            scope,
            requisition_id,
            new_amount=held.remaining - unused,
            version=version,
            actor_id=actor_id,
            source=f"award:{award_id}",
        )
        main_id = cast(UUID, row["project_id"])
        if held.project_id != main_id:
            move_funds(
                conn,
                scope,
                row,
                source_id=held.project_id,
                target_id=main_id,
                amount=unused,
                key=f"award:{award_id}:return:{requisition_id}",
                reason=f"Award released unused funds of requisition {row['number']}",
                actor_id=actor_id,
            )


def mark_awarded(conn: Conn, scope: Scope, requisition_id: UUID, actor_id: UUID) -> bool:
    """`sourced` -> `awarded` -> `open_for_purchase` once every line is fully awarded (206).

    Returns whether the requisition moved; a no-op for any other state. Call in a transaction.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        row = read_row(repo, requisition_id, lock=True)
        if row["status"] != "sourced":
            return False
        open_lines = repo.execute(
            "SELECT 1 FROM requisition_line WHERE org_id = %(org_id)s AND requisition_id = %(id)s "
            "AND awarded_qty <> quantity - cancelled_qty LIMIT 1",
            {"id": requisition_id},
        ).fetchone()
        if open_lines:
            return False
        write_status(conn, scope, row, "awarded", actor_id, None, None)
        write_status(
            conn, scope, row | {"status": "awarded"}, "open_for_purchase", actor_id, None, None
        )
        return True


def award_funding(
    conn: Conn, scope: Scope, requisition_ids: Sequence[UUID], *, lock: bool = False
) -> dict[UUID, Decimal]:
    """The live reservation remaining per requisition, 0 when it holds none (SRC-016).

    `lock` first takes every project_balance involved (the main project and the one holding the
    reservation) in id order, before any requisition row, as every award posting does.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        rows = {i: read_row(repo, i) for i in sorted(set(requisition_ids))}
        held = {
            i: reservation_remaining(conn, scope, cast(int, r["reservation_entry_id"]))
            for i, r in rows.items()
            if r["reservation_entry_id"] is not None
        }
        if lock:
            projects = {cast(UUID, r["project_id"]) for r in rows.values() if r["project_id"]}
            for project_id in sorted(projects | {h.project_id for h in held.values()}):
                lock_balance(conn, scope, project_id)
            held = {
                i: reservation_remaining(conn, scope, cast(int, rows[i]["reservation_entry_id"]))
                for i in held
            }
        return {i: held[i].remaining if i in held else Decimal(0) for i in rows}


def mark_unsourced(conn: Conn, scope: Scope, requisition_id: UUID, actor_id: UUID) -> bool:
    """`sourced` -> `sourcing_in_progress` once quantity is open again (inverse of mark_sourced).

    Returns whether the requisition moved; a no-op for any other state. Call in a transaction.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        row = read_row(repo, requisition_id, lock=True)
        if row["status"] != "sourced":
            return False
        open_lines = repo.execute(
            "SELECT 1 FROM requisition_line WHERE org_id = %(org_id)s AND requisition_id = %(id)s "
            "AND cancelled_qty < quantity AND sourced_qty <> quantity - cancelled_qty LIMIT 1",
            {"id": requisition_id},
        ).fetchone()
        if not open_lines:
            return False
        write_status(conn, scope, row, "sourcing_in_progress", actor_id, None, None)
        return True


def restore_unawarded(
    conn: Conn, scope: Scope, requisition_id: UUID, award_id: UUID, actor_id: UUID
) -> None:
    """Exactly reverse what `release_unawarded` did for this award (decision 213).

    Moves the returned amount main -> sub (refused when main no longer has it, whatever
    `allow_negative_budget` says) and adds it back to the live reservation. The caller holds the
    project_balance locks. No-op when the award used the whole scope.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = read_row(RequisitionRepository(conn, scope), requisition_id, lock=True)
        back = entry_by_idempotency_key(conn, scope, f"award:{award_id}:return:{requisition_id}")
        if back is None or back.amount == 0:
            return
        held = reservation_remaining(conn, scope, cast(int, row["reservation_entry_id"]))
        move_funds(
            conn,
            scope,
            row,
            source_id=cast(UUID, row["project_id"]),
            target_id=held.project_id,
            amount=back.amount,
            key=f"award:{award_id}:supersede:return:{requisition_id}",
            reason=f"Award superseded, funds of requisition {row['number']} returned",
            actor_id=actor_id,
            allow_negative=False,
        )
        rereserve(
            conn,
            scope,
            requisition_id,
            amount=back.amount,
            key=f"award:{award_id}:supersede",
            actor_id=actor_id,
        )


def mark_unawarded(conn: Conn, scope: Scope, requisition_id: UUID, actor_id: UUID) -> bool:
    """`awarded` / `open_for_purchase` -> `sourced` when an award is superseded.

    Returns whether the requisition moved; a no-op for any other state. Call in a transaction.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = read_row(RequisitionRepository(conn, scope), requisition_id, lock=True)
        if row["status"] not in ("awarded", "open_for_purchase"):
            return False
        write_status(conn, scope, row, "sourced", actor_id, None, None)
        return True
