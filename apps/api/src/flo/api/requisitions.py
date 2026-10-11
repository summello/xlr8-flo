"""Parse and authorize requisition header operations."""

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from flo.api.admin_users import organization_target
from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import permits, require
from flo.kernel.config import Settings
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService
from flo.modules.projects.service import ProjectService
from flo.modules.requisitions.lines import LineService
from flo.modules.requisitions.schemas import (
    Direction,
    LineCreate,
    LinesRead,
    LinesReplace,
    LineWriteResult,
    RequisitionCreate,
    RequisitionPage,
    RequisitionPatch,
    RequisitionRead,
    RequisitionSort,
    RequisitionStatus,
)
from flo.modules.requisitions.service import RequisitionService

router = APIRouter(prefix="/api/v1/requisitions", tags=["requisitions"])


def create_target(body: RequisitionCreate, connection: Connection) -> TargetProtocol:
    if body.project_id is not None:
        project = ProjectService(connection, current_scope()).get(body.project_id)
        return cast(TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project.id))
    unit = OrgService(connection, current_scope()).get_unit(cast(UUID, body.bu_id))
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit.id))


def requisition_target(id: UUID, connection: Connection) -> TargetProtocol:
    row = RequisitionService(connection, current_scope()).get(id)
    return cast(
        TargetProtocol,
        AuthorizationTarget(
            ScopeType.BU if row.standalone else ScopeType.PROJECT,
            row.bu_id if row.standalone else cast(UUID, row.project_id),
            "requisition",
            row.id,
        ),
    )


CreateContext = Annotated[
    AuthorizationContext, Depends(require("requisition.create", create_target))
]
ReadContext = Annotated[
    AuthorizationContext, Depends(require("requisition.view", requisition_target))
]
EditContext = Annotated[
    AuthorizationContext, Depends(require("requisition.edit", requisition_target))
]
ListContext = Annotated[
    AuthorizationContext, Depends(require("requisition.view", organization_target))
]


@router.post("", status_code=201, response_model=RequisitionRead)
def create_requisition(
    body: RequisitionCreate,
    context: CreateContext,
    connection: Connection,
    response: Response,
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> RequisitionRead:
    row = RequisitionService(connection, current_scope(), context.user_id, settings).create(body)
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.get("", response_model=RequisitionPage)
def list_requisitions(
    context: ListContext,
    connection: Connection,
    status: RequisitionStatus | None = None,
    project_id: UUID | None = None,
    bu_id: UUID | None = None,
    requester: Literal["me"] | None = None,
    q: str = "",
    sort: RequisitionSort = "number",
    direction: Direction = "asc",
    cursor: str | None = None,
) -> RequisitionPage:
    # ponytail: BU-only grants cannot list; add granted_scopes filtering
    # when a tenant uses BU-only grants.
    return RequisitionService(connection, current_scope(), context.user_id).list(
        status=status,
        project_id=project_id,
        bu_id=bu_id,
        requester=requester,
        q=q,
        sort=sort,
        direction=direction,
        cursor=cursor,
    )


@router.get("/{id}", response_model=RequisitionRead)
def get_requisition(
    id: UUID, context: ReadContext, connection: Connection, response: Response
) -> RequisitionRead:
    row = RequisitionService(connection, current_scope(), context.user_id).get(id)
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.patch("/{id}", response_model=RequisitionRead)
def patch_requisition(
    id: UUID,
    body: RequisitionPatch,
    context: EditContext,
    connection: Connection,
    request: Request,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> RequisitionRead:
    row = RequisitionService(connection, current_scope(), context.user_id).update(
        id,
        body,
        version,
        lambda project_id: permits(
            request,
            "requisition.create",
            cast(TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project_id)),
        ),
    )
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.get("/{id}/lines", response_model=LinesRead)
def get_lines(id: UUID, context: ReadContext, connection: Connection) -> LinesRead:
    return LineService(connection, current_scope(), context.user_id).list_lines(id)


@router.post("/{id}/lines", status_code=201, response_model=LineWriteResult)
def add_line(
    id: UUID,
    body: LineCreate,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> LineWriteResult:
    result = cast(
        LineWriteResult,
        LineService(connection, current_scope(), context.user_id).write(id, version, body=body),
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.put("/{id}/lines", response_model=LinesRead)
def replace_lines(
    id: UUID,
    body: LinesReplace,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> LinesRead:
    result = cast(
        LinesRead,
        LineService(connection, current_scope(), context.user_id).write(
            id, version, replacement=body
        ),
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.put("/{id}/lines/{line_id}", response_model=LineWriteResult)
def put_line(
    id: UUID,
    line_id: UUID,
    body: LineCreate,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> LineWriteResult:
    result = cast(
        LineWriteResult,
        LineService(connection, current_scope(), context.user_id).write(
            id, version, body=body, line_id=line_id
        ),
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.delete("/{id}/lines/{line_id}", response_model=LineWriteResult)
def delete_line(
    id: UUID,
    line_id: UUID,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> LineWriteResult:
    result = cast(
        LineWriteResult,
        LineService(connection, current_scope(), context.user_id).write(
            id, version, line_id=line_id, delete=True
        ),
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result
