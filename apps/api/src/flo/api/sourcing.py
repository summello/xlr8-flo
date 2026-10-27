"""Parse and authorize RFQ drafts built from requisition lines (SRC-001, SRC-003)."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from flo.api.admin_users import organization_target
from flo.api.auth import get_auth_settings
from flo.api.authz_factory import production_authorization_resolver
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import HoldersResolverFactory, permits, require, require_member
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.identity.service import IdentityAuthorizationConnection, granted_scopes
from flo.modules.org.service import OrgService
from flo.modules.sourcing.awards import (
    AwardCreate,
    AwardLinesPut,
    AwardPatch,
    AwardRead,
    AwardService,
)
from flo.modules.sourcing.bids import BidService
from flo.modules.sourcing.comparison import (
    ComparisonRead,
    ComparisonService,
    SealedComparisonRead,
)
from flo.modules.sourcing.evaluation import (
    CriteriaPut,
    CriteriaRead,
    DeclarationIn,
    EligibleEvaluatorPage,
    EvaluationRead,
    EvaluationScoreResult,
    EvaluationService,
    EvaluatorAdd,
    EvaluatorRead,
    ScoresPut,
)
from flo.modules.sourcing.lifecycle import RfqLifecycle
from flo.modules.sourcing.schemas import (
    BidCreate,
    BidPatch,
    BidRead,
    BidReplace,
    BidSubmit,
    ExtendDeadline,
    InviteeCandidatePage,
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
    SealedBidRead,
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


@router.get("/rfqs/{id}/invitee-candidates", response_model=InviteeCandidatePage)
def invitee_candidates(
    id: UUID,
    context: EditContext,
    connection: Connection,
    q: Annotated[str, Query(max_length=200)] = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> InviteeCandidatePage:
    service = RfqService(connection, current_scope(), context.user_id)
    return service.invitee_candidates(id, q, cursor, limit)


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


def _bid_done(bid: BidRead | SealedBidRead, response: Response) -> BidRead | SealedBidRead:
    response.headers["ETag"] = f'"{bid.row_version}"'
    return bid


@router.post("/rfqs/{id}/bids", status_code=201, response_model=BidRead | SealedBidRead)
def create_bid(
    id: UUID, body: BidCreate, context: EditContext, connection: Connection, response: Response
) -> BidRead | SealedBidRead:
    return _bid_done(
        BidService(connection, current_scope(), context.user_id).capture(id, body), response
    )


@router.get("/rfqs/{id}/bids", response_model=list[BidRead | SealedBidRead])
def list_bids(
    id: UUID,
    user_id: Annotated[IdentityId, Depends(require_member())],
    connection: Connection,
    request: Request,
) -> list[BidRead | SealedBidRead]:
    target = rfq_target(id, connection)  # a foreign RFQ is 404 before any permission is weighed
    approve = permits(request, "rfq.approve", target)
    if not approve and not permits(request, "rfq.edit", target):
        raise ProblemError(ErrorCode.FORBIDDEN)
    return BidService(connection, current_scope(), user_id).list_bids(id, all_bids=approve)


@router.get("/rfqs/{id}/bids/{bid_id}", response_model=BidRead | SealedBidRead)
def get_bid(
    id: UUID,
    bid_id: UUID,
    user_id: Annotated[IdentityId, Depends(require_member())],
    connection: Connection,
    request: Request,
    response: Response,
) -> BidRead | SealedBidRead:
    target = rfq_target(id, connection)
    approve = permits(request, "rfq.approve", target)
    bid = BidService(connection, current_scope(), user_id).read(id, bid_id, all_bids=approve)
    return _bid_done(bid, response)


@router.patch("/rfqs/{id}/bids/{bid_id}", response_model=BidRead | SealedBidRead)
def patch_bid(
    id: UUID,
    bid_id: UUID,
    body: BidPatch,
    context: EditContext,
    connection: Connection,
    response: Response,
    revision: Revision = None,
) -> BidRead | SealedBidRead:
    service = BidService(connection, current_scope(), context.user_id)
    return _bid_done(service.patch(id, bid_id, body, revision), response)


@router.post("/rfqs/{id}/bids/{bid_id}/submit", response_model=BidRead | SealedBidRead)
def submit_bid(
    id: UUID,
    bid_id: UUID,
    context: EditContext,
    connection: Connection,
    response: Response,
    request: Request,
    body: BidSubmit | None = None,
    revision: Revision = None,
) -> BidRead | SealedBidRead:
    service = BidService(connection, current_scope(), context.user_id)
    # the lead may accept a late bid; the service never reads permissions itself
    late_ok = permits(request, "rfq.approve", rfq_target(id, connection))
    submitted = service.submit(id, bid_id, body or BidSubmit(), revision, may_accept_late=late_ok)
    return _bid_done(submitted, response)


@router.post(
    "/rfqs/{id}/bids/{bid_id}/replace", status_code=201, response_model=BidRead | SealedBidRead
)
def replace_bid(
    id: UUID,
    bid_id: UUID,
    body: BidReplace,
    context: EditContext,
    connection: Connection,
    response: Response,
) -> BidRead | SealedBidRead:
    service = BidService(connection, current_scope(), context.user_id)
    return _bid_done(service.replace(id, bid_id, body), response)


@router.get("/rfqs/{id}/comparison", response_model=ComparisonRead | SealedComparisonRead)
def get_comparison(
    id: UUID, context: ViewContext, connection: Connection
) -> ComparisonRead | SealedComparisonRead:
    return ComparisonService(connection, current_scope(), context.user_id).comparison(id)


@router.post("/rfqs/{id}/comparison/refresh-rate", response_model=ComparisonRead)
def refresh_comparison_rate(
    id: UUID, context: EditContext, connection: Connection
) -> ComparisonRead:
    return ComparisonService(connection, current_scope(), context.user_id).refresh_rate(id)


AWARD = "/rfqs/{id}/award"


def _award_done(award: AwardRead, response: Response) -> AwardRead:
    response.headers["ETag"] = f'"{award.version}"'
    return award


@router.post(AWARD, status_code=201, response_model=AwardRead)
def create_award(
    id: UUID, body: AwardCreate, context: EditContext, connection: Connection, response: Response
) -> AwardRead:
    service = AwardService(connection, current_scope(), context.user_id)
    return _award_done(service.draft(id, body), response)


@router.get(AWARD, response_model=AwardRead)
def get_award(
    id: UUID, context: ViewContext, connection: Connection, response: Response
) -> AwardRead:
    return _award_done(
        AwardService(connection, current_scope(), context.user_id).read(id), response
    )


@router.put(f"{AWARD}/lines", response_model=AwardRead)
def put_award_lines(
    id: UUID,
    body: AwardLinesPut,
    context: EditContext,
    connection: Connection,
    response: Response,
    revision: Revision = None,
) -> AwardRead:
    service = AwardService(connection, current_scope(), context.user_id)
    return _award_done(service.put_lines(id, body, revision), response)


@router.patch(AWARD, response_model=AwardRead)
def patch_award(
    id: UUID,
    body: AwardPatch,
    context: EditContext,
    connection: Connection,
    response: Response,
    revision: Revision = None,
) -> AwardRead:
    service = AwardService(connection, current_scope(), context.user_id)
    return _award_done(service.patch(id, body, revision), response)


@router.post(f"{AWARD}/submit", response_model=AwardRead)
def submit_award(
    id: UUID, context: ApproveContext, connection: Connection, response: Response
) -> AwardRead:
    service = AwardService(connection, current_scope(), context.user_id)
    return _award_done(service.submit(id), response)


@router.post(f"{AWARD}/withdraw", response_model=AwardRead)
def withdraw_award(
    id: UUID, context: EditContext, connection: Connection, response: Response
) -> AwardRead:
    service = AwardService(connection, current_scope(), context.user_id)
    return _award_done(service.withdraw(id), response)


EVALUATION = "/rfqs/{id}/evaluation"


@router.get(EVALUATION, response_model=EvaluationRead)
def get_evaluation(
    id: UUID, context: ViewContext, connection: Connection, request: Request, response: Response
) -> EvaluationRead:
    target = rfq_target(id, connection)
    view = EvaluationService(connection, current_scope(), context.user_id).view(
        id,
        can_edit=permits(request, "rfq.edit", target),
        can_approve=permits(request, "rfq.approve", target),
    )
    if view.me is not None:
        response.headers["ETag"] = f'"{view.me.version}"'
    return view


@router.put(f"{EVALUATION}/criteria", response_model=CriteriaRead)
def put_criteria(
    id: UUID,
    body: CriteriaPut,
    context: EditContext,
    connection: Connection,
    response: Response,
    revision: Revision = None,
) -> CriteriaRead:
    saved = EvaluationService(connection, current_scope(), context.user_id).put_criteria(
        id, body, revision
    )
    response.headers["ETag"] = f'"{saved.edit_revision}"'
    return saved


@router.post(f"{EVALUATION}/evaluators", status_code=201, response_model=EvaluatorRead)
def add_evaluator(
    id: UUID, body: EvaluatorAdd, context: EditContext, connection: Connection
) -> EvaluatorRead:
    unit_id = RfqService(connection, current_scope()).unit_of(id)
    org_wide, units, _ = granted_scopes(
        cast(IdentityAuthorizationConnection, connection),
        current_scope(),
        IdentityId(body.identity_id),
        "rfq.view",
    )
    return EvaluationService(connection, current_scope(), context.user_id).add_evaluator(
        id, body.identity_id, may_view=org_wide or unit_id in units
    )


@router.get(f"{EVALUATION}/evaluators/eligible", response_model=EligibleEvaluatorPage)
def eligible_evaluators(
    id: UUID,
    context: EditContext,
    connection: Connection,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EligibleEvaluatorPage:
    target = rfq_target(id, connection)
    factory: HoldersResolverFactory = production_authorization_resolver
    with factory(current_scope()) as resolver:
        holders = resolver.holders("rfq.view", target, limit=0)
    # ponytail: all holder ids go to one query, paged by offset; keyset past ~10k holders
    return EvaluationService(connection, current_scope(), context.user_id).eligible_evaluators(
        id, list(holders), q, limit, offset
    )


@router.delete(f"{EVALUATION}/evaluators/{{identity_id}}", status_code=204)
def remove_evaluator(
    id: UUID,
    identity_id: UUID,
    context: EditContext,
    connection: Connection,
    revision: Revision = None,
) -> Response:
    EvaluationService(connection, current_scope(), context.user_id).remove_evaluator(
        id, identity_id, revision
    )
    return Response(status_code=204)


@router.post(f"{EVALUATION}/declaration", status_code=201, response_model=EvaluatorRead)
def declare_conflict(
    id: UUID, body: DeclarationIn, context: ViewContext, connection: Connection
) -> EvaluatorRead:
    return EvaluationService(connection, current_scope(), context.user_id).declare(id, body)


@router.put(f"{EVALUATION}/scores", response_model=EvaluationScoreResult)
def put_scores(
    id: UUID,
    body: ScoresPut,
    context: ViewContext,
    connection: Connection,
    response: Response,
    revision: Revision = None,
) -> EvaluationScoreResult:
    saved = EvaluationService(connection, current_scope(), context.user_id).save_scores(
        id, body, revision
    )
    response.headers["ETag"] = f'"{saved.version}"'
    return saved


@router.post(f"{EVALUATION}/scores/submit", response_model=EvaluationScoreResult)
def submit_scores(
    id: UUID, context: ViewContext, connection: Connection, response: Response
) -> EvaluationScoreResult:
    done = EvaluationService(connection, current_scope(), context.user_id).submit_scores(id)
    response.headers["ETag"] = f'"{done.version}"'
    return done
