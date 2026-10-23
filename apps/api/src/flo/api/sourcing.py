"""Parse and authorize RFQ drafts built from requisition lines (SRC-001, SRC-003)."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from flo.api.admin_users import organization_target
from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import permits, require, require_member
from flo.kernel.config import Settings
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.identity.service import IdentityAuthorizationConnection, granted_scopes
from flo.modules.org.service import OrgService
from flo.modules.sourcing.lifecycle import RfqLifecycle
from flo.modules.sourcing.schemas import (
    ExtendDeadline,
    InviteeCreate,
    InviteePatch,
    RequirementsFromTemplate,
    RequirementsPut,
    RequirementWaive,
    RfqAmend,
    RfqContentPatch,
    RfqCreate,
    RfqEvent,
    RfqLinesPatch,
    RfqPage,
    RfqRead,
    RfqTab,
    RfqVersionRead,
    SourceablePage,
    VisibilityPut,
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
ApproveContext = Annotated[AuthorizationContext, Depends(require("rfq.approve", rfq_target))]
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


@router.get("/rfqs", response_model=RfqPage)
def list_rfqs(
    user_id: Annotated[IdentityId, Depends(require_member())],
    connection: Connection,
    tab: RfqTab = "all",
    q: str = "",
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
) -> RfqPage:
    org_wide, units, _ = granted_scopes(
        cast(IdentityAuthorizationConnection, connection), current_scope(), user_id, "rfq.view"
    )
    return RfqService(connection, current_scope(), user_id).list_rfqs(
        visible=None if org_wide else units, tab=tab, q=q, cursor=cursor, page_size=page_size
    )


@router.get("/rfqs/{id}", response_model=RfqRead)
def get_rfq(
    id: UUID,
    context: ViewContext,
    connection: Connection,
    request: Request,
    response: Response,
) -> RfqRead:
    rfq = RfqService(connection, current_scope(), context.user_id).get(id)
    target = rfq_target(id, connection)
    rfq.can_edit = permits(request, "rfq.edit", target)
    rfq.can_approve = permits(request, "rfq.approve", target)
    response.headers["ETag"] = f'"{rfq.edit_revision}"'
    return rfq


Revision = Annotated[str | None, Header(alias="If-Match")]


def _done(rfq: RfqRead, response: Response) -> RfqRead:
    response.headers["ETag"] = f'"{rfq.edit_revision}"'
    return rfq


@router.patch("/rfqs/{id}", response_model=RfqRead)
def patch_rfq(
    id: UUID,
    body: RfqContentPatch,
    context: EditContext,
    connection: Connection,
    response: Response,
    revision: Revision = None,
) -> RfqRead:
    service = RfqService(connection, current_scope(), context.user_id)
    return _done(service.update_content(id, body, revision), response)


@router.put("/rfqs/{id}/requirements", response_model=RfqRead)
def put_requirements(
    id: UUID,
    body: RequirementsPut,
    context: EditContext,
    connection: Connection,
    response: Response,
    revision: Revision = None,
) -> RfqRead:
    service = RfqService(connection, current_scope(), context.user_id)
    return _done(service.put_requirements(id, body, revision), response)


@router.post("/rfqs/{id}/requirements/from-template", response_model=RfqRead)
def requirements_from_template(
    id: UUID,
    body: RequirementsFromTemplate,
    context: EditContext,
    connection: Connection,
    response: Response,
) -> RfqRead:
    service = RfqService(connection, current_scope(), context.user_id)
    return _done(service.requirements_from_template(id, body), response)


@router.post("/rfqs/{id}/requirements/{rid}/waive", response_model=RfqRead)
def waive_requirement(
    id: UUID,
    rid: UUID,
    body: RequirementWaive,
    context: ApproveContext,
    connection: Connection,
    response: Response,
) -> RfqRead:
    service = RfqService(connection, current_scope(), context.user_id)
    return _done(service.waive_requirement(id, rid, body), response)


@router.put("/rfqs/{id}/attachments/{aid}/visibility", response_model=RfqRead)
def put_visibility(
    id: UUID,
    aid: UUID,
    body: VisibilityPut,
    context: EditContext,
    connection: Connection,
    response: Response,
    revision: Revision = None,
) -> RfqRead:
    service = RfqService(connection, current_scope(), context.user_id)
    return _done(service.set_visibility(id, aid, body, revision), response)


@router.post("/rfqs/{id}/invitees", status_code=201, response_model=RfqRead)
def add_invitee(
    id: UUID,
    body: InviteeCreate,
    context: EditContext,
    connection: Connection,
    request: Request,
    response: Response,
) -> RfqRead:
    service = RfqService(connection, current_scope(), context.user_id)
    # An override is an approver's act (decision 146); the body alone never grants it.
    can_override = permits(request, "rfq.approve", rfq_target(id, connection))
    return _done(service.add_invitee(id, body, can_override), response)


@router.delete("/rfqs/{id}/invitees/{vendor_id}", response_model=RfqRead)
def remove_invitee(
    id: UUID,
    vendor_id: UUID,
    context: EditContext,
    connection: Connection,
    response: Response,
    revision: Revision = None,
) -> RfqRead:
    service = RfqService(connection, current_scope(), context.user_id)
    return _done(service.remove_invitee(id, vendor_id, revision), response)


@router.patch("/rfqs/{id}/invitees/{vendor_id}", response_model=RfqRead)
def patch_invitee(
    id: UUID,
    vendor_id: UUID,
    body: InviteePatch,
    context: EditContext,
    connection: Connection,
    response: Response,
    revision: Revision = None,
) -> RfqRead:
    service = RfqService(connection, current_scope(), context.user_id)
    return _done(service.update_invitee(id, vendor_id, body, revision), response)


@router.post("/rfqs/{id}/publish", response_model=RfqRead)
def publish_rfq(
    id: UUID, context: EditContext, connection: Connection, response: Response
) -> RfqRead:
    return _done(RfqLifecycle(connection, current_scope(), context.user_id).publish(id), response)


@router.post("/rfqs/{id}/amend", response_model=RfqRead)
def amend_rfq(
    id: UUID, body: RfqAmend, context: EditContext, connection: Connection, response: Response
) -> RfqRead:
    return _done(
        RfqLifecycle(connection, current_scope(), context.user_id).amend(id, body), response
    )


@router.post("/rfqs/{id}/extend-deadline", response_model=RfqRead)
def extend_rfq_deadline(
    id: UUID,
    body: ExtendDeadline,
    context: EditContext,
    connection: Connection,
    response: Response,
) -> RfqRead:
    return _done(
        RfqLifecycle(connection, current_scope(), context.user_id).extend(id, body), response
    )


@router.post("/rfqs/{id}/start-evaluation", response_model=RfqRead)
def start_rfq_evaluation(
    id: UUID, context: EditContext, connection: Connection, response: Response
) -> RfqRead:
    service = RfqLifecycle(connection, current_scope(), context.user_id)
    return _done(service.start_evaluation(id), response)


@router.post("/rfqs/{id}/invitees/{vendor_id}/resend", response_model=RfqRead)
def resend_rfq(
    id: UUID, vendor_id: UUID, context: EditContext, connection: Connection, response: Response
) -> RfqRead:
    service = RfqLifecycle(connection, current_scope(), context.user_id)
    return _done(service.resend(id, vendor_id), response)


@router.get("/rfqs/{id}/versions", response_model=list[RfqVersionRead])
def list_rfq_versions(
    id: UUID, context: ViewContext, connection: Connection
) -> list[RfqVersionRead]:
    return RfqLifecycle(connection, current_scope(), context.user_id).versions(id)


@router.get("/rfqs/{id}/history", response_model=list[RfqEvent])
def rfq_history(id: UUID, context: ViewContext, connection: Connection) -> list[RfqEvent]:
    return RfqService(connection, current_scope(), context.user_id).history(id)
