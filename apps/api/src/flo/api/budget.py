"""Parse and authorize project budget commands and ledger reads."""

from datetime import date
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.tenancy.context import current_scope
from flo.modules.budget.models import LedgerBucket, LedgerType
from flo.modules.budget.schemas import (
    AdjustmentCreate,
    AllocationCreate,
    AllocationResult,
    BalanceQueryRead,
    LedgerPage,
    ReconciliationRead,
)
from flo.modules.budget.service import adjust, allocate, balance_query, ledger_query
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.projects.service import ProjectService

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["budget"])


def project_target(project_id: UUID, connection: Connection) -> TargetProtocol:
    project = ProjectService(connection, current_scope()).get(project_id)
    return cast(
        TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project.id, "project", project.id)
    )


AllocateContext = Annotated[
    AuthorizationContext, Depends(require("budget.allocate", project_target))
]
ReadContext = Annotated[AuthorizationContext, Depends(require("ledger.read", project_target))]
AdjustContext = Annotated[AuthorizationContext, Depends(require("budget.adjust", project_target))]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1)]


@router.post("/budget/allocations", status_code=201, response_model=AllocationResult)
def allocate_budget(
    project_id: UUID,
    body: AllocationCreate,
    context: AllocateContext,
    connection: Connection,
    idempotency_key: Key,
) -> AllocationResult:
    return allocate(connection, current_scope(), project_id, body, context.user_id, idempotency_key)


@router.post("/budget/adjustments", status_code=201, response_model=AllocationResult)
def adjust_budget(
    project_id: UUID,
    body: AdjustmentCreate,
    context: AdjustContext,
    connection: Connection,
    idempotency_key: Key,
) -> AllocationResult:
    return adjust(connection, current_scope(), project_id, body, context.user_id, idempotency_key)


class BalanceParameters:
    def __init__(
        self,
        period: Literal["mtd", "qtd", "ytd", "fiscal_year", "life", "range"] = "life",
        as_of: date | None = None,
        start: Annotated[date | None, Query(alias="from")] = None,
        end: Annotated[date | None, Query(alias="to")] = None,
    ) -> None:
        self.period, self.as_of, self.start, self.end = period, as_of, start, end


@router.get("/balance", response_model=BalanceQueryRead)
def read_balance(
    project_id: UUID,
    context: ReadContext,
    connection: Connection,
    params: Annotated[BalanceParameters, Depends()],
) -> BalanceQueryRead:
    return cast(
        BalanceQueryRead,
        balance_query(
            connection,
            current_scope(),
            project_id,
            params.period,
            params.as_of,
            params.start,
            params.end,
        ),
    )


@router.get("/balance/reconcile", response_model=ReconciliationRead)
def reconcile_balance(
    project_id: UUID,
    context: ReadContext,
    connection: Connection,
    params: Annotated[BalanceParameters, Depends()],
) -> ReconciliationRead:
    return cast(
        ReconciliationRead,
        balance_query(
            connection,
            current_scope(),
            project_id,
            params.period,
            params.as_of,
            params.start,
            params.end,
            reconcile=True,
        ),
    )


@router.get("/ledger", response_model=LedgerPage)
def read_ledger(
    project_id: UUID,
    context: ReadContext,
    connection: Connection,
    bucket: LedgerBucket | None = None,
    entry_type: LedgerType | None = None,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
) -> LedgerPage:
    return ledger_query(
        connection, current_scope(), project_id, bucket, entry_type, start, end, cursor, page_size
    )
