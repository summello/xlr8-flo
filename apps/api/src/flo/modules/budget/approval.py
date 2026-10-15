"""Budget approval adapter; financial effects commit with the decision."""

from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.money import Money
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.schemas import (
    DocumentSnapshot,
    DocumentSummary,
    FieldType,
    Outcome,
    OutcomeContext,
)
from flo.modules.budget.change_requests import (
    PLANNED,
    ChangeRepository,
    get_request,
    planned_parent,
)
from flo.modules.budget.schemas import AdjustmentCreate, AllocationCreate, TransferCreate
from flo.modules.identity.service import AuthorizationTarget, ScopeType


class BudgetChangeAdapter:
    document_type = "budget_change"
    approve_permission = "budget.approve"
    read_permission = "ledger.read"
    accepts_project_assignment = False

    def fields(self) -> Mapping[str, FieldType]:
        return {"amount": "money"}

    def snapshot(
        self, conn: psycopg.Connection[tuple[object, ...]], scope: Scope, document_id: UUID
    ) -> DocumentSnapshot:
        row = get_request(conn, scope, document_id)
        return DocumentSnapshot(
            1,
            row.requested_by,
            cast(
                TargetProtocol,
                AuthorizationTarget(ScopeType.PROJECT, row.project_id, "budget_change", row.id),
            ),
            {"amount": Money(row.amount, row.currency)},
            {"payload": row.model_dump_json(include={"payload", "project_id", "kind"})},
        )

    def summaries(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_ids: Sequence[UUID],
    ) -> Mapping[UUID, DocumentSummary]:
        result = {}
        for id in document_ids:
            row = get_request(conn, scope, id)
            result[id] = DocumentSummary(
                str(id),
                "Budget " + row.kind,
                Money(row.amount, row.currency),
                row.requested_by,
                f"/projects/{row.project_id}",
                None,
            )
        return result

    def on_outcome(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_id: UUID,
        outcome: Outcome,
        context: OutcomeContext,
    ) -> None:
        from flo.modules.budget.service import adjust, allocate, transfer

        with tenant_transaction(cast(RlsSession, conn), scope):
            repo = ChangeRepository(conn, scope)
            repo.execute(
                "SELECT 1 FROM budget_change_request WHERE org_id=%(org_id)s AND id=%(id)s "
                "FOR UPDATE",
                {"id": document_id},
            )
            row = get_request(conn, scope, document_id)
            status = (
                "approved"
                if outcome == Outcome.APPROVED
                else "withdrawn"
                if outcome == Outcome.WITHDRAWN
                else "rejected"
            )
            # One outcome per request: an identical replay is a no-op, a different one is refused.
            if row.status != "pending":
                if row.status == status:
                    return
                raise ProblemError(ErrorCode.CONFLICT, detail="Request already decided.")
            result = None
            if outcome == Outcome.APPROVED and row.kind == "allocation":
                # The parent was authorized at request time; refuse if the plan has since changed.
                if row.payload.get(PLANNED) != planned_parent(
                    conn, scope, row.project_id, row.amount
                ):
                    raise ProblemError(
                        ErrorCode.CONFLICT,
                        detail="Funding mode changed since this request. Submit a new request.",
                        checks={"problem": "funding_plan_changed"},
                    )
            if outcome == Outcome.APPROVED:
                key = f"bcr:{row.id}"
                posted = (
                    transfer(
                        conn,
                        scope,
                        TransferCreate.model_validate(row.payload),
                        row.requested_by,
                        key,
                    )
                    if row.kind == "transfer"
                    else allocate(
                        conn,
                        scope,
                        row.project_id,
                        AllocationCreate.model_validate(
                            {k: v for k, v in row.payload.items() if k != PLANNED}
                        ),
                        row.requested_by,
                        key,
                        # The requester's parent-funding permission was checked at request time.
                        permitted_at=lambda _: True,
                    )
                    if row.kind == "allocation"
                    else adjust(
                        conn,
                        scope,
                        row.project_id,
                        AdjustmentCreate.model_validate(row.payload),
                        row.requested_by,
                        key,
                    )
                )
                result = posted.model_dump(mode="json")
            repo.execute(
                "UPDATE budget_change_request SET status=%(status)s,decided_at=now(),"
                "result=%(result)s WHERE org_id=%(org_id)s AND id=%(id)s",
                {
                    "id": document_id,
                    "status": status,
                    "result": Jsonb(result) if result is not None else None,
                },
            )
