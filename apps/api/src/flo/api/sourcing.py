"""Parse and authorize RFQ drafts built from requisition lines (SRC-001, SRC-003)."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from flo.api.admin_users import organization_target
from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.config import Settings
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService
from flo.modules.sourcing.schemas import (
    RfqCreate,
    RfqLinesPatch,
    RfqRead,
    SourceablePage,
)
from flo.modules.sourcing.service import RfqService

router = APIRouter(prefix="/api/v1/sourcing", tags=["sourcing"])


def _unit(unit_id: UUID) -> TargetProtocol:
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit_id, "org_unit", unit_id))


def list_target(connection: Connection, unit_id: UUID | None = None) -> TargetProtocol:
    """The unit asked about (404 if foreign), else the organization."""
    if unit_id is None:
        return organization_target()
    return _unit(OrgService(connection, current_scope()).get_unit(unit_id).id)


def create_target(body: RfqCreate, connection: Connection) -> TargetProtocol:
    """The unit of the first line: a foreign line is 404 before any permission is weighed."""
    return _unit(
        RfqService(connection, current_scope()).unit_of_line(body.lines[0].requisition_line_id)
    )


def rfq_target(id: UUID, connection: Connection) -> TargetProtocol:
    return _unit(RfqService(connection, current_scope()).unit_of(id))


ListContext = Annotated[AuthorizationContext, Depends(require("rfq.create", list_target))]
CreateContext = Annotated[AuthorizationContext, Depends(require("rfq.create", create_target))]
EditContext = Annotated[AuthorizationContext, Depends(require("rfq.edit", rfq_target))]
ViewContext = Annotated[AuthorizationContext, Depends(require("rfq.view", rfq_target))]


@router.get("/requisition-lines", response_model=SourceablePage)
def list_requisition_lines(
    context: ListContext,
    connection: Connection,
    unit_id: UUID | None = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    q: str = "",
    cursor: str | None = None,
    with_rfq: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SourceablePage:
    return RfqService(connection, current_scope(), context.user_id).sourceable(
        unit_id=unit_id, currency=currency, q=q, cursor=cursor, limit=limit, with_rfq=with_rfq
    )


@router.post("/rfqs", status_code=201, response_model=RfqRead)
def create_rfq(
    body: RfqCreate,
    context: CreateContext,
    connection: Connection,
    settings: Annotated[Settings, Depends(get_auth_settings)],
    response: Response,
) -> RfqRead:
    rfq = RfqService(connection, current_scope(), context.user_id, settings).create(body)
    response.headers["ETag"] = f'"{rfq.edit_revision}"'
    return rfq


@router.patch("/rfqs/{id}/lines", response_model=RfqRead)
def patch_rfq_lines(
    id: UUID,
    body: RfqLinesPatch,
    context: EditContext,
    connection: Connection,
    response: Response,
    revision: Annotated[str | None, Header(alias="If-Match")] = None,
) -> RfqRead:
    rfq = RfqService(connection, current_scope(), context.user_id).update_lines(id, body, revision)
    response.headers["ETag"] = f'"{rfq.edit_revision}"'
    return rfq


@router.get("/rfqs/{id}", response_model=RfqRead)
def get_rfq(id: UUID, context: ViewContext, connection: Connection, response: Response) -> RfqRead:
    rfq = RfqService(connection, current_scope(), context.user_id).get(id)
    response.headers["ETag"] = f'"{rfq.edit_revision}"'
    return rfq
