"""Atomic requisition state changes, with instance → balance → document locking."""

import logging
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast, get_args
from uuid import UUID

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.service import (
    active_instance,
    preview_route,
    require_workflow_for,
    start,
    withdraw,
)
from flo.modules.budget.service import get_balance, lock_balance, reservation_remaining, reserve
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    IdentityAuthorizationConnection,
    emails_by_id,
)
from flo.modules.org.service import OrgService, today
from flo.modules.projects.service import (
    abandon_generated,
    abandon_requested,
    generated_for,
    requested_for,
)
from flo.modules.requisitions import revisions
from flo.modules.requisitions.funding import lock_funding, move_funds, release_live
from flo.modules.requisitions.models import RequisitionRepository, doc_type
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

logger = logging.getLogger(__name__)
pending_terminal: ContextVar[tuple[str, str] | None] = ContextVar(
    "requisition_pending_terminal", default=None
)
TRANSITIONS = {
    ("draft", "approval_pending"): "requisition.submit",
    ("draft", "withdrawn"): "requisition.withdraw",
    ("approval_pending", "withdrawn"): "requisition.withdraw",
    ("approval_pending", "cancelled"): "requisition.cancel",
    ("sourcing_in_progress", "withdrawn"): "requisition.withdraw",
    ("sourcing_in_progress", "cancelled"): "requisition.cancel",
}
SYSTEM_ACTOR_ID = UUID(int=0)
EXPIRY_ACTOR = "requisition-expiry"
_downstream: list[Callable[[psycopg.Connection[tuple[object, ...]], Scope, UUID], int]] = []


def register_downstream_counter(
    fn: Callable[[psycopg.Connection[tuple[object, ...]], Scope, UUID], int],
) -> None:
    if fn not in _downstream:
        _downstream.append(fn)


_sourcing_probes: list[Callable[[psycopg.Connection[tuple[object, ...]], Scope, UUID], bool]] = []


def register_sourcing_probe(
    fn: Callable[[psycopg.Connection[tuple[object, ...]], Scope, UUID], bool],
) -> None:
    """M3's RFQ story registers 'an RFQ exists for this requisition'; none in M2."""
    if fn not in _sourcing_probes:
        _sourcing_probes.append(fn)


def sourcing_started(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, requisition_id: UUID
) -> bool:
    return any(fn(conn, scope, requisition_id) for fn in _sourcing_probes)


def assert_completable(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, requisition_id: UUID
) -> None:
    total = sum(fn(conn, scope, requisition_id) for fn in _downstream)
    if total:
        raise conflict(
            "completion_blocked",
            f"{total} actionable downstream documents block completion. Resolve them first.",
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
        "pending_amendment_id",
    )
    row = repo.execute(
        "SELECT "
        + ",".join(fields)
        + " FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s AND status<>'deleted'"
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
        document_type=doc_type(conn, scope, id),
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
                    document_type=doc_type(conn, scope, id),
                    document_id=id,
                    actor_id=actor_id,
                    reason=body.reason or body.to,
                )
            finally:
                pending_terminal.reset(token)
            return
        if before["status"] == "sourcing_in_progress":
            stop_approved(
                conn,
                scope,
                id,
                body.to,
                body.reason,
                actor_id,
                version=version,
                permitted=permitted,
            )
            return
        held = (
            reservation_remaining(conn, scope, cast(int, before["reservation_entry_id"]))
            if before["reservation_entry_id"] is not None
            else None
        )
        check: SubmissionCheck | None = None
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
        working = revisions.live_snapshot(conn, scope, id)
        previous = revisions.latest_applied(repo, id)
        if previous is not None and revisions.unchanged(
            cast(revisions.Snapshot, previous["snapshot"]), working
        ):
            raise conflict("nothing_changed", "Not resubmitted. Nothing has changed.")
        if check is None:  # only a submission reaches here; withdraw returned above
            raise conflict("invalid_transition", "This requisition cannot move to that status.")
        latest_instance = revisions.latest_instance(conn, scope, id)
        if not (latest_instance and latest_instance[1] == "returned"):
            # D-M2-21: one code for "nothing published"; a returned approval resumes its pin.
            kind = doc_type(conn, scope, id)
            require_workflow_for(
                conn,
                scope,
                kind,
                id,
                "Project Requests" if kind == "project_request" else "Capital Requisitions",
            )
        check = routing_check(conn, scope, id, check)
        if check.problems:
            raise SubmissionInvalid(check.problems)
        revisions.record_submission(conn, scope, id, actor_id, body.note)
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
        # A returned approval is offered for resuming at its step; approvals compares its own
        # material digest and refuses (cannot_resume) when the change is material (REQ-012).
        latest = revisions.latest_instance(conn, scope, id)
        resume_from = latest[0] if latest and latest[1] == "returned" else None
        resolver = AuthorizationResolver(cast(AuthorizationConnection, conn), scope)
        try:
            start(
                conn,
                scope,
                document_type=doc_type(conn, scope, id),
                document_id=id,
                actor_id=actor_id,
                resolver=resolver,
                resume_from=resume_from,
            )
        except ProblemError as exc:
            if resume_from is None or (exc.checks or {}).get("problem") != "cannot_resume":
                raise
            start(
                conn,
                scope,
                document_type=doc_type(conn, scope, id),
                document_id=id,
                actor_id=actor_id,
                resolver=resolver,
            )


def stop_approved(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    id: UUID,
    to: str,
    reason: str | None,
    actor_id: UUID,
    *,
    version: str | None = None,
    permitted: Callable[[str], bool] | None = None,
    system_actor: str | None = None,
) -> bool:
    """Withdraw, cancel or expire an approved requisition: release once, abandon its sub-project.

    Lock order: any running instance (a pending amendment), project_balance, requisition.
    With `system_actor` (the expiry job) a requisition that moved on or whose sourcing has
    started is skipped (False) instead of refused.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = RequisitionRepository(conn, scope)
        if active_instance(conn, scope, doc_type(conn, scope, id), id) is not None:
            withdraw(
                conn,
                scope,
                document_type=doc_type(conn, scope, id),
                document_id=id,
                actor_id=actor_id,
                reason=f"Requisition {to}",
            )
        lock_funding(conn, scope, read_row(repo, id))
        row = read_row(repo, id, lock=True)
        if row["status"] != "sourcing_in_progress":
            if system_actor:
                return False
            raise conflict(
                "invalid_transition",
                "This status transition is invalid. Choose an available action.",
            )
        if system_actor is None:
            check_version(row, version)
            if permitted is not None:
                authorize(row, to, actor_id, permitted)
        if to != "cancelled" and sourcing_started(conn, scope, id):
            if system_actor:
                return False
            raise conflict(
                "sourcing_started",
                f"Sourcing has started on {row['number']}. Ask Procurement to cancel it.",
            )
        held = (
            reservation_remaining(conn, scope, cast(int, row["reservation_entry_id"]))
            if row["reservation_entry_id"] is not None
            else None
        )
        entry = release_live(conn, scope, row, to, actor_id=actor_id)
        sub = generated_for(conn, scope, id)
        if sub is not None:
            if held is not None and held.project_id == sub.id:
                # Funded sub-project (E09-S16, D-M2-22): what is left goes back to the main project.
                left = get_balance(conn, scope, sub.id)
                if left.available > 0:
                    move_funds(
                        conn,
                        scope,
                        row,
                        source_id=sub.id,
                        target_id=cast(UUID, row["project_id"]),
                        amount=left.available,
                        key=f"req:{id}:unfund:{row['funding_round']}",
                        reason=f"Requisition {row['number']} {to}, funds returned to project",
                        actor_id=actor_id,
                    )
            abandon_generated(
                conn,
                scope,
                sub.id,
                reason=f"Abandoned because {row['number']} was {to}",
                actor_id=None if system_actor else actor_id,
                system_actor=system_actor,
            )
        created = requested_for(conn, scope, id)
        if created is not None:
            # A Project Request's own project: abandoned, allocation kept (D-M2-22).
            abandon_requested(
                conn,
                scope,
                created,
                reason=f"Abandoned because {row['number']} was {to}",
                actor_id=None if system_actor else actor_id,
                system_actor=system_actor,
            )
        write_status(conn, scope, row, to, actor_id, reason or to, entry)
        return True


def expire_org(conn: psycopg.Connection[tuple[object, ...]], scope: Scope) -> int:
    """Expire approved requisitions older than their effective `requisition.expiry_days`.

    Each requisition expires in its own transaction; one failure is logged, the rest go on.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        candidates = (
            RequisitionRepository(conn, scope)
            .execute(
                "SELECT id,bu_id,decided_at FROM requisition WHERE org_id=%(org_id)s "
                "AND status='sourcing_in_progress' AND decided_at IS NOT NULL "
                "ORDER BY decided_at,id"
            )
            .fetchall()
        )
    org = OrgService(conn, scope)
    expired = 0
    for requisition_id, bu_id, decided_at in candidates:
        try:
            # ponytail: one settings query per requisition; batch by BU/OU
            # when an organization has more than 1,000 open requisitions.
            days = org.effective_setting(cast(UUID, bu_id), "requisition.expiry_days").value
            if days is None or (
                today() - cast(datetime, decided_at).astimezone(UTC).date()
            ).days <= cast(int, days):
                continue
            if stop_approved(
                conn,
                scope,
                cast(UUID, requisition_id),
                "expired",
                "expired",
                SYSTEM_ACTOR_ID,
                system_actor=EXPIRY_ACTOR,
            ):
                expired += 1
        except Exception:
            logger.exception("requisition %s expiry failed", requisition_id)
    return expired


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
