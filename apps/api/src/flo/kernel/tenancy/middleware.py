"""Middleware that derives tenant context exclusively from the authenticated session."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from flo.kernel.tenancy.context import Scope, use_scope


class AuthenticatedSession(Protocol):
    """The tenancy portion of the server-side authenticated-session contract."""

    org_id: UUID


SessionResolver = Callable[[Request], AuthenticatedSession | None]


def authenticated_session_from_request(request: Request) -> AuthenticatedSession | None:
    """Read the authenticated session installed by the session middleware."""

    session = getattr(request.state, "session", None)
    return session if isinstance(getattr(session, "org_id", None), UUID) else None


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Bind authenticated organization data to the current request context."""

    def __init__(
        self,
        app: ASGIApp,
        session_resolver: SessionResolver = authenticated_session_from_request,
    ) -> None:
        super().__init__(app)
        self._session_resolver = session_resolver

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        session = self._session_resolver(request)
        if session is None:
            return await call_next(request)

        with use_scope(Scope(org_id=session.org_id)):
            return await call_next(request)


def install_tenant_context(
    app: Starlette,
    session_resolver: SessionResolver = authenticated_session_from_request,
) -> None:
    """Register tenant context with an authenticated-session resolver."""

    app.add_middleware(TenantContextMiddleware, session_resolver=session_resolver)
