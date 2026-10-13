"""Atomic requisition state changes, with instance → balance → document locking."""

from collections.abc import Callable
from contextvars import ContextVar
from decimal import Decimal
from typing import cast, get_args
from uuid import UUID

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.service import preview_route, start, withdraw
from flo.modules.budget.service import lock_balance, reservation_remaining, reserve
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    IdentityAuthorizationConnection,
    emails_by_id,
)
from flo.modules.org.service import today
from flo.modules.requisitions.funding import release_live
from flo.modules.requisitions.models import RequisitionRepository
from flo.modules.requisitions.schemas import (
    RequisitionStatus,
    RequisitionTransitionAvailable,
    RequisitionTransitionCreate,
    StatusEventRead,
    SubmissionCheck,
    SubmissionInvalid,
    SubmissionProblem,
    invalid,
)
from flo.modules.requisitions.validation import check_submission

pending_terminal: ContextVar[tuple[str, str] | None] = ContextVar(
    "requisition_pending_terminal", default=None
)
TRANSITIONS = {
    ("draft", "approval_pending"): "requisition.submit",
    ("draft", "withdrawn"): "requisition.withdraw",
    ("approval_pending", "withdrawn"): "requisition.withdraw",
    ("approval_pending", "cancelled"): "requisition.cancel",
}
_downstream: list[Callable[[psycopg.Connection[tuple[object, ...]], Scope, UUID], int]] = []


def register_downstream_counter(
    fn: Callable[[psycopg.Connection[tuple[object, ...]], Scope, UUID], int],
) -> None:
    if fn not in _downstream:
        _downstream.append(fn)


def assert_completable(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, requisition_id: UUID
) -> None:
    total = sum(fn(conn, scope, requisition_id) for fn in _downstream)
    if total:
        raise conflict(
            "completion_blocked",
            f"{total} actionable downstream documents block completion. Resolve them first.",
        )


def record_submission_revision(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    requisition: dict[str, object],
    note: str | None,
) -> None:
    if note is not None:
        raise conflict(
            "notes_unavailable", "Submission notes are not available yet. Submit without a note."
        )


def read_row(repo: RequisitionRepository, id: UUID, *, lock: bool = False) -> dict[str, object]:
    fields = (
        "id",
        "project_id",
        "requester_id",
        "status",
        "estimate",
        "currency",
        "version",
        "reservation_entry_id",
        "funding_round",
        "department_code",
        "ledger_account_code",
        "bu_id",
        "number",
        "title",
        "needed_by",
        "standalone",
    )
    row = repo.execute(
        "SELECT "
        + ",".join(fields)
        + " FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s"
        + (" FOR UPDATE" if lock else ""),
        {"id": id},
    ).fetchone()
    if row is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return dict(zip(fields, row, strict=True))


def check_version(row: dict[str, object], version: str | None) -> None:
    if version is None:
        raise ProblemError(
            ErrorCode.BAD_REQUEST, detail="Supply If-Match with the requisition version."
        )
    try:
        expected = int(version.strip('"'))
    except ValueError as exc:
        raise ProblemError(
            ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
        ) from exc
    if row["version"] != expected:
        raise conflict("stale_version", "The requisition changed. Reload it and retry.")


def authorize(
    row: dict[str, object], to: str, actor_id: UUID, permitted: Callable[[str], bool]
) -> None:
    permission = TRANSITIONS.get((str(row["status"]), to))
    if permission is None:
        raise conflict(
            "invalid_transition",
            "Not available yet."
            if row["status"] == "sourcing_in_progress"
            else "This status transition is invalid. Choose an available action.",
        )
    if not permitted(permission):
        raise ProblemError(
            ErrorCode.FORBIDDEN, detail=f"You need {permission}. Ask an administrator for access."
        )
    if to == "withdrawn" and row["requester_id"] != actor_id:
        raise ProblemError(
            ErrorCode.FORBIDDEN,
            detail="Only the requester can withdraw. Ask an administrator to cancel it.",
        )


def routing_check(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, id: UUID, check: SubmissionCheck
) -> SubmissionCheck:
    route = preview_route(
        conn,
        scope,
        document_type="requisition",
        document_id=id,
        resolver=AuthorizationResolver(cast(AuthorizationConnection, conn), scope),
    )
    problems = check.problems + [
        SubmissionProblem(category="routing", field="routing/workflow", message=p.message)
        for p in route.problems
    ]
    return check.model_copy(
        update={"route": route, "problems": problems, "ready": check.ready and not problems}
    )


def write_status(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    row: dict[str, object],
    to: str,
    actor_id: UUID,
    reason: str | None,
    ledger_entry_id: int | None,
    decision_id: UUID | None = None,
    audit_action: str | None = None,
    audit_extra: dict[str, object] | None = None,
) -> None:
    from flo.modules.requisitions.service import RequisitionService

    repo = RequisitionRepository(conn, scope)
    repo.execute(
        "UPDATE requisition SET status=%(to)s,version=version+1,updated_at=now(), "
        "submitted_at=CASE WHEN %(to)s='approval_pending' THEN now() ELSE submitted_at END, "
        "decided_at=CASE WHEN %(to)s='approval_pending' THEN decided_at ELSE now() END "
        "WHERE org_id=%(org_id)s AND id=%(id)s",
        {"id": row["id"], "to": to},
    )
    repo.execute(
        "INSERT INTO "
        "requisition_status_event(org_id,"
        "requisition_id,from_status,to_status,actor_id,reason,"
        "funding_round,ledger_entry_id,decision_id) "
        "VALUES(%(org_id)s,"
        "%(id)s,%(from)s,%(to)s,%(actor)s,%(reason)s,%(round)s,%(ledger)s,%(decision)s)",
        {
            "id": row["id"],
            "from": row["status"],
            "to": to,
            "actor": actor_id,
            "reason": reason,
            "round": row["funding_round"],
            "ledger": ledger_entry_id,
            "decision": decision_id,
        },
    )
    RequisitionService(conn, scope, actor_id)._audit(
        audit_action
        or ("requisition.submit" if to == "approval_pending" else "requisition.transition"),
        cast(UUID, row["id"]),
        {"status": row["status"]},
        {"status": to} | (audit_extra or {}),
    )


def transition(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    id: UUID,
    body: RequisitionTransitionCreate,
    version: str | None,
    actor_id: UUID,
    permitted: Callable[[str], bool],
) -> None:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        before = read_row(repo, id)
        authorize(before, body.to, actor_id, permitted)
        check_version(before, version)
        if body.to == "cancelled" and len((body.reason or "").strip()) < 10:
            raise invalid("reason", "Explain cancellation in at least 10 characters.")
        if before["status"] == "approval_pending":
            token = pending_terminal.set((body.to, body.reason or body.to))
            try:
                withdraw(
                    conn,
                    scope,
                    document_type="requisition",
                    document_id=id,
                    actor_id=actor_id,
                    reason=body.reason or body.to,
                )
            finally:
                pending_terminal.reset(token)
            return
        held = (
            reservation_remaining(conn, scope, cast(int, before["reservation_entry_id"]))
            if before["reservation_entry_id"] is not None
            else None
        )
        if body.to == "approval_pending":
            moved = held is not None and held.project_id != before["project_id"]
            remaining = held.remaining if held and not moved else Decimal(0)
            check = check_submission(
                conn,
                scope,
                id,
                lock=True,
                need=cast(Decimal, before["estimate"]) - remaining,
                also_lock=[held.project_id] if held else [],
            )
        else:
            if held:
                lock_balance(conn, scope, held.project_id)
        row = read_row(repo, id, lock=True)
        check_version(row, version)
        authorize(row, body.to, actor_id, permitted)
        live = (
            reservation_remaining(conn, scope, cast(int, row["reservation_entry_id"]))
            if row["reservation_entry_id"] is not None
            else None
        )
        if live != held or row["project_id"] != before["project_id"]:
            raise conflict("stale_version", "The reservation changed. Retry.")
        if body.to == "withdrawn":
            entry = release_live(conn, scope, row, "withdrawn", actor_id=actor_id)
            write_status(conn, scope, row, "withdrawn", actor_id, body.reason, entry)
            return
        check = routing_check(conn, scope, id, check)
        if check.problems:
            raise SubmissionInvalid(check.problems)
        record_submission_revision(conn, scope, row, body.note)
        estimate = cast(Decimal, row["estimate"])
        entry_id = cast(int | None, row["reservation_entry_id"])
        if (held and held.project_id != row["project_id"]) or (
            row["project_id"] is not None and (held is None or held.remaining != estimate)
        ):
            release_live(conn, scope, row, "resubmit", actor_id=actor_id)
            round = cast(int, row["funding_round"]) + 1
            reservation = reserve(
                conn,
                scope,
                project_id=cast(UUID, row["project_id"]),
                amount=estimate,
                currency=str(row["currency"]),
                source_type="requisition",
                source_id=id,
                effective_date=today(),
                actor_id=actor_id,
                department_code=str(row["department_code"]),
                ledger_account_code=str(row["ledger_account_code"]),
                idempotency_key=f"req:{id}:reserve:{round}",
            )
            entry_id = reservation.id
            row.update(funding_round=round, reservation_entry_id=entry_id)
            repo.execute(
                "UPDATE requisition SET "
                "funding_round=%(round)s,reservation_entry_id=%(entry)s"
                " WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": id, "round": round, "entry": entry_id},
            )
        from flo.modules.requisitions.service import record_requested

        record_requested(conn, scope, id)
        write_status(conn, scope, row, "approval_pending", actor_id, body.reason, entry_id)
        start(
            conn,
            scope,
            document_type="requisition",
            document_id=id,
            actor_id=actor_id,
            resolver=AuthorizationResolver(cast(AuthorizationConnection, conn), scope),
        )


def available(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    id: UUID,
    actor_id: UUID,
    permitted: Callable[[str], bool],
) -> list[RequisitionTransitionAvailable]:
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = read_row(RequisitionRepository(conn, scope), id)
        result = []
        for to in get_args(RequisitionStatus.__value__):
            reasons: list[str] = []
            try:
                authorize(row, to, actor_id, permitted)
                if to == "approval_pending":
                    check = routing_check(
                        conn, scope, id, check_submission(conn, scope, id, lock=False)
                    )
                    reasons.extend(p.message for p in check.problems)
            except ProblemError as exc:
                reasons.append(exc.detail or exc.code.value)
            result.append(
                RequisitionTransitionAvailable(to=to, allowed=not reasons, blocked_reasons=reasons)
            )
        return result


def events(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, id: UUID
) -> list[StatusEventRead]:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        read_row(repo, id)
        fields = (
            "at",
            "from_status",
            "to_status",
            "actor_id",
            "reason",
            "funding_round",
            "ledger_entry_id",
            "decision_id",
        )
        rows = repo.execute(
            "SELECT "
            + ",".join(fields)
            + " FROM requisition_status_event WHERE org_id=%(org_id)s AND "
            + "requisition_id=%(id)s ORDER BY id DESC",
            {"id": id},
        ).fetchall()
        emails = emails_by_id(
            cast(IdentityAuthorizationConnection, conn),
            [cast(UUID, r[3]) for r in rows if r[3] is not None and r[3] != UUID(int=0)],
        )
        return [
            StatusEventRead.model_validate(
                dict(zip(fields, r, strict=True))
                | {
                    "system": r[3] is None or r[3] == UUID(int=0),
                    "actor_email": emails.get(cast(UUID, r[3])),
                }
            )
            for r in rows
        ]
