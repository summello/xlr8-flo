"""Parse and authorize project budget commands and ledger reads."""

from datetime import date
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import AfterValidator

from flo.api.admin_users import deleted_visible, organization_target
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import permits, require
from flo.kernel.tenancy.context import current_scope
from flo.modules.budget.models import LedgerBucket, LedgerType
from flo.modules.budget.reconcile import (
    DriftPage,
    ReconcileStatus,
    reconciliation_drift,
    reconciliation_status,
)
from flo.modules.budget.schemas import (
    AdjustmentCreate,
    AggregateRead,
    AllocationCreate,
    BalanceQueryRead,
    BudgetChangeRequestRead,
    LedgerPage,
    ReconciliationRead,
    TransferCreate,
)
from flo.modules.budget.service import (
    BudgetSummary,
    aggregate_balance,
    balance_query,
    budget_summary,
    delete_request,
    get_request,
    ledger_query,
    request_change,
)
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService
from flo.modules.projects.service import ProjectService

router = APIRouter(tags=["budget"])


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


def _client_key(value: str) -> str:
    # bcr:/req: are server-built ledger posting keys; a client key there could pre-empt one.
    if value.startswith(("bcr:", "req:")):
        raise ValueError("Idempotency-Key prefixes bcr: and req: are reserved")
    return value


Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1), AfterValidator(_client_key)]


@router.post(
    "/api/v1/projects/{project_id}/budget/allocations",
    status_code=202,
    response_model=BudgetChangeRequestRead,
)
def allocate_budget(
    request: Request,
    project_id: UUID,
    body: AllocationCreate,
    context: AllocateContext,
    connection: Connection,
    idempotency_key: Key,
) -> BudgetChangeRequestRead:
    return request_change(
        connection,
        current_scope(),
        project_id,
        body,
        context.user_id,
        idempotency_key,
        permitted_at=lambda node_id: permits(
            request, "budget.allocate", project_target(node_id, connection)
        ),
    )


@router.post(
    "/api/v1/projects/{project_id}/budget/adjustments",
    status_code=202,
    response_model=BudgetChangeRequestRead,
)
def adjust_budget(
    project_id: UUID,
    body: AdjustmentCreate,
    context: AdjustContext,
    connection: Connection,
    idempotency_key: Key,
) -> BudgetChangeRequestRead:
    return request_change(
        connection, current_scope(), project_id, body, context.user_id, idempotency_key
    )


class BalanceParameters:
    def __init__(
        self,
        period: Literal["mtd", "qtd", "ytd", "fiscal_year", "life", "range"] = "life",
        as_of: date | None = None,
        start: Annotated[date | None, Query(alias="from")] = None,
        end: Annotated[date | None, Query(alias="to")] = None,
    ) -> None:
        self.period, self.as_of, self.start, self.end = period, as_of, start, end


@router.get("/api/v1/projects/{project_id}/balance", response_model=BalanceQueryRead)
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


@router.get("/api/v1/projects/{project_id}/balance/reconcile", response_model=ReconciliationRead)
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


@router.get("/api/v1/projects/{project_id}/ledger", response_model=LedgerPage)
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


def giving_target(body: TransferCreate, connection: Connection) -> TargetProtocol:
    return project_target(body.from_project_id, connection)


def receiving_target(body: TransferCreate, connection: Connection) -> TargetProtocol:
    return project_target(body.to_project_id, connection)


GivingContext = Annotated[AuthorizationContext, Depends(require("budget.transfer", giving_target))]
ReceivingContext = Annotated[
    AuthorizationContext, Depends(require("budget.transfer", receiving_target))
]


@router.post(
    "/api/v1/budget/transfers",
    status_code=202,
    response_model=BudgetChangeRequestRead,
)
def transfer_budget(
    body: TransferCreate,
    context: GivingContext,
    receiving_context: ReceivingContext,
    connection: Connection,
    idempotency_key: Key,
) -> BudgetChangeRequestRead:
    return request_change(
        connection, current_scope(), body.from_project_id, body, context.user_id, idempotency_key
    )


@router.get("/api/v1/projects/{project_id}/balance/aggregate", response_model=AggregateRead)
def read_aggregate(
    project_id: UUID,
    context: ReadContext,
    connection: Connection,
    params: Annotated[BalanceParameters, Depends()],
) -> AggregateRead:
    return aggregate_balance(
        connection,
        current_scope(),
        project_id,
        params.period,
        params.as_of,
        params.start,
        params.end,
    )


OrganizationLedgerContext = Annotated[
    AuthorizationContext, Depends(require("ledger.read", organization_target))
]


@router.get("/api/v1/budget/reconciliation/status", response_model=ReconcileStatus)
def read_reconciliation_status(
    context: OrganizationLedgerContext,
    connection: Connection,
) -> ReconcileStatus:
    return reconciliation_status(connection, current_scope())


@router.get("/api/v1/budget/reconciliation/drift", response_model=DriftPage)
def read_reconciliation_drift(
    context: OrganizationLedgerContext,
    connection: Connection,
    run_id: UUID,
    cursor: UUID | None = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
) -> DriftPage:
    return reconciliation_drift(connection, current_scope(), run_id, cursor, page_size)


def summary_target(connection: Connection, unit_id: UUID | None = None) -> TargetProtocol:
    if unit_id is None:
        return organization_target()
    unit = OrgService(connection, current_scope()).get_unit(unit_id)
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit.id))


@router.get("/api/v1/budget/summary", response_model=BudgetSummary)
def read_budget_summary(
    context: Annotated[AuthorizationContext, Depends(require("ledger.read", summary_target))],
    connection: Connection,
    unit_id: UUID | None = None,
) -> BudgetSummary:
    return budget_summary(connection, current_scope(), unit_id)


def change_request_target(
    id: UUID, connection: Connection, include_deleted: bool = False
) -> TargetProtocol:
    row = get_request(connection, current_scope(), id, include_deleted=include_deleted)
    return project_target(row.project_id, connection)


@router.get("/api/v1/budget/change-requests/{id}", response_model=BudgetChangeRequestRead)
def read_change_request(
    id: UUID,
    connection: Connection,
    context: Annotated[
        AuthorizationContext, Depends(require("ledger.read", change_request_target))
    ],
    request: Request,
    include_deleted: bool = False,
) -> BudgetChangeRequestRead:
    return get_request(
        connection,
        current_scope(),
        id,
        include_deleted=deleted_visible(request, include_deleted),
    )


@router.delete("/api/v1/budget/change-requests/{id}", status_code=204)
def delete_change_request(
    id: UUID,
    connection: Connection,
    context: Annotated[
        AuthorizationContext, Depends(require("ledger.read", change_request_target))
    ],
) -> Response:
    delete_request(connection, current_scope(), id)
    return Response(status_code=204)
