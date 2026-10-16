"""Parse and authorize vendor operations (VEN-001, VEN-008, VEN-009)."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from flo.api.admin_users import organization_target
from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.config import Settings
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService
from flo.modules.vendors.schemas import (
    DisableCreate,
    VendorCreate,
    VendorDetail,
    VendorPage,
    VendorPatch,
    VendorStatus,
)
from flo.modules.vendors.service import VendorService

router = APIRouter(prefix="/api/v1/vendors", tags=["vendors"])


def unit_target(id: UUID, unit_id: UUID, connection: Connection) -> TargetProtocol:
    """Vendor first, then unit: a foreign id is 404 before any permission is weighed."""
    VendorService(connection, current_scope()).get(id)
    unit = OrgService(connection, current_scope()).get_unit(unit_id)
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit.id, "org_unit", unit.id))


ViewContext = Annotated[AuthorizationContext, Depends(require("vendor.view", organization_target))]
CreateContext = Annotated[
    AuthorizationContext, Depends(require("vendor.create", organization_target))
]
EditContext = Annotated[AuthorizationContext, Depends(require("vendor.edit", organization_target))]
UnitEditContext = Annotated[AuthorizationContext, Depends(require("vendor.edit", unit_target))]


@router.get("", response_model=VendorPage)
def list_vendors(
    context: ViewContext,
    connection: Connection,
    q: str = "",
    status: Annotated[list[VendorStatus] | None, Query()] = None,
    available_in: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> VendorPage:
    return VendorService(connection, current_scope(), context.user_id).search(
        q=q, statuses=status, available_in=available_in, cursor=cursor, limit=limit
    )


@router.post("", status_code=201, response_model=VendorDetail)
def create_vendor(
    body: VendorCreate,
    context: CreateContext,
    connection: Connection,
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> VendorDetail:
    return VendorService(connection, current_scope(), context.user_id, settings).create(body)


@router.get("/{id}", response_model=VendorDetail)
def get_vendor(id: UUID, context: ViewContext, connection: Connection) -> VendorDetail:
    return VendorService(connection, current_scope(), context.user_id).get(id)


@router.patch("/{id}", response_model=VendorDetail)
def patch_vendor(
    id: UUID, body: VendorPatch, context: EditContext, connection: Connection
) -> VendorDetail:
    return VendorService(connection, current_scope(), context.user_id).update(id, body)


@router.post("/{id}/units/{unit_id}/disable", response_model=VendorDetail)
def disable_vendor(
    id: UUID, unit_id: UUID, body: DisableCreate, context: UnitEditContext, connection: Connection
) -> VendorDetail:
    return VendorService(connection, current_scope(), context.user_id).disable_locally(
        id, unit_id, body
    )


@router.post("/{id}/units/{unit_id}/enable", response_model=VendorDetail)
def enable_vendor(
    id: UUID, unit_id: UUID, context: UnitEditContext, connection: Connection
) -> VendorDetail:
    return VendorService(connection, current_scope(), context.user_id).enable_locally(id, unit_id)
