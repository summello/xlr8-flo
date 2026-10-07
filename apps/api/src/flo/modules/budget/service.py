"""Public module boundary for transactional ledger consumers."""

from dataclasses import asdict
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget import funding_policy
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
from flo.modules.org.service import OrgService
from flo.modules.projects.service import ProjectService, get_status


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
        if prior_entry is None and entry_type == LedgerType.ALLOCATION:
            if get_status(conn, scope, project_id) not in {"draft", "active"}:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=(
                        "The project is not open for funding. "
                        "Allocate to a Draft or Active project."
                    ),
                    checks={"problem": "project_not_funding"},
                )
            funding_policy.check_allocation(project, body.amount)
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
) -> AllocationResult:
    return _manual_entry(
        conn, scope, project_id, body, actor_id, idempotency_key, LedgerType.ALLOCATION
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
