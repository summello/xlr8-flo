"""Thin HTTP adapter for the administrative effective-access explorer."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Annotated, Literal, cast
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from flo.api.auth import _database_url, get_auth_settings
from flo.kernel.authz import AuthorizationTarget as AuthorizationTargetProtocol
from flo.kernel.authz import require
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.session import requires_recent_auth
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.explain import EffectiveAccessConnection, EffectiveAccessService
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.identity.service import (
    IdentityAuthorizationConnection,
    IdentityAuthorizationService,
)

router = APIRouter(prefix="/api/v1/admin/users", tags=["administration"])
_STEP_UP_MAX_AGE = timedelta(minutes=15)


class AccessUserResponse(BaseModel):
    """Non-secret identity facts shown in the explorer header."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    status: str


class AccessGrantResponse(BaseModel):
    """One role assignment with timing and grant provenance."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    role_name: str
    scope_type: ScopeType
    scope_id: UUID
    scope_name: str
    granted_by: str | None
    granted_at: datetime
    effective_from: datetime | None


class PermissionSourceResponse(BaseModel):
    """The concrete source of one effective permission."""

    model_config = ConfigDict(from_attributes=True)

    via_role: str
    via_scope_type: ScopeType
    via_scope_id: UUID
    inherited_from: str | None


class EffectivePermissionResponse(BaseModel):
    """An allowed permission that can never omit its source."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    allowed: bool
    source: PermissionSourceResponse


class DeniedExampleResponse(BaseModel):
    """A missing permission paired with an actionable explanation."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    reason: str


class EffectiveAccessResponse(BaseModel):
    """Serialized effective-access contract."""

    model_config = ConfigDict(from_attributes=True)

    user: AccessUserResponse
    grants: tuple[AccessGrantResponse, ...]
    pending_grants: tuple[AccessGrantResponse, ...]
    permissions: tuple[EffectivePermissionResponse, ...]
    denied_examples: tuple[DeniedExampleResponse, ...]


class PermissionTableRowResponse(BaseModel):
    """One searchable permission-grid row."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    access: str
    source: str
    explanation: str


class PermissionPageResponse(BaseModel):
    """Cursor page in the shared DataGrid wire format."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    last_cursor: str | None = Field(default=None, serialization_alias="lastCursor")
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")
    previous_cursor: str | None = Field(default=None, serialization_alias="previousCursor")
    rows: tuple[PermissionTableRowResponse, ...]
    start_index: int = Field(serialization_alias="startIndex")
    total: int


def organization_target() -> AuthorizationTargetProtocol:
    """Resolve the authorization target only from the authenticated tenant scope."""

    scope = current_scope()
    return cast(AuthorizationTargetProtocol, AuthorizationTarget.organization(scope.org_id))


_read_access = require("admin.access.read", organization_target)
_administer_users = require("organization.admin", organization_target)


def get_admin_connection(
    request: Request,
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """Reuse an idempotency transaction or open one request-scoped connection."""

    existing = getattr(request.state, "transaction_connection", None)
    if existing is not None:
        yield cast(psycopg.Connection[tuple[object, ...]], existing)
        return
    try:
        connection = psycopg.connect(_database_url(settings), autocommit=False)
    except psycopg.Error as exc:
        raise ProblemError(ErrorCode.SERVICE_UNAVAILABLE) from exc
    try:
        yield connection
    finally:
        connection.close()


@router.get("/{user_id}/effective-access", response_model=EffectiveAccessResponse)
def effective_access(
    user_id: UUID,
    context: Annotated[AuthorizationContext, Depends(_read_access)],
    connection: Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_admin_connection)],
) -> EffectiveAccessResponse:
    """Explain active, pending, allowed, and denied access and audit the inspection."""

    try:
        explanation = EffectiveAccessService(
            cast(EffectiveAccessConnection, connection),
            current_scope(),
            context.user_id,
        ).explain(IdentityId(user_id))
    except LookupError as exc:
        raise ProblemError(ErrorCode.NOT_FOUND) from exc
    return EffectiveAccessResponse.model_validate(explanation)


@router.get(
    "/{user_id}/effective-access/permissions",
    response_model=PermissionPageResponse,
    response_model_by_alias=True,
)
def effective_access_permissions(
    user_id: UUID,
    context: Annotated[AuthorizationContext, Depends(_read_access)],
    connection: Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_admin_connection)],
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
    filter: Annotated[str, Query(max_length=200)] = "",
    sort: Literal["code", "access", "source"] | None = None,
    direction: Literal["asc", "desc"] = "asc",
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> PermissionPageResponse:
    """Return a server-filtered permission table without exposing unguarded data."""

    try:
        page = EffectiveAccessService(
            cast(EffectiveAccessConnection, connection),
            current_scope(),
            context.user_id,
        ).permission_page(
            IdentityId(user_id),
            filter_text=filter,
            sort_by=sort,
            descending=direction == "desc",
            cursor=cursor,
            page_size=page_size,
        )
    except LookupError as exc:
        raise ProblemError(ErrorCode.NOT_FOUND) from exc
    except ValueError as exc:
        raise ProblemError(ErrorCode.BAD_REQUEST) from exc
    return PermissionPageResponse.model_validate(page)


@router.post("/{user_id}/deactivate", status_code=204)
@requires_recent_auth(max_age=_STEP_UP_MAX_AGE)
async def deactivate_user(
    user_id: UUID,
    request: Request,
    context: Annotated[AuthorizationContext, Depends(_administer_users)],
    connection: Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_admin_connection)],
) -> Response:
    """Deactivate a tenant user and atomically revoke every browser session."""

    del request
    service = IdentityAuthorizationService(
        cast(IdentityAuthorizationConnection, connection),
        current_scope(),
        context.user_id,
    )
    try:
        service.deactivate_user(IdentityId(user_id))
    except LookupError as exc:
        raise ProblemError(ErrorCode.NOT_FOUND) from exc
    return Response(status_code=204)


@router.delete("/{user_id}/roles/{grant_id}", status_code=204)
@requires_recent_auth(max_age=_STEP_UP_MAX_AGE)
async def revoke_user_role(
    user_id: UUID,
    grant_id: UUID,
    request: Request,
    context: Annotated[AuthorizationContext, Depends(_administer_users)],
    connection: Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_admin_connection)],
) -> Response:
    """Revoke exactly one subject-owned assignment after recent authentication."""

    del request
    service = IdentityAuthorizationService(
        cast(IdentityAuthorizationConnection, connection),
        current_scope(),
        context.user_id,
    )
    if not service.revoke_user_role(IdentityId(user_id), grant_id):
        raise ProblemError(ErrorCode.NOT_FOUND)
    return Response(status_code=204)
