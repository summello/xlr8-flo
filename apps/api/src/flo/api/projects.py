"""Parse and authorize project operations."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from flo.api.admin_users import organization_target
from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.config import Settings
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService
from flo.modules.projects.schemas import (
    ChildrenPage,
    Direction,
    ProjectCreate,
    ProjectPage,
    ProjectPatch,
    ProjectRead,
    ProjectSort,
    ProjectStatus,
    TreeRead,
)
from flo.modules.projects.service import ProjectService

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def create_target(body: ProjectCreate, connection: Connection) -> TargetProtocol:
    unit = OrgService(connection, current_scope()).get_unit(body.bu_id)
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit.id))


def project_target(id: UUID, connection: Connection) -> TargetProtocol:
    project = ProjectService(connection, current_scope()).get(id)
    return cast(
        TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project.id, "project", project.id)
    )


def parent_target(body: ProjectCreate, connection: Connection) -> TargetProtocol:
    if body.parent_id is None:
        return create_target(body, connection)
    project = ProjectService(connection, current_scope()).get(body.parent_id)
    return cast(
        TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project.id, "project", project.id)
    )


def parent_read(
    body: ProjectCreate,
    request: Request,
    connection: Connection,
) -> None:
    if body.parent_id is not None:
        require("project.read", parent_target)(request, parent_target(body, connection))


CreateContext = Annotated[AuthorizationContext, Depends(require("project.create", create_target))]
ReadContext = Annotated[AuthorizationContext, Depends(require("project.read", project_target))]
UpdateContext = Annotated[AuthorizationContext, Depends(require("project.update", project_target))]
ListContext = Annotated[AuthorizationContext, Depends(require("project.read", organization_target))]


@router.post("", status_code=201, response_model=ProjectRead, dependencies=[Depends(parent_read)])
def create_project(
    body: ProjectCreate,
    context: CreateContext,
    connection: Connection,
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> ProjectRead:
    return ProjectService(connection, current_scope(), context.user_id, settings).create(body)


@router.get("", response_model=ProjectPage)
def list_projects(
    context: ListContext,
    connection: Connection,
    q: str = "",
    status: ProjectStatus | None = None,
    bu_id: UUID | None = None,
    sort: ProjectSort = "number",
    direction: Direction = "asc",
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
) -> ProjectPage:
    return ProjectService(connection, current_scope(), context.user_id).list(
        q=q,
        status=status,
        bu_id=bu_id,
        sort=sort,
        direction=direction,
        cursor=cursor,
        page_size=page_size,
    )


@router.get("/{id}", response_model=ProjectRead)
def get_project(id: UUID, context: ReadContext, connection: Connection) -> ProjectRead:
    return ProjectService(connection, current_scope(), context.user_id).get(id)


@router.patch("/{id}", response_model=ProjectRead)
def patch_project(
    id: UUID,
    body: ProjectPatch,
    context: UpdateContext,
    connection: Connection,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ProjectRead:
    return ProjectService(connection, current_scope(), context.user_id).update(id, body, version)


@router.get("/{id}/children", response_model=ChildrenPage)
def project_children(
    id: UUID,
    context: ReadContext,
    connection: Connection,
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
) -> ChildrenPage:
    return ProjectService(connection, current_scope(), context.user_id).children(
        id, cursor, page_size
    )


@router.get("/{id}/tree", response_model=TreeRead)
def project_tree(
    id: UUID,
    context: ReadContext,
    connection: Connection,
    max_depth: Annotated[int, Query(ge=1, le=5)] = 5,
) -> TreeRead:
    return ProjectService(connection, current_scope(), context.user_id).tree(id, max_depth)
