"""Double-submit CSRF protection for same-origin browser requests."""

from __future__ import annotations

import hmac
import secrets

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from flo.kernel.errors import ErrorCode, ProblemError

CSRF_COOKIE_NAME = "flo_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"DELETE", "PATCH", "POST", "PUT"})


def _csrf_cookie_header(token: str) -> tuple[bytes, bytes]:
    response = Response()
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        secure=True,
        httponly=False,
        samesite="lax",
        path="/",
    )
    return next(header for header in response.raw_headers if header[0] == b"set-cookie")


def rotate_csrf_cookie(response: Response) -> str:
    """Replace the readable CSRF cookie after authentication state changes."""

    token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        secure=True,
        httponly=False,
        samesite="lax",
        path="/",
    )
    return token


class CsrfMiddleware:
    """Require an exact header/cookie match on every unsafe HTTP method."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
        if request.method in UNSAFE_METHODS:
            header_token = Headers(scope=scope).get(CSRF_HEADER_NAME, "")
            if not cookie_token or not hmac.compare_digest(cookie_token, header_token):
                raise ProblemError(
                    ErrorCode.FORBIDDEN,
                    detail="The CSRF token is missing or does not match. No data was changed.",
                )

        seed_cookie = not cookie_token and request.method not in UNSAFE_METHODS
        cookie_header = _csrf_cookie_header(secrets.token_urlsafe(32)) if seed_cookie else None

        async def send_with_cookie(message: Message) -> None:
            if cookie_header is not None and message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append(cookie_header)
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_cookie)


def install_csrf_protection(app: ASGIApp) -> None:
    """Install double-submit protection and safe-request token seeding."""

    if not hasattr(app, "add_middleware"):
        raise TypeError("CSRF protection requires a Starlette-compatible application")
    app.add_middleware(CsrfMiddleware)
