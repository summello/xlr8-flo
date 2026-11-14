"""Parse and authorize the onboarding questionnaire administration (VEN-006)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response

from flo.api.admin_users import organization_target
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext
from flo.modules.vendors.schemas import (
    QuestionAdmin,
    QuestionCreate,
    QuestionMove,
    QuestionPatch,
    VersionDetail,
    VersionPatch,
    VersionRead,
)
from flo.modules.vendors.service import VendorService

router = APIRouter(prefix="/api/v1/admin/vendor-questionnaires", tags=["vendor-questionnaires"])


def version_target(vid: UUID, connection: Connection) -> TargetProtocol:
    """A foreign or unknown version is 404 before any permission is weighed."""
    VendorService(connection, current_scope()).get_questionnaire(vid)
    return organization_target()


OrgContext = Annotated[
    AuthorizationContext, Depends(require("org.setting.manage", organization_target))
]
VersionContext = Annotated[
    AuthorizationContext, Depends(require("org.setting.manage", version_target))
]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


def service(context: AuthorizationContext, connection: Connection) -> VendorService:
    return VendorService(connection, current_scope(), context.user_id)


@router.get("", response_model=list[VersionRead])
def list_versions(context: OrgContext, connection: Connection) -> list[VersionRead]:
    return service(context, connection).list_questionnaires()


@router.post("", status_code=201, response_model=VersionDetail)
def create_version(
    context: OrgContext, connection: Connection, response: Response
) -> VersionDetail:
    detail = service(context, connection).create_questionnaire()
    response.headers["ETag"] = f'"{detail.row_version}"'
    return detail


@router.get("/{vid}", response_model=VersionDetail)
def get_version(
    vid: UUID, context: VersionContext, connection: Connection, response: Response
) -> VersionDetail:
    detail = service(context, connection).get_questionnaire(vid)
    response.headers["ETag"] = f'"{detail.row_version}"'
    return detail


@router.patch("/{vid}", response_model=VersionDetail)
def patch_version(
    vid: UUID,
    body: VersionPatch,
    context: VersionContext,
    connection: Connection,
    response: Response,
    if_match: IfMatch = None,
) -> VersionDetail:
    detail = service(context, connection).update_questionnaire(vid, body, if_match)
    response.headers["ETag"] = f'"{detail.row_version}"'
    return detail


@router.post("/{vid}/questions", status_code=201, response_model=QuestionAdmin)
def add_question(
    vid: UUID,
    body: QuestionCreate,
    context: VersionContext,
    connection: Connection,
    response: Response,
) -> QuestionAdmin:
    question = service(context, connection).add_question(vid, body)
    response.headers["ETag"] = f'"{question.row_version}"'
    return question


@router.patch("/{vid}/questions/{key}", response_model=QuestionAdmin)
def patch_question(
    vid: UUID,
    key: str,
    body: QuestionPatch,
    context: VersionContext,
    connection: Connection,
    response: Response,
    if_match: IfMatch = None,
) -> QuestionAdmin:
    question = service(context, connection).update_question(vid, key, body, if_match)
    response.headers["ETag"] = f'"{question.row_version}"'
    return question


@router.delete("/{vid}/questions/{key}", status_code=204)
def delete_question(
    vid: UUID, key: str, context: VersionContext, connection: Connection, if_match: IfMatch = None
) -> Response:
    service(context, connection).delete_question(vid, key, if_match)
    return Response(status_code=204)


@router.post("/{vid}/questions/{key}/move", response_model=VersionDetail)
def move_question(
    vid: UUID, key: str, body: QuestionMove, context: VersionContext, connection: Connection
) -> VersionDetail:
    return service(context, connection).move_question(vid, key, body)


@router.post("/{vid}/publish", response_model=VersionDetail)
def publish_version(vid: UUID, context: VersionContext, connection: Connection) -> VersionDetail:
    return service(context, connection).publish_questionnaire(vid)


@router.post("/{vid}/copy", status_code=201, response_model=VersionDetail)
def copy_version(vid: UUID, context: VersionContext, connection: Connection) -> VersionDetail:
    return service(context, connection).copy_questionnaire(vid)
