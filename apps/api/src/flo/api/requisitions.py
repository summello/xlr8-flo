"""Parse and authorize requisition header operations."""

from collections.abc import Callable
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from flo.api.admin_users import organization_target
from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import permits, require, require_resolved
from flo.kernel.config import Settings
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService
from flo.modules.projects.service import ProjectService
from flo.modules.requisitions import amendments, codes, revisions
from flo.modules.requisitions.lines import LineService
from flo.modules.requisitions.lookups import LookupService
from flo.modules.requisitions.schemas import (
    AmendmentPut,
    AmendmentRead,
    AmendmentSubmit,
    ChangeSet,
    CodeSubstitute,
    Direction,
    LineCreate,
    LinesRead,
    LinesReplace,
    LineWriteResult,
    LinksRead,
    LookupRows,
    RequisitionCreate,
    RequisitionPage,
    RequisitionPatch,
    RequisitionRead,
    RequisitionSort,
    RequisitionStatus,
    RequisitionTransitionAvailable,
    RequisitionTransitionCreate,
    RevisionSummary,
    StatusEventRead,
    SubmissionCheck,
)
from flo.modules.requisitions.service import (
    RequisitionService,
    available,
    check_submission,
    events,
    links,
    routing_check,
)

router = APIRouter(prefix="/api/v1/requisitions", tags=["requisitions"])


def create_target(body: RequisitionCreate, connection: Connection) -> tuple[str, TargetProtocol]:
    """A standalone request is a Project Request: its own permission on the unit (D-M2-18)."""
    if body.project_id is not None:
        project = ProjectService(connection, current_scope()).get(body.project_id)
        return "requisition.create", cast(
            TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project.id)
        )
    unit = OrgService(connection, current_scope()).get_unit(cast(UUID, body.bu_id))
    return "project_request.create", cast(
        TargetProtocol, AuthorizationTarget(ScopeType.BU, unit.id)
    )


def requisition_target(id: UUID, connection: Connection) -> TargetProtocol:
    row = RequisitionService(connection, current_scope()).get(id)
    return cast(
        TargetProtocol,
        AuthorizationTarget(
            ScopeType.BU if row.standalone else ScopeType.PROJECT,
            row.bu_id if row.standalone else cast(UUID, row.project_id),
            "requisition",
            row.id,
        ),
    )


CreateContext = Annotated[AuthorizationContext, Depends(require_resolved(create_target))]
ReadContext = Annotated[
    AuthorizationContext, Depends(require("requisition.view", requisition_target))
]
EditContext = Annotated[
    AuthorizationContext, Depends(require("requisition.edit", requisition_target))
]
SubstituteContext = Annotated[
    AuthorizationContext, Depends(require("master_code.substitute", requisition_target))
]
SubmitContext = Annotated[
    AuthorizationContext, Depends(require("requisition.submit", requisition_target))
]
WithdrawContext = Annotated[
    AuthorizationContext, Depends(require("requisition.withdraw", requisition_target))
]
ListContext = Annotated[
    AuthorizationContext, Depends(require("requisition.view", organization_target))
]


@router.post("", status_code=201, response_model=RequisitionRead)
def create_requisition(
    body: RequisitionCreate,
    context: CreateContext,
    connection: Connection,
    response: Response,
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> RequisitionRead:
    row = RequisitionService(connection, current_scope(), context.user_id, settings).create(body)
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.get("", response_model=RequisitionPage)
def list_requisitions(
    context: ListContext,
    connection: Connection,
    status: RequisitionStatus | None = None,
    project_id: UUID | None = None,
    bu_id: UUID | None = None,
    kind: Literal["project_request", "requisition"] | None = None,
    requester: Literal["me"] | None = None,
    q: str = "",
    sort: RequisitionSort = "number",
    direction: Direction = "asc",
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
) -> RequisitionPage:
    # ponytail: BU-only grants cannot list; add granted_scopes filtering
    # when a tenant uses BU-only grants.
    return RequisitionService(connection, current_scope(), context.user_id).list(
        status=status,
        project_id=project_id,
        bu_id=bu_id,
        kind=kind,
        requester=requester,
        q=q,
        sort=sort,
        direction=direction,
        cursor=cursor,
        page_size=page_size,
    )


OrgCreateContext = Annotated[
    AuthorizationContext, Depends(require("requisition.create", organization_target))
]


# Registered before "/{id}" so "lookups" is never parsed as a requisition id.
@router.get("/lookups/{kind}", response_model=LookupRows)
def lookups(
    kind: str,
    context: OrgCreateContext,
    connection: Connection,
    request: Request,
    q: str = "",
    bu_id: UUID | None = None,
    limit: int = 20,
) -> LookupRows:
    def can_raise(kind: ScopeType, id: UUID, permission: str = "requisition.create") -> bool:
        return permits(
            request,
            permission,
            cast(TargetProtocol, AuthorizationTarget(kind, id)),
        )

    return LookupService(
        connection,
        current_scope(),
        lambda id: can_raise(ScopeType.PROJECT, id),
        lambda id: can_raise(ScopeType.BU, id),
        lambda id: can_raise(ScopeType.BU, id, "project_request.create"),
    ).options(kind, q, bu_id, limit)


@router.get("/{id}", response_model=RequisitionRead)
def get_requisition(
    id: UUID, context: ReadContext, connection: Connection, response: Response, request: Request
) -> RequisitionRead:
    row = RequisitionService(connection, current_scope(), context.user_id).detail(
        id,
        lambda project_id: permits(
            request,
            "budget.view",
            cast(TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project_id)),
        ),
        lambda: permits(request, "requisition.edit", requisition_target(id, connection)),
    )
    row.can_substitute_codes = (
        row.codes_expired
        and codes.can_substitute_requisition(connection, current_scope(), id)
        and permits(request, "master_code.substitute", requisition_target(id, connection))
    )
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.patch("/{id}", response_model=RequisitionRead)
def patch_requisition(
    id: UUID,
    body: RequisitionPatch,
    context: EditContext,
    connection: Connection,
    request: Request,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> RequisitionRead:
    row = RequisitionService(connection, current_scope(), context.user_id).update(
        id,
        body,
        version,
        lambda project_id: permits(
            request,
            "requisition.create",
            cast(TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project_id)),
        ),
        can_view_budget=lambda project_id: permits(
            request,
            "budget.view",
            cast(TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project_id)),
        ),
        can_edit=lambda: True,
    )
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.get("/{id}/lines", response_model=LinesRead)
def get_lines(id: UUID, context: ReadContext, connection: Connection) -> LinesRead:
    return LineService(connection, current_scope(), context.user_id).list_lines(id)


@router.post("/{id}/lines", status_code=201, response_model=LineWriteResult)
def add_line(
    id: UUID,
    body: LineCreate,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> LineWriteResult:
    result = cast(
        LineWriteResult,
        LineService(connection, current_scope(), context.user_id).write(id, version, body=body),
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.put("/{id}/lines", response_model=LinesRead)
def replace_lines(
    id: UUID,
    body: LinesReplace,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> LinesRead:
    result = cast(
        LinesRead,
        LineService(connection, current_scope(), context.user_id).write(
            id, version, replacement=body
        ),
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.put("/{id}/lines/{line_id}", response_model=LineWriteResult)
def put_line(
    id: UUID,
    line_id: UUID,
    body: LineCreate,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> LineWriteResult:
    result = cast(
        LineWriteResult,
        LineService(connection, current_scope(), context.user_id).write(
            id, version, body=body, line_id=line_id
        ),
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.delete("/{id}/lines/{line_id}", response_model=LineWriteResult)
def delete_line(
    id: UUID,
    line_id: UUID,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> LineWriteResult:
    result = cast(
        LineWriteResult,
        LineService(connection, current_scope(), context.user_id).write(
            id, version, line_id=line_id, delete=True
        ),
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.get("/{id}/submission-check", response_model=SubmissionCheck)
def submission_check(id: UUID, context: ReadContext, connection: Connection) -> SubmissionCheck:
    return routing_check(
        connection,
        current_scope(),
        id,
        check_submission(connection, current_scope(), id, lock=False),
    )


@router.post("/{id}/transitions", response_model=RequisitionRead)
def transition_requisition(
    id: UUID,
    body: RequisitionTransitionCreate,
    context: ReadContext,
    connection: Connection,
    request: Request,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> RequisitionRead:
    # Resolve permission at the locked record's current project, including a concurrent draft move.
    row = RequisitionService(connection, current_scope(), context.user_id).transition(
        id,
        body,
        version,
        lambda permission: permits(request, permission, requisition_target(id, connection)),
        lambda project_id: permits(
            request,
            "budget.view",
            cast(TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project_id)),
        ),
        lambda: permits(request, "requisition.edit", requisition_target(id, connection)),
    )
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.get("/{id}/transitions/available", response_model=list[RequisitionTransitionAvailable])
def available_transitions(
    id: UUID, context: ReadContext, connection: Connection, request: Request
) -> list[RequisitionTransitionAvailable]:
    return available(
        connection,
        current_scope(),
        id,
        context.user_id,
        lambda permission: permits(request, permission, requisition_target(id, connection)),
    )


@router.get("/{id}/events", response_model=list[StatusEventRead])
def requisition_events(
    id: UUID, context: ReadContext, connection: Connection
) -> list[StatusEventRead]:
    return events(connection, current_scope(), id)


def _budget_view(request: Request) -> Callable[[UUID], bool]:
    return lambda project_id: permits(
        request,
        "budget.view",
        cast(TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, project_id)),
    )


@router.get("/{id}/links", response_model=LinksRead)
def requisition_links(
    id: UUID, context: ReadContext, connection: Connection, request: Request
) -> LinksRead:
    return links(
        connection,
        current_scope(),
        id,
        lambda permission, scope_type, target_id: permits(
            request,
            permission,
            cast(TargetProtocol, AuthorizationTarget(ScopeType(scope_type), target_id)),
        ),
    )


@router.get("/{id}/revisions", response_model=list[RevisionSummary])
def list_revisions(id: UUID, context: ReadContext, connection: Connection) -> list[RevisionSummary]:
    RequisitionService(connection, current_scope()).get(id)
    return revisions.summaries(connection, current_scope(), id)


@router.get("/{id}/changes", response_model=ChangeSet)
def requisition_changes(
    id: UUID,
    context: ReadContext,
    connection: Connection,
    request: Request,
    against: int | None = None,
) -> ChangeSet:
    return revisions.change_set(connection, current_scope(), id, against, _budget_view(request))


@router.post("/{id}/amendments", status_code=201, response_model=AmendmentRead)
def open_amendment(
    id: UUID,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> AmendmentRead:
    row = amendments.create(connection, current_scope(), id, version, context.user_id)
    response.headers["ETag"] = f'"{row.version}"'
    return row


# Registered before the numbered routes so "current" is never parsed as a revision number.
@router.get("/{id}/amendments/current", response_model=AmendmentRead)
def current_amendment(
    id: UUID, context: ReadContext, connection: Connection, response: Response
) -> AmendmentRead:
    row = amendments.current(connection, current_scope(), id)
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.put("/{id}/amendments/{revision_no}", response_model=AmendmentRead)
def put_amendment(
    id: UUID,
    revision_no: int,
    body: AmendmentPut,
    context: EditContext,
    connection: Connection,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> AmendmentRead:
    row = amendments.put(
        connection, current_scope(), id, revision_no, body, version, context.user_id
    )
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.delete("/{id}/amendments/{revision_no}", status_code=204)
def delete_amendment(
    id: UUID, revision_no: int, context: EditContext, connection: Connection
) -> Response:
    amendments.delete(connection, current_scope(), id, revision_no, context.user_id)
    return Response(status_code=204)


@router.post("/{id}/amendments/{revision_no}/submit", response_model=RequisitionRead)
def submit_amendment(
    id: UUID,
    revision_no: int,
    context: SubmitContext,
    connection: Connection,
    request: Request,
    response: Response,
    body: AmendmentSubmit | None = None,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> RequisitionRead:
    service = RequisitionService(connection, current_scope(), context.user_id)
    amendments.submit(
        connection,
        current_scope(),
        id,
        revision_no,
        body or AmendmentSubmit(),
        version,
        context.user_id,
    )
    row = service.detail(
        id,
        _budget_view(request),
        lambda: permits(request, "requisition.edit", requisition_target(id, connection)),
    )
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.post("/{id}/amendments/{revision_no}/withdraw", response_model=RequisitionRead)
def withdraw_amendment(
    id: UUID,
    revision_no: int,
    context: WithdrawContext,
    connection: Connection,
    request: Request,
    response: Response,
) -> RequisitionRead:
    amendments.withdraw_amendment(
        connection,
        current_scope(),
        id,
        revision_no,
        context.user_id,
        lambda permission: permits(request, permission, requisition_target(id, connection)),
    )
    row = RequisitionService(connection, current_scope(), context.user_id).detail(
        id,
        _budget_view(request),
        lambda: permits(request, "requisition.edit", requisition_target(id, connection)),
    )
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.delete("/{id}", status_code=204)
def delete_requisition(id: UUID, context: EditContext, connection: Connection) -> Response:
    RequisitionService(connection, current_scope(), context.user_id).delete(id)
    return Response(status_code=204)


@router.post("/{id}/codes", response_model=RequisitionRead)
def substitute_requisition_codes(
    id: UUID, body: CodeSubstitute, context: SubstituteContext, connection: Connection
) -> RequisitionRead:
    codes.substitute_requisition(
        connection,
        current_scope(),
        context.user_id,
        id,
        body.model_dump(exclude={"reason"}, exclude_none=True),
        body.reason,
    )
    return RequisitionService(connection, current_scope(), context.user_id).get(id)
