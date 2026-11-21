"""Parse, authorize and serialize workflow configuration operations."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from flo.api.admin_users import deleted_visible, organization_target
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.tenancy.context import current_scope
from flo.modules.approvals.schemas import (
    DocumentType,
    FieldType,
    RoutePreview,
    WorkflowCreate,
    WorkflowPage,
    WorkflowRead,
    WorkflowVersionRead,
    WorkflowVersionWrite,
)
from flo.modules.approvals.service import WorkflowService, field_catalog
from flo.modules.approvals.validation import PublishWrite, ValidationReport
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
    search: str = "",
) -> WorkflowPage:
    return WorkflowService(connection, current_scope(), context.user_id).list(
        document_type, cursor, search
    )


@router.get("/catalog", response_model=dict[str, FieldType])
def workflow_catalog(document_type: DocumentType, context: ListContext) -> dict[str, FieldType]:
    """Expose the registered document catalogue to authorized workflow readers."""
    return dict(field_catalog(document_type))


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
    id: UUID,
    version: int,
    context: ReadContext,
    connection: Connection,
    response: Response,
    request: Request,
    include_deleted: bool = False,
) -> WorkflowVersionRead:
    row = WorkflowService(connection, current_scope(), context.user_id).get_version(
        id, version, include_deleted=deleted_visible(request, include_deleted)
    )
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


@router.delete("/{id}/versions/{version}", status_code=204)
def delete_version(
    id: UUID, version: int, context: ManageContext, connection: Connection
) -> Response:
    WorkflowService(connection, current_scope(), context.user_id).delete_version(id, version)
    return Response(status_code=204)


@router.post("/{id}/versions/{version}/publish", response_model=WorkflowVersionRead)
def publish_version(
    id: UUID, version: int, body: PublishWrite, context: ManageContext, connection: Connection
) -> WorkflowVersionRead:
    return WorkflowService(connection, current_scope(), context.user_id).publish(
        id, version, body.acknowledge_warnings
    )


@router.post("/{id}/retire", response_model=WorkflowRead)
def retire_workflow(id: UUID, context: ManageContext, connection: Connection) -> WorkflowRead:
    return WorkflowService(connection, current_scope(), context.user_id).retire(id)


@router.get(
    "/{id}/versions/{version}/validation",
    response_model=ValidationReport,
    response_model_exclude_none=True,
)
def validate_version(
    id: UUID, version: int, context: ReadContext, connection: Connection
) -> ValidationReport:
    return WorkflowService(connection, current_scope(), context.user_id).validation(id, version)


@router.get("/{id}/versions/{version}/route-preview", response_model=RoutePreview)
def route_preview(
    id: UUID,
    version: int,
    context: ReadContext,
    connection: Connection,
    amount: str,
    currency: str,
    bu_id: UUID | None = None,
) -> RoutePreview:
    return WorkflowService(connection, current_scope(), context.user_id).route_preview(
        id, version, amount, currency, bu_id
    )
