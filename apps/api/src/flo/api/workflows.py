"""Parse, authorize and serialize workflow configuration operations."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response

from flo.api.admin_users import organization_target
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.tenancy.context import current_scope
from flo.modules.approvals.schemas import (
    DocumentType,
    WorkflowCreate,
    WorkflowPage,
    WorkflowRead,
    WorkflowVersionRead,
    WorkflowVersionWrite,
)
from flo.modules.approvals.service import WorkflowService
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


def create_target(body: WorkflowCreate, connection: Connection) -> TargetProtocol:
    if body.scope_type == "org":
        return organization_target()
    assert body.scope_id is not None
    unit = OrgService(connection, current_scope()).get_unit(body.scope_id)
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit.id))


def workflow_target(id: UUID, connection: Connection) -> TargetProtocol:
    row = WorkflowService(connection, current_scope()).get(id)
    return cast(
        TargetProtocol,
        AuthorizationTarget(ScopeType(row.scope_type), row.scope_id, "workflow", row.id),
    )


CreateContext = Annotated[AuthorizationContext, Depends(require("workflow.manage", create_target))]
ReadContext = Annotated[AuthorizationContext, Depends(require("workflow.read", workflow_target))]
ManageContext = Annotated[
    AuthorizationContext, Depends(require("workflow.manage", workflow_target))
]
ListContext = Annotated[
    AuthorizationContext, Depends(require("workflow.read", organization_target))
]


@router.post("", status_code=201, response_model=WorkflowRead)
def create_workflow(
    body: WorkflowCreate, context: CreateContext, connection: Connection
) -> WorkflowRead:
    return WorkflowService(connection, current_scope(), context.user_id).create(body)


@router.get("", response_model=WorkflowPage)
def list_workflows(
    context: ListContext,
    connection: Connection,
    document_type: DocumentType | None = None,
    cursor: str | None = None,
) -> WorkflowPage:
    return WorkflowService(connection, current_scope(), context.user_id).list(document_type, cursor)


@router.get("/{id}", response_model=WorkflowRead)
def get_workflow(id: UUID, context: ReadContext, connection: Connection) -> WorkflowRead:
    return WorkflowService(connection, current_scope(), context.user_id).get(id)


@router.post("/{id}/versions", status_code=201, response_model=WorkflowVersionRead)
def create_version(
    id: UUID, body: WorkflowVersionWrite, context: ManageContext, connection: Connection
) -> WorkflowVersionRead:
    return WorkflowService(connection, current_scope(), context.user_id).create_version(id, body)


@router.get("/{id}/versions/{version}", response_model=WorkflowVersionRead)
def get_version(
    id: UUID, version: int, context: ReadContext, connection: Connection, response: Response
) -> WorkflowVersionRead:
    row = WorkflowService(connection, current_scope(), context.user_id).get_version(id, version)
    response.headers["ETag"] = str(row.row_version)
    return row


@router.put("/{id}/versions/{version}", response_model=WorkflowVersionRead)
def update_version(
    id: UUID,
    version: int,
    body: WorkflowVersionWrite,
    context: ManageContext,
    connection: Connection,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> WorkflowVersionRead:
    return WorkflowService(connection, current_scope(), context.user_id).update_version(
        id, version, body, if_match
    )


@router.post("/{id}/versions/{version}/publish", response_model=WorkflowVersionRead)
def publish_version(
    id: UUID, version: int, context: ManageContext, connection: Connection
) -> WorkflowVersionRead:
    return WorkflowService(connection, current_scope(), context.user_id).publish(id, version)


@router.post("/{id}/retire", response_model=WorkflowRead)
def retire_workflow(id: UUID, context: ManageContext, connection: Connection) -> WorkflowRead:
    return WorkflowService(connection, current_scope(), context.user_id).retire(id)
