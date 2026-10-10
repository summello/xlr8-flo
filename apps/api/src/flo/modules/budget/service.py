"""Public module boundary for transactional ledger consumers."""

from collections.abc import Callable
from dataclasses import asdict
from typing import cast
from uuid import UUID

import psycopg
from pydantic import JsonValue

from flo.kernel import setting_guards
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget import funding_policy
from flo.modules.budget.aggregate import aggregate_balance as aggregate_balance
from flo.modules.budget.ledger import (
    InsufficientBudget as InsufficientBudget,
)
from flo.modules.budget.ledger import LedgerRepository, invalid
from flo.modules.budget.ledger import (
    get_balance as get_balance,
)
from flo.modules.budget.ledger import (
    post_entry as post_entry,
)
from flo.modules.budget.models import LedgerType
from flo.modules.budget.postings import commit as commit
from flo.modules.budget.postings import record_actual as record_actual
from flo.modules.budget.postings import release_commitment as release_commitment
from flo.modules.budget.postings import reverse_entry as reverse_entry
from flo.modules.budget.queries import balance_query as balance_query
from flo.modules.budget.queries import ledger_query as ledger_query
from flo.modules.budget.reservations import (
    release_reservation as release_reservation,
)
from flo.modules.budget.reservations import (
    reserve as reserve,
)
from flo.modules.budget.schemas import (
    AdjustmentCreate,
    AllocationCreate,
    AllocationResult,
    LedgerEntryRead,
)
from flo.modules.budget.transfers import post_legs
from flo.modules.budget.transfers import transfer as transfer
from flo.modules.org.service import OrgService
from flo.modules.projects.service import ProjectService

# The module boundary (AGENTS.md 3.3): other modules import these from here, never from
# ledger, postings, reservations, transfers or queries directly.
__all__ = [
    "InsufficientBudget",
    "adjust",
    "aggregate_balance",
    "allocate",
    "balance_query",
    "commit",
    "get_balance",
    "entry_by_idempotency_key",
    "ledger_query",
    "post_entry",
    "record_actual",
    "release_commitment",
    "release_reservation",
    "reserve",
    "reverse_entry",
    "transfer",
]


def _manual_entry(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
    body: AdjustmentCreate,
    actor_id: UUID,
    idempotency_key: str,
    entry_type: LedgerType,
) -> AllocationResult:
    with tenant_transaction(cast(RlsSession, conn), scope):
        project = ProjectService(conn, scope).get(project_id)
        repo = LedgerRepository(conn, scope)
        repo.balance(project_id, lock=True)
        prior_entry = repo.entry("idempotency_key = %(key)s", {"key": idempotency_key})
        if (
            prior_entry is None
            and OrgService(conn, scope)
            .effective_setting(project.bu_id, "budget.evidence_required")
            .value
            is True
            and body.evidence_ref is None
        ):
            raise invalid(
                "Supporting evidence is required. Supply an evidence reference.",
                field="evidence_ref",
            )
        entry = post_entry(
            conn,
            scope,
            project_id=project_id,
            entry_type=entry_type,
            amount=body.amount,
            currency=body.currency,
            source_type="manual",
            source_id=None,
            effective_date=body.effective_date,
            actor_id=actor_id,
            department_code=project.department_code,
            ledger_account_code=project.ledger_account_code,
            idempotency_key=idempotency_key,
            reason=body.reason,
        )
        existing = repo.execute(
            "SELECT ref FROM ledger_evidence WHERE org_id = %(org_id)s AND entry_id = %(id)s",
            {"id": entry.id},
        ).fetchone()
        if prior_entry is not None and (existing[0] if existing else None) != body.evidence_ref:
            raise ProblemError(
                ErrorCode.IDEMPOTENCY_KEY_REUSED,
                detail=(
                    "This key already carries other evidence. Use a new key for changed content."
                ),
                checks={"problem": "idempotency_conflict"},
            )
        if body.evidence_ref is not None and existing is None:
            repo.execute(
                "INSERT INTO ledger_evidence(entry_id, org_id, ref) "
                "VALUES (%(id)s, %(org_id)s, %(ref)s)",
                {"id": entry.id, "ref": body.evidence_ref},
            )
        return AllocationResult(
            entries=[LedgerEntryRead(**asdict(entry))], balance=get_balance(conn, scope, project_id)
        )


def allocate(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
    body: AllocationCreate,
    actor_id: UUID,
    idempotency_key: str,
    *,
    permitted_at: Callable[[UUID], bool] | None = None,
) -> AllocationResult:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = LedgerRepository(conn, scope)
        # Serialize policy changes against the first funding command.
        repo.execute("SELECT id FROM organization WHERE id=%(org_id)s FOR UPDATE")
        project = ProjectService(conn, scope).get(project_id)
        plan = funding_policy.plan_allocation(
            project, body.amount, funding_policy.mode_for(conn, scope, project)
        )
        if isinstance(plan, funding_policy.Direct):
            return _manual_entry(
                conn, scope, project_id, body, actor_id, idempotency_key, LedgerType.ALLOCATION
            )
        if permitted_at is None or not permitted_at(plan.parent_id):
            raise ProblemError(
                ErrorCode.FORBIDDEN,
                detail=f"Funding requires budget.allocate on parent project {plan.parent_id}.",
            )
        for node_id in sorted((plan.parent_id, project_id)):
            if repo.balance(node_id, lock=True) is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
        parent = ProjectService(conn, scope).get(plan.parent_id)
        project = ProjectService(conn, scope).get(project_id)
        prior = repo.entry("idempotency_key=%(key)s", {"key": idempotency_key + ":out"})
        if prior is None:
            if parent.currency != project.currency:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="Parent and child currencies differ. Use matching currencies.",
                    checks={"problem": "currency_mismatch"},
                )
            if body.evidence_ref is None and any(
                OrgService(conn, scope).effective_setting(p.bu_id, "budget.evidence_required").value
                is True
                for p in (parent, project)
            ):
                raise invalid(
                    "Supporting evidence is required. Supply an evidence reference.",
                    field="evidence_ref",
                )
        result = post_legs(
            conn,
            scope,
            body,
            actor_id,
            idempotency_key,
            [(parent, -body.amount, ":out"), (project, body.amount, ":in")],
            allow_negative=False,
            poster=post_entry,
        )
        return AllocationResult(
            entries=result.entries, balance=get_balance(conn, scope, project_id)
        )


def adjust(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
    body: AdjustmentCreate,
    actor_id: UUID,
    idempotency_key: str,
) -> AllocationResult:
    return _manual_entry(
        conn, scope, project_id, body, actor_id, idempotency_key, LedgerType.ADJUSTMENT
    )


def _funding_mode_guard(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    unit_id: UUID | None,
    value: JsonValue | None,
) -> None:
    repo = LedgerRepository(conn, scope)
    # Organization-only installations have no funded projects yet.
    if repo.execute("SELECT to_regclass('ledger_entry')").fetchone() == (None,):
        return
    row = repo.execute(
        "WITH RECURSIVE units AS (SELECT id FROM org_unit WHERE org_id=%(org_id)s "
        "AND (%(unit)s::uuid IS NULL OR id=%(unit)s) UNION "
        "SELECT u.id FROM org_unit u JOIN units a ON u.parent_id=a.id "
        "WHERE u.org_id=%(org_id)s) SELECT 1 FROM ledger_entry e "
        "JOIN project p ON p.id=e.project_id AND p.org_id=e.org_id "
        "JOIN project root ON root.id=p.root_id AND root.org_id=p.org_id "
        "WHERE e.org_id=%(org_id)s AND (p.bu_id IN (SELECT id FROM units) "
        "OR root.bu_id IN (SELECT id FROM units)) LIMIT 1",
        {"unit": unit_id},
    ).fetchone()
    if row:
        raise ProblemError(
            ErrorCode.CONFLICT,
            detail="Ledger entries lock the funding mode. Keep the current mode.",
            checks={"problem": "funding_mode_locked"},
        )


setting_guards.register("funding_mode", _funding_mode_guard)


def entry_by_idempotency_key(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, key: str
) -> LedgerEntryRead | None:
    """Resolve the recipient credit in direct and roll-down allocations."""
    with tenant_transaction(cast(RlsSession, connection), scope):
        entry = LedgerRepository(connection, scope).entry(
            "idempotency_key IN (%(key)s, %(key)s || ':in') "
            "ORDER BY (idempotency_key = %(key)s) DESC LIMIT 1",
            {"key": key},
        )
        return LedgerEntryRead(**asdict(entry)) if entry else None
