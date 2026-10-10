"""Identity-level organization choice gate, inside the MFA gate."""

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.session.store import SessionRecord

ORGANIZATION_SELECTION_PATHS = frozenset(
    {
        "/api/v1/invitations/accept",
        "/api/v1/invitations/by-token",
        "/api/v1/auth/organizations",
        "/api/v1/auth/organization",
        "/api/v1/auth/logout",
        "/api/v1/auth/mfa/enroll",
        "/api/v1/auth/mfa/confirm",
        "/api/v1/auth/mfa/verify",
    }
)


class OrganizationSelectionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            session = getattr(Request(scope).state, "session", None)
            if (
                isinstance(session, SessionRecord)
                and session.org_selection_required
                and scope.get("path", "") not in ORGANIZATION_SELECTION_PATHS
            ):
                raise ProblemError(
                    ErrorCode.ORGANIZATION_SELECTION_REQUIRED,
                    headers={"WWW-Authenticate": "org-select"},
                )
        await self._app(scope, receive, send)
