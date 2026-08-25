"""Global authenticated-route gate for mandatory and enrolled MFA factors."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity.mfa import MfaAccessRequirement, MfaService
from flo.kernel.session.store import SessionRecord

type MfaServiceFactory = Callable[[], AbstractContextManager[MfaService]]

_ENROLLMENT_PATHS = frozenset(
    {
        "/api/v1/auth/mfa/enroll",
        "/api/v1/auth/mfa/confirm",
        "/api/v1/auth/logout",
    }
)
_VERIFICATION_PATHS = frozenset(
    {
        "/api/v1/auth/mfa/verify",
        "/api/v1/auth/logout",
    }
)


class MfaAccessMiddleware:
    """Block every authenticated route outside the identity's required MFA flow."""

    def __init__(self, app: ASGIApp, service_factory: MfaServiceFactory) -> None:
        self._app = app
        self._service_factory = service_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        session = getattr(request.state, "session", None)
        if not isinstance(session, SessionRecord):
            await self._app(scope, receive, send)
            return

        with self._service_factory() as service:
            requirement = service.access_requirement(session.identity_id)
        path = scope.get("path", "")
        if requirement is MfaAccessRequirement.ENROLL and path not in _ENROLLMENT_PATHS:
            raise ProblemError(
                ErrorCode.MFA_ENROLLMENT_REQUIRED,
                headers={"WWW-Authenticate": "mfa-enroll"},
            )
        if (
            requirement is MfaAccessRequirement.VERIFY
            and session.mfa_verified_at is None
            and path not in _VERIFICATION_PATHS
        ):
            raise ProblemError(
                ErrorCode.MFA_VERIFICATION_REQUIRED,
                headers={"WWW-Authenticate": "mfa"},
            )
        await self._app(scope, receive, send)


def install_mfa_access_gate(app: object, service_factory: MfaServiceFactory) -> None:
    """Install the global MFA route gate after session authentication."""

    add_middleware = getattr(app, "add_middleware", None)
    if add_middleware is None:
        raise TypeError("MFA access enforcement requires a Starlette-compatible application")
    add_middleware(MfaAccessMiddleware, service_factory=service_factory)
