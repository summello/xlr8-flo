"""Thin adapters for organization structure and replacing settings."""

from datetime import date
from typing import Annotated, Literal, cast
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query, Response

from flo.api.admin_users import get_admin_connection, organization_target
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.schemas import (
    EffectiveSetting,
    OrgAddressClose,
    OrgAddressCreate,
    OrgAddressRead,
    OrgUnitCreate,
    OrgUnitPage,
    OrgUnitPatch,
    OrgUnitRead,
    SettingPut,
)
from flo.modules.org.service import OrgService

router = APIRouter(prefix="/api/v1/org", tags=["organization"])
Connection = Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_admin_connection)]


def unit_target(id: UUID, connection: Connection) -> TargetProtocol:
    unit = OrgService(connection, current_scope()).get_unit(id)
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit.id, "org_unit", unit.id))


def put_target(body: SettingPut, connection: Connection) -> TargetProtocol:
    if body.unit_id is None:
        return organization_target()
    return unit_target(body.unit_id, connection)


def delete_target(connection: Connection, unit_id: UUID | None = None) -> TargetProtocol:
    if unit_id is None:
        return organization_target()
    return unit_target(unit_id, connection)


CreateContext = Annotated[
    AuthorizationContext, Depends(require("org.unit.manage", organization_target))
]
ReadContext = Annotated[AuthorizationContext, Depends(require("org.unit.read", unit_target))]
UpdateContext = Annotated[AuthorizationContext, Depends(require("org.unit.manage", unit_target))]
PutContext = Annotated[AuthorizationContext, Depends(require("org.setting.manage", put_target))]
DeleteContext = Annotated[
    AuthorizationContext, Depends(require("org.setting.manage", delete_target))
]


@router.post("/units", status_code=201, response_model=OrgUnitRead)
def create_unit(body: OrgUnitCreate, context: CreateContext, connection: Connection) -> OrgUnitRead:
    return OrgService(connection, current_scope(), context.user_id).create_unit(body)


@router.get("/units", response_model=OrgUnitPage)
def list_units(
    context: Annotated[
        AuthorizationContext, Depends(require("org.unit.read", organization_target))
    ],
    connection: Connection,
    kind: Literal["bu", "ou"] | None = None,
    active: bool | None = None,
    cursor: UUID | None = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> OrgUnitPage:
    return OrgService(connection, current_scope(), context.user_id).list_units(
        kind=kind, active=active, cursor=cursor, page_size=page_size
    )


@router.get("/units/{id}", response_model=OrgUnitRead)
def get_unit(id: UUID, context: ReadContext, connection: Connection) -> OrgUnitRead:
    return OrgService(connection, current_scope(), context.user_id).get_unit(id)


@router.patch("/units/{id}", response_model=OrgUnitRead)
def patch_unit(
    id: UUID, body: OrgUnitPatch, context: UpdateContext, connection: Connection
) -> OrgUnitRead:
    return OrgService(connection, current_scope(), context.user_id).update_unit(id, body)


@router.put("/settings/{key}", response_model=SettingPut)
def set_setting(
    key: str, body: SettingPut, context: PutContext, connection: Connection
) -> SettingPut:
    return OrgService(connection, current_scope(), context.user_id).set_setting(key, body)


@router.delete("/settings/{key}", status_code=204)
def clear_setting(
    key: str, context: DeleteContext, connection: Connection, unit_id: UUID | None = None
) -> Response:
    """Clear an override. An absent override succeeds without another audit event."""
    OrgService(connection, current_scope(), context.user_id).clear_setting(key, unit_id)
    return Response(status_code=204)


@router.get("/units/{id}/settings/{key}/effective", response_model=EffectiveSetting)
def effective_setting(
    id: UUID, key: str, context: ReadContext, connection: Connection
) -> EffectiveSetting:
    """Resolve unit, ancestors, org, then built-in default; name the winning scope.

    Each override replaces the entire value. Clear it to restore the next inherited value.
    """
    return OrgService(connection, current_scope(), context.user_id).effective_setting(id, key)


def address_unit_target(unit_id: UUID, connection: Connection) -> TargetProtocol:
    return unit_target(unit_id, connection)


def address_target(unit_id: UUID, id: UUID, connection: Connection) -> TargetProtocol:
    address = OrgService(connection, current_scope()).get_address(unit_id, id)
    return cast(
        TargetProtocol,
        AuthorizationTarget(ScopeType.BU, address.unit_id, "org_address", address.id),
    )


AddressCloseContext = Annotated[
    AuthorizationContext, Depends(require("org.unit.manage", address_target))
]
AddressReadContext = Annotated[
    AuthorizationContext, Depends(require("org.unit.read", address_unit_target))
]
AddressWriteContext = Annotated[
    AuthorizationContext, Depends(require("org.unit.manage", address_unit_target))
]


@router.post("/units/{unit_id}/addresses", status_code=201, response_model=OrgAddressRead)
def create_address(
    unit_id: UUID, body: OrgAddressCreate, context: AddressWriteContext, connection: Connection
) -> OrgAddressRead:
    return OrgService(connection, current_scope(), context.user_id).create_address(unit_id, body)


@router.get("/units/{unit_id}/addresses", response_model=list[OrgAddressRead])
def list_addresses(
    unit_id: UUID,
    context: AddressReadContext,
    connection: Connection,
    kind: Literal["bill_to", "ship_to"] | None = None,
    as_of: date | None = None,
) -> list[OrgAddressRead]:
    return OrgService(connection, current_scope(), context.user_id).list_addresses(
        unit_id, kind, as_of
    )


@router.patch("/units/{unit_id}/addresses/{id}", response_model=OrgAddressRead)
def close_address(
    unit_id: UUID,
    id: UUID,
    body: OrgAddressClose,
    context: AddressCloseContext,
    connection: Connection,
) -> OrgAddressRead:
    return OrgService(connection, current_scope(), context.user_id).close_address(unit_id, id, body)
