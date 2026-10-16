"""Parse and authorize vendor operations (VEN-001, VEN-008, VEN-009)."""

from datetime import date
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
from flo.modules.org.service import OrgService, today
from flo.modules.vendors.schemas import (
    AnswersPut,
    ContactCreate,
    ContactPatch,
    ContactRead,
    DisableCreate,
    DisputeCreate,
    DisputeRead,
    DisputeResolve,
    DocState,
    DocumentCreate,
    DocumentReject,
    DocumentReplace,
    OnboardingRead,
    PerformanceRead,
    ScoreCreate,
    ScorePage,
    ScoreRead,
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
ApproveContext = Annotated[
    AuthorizationContext, Depends(require("vendor.approve", organization_target))
]
RequestContext = Annotated[
    AuthorizationContext, Depends(require("vendor.request", organization_target))
]
ScoreOrdersContext = Annotated[
    AuthorizationContext, Depends(require("purchase_order.view", organization_target))
]
UnitEditContext = Annotated[AuthorizationContext, Depends(require("vendor.edit", unit_target))]


@router.post("/onboarding-requests", status_code=201, response_model=OnboardingRead)
def create_onboarding_request(
    body: VendorCreate,
    context: RequestContext,
    connection: Connection,
    settings: Annotated[Settings, Depends(get_auth_settings)],
    response: Response,
) -> OnboardingRead:
    request = VendorService(
        connection, current_scope(), context.user_id, settings
    ).create_onboarding(body)
    response.headers["ETag"] = f'"{request.row_version}"'
    return request


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


@router.get("/{id}/contacts", response_model=list[ContactRead])
def list_contacts(id: UUID, context: ViewContext, connection: Connection) -> list[ContactRead]:
    return VendorService(connection, current_scope(), context.user_id).list_contacts(id)


@router.post("/{id}/contacts", status_code=201, response_model=ContactRead)
def create_contact(
    id: UUID, body: ContactCreate, context: EditContext, connection: Connection
) -> ContactRead:
    return VendorService(connection, current_scope(), context.user_id).create_contact(id, body)


@router.patch("/{id}/contacts/{contact_id}", response_model=ContactRead)
def patch_contact(
    id: UUID, contact_id: UUID, body: ContactPatch, context: EditContext, connection: Connection
) -> ContactRead:
    return VendorService(connection, current_scope(), context.user_id).update_contact(
        id, contact_id, body
    )


@router.delete("/{id}/contacts/{contact_id}", status_code=204)
def delete_contact(
    id: UUID, contact_id: UUID, context: EditContext, connection: Connection
) -> Response:
    VendorService(connection, current_scope(), context.user_id).delete_contact(id, contact_id)
    return Response(status_code=204)


@router.get("/{id}/documents", response_model=list[DocState])
def list_documents(id: UUID, context: ViewContext, connection: Connection) -> list[DocState]:
    return VendorService(connection, current_scope(), context.user_id).document_state(id, today())


@router.post("/{id}/documents", status_code=201, response_model=DocState)
def add_document(
    id: UUID, body: DocumentCreate, context: EditContext, connection: Connection
) -> DocState:
    return VendorService(connection, current_scope(), context.user_id).add_document(id, body)


@router.post("/{id}/documents/{did}/replace", response_model=DocState)
def replace_document(
    id: UUID, did: UUID, body: DocumentReplace, context: EditContext, connection: Connection
) -> DocState:
    return VendorService(connection, current_scope(), context.user_id).replace_document(
        id, did, body
    )


@router.post("/{id}/documents/{did}/verify", response_model=DocState)
def verify_document(
    id: UUID, did: UUID, context: ApproveContext, connection: Connection
) -> DocState:
    return VendorService(connection, current_scope(), context.user_id).verify_document(id, did)


@router.post("/{id}/documents/{did}/reject", response_model=DocState)
def reject_document(
    id: UUID, did: UUID, body: DocumentReject, context: ApproveContext, connection: Connection
) -> DocState:
    return VendorService(connection, current_scope(), context.user_id).reject_document(
        id, did, body
    )


@router.get("/{id}/onboarding", response_model=OnboardingRead)
def get_onboarding(
    id: UUID, context: RequestContext, connection: Connection, response: Response
) -> OnboardingRead:
    request = VendorService(connection, current_scope(), context.user_id).get_onboarding(id)
    response.headers["ETag"] = f'"{request.row_version}"'
    return request


@router.put("/{id}/onboarding/answers", response_model=OnboardingRead)
def put_onboarding_answers(
    id: UUID,
    body: AnswersPut,
    context: RequestContext,
    connection: Connection,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> OnboardingRead:
    request = VendorService(connection, current_scope(), context.user_id).put_answers(
        id, body, if_match
    )
    response.headers["ETag"] = f'"{request.row_version}"'
    return request


@router.get("/{id}/scores", response_model=ScorePage)
def list_scores(
    id: UUID,
    context: ViewContext,
    connection: Connection,
    category: str | None = None,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ScorePage:
    return VendorService(connection, current_scope(), context.user_id).list_scores(
        id, category, start, end, cursor, limit
    )


@router.post("/{id}/scores", status_code=201, response_model=ScoreRead)
def add_score(
    id: UUID,
    body: ScoreCreate,
    context: ViewContext,
    orders: ScoreOrdersContext,
    connection: Connection,
) -> ScoreRead:
    """Purchasing users score: they hold purchase_order.view and vendor.view (E11-S07)."""
    return VendorService(connection, current_scope(), context.user_id).add_score(id, body)


@router.post("/{id}/scores/{sid}/disputes", status_code=201, response_model=DisputeRead)
def dispute_score(
    id: UUID, sid: UUID, body: DisputeCreate, context: EditContext, connection: Connection
) -> DisputeRead:
    return VendorService(connection, current_scope(), context.user_id).dispute_score(id, sid, body)


@router.post("/{id}/scores/{sid}/disputes/{did}/resolve", response_model=DisputeRead)
def resolve_dispute(
    id: UUID,
    sid: UUID,
    did: UUID,
    body: DisputeResolve,
    context: ApproveContext,
    connection: Connection,
) -> DisputeRead:
    return VendorService(connection, current_scope(), context.user_id).resolve_dispute(
        id, sid, did, body
    )


@router.get("/{id}/performance", response_model=PerformanceRead)
def performance(
    id: UUID,
    context: ViewContext,
    connection: Connection,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> PerformanceRead:
    return VendorService(connection, current_scope(), context.user_id).performance(id, start, end)
