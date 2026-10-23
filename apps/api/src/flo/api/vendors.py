"""Parse and authorize vendor operations (VEN-001, VEN-008, VEN-009)."""

import re
from datetime import date
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from flo.api.admin_users import organization_target
from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import permits, require, require_member
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService, today
from flo.modules.vendors.schemas import (
    AnswersPut,
    AttentionState,
    ChangeCreate,
    ChangeRead,
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
    DuplicateResult,
    Eligibility,
    InactivateCreate,
    OnboardingRead,
    PerformanceRead,
    ScoreCreate,
    ScorePage,
    ScoreRead,
    SensitivePut,
    SensitiveReveal,
    SensitiveValues,
    SensitiveWritten,
    SubmitCreate,
    VendorCounts,
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


def with_actions(request: Request, connection: Connection, detail: VendorDetail) -> VendorDetail:
    """Add the caller's permitted actions, judged by the same targets the routes use."""

    def can(permission: str, unit_id: UUID | None) -> bool:
        target = (
            organization_target()
            if unit_id is None
            else AuthorizationTarget(ScopeType.BU, unit_id, "org_unit", unit_id)
        )
        return permits(request, permission, cast(TargetProtocol, target))

    return VendorService(connection, current_scope()).with_actions(detail, can)


ViewContext = Annotated[AuthorizationContext, Depends(require("vendor.view", organization_target))]
CreateContext = Annotated[
    AuthorizationContext, Depends(require("vendor.create", organization_target))
]
EditContext = Annotated[AuthorizationContext, Depends(require("vendor.edit", organization_target))]
ApproveContext = Annotated[
    AuthorizationContext, Depends(require("vendor.approve", organization_target))
]
RevealContext = Annotated[
    AuthorizationContext, Depends(require("vendor.sensitive.reveal", organization_target))
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
    documents: Annotated[list[AttentionState] | None, Query()] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> VendorPage:
    return VendorService(connection, current_scope(), context.user_id).search(
        q=q,
        statuses=status,
        available_in=available_in,
        documents=documents,
        cursor=cursor,
        limit=limit,
    )


@router.get("/counts", response_model=VendorCounts)
def vendor_counts(context: ViewContext, connection: Connection) -> VendorCounts:
    return VendorService(connection, current_scope(), context.user_id).counts()


@router.get("/{id}/eligibility", response_model=Eligibility)
def vendor_eligibility(
    id: UUID, unit_id: UUID, context: ViewContext, connection: Connection
) -> Eligibility:
    rules = VendorService(connection, current_scope(), context.user_id).eligibility(
        [id], unit_id, today()
    )
    if id not in rules:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return rules[id]


@router.get("/duplicate-check", response_model=DuplicateResult)
def duplicate_check(
    request: Request,
    context: RequestContext,
    connection: Connection,
    tax_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    tax_id_country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
) -> DuplicateResult:
    """`identifiers[type]=value` pairs ride the query string beside the tax id."""
    identifiers = {
        match[1]: value
        for key, value in request.query_params.items()
        if (match := re.fullmatch(r"identifiers\[(.{1,64})\]", key)) and value
    }
    return VendorService(connection, current_scope(), context.user_id).duplicate_check(
        tax_id, tax_id_country, identifiers
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
def get_vendor(
    id: UUID, context: ViewContext, connection: Connection, response: Response, request: Request
) -> VendorDetail:
    detail = VendorService(connection, current_scope(), context.user_id).get(id)
    detail = with_actions(request, connection, detail)
    response.headers["ETag"] = f'"{detail.version}"'  # the vendor's version; PATCH's If-Match
    return detail


@router.patch("/{id}", response_model=VendorDetail)
def patch_vendor(
    id: UUID,
    body: VendorPatch,
    context: EditContext,
    connection: Connection,
    response: Response,
    request: Request,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> VendorDetail:
    vendor = VendorService(connection, current_scope(), context.user_id).update(id, body, version)
    vendor = with_actions(request, connection, vendor)
    response.headers["ETag"] = f'"{vendor.version}"'
    return vendor


@router.post("/{id}/units/{unit_id}/disable", response_model=VendorDetail)
def disable_vendor(
    id: UUID,
    unit_id: UUID,
    body: DisableCreate,
    context: UnitEditContext,
    connection: Connection,
    request: Request,
) -> VendorDetail:
    detail = VendorService(connection, current_scope(), context.user_id).disable_locally(
        id, unit_id, body
    )
    return with_actions(request, connection, detail)


@router.post("/{id}/units/{unit_id}/enable", response_model=VendorDetail)
def enable_vendor(
    id: UUID, unit_id: UUID, context: UnitEditContext, connection: Connection, request: Request
) -> VendorDetail:
    detail = VendorService(connection, current_scope(), context.user_id).enable_locally(id, unit_id)
    return with_actions(request, connection, detail)


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
    id: UUID,
    contact_id: UUID,
    body: ContactPatch,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ContactRead:
    contact = VendorService(connection, current_scope(), context.user_id).update_contact(
        id, contact_id, body, version
    )
    response.headers["ETag"] = f'"{contact.version}"'
    return contact


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


@router.post("/{id}/sensitive/reveal", response_model=SensitiveValues)
def reveal_sensitive(
    id: UUID, body: SensitiveReveal, context: RevealContext, connection: Connection
) -> JSONResponse:
    values = VendorService(connection, current_scope(), context.user_id).reveal_sensitive(id, body)
    return JSONResponse(
        values.model_dump(), headers={"Cache-Control": "no-store", "ETag": f'"{values.version}"'}
    )


@router.put("/{id}/sensitive", response_model=SensitiveWritten)
def put_sensitive(
    id: UUID,
    body: SensitivePut,
    edit: EditContext,
    context: RevealContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> SensitiveWritten:
    written = VendorService(connection, current_scope(), context.user_id).put_sensitive(
        id, body, version
    )
    response.headers["ETag"] = f'"{written.version}"'
    return written


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


@router.post("/{id}/submit", response_model=ChangeRead)
def submit_vendor(
    id: UUID,
    body: SubmitCreate,
    user: Annotated[UUID, Depends(require_member())],
    connection: Connection,
) -> ChangeRead:
    """A member; the service admits only the vendor's requester or a vendor.edit holder."""
    return VendorService(connection, current_scope(), user).submit(id, body)


@router.post("/{id}/revise", status_code=204)
def revise_vendor(id: UUID, context: EditContext, connection: Connection) -> Response:
    VendorService(connection, current_scope(), context.user_id).revise(id)
    return Response(status_code=204)


@router.post("/{id}/changes", status_code=201, response_model=ChangeRead)
def request_change(
    id: UUID, body: ChangeCreate, context: EditContext, connection: Connection
) -> ChangeRead:
    return VendorService(connection, current_scope(), context.user_id).request_change(id, body)


@router.get("/{id}/changes", response_model=list[ChangeRead])
def list_changes(id: UUID, context: ViewContext, connection: Connection) -> list[ChangeRead]:
    return VendorService(connection, current_scope(), context.user_id).list_changes(id)


@router.post("/{id}/changes/{cid}/withdraw", response_model=ChangeRead)
def withdraw_change(
    id: UUID, cid: UUID, context: RequestContext, connection: Connection
) -> ChangeRead:
    return VendorService(connection, current_scope(), context.user_id).withdraw_change(id, cid)


@router.post("/{id}/inactivate", status_code=204)
def inactivate_vendor(
    id: UUID, body: InactivateCreate, context: ApproveContext, connection: Connection
) -> Response:
    VendorService(connection, current_scope(), context.user_id).inactivate(id, body)
    return Response(status_code=204)
