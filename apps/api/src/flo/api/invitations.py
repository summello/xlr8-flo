"""Invitation HTTP adapters using the existing authorization and auth ports."""

import asyncio
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Header, Request, Response

from flo.api.admin_users import _administer_users, get_admin_connection
from flo.api.auth import (
    get_auth_connection,
    get_auth_settings,
    get_identity_provider,
    get_login_throttle,
    get_mfa_service,
    get_session_store,
)
from flo.kernel.authz import public_route
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.errors.handler import problem_exception_handler
from flo.kernel.identity import IdentityProvider
from flo.kernel.identity.mfa import MfaService
from flo.kernel.identity.throttle import LoginThrottle
from flo.kernel.session.client_address import throttle_client_value
from flo.kernel.session.csrf import rotate_csrf_cookie
from flo.kernel.session.middleware import set_session_cookie
from flo.kernel.session.store import SessionIssueDenied, SessionRecord, SessionStore, request_device
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.invitation import InvitationService, unavailable
from flo.modules.identity.models import AuthorizationContext
from flo.modules.identity.schemas import (
    InvitationAccept,
    InvitationAccepted,
    InvitationCreate,
    InvitationExpired,
    InvitationRecord,
    InvitationValid,
)

router = APIRouter(prefix="/api/v1/invitations", tags=["invitations"])


def get_public_invitations(
    connection: Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_auth_connection)],
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> InvitationService:
    return InvitationService(connection, settings)


def get_admin_invitations(
    connection: Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_admin_connection)],
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> InvitationService:
    return InvitationService(connection, settings)


async def checked_token(request: Request, throttle: LoginThrottle) -> Response | None:
    if throttle.check_client(throttle_client_value(request)):
        await throttle.jitter()
        response = await problem_exception_handler(request, unavailable())
        response.headers["Cache-Control"] = "no-store"
        return response
    return None


async def failed_token(request: Request, throttle: LoginThrottle, exc: ProblemError) -> Response:
    if exc.code == ErrorCode.NOT_FOUND:
        throttle.record_client_failure(throttle_client_value(request))
        await throttle.jitter()
    response = await problem_exception_handler(request, exc)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/by-token", response_model=InvitationValid | InvitationExpired)
@public_route
async def by_token(
    request: Request,
    response: Response,
    service: Annotated[InvitationService, Depends(get_public_invitations)],
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
    token: Annotated[str, Header(alias="X-Invitation-Token")] = "",
) -> InvitationValid | InvitationExpired | Response:
    denied = await checked_token(request, throttle)
    if denied is not None:
        return denied
    try:
        result = await asyncio.to_thread(service.by_token, token)
    except ProblemError as exc:
        return await failed_token(request, throttle, exc)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/accept", response_model=InvitationAccepted)
@public_route
async def accept(
    request: Request,
    response: Response,
    service: Annotated[InvitationService, Depends(get_public_invitations)],
    throttle: Annotated[LoginThrottle, Depends(get_login_throttle)],
    provider: Annotated[IdentityProvider, Depends(get_identity_provider)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    mfa: Annotated[MfaService, Depends(get_mfa_service)],
    body: InvitationAccept = InvitationAccept(),
    token: Annotated[str, Header(alias="X-Invitation-Token")] = "",
) -> InvitationAccepted | Response:
    current = getattr(request.state, "session", None)
    session = current if isinstance(current, SessionRecord) else None
    denied = await checked_token(request, throttle)
    if denied is not None:
        return denied
    try:
        identity = await service.accept(token, body, session, provider, request_device(request))
    except ProblemError as exc:
        return await failed_token(request, throttle, exc)
    response.headers["Cache-Control"] = "no-store"
    if identity is None:
        return InvitationAccepted(next="home")
    requirement = mfa.access_requirement(identity)
    try:
        issued = store.issue(
            identity, request_device(request), mfa_verified=requirement.value == "none"
        )
    except SessionIssueDenied as exc:
        raise ProblemError(ErrorCode.UNAUTHORIZED) from exc
    set_session_cookie(response, issued.cookie_value())
    rotate_csrf_cookie(response)
    return InvitationAccepted(next="home" if requirement.value == "none" else "enroll")


@router.post("", status_code=201, response_model=InvitationRecord)
def issue(
    body: InvitationCreate,
    request: Request,
    context: Annotated[AuthorizationContext, Depends(_administer_users)],
    service: Annotated[InvitationService, Depends(get_admin_invitations)],
) -> InvitationRecord:
    return service.issue(current_scope(), context.user_id, body, request_device(request))


@router.get("", response_model=list[InvitationRecord])
def list_invitations(
    context: Annotated[AuthorizationContext, Depends(_administer_users)],
    service: Annotated[InvitationService, Depends(get_admin_invitations)],
) -> list[InvitationRecord]:
    return service.list(current_scope())


@router.post("/{id}/resend", response_model=InvitationRecord)
def resend(
    id: UUID,
    request: Request,
    context: Annotated[AuthorizationContext, Depends(_administer_users)],
    service: Annotated[InvitationService, Depends(get_admin_invitations)],
) -> InvitationRecord:
    return service.resend(current_scope(), context.user_id, id, request_device(request))


@router.post("/{id}/withdraw", status_code=204)
def withdraw(
    id: UUID,
    request: Request,
    context: Annotated[AuthorizationContext, Depends(_administer_users)],
    service: Annotated[InvitationService, Depends(get_admin_invitations)],
) -> Response:
    service.withdraw(current_scope(), context.user_id, id, request_device(request))
    return Response(status_code=204)
