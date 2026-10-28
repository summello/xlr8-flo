"""Parse and authorize purchase order drafts built from awarded lines (E13-S01)."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import GrantedScopes, permits, require, require_granted
from flo.kernel.config import Settings
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.purchasing.schemas import (
    AwardedLinesRead,
    PoCreate,
    PoPage,
    PoPatch,
    PoRead,
    PoTab,
)
from flo.modules.purchasing.service import PurchaseOrderService

router = APIRouter(prefix="/api/v1/purchase-orders", tags=["purchasing"])


def _unit(unit_id: UUID) -> TargetProtocol:
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit_id, "org_unit", unit_id))


def po_target(id: UUID, connection: Connection) -> TargetProtocol:
    """The PO's unit; a foreign, deleted or unknown id is a 404 before any permission."""
    return _unit(PurchaseOrderService(connection, current_scope()).unit_of(id))


def award_target(award_id: UUID, connection: Connection) -> TargetProtocol:
    """The award's RFQ unit (query parameter)."""
    return _unit(PurchaseOrderService(connection, current_scope()).award_unit(award_id))


def create_target(body: PoCreate, connection: Connection) -> TargetProtocol:
    return award_target(body.award_id, connection)


ViewContext = Annotated[AuthorizationContext, Depends(require("purchase_order.view", po_target))]
AwardViewContext = Annotated[
    AuthorizationContext, Depends(require("purchase_order.view", award_target))
]
CreateContext = Annotated[
    AuthorizationContext, Depends(require("purchase_order.create", create_target))
]
EditContext = Annotated[AuthorizationContext, Depends(require("purchase_order.create", po_target))]
ListScopes = Annotated[GrantedScopes, Depends(require_granted("purchase_order.view"))]


def _etag(po: PoRead, response: Response) -> PoRead:
    response.headers["ETag"] = f'"{po.version}"'
    return po


@router.get("", response_model=PoPage)
def list_purchase_orders(
    granted: ListScopes,
    connection: Connection,
    tab: PoTab = "all",
    cursor: str | None = None,
) -> PoPage:
    return PurchaseOrderService(connection, current_scope(), granted.user_id).list_orders(
        granted, tab, cursor
    )


@router.get("/awarded-lines", response_model=AwardedLinesRead)
def awarded_lines(
    award_id: UUID,
    context: AwardViewContext,
    connection: Connection,
    vendor_id: Annotated[UUID | None, Query()] = None,
) -> AwardedLinesRead:
    return PurchaseOrderService(connection, current_scope(), context.user_id).awarded(
        award_id, vendor_id
    )


@router.post("", status_code=201, response_model=PoRead)
def create_purchase_order(
    body: PoCreate,
    context: CreateContext,
    connection: Connection,
    settings: Annotated[Settings, Depends(get_auth_settings)],
    request: Request,
    response: Response,
) -> PoRead:
    can_override = permits(request, "rfq.approve", award_target(body.award_id, connection))
    po = PurchaseOrderService(connection, current_scope(), context.user_id, settings).create(
        body, can_override
    )
    return _etag(po, response)


@router.get("/{id}", response_model=PoRead)
def get_purchase_order(
    id: UUID, context: ViewContext, connection: Connection, response: Response
) -> PoRead:
    return _etag(
        PurchaseOrderService(connection, current_scope(), context.user_id).get(id), response
    )


@router.patch("/{id}", response_model=PoRead)
def patch_purchase_order(
    id: UUID,
    body: PoPatch,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> PoRead:
    po = PurchaseOrderService(connection, current_scope(), context.user_id).update(
        id, body, version
    )
    return _etag(po, response)


@router.delete("/{id}", status_code=204)
def delete_purchase_order(
    id: UUID,
    context: EditContext,
    connection: Connection,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    PurchaseOrderService(connection, current_scope(), context.user_id).delete(id, version)
    return Response(status_code=204)
