"""Parse and authorize manual project budget commands."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.tenancy.context import current_scope
from flo.modules.budget.schemas import AdjustmentCreate, AllocationCreate, AllocationResult
from flo.modules.budget.service import adjust, allocate
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.projects.service import ProjectService

router = APIRouter(prefix="/api/v1/projects/{project_id}/budget", tags=["budget"])


def project_target(project_id: UUID, connection: Connection) -> TargetProtocol:
    project = ProjectService(connection, current_scope()).get(project_id)
    return cast(
        TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project.id, "project", project.id)
    )


AllocateContext = Annotated[
    AuthorizationContext, Depends(require("budget.allocate", project_target))
]
AdjustContext = Annotated[AuthorizationContext, Depends(require("budget.adjust", project_target))]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1)]


@router.post("/allocations", status_code=201, response_model=AllocationResult)
def allocate_budget(
    project_id: UUID,
    body: AllocationCreate,
    context: AllocateContext,
    connection: Connection,
    idempotency_key: Key,
) -> AllocationResult:
    return allocate(connection, current_scope(), project_id, body, context.user_id, idempotency_key)


@router.post("/adjustments", status_code=201, response_model=AllocationResult)
def adjust_budget(
    project_id: UUID,
    body: AdjustmentCreate,
    context: AdjustContext,
    connection: Connection,
    idempotency_key: Key,
) -> AllocationResult:
    return adjust(connection, current_scope(), project_id, body, context.user_id, idempotency_key)
