"""Scoped master-data selection and lifecycle routes."""

from datetime import date
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from flo.api.admin_users import organization_target
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.schemas import CurrencyRead, MasterCreate, MasterPage, MasterPatch, MasterRef
from flo.modules.org.service import OrgService, list_currencies

router = APIRouter(prefix="/api/v1/master", tags=["master"])


def record_target(kind: str, id: UUID, connection: Connection) -> TargetProtocol:
    record = OrgService(connection, current_scope()).get_master(kind, id)
    return cast(
        TargetProtocol,
        AuthorizationTarget(ScopeType.ORG, current_scope().org_id, "master_record", record.id),
    )


ReadContext = Annotated[AuthorizationContext, Depends(require("master.read", organization_target))]
CreateContext = Annotated[
    AuthorizationContext, Depends(require("master.manage", organization_target))
]
WriteContext = Annotated[AuthorizationContext, Depends(require("master.manage", record_target))]


@router.get("/currency", response_model=list[CurrencyRead])
def currencies(context: ReadContext, q: str = "") -> list[CurrencyRead]:
    return list_currencies(q)


@router.post("/{kind}", status_code=201, response_model=MasterRef)
def create(
    kind: str, body: MasterCreate, context: CreateContext, connection: Connection
) -> MasterRef:
    return OrgService(connection, current_scope(), context.user_id).create_master(kind, body)


@router.get("/{kind}", response_model=MasterPage)
def select(
    kind: str,
    context: ReadContext,
    connection: Connection,
    q: str = "",
    active: bool | None = None,
    as_of: date | None = None,
    cursor: UUID | None = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
) -> MasterPage:
    return OrgService(connection, current_scope(), context.user_id).list_master(
        kind, q=q, active=active, as_of=as_of, cursor=cursor, page_size=page_size
    )


@router.patch("/{kind}/{id}", response_model=MasterRef)
def patch(
    kind: str, id: UUID, body: MasterPatch, context: WriteContext, connection: Connection
) -> MasterRef:
    return OrgService(connection, current_scope(), context.user_id).update_master(kind, id, body)


@router.post("/{kind}/{id}:deactivate", response_model=MasterRef)
def deactivate(kind: str, id: UUID, context: WriteContext, connection: Connection) -> MasterRef:
    return OrgService(connection, current_scope(), context.user_id).deactivate_master(kind, id)
