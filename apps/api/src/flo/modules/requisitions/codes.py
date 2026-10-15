"""Expired master codes: substitution at approval and after it (D-M2-25, AUD-001)."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.errors import ProblemError, conflict
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.service import rebase_digest
from flo.modules.budget.service import lock_balance, reservation_remaining
from flo.modules.org.service import MasterCodeUnusable, assert_usable, codes_read, today
from flo.modules.projects.service import ProjectService, generated_for
from flo.modules.requisitions.lifecycle import read_row
from flo.modules.requisitions.models import RequisitionRepository
from flo.modules.requisitions.schemas import invalid

type Conn = psycopg.Connection[tuple[object, ...]]
FIELDS = (
    ("department_code", "department", "Department"),
    ("ledger_account_code", "ledger_account", "Ledger account"),
)
SUBSTITUTABLE = ("sourcing_in_progress", "sourced", "awarded", "open_for_purchase")


def _plan(
    conn: Conn,
    scope: Scope,
    current: Mapping[str, object],
    wanted: dict[str, str],
    *,
    approving: bool,
) -> dict[str, str]:
    """The validated substitutions for `current`'s codes; refuses an unusable code left unreplaced
    when `approving`. A substitute must be usable, and only an unusable code may be replaced."""
    states = codes_read(
        conn,
        scope,
        cast(str | None, current["department_code"]),
        cast(str | None, current["ledger_account_code"]),
    )
    on_date = today()
    changes: dict[str, str] = {}
    for field, kind, label in FIELDS:
        state = getattr(states, kind)
        new = wanted.get(field)
        if new is None:
            if approving and state.status != "usable":
                raise conflict(
                    "code_expired",
                    f"{label} code {state.code} is {state.status.replace('_', ' ')}. Substitute "
                    "a usable code to approve, or reject this request.",
                )
            continue
        if state.status == "usable":
            raise invalid(
                field, f"{label} code {state.code} is usable; it cannot be replaced.", "code_usable"
            )
        try:
            changes[field] = assert_usable(conn, scope, kind, new, on_date).code
        except MasterCodeUnusable as exc:
            raise invalid(
                field,
                f"{label} code {exc.code} is {exc.reason.replace('_', ' ')}. Choose a usable one.",
                "code_unusable",
            ) from exc
    return changes


def _write(
    conn: Conn,
    scope: Scope,
    actor_id: UUID,
    row: dict[str, object],
    changes: dict[str, str],
    reason: str | None,
) -> None:
    from flo.modules.requisitions.service import RequisitionService

    repo = RequisitionRepository(conn, scope)
    keys = ("department_code", "ledger_account_code")
    after = {k: changes.get(k, row[k]) for k in keys}
    repo.execute(
        "UPDATE requisition SET department_code=%(department_code)s,"
        "ledger_account_code=%(ledger_account_code)s,updated_at=now() "
        "WHERE org_id=%(org_id)s AND id=%(id)s",
        {"id": row["id"]} | after,
    )
    if row["pending_amendment_id"] is not None:
        # The running proposal must carry the corrected codes, or applying it would undo them.
        for field, value in changes.items():
            repo.execute(
                "UPDATE requisition_revision SET snapshot=jsonb_set(snapshot,"
                "ARRAY['header',%(field)s],to_jsonb(%(value)s::text)) "
                "WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": row["pending_amendment_id"], "field": field, "value": value},
            )
    RequisitionService(conn, scope, actor_id)._audit(
        "requisition.code_substitute",
        cast(UUID, row["id"]),
        {k: row[k] for k in keys},
        after | ({"reason": reason} if reason else {}),
    )


def prepare_approval(
    conn: Conn, scope: Scope, document_id: UUID, actor_id: UUID, wanted: dict[str, str]
) -> None:
    """Called inside the decision transaction, before the decision takes effect (any step)."""
    repo = RequisitionRepository(conn, scope)
    row = read_row(repo, document_id)
    changes = _plan(conn, scope, row, wanted, approving=True)
    if not changes:
        return
    if row["reservation_entry_id"] is not None:
        # Lock order after the instance: project_balance, then the requisition.
        lock_balance(
            conn,
            scope,
            reservation_remaining(conn, scope, cast(int, row["reservation_entry_id"])).project_id,
        )
    row = read_row(repo, document_id, lock=True)
    _write(conn, scope, actor_id, row, changes, "Substituted at approval")


def substitute_requisition(
    conn: Conn,
    scope: Scope,
    actor_id: UUID,
    requisition_id: UUID,
    wanted: dict[str, str],
    reason: str,
) -> None:
    """After approval: replace an unusable code on the requisition and its generated project."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        read_row(RequisitionRepository(conn, scope), requisition_id)  # 404 before any lock
        sub = generated_for(conn, scope, requisition_id)
        _apply(conn, scope, actor_id, requisition_id, wanted, reason, sub.id if sub else None)


def substitute_project(
    conn: Conn, scope: Scope, actor_id: UUID, project_id: UUID, wanted: dict[str, str], reason: str
) -> None:
    """After approval: replace an unusable code on a project and the requisition that made it."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        projects = ProjectService(conn, scope, actor_id)
        source = projects.get(project_id).generated_by_requisition_id
        if source is not None:
            _apply(conn, scope, actor_id, source, wanted, reason, project_id)
            return
        locked = projects.lock(project_id)  # lock first, then plan against the locked row
        current = {
            "department_code": locked.department_code,
            "ledger_account_code": locked.ledger_account_code,
        }
        changes = _plan(conn, scope, current, wanted, approving=False)
        projects.substitute_codes(project_id, changes, reason)


def _blocker(row: dict[str, object]) -> ProblemError | None:
    """Why a post-approval substitution is refused on this requisition, or None.

    The one decision the POST and the read model's `can_substitute_codes` share."""
    if row["status"] not in SUBSTITUTABLE:
        return conflict(
            "invalid_transition",
            "Codes can be substituted here only after the requisition is approved. "
            "Substitute them while approving.",
        )
    if row["pending_amendment_id"] is not None:
        return conflict(
            "amendment_pending", "An amendment is pending. Settle it, then substitute the codes."
        )
    return None


def _guard(row: dict[str, object]) -> None:
    blocker = _blocker(row)
    if blocker is not None:
        raise blocker


def can_substitute_requisition(conn: Conn, scope: Scope, requisition_id: UUID) -> bool:
    """State half of the answer (permission is the caller's): status and no pending amendment."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        return _blocker(read_row(RequisitionRepository(conn, scope), requisition_id)) is None


def can_substitute_project(conn: Conn, scope: Scope, project_id: UUID) -> bool:
    """A generated project is substituted through its requisition; any other project is free."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        source = ProjectService(conn, scope).get(project_id).generated_by_requisition_id
        return source is None or can_substitute_requisition(conn, scope, source)


def _apply(
    conn: Conn,
    scope: Scope,
    actor_id: UUID,
    requisition_id: UUID,
    wanted: dict[str, str],
    reason: str,
    project_id: UUID | None,
) -> None:
    """Lock order: approval instance, requisition, project. The plan runs on the locked row, so
    two concurrent substitutions of one expired code cannot both validate it."""

    def mutate() -> None:
        locked = read_row(RequisitionRepository(conn, scope), requisition_id, lock=True)
        _guard(locked)
        changes = _plan(conn, scope, locked, wanted, approving=False)
        _write(conn, scope, actor_id, locked, changes, reason)
        if project_id is not None:
            ProjectService(conn, scope, actor_id).substitute_codes(project_id, changes, reason)

    rebase_digest(conn, scope, "requisition", requisition_id, mutate)
