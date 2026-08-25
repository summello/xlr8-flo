"""Session resolution, secure cookies, and browser response protections."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from flo.kernel.session.store import SessionStoreFactory, request_device

SESSION_COOKIE_NAME = "__Host-flo_session"

SECURITY_HEADERS: dict[bytes, bytes] = {
    b"content-security-policy": (
        b"default-src 'self'; base-uri 'self'; object-src 'none'; "
        b"frame-ancestors 'none'; form-action 'self'"
    ),
    b"cross-origin-opener-policy": b"same-origin",
    b"permissions-policy": b"camera=(), geolocation=(), microphone=()",
    b"referrer-policy": b"strict-origin-when-cross-origin",
    b"strict-transport-security": b"max-age=31536000; includeSubDomains",
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
}


def set_session_cookie(response: Response, token: str) -> None:
    """Set the host-only opaque session cookie with the packet's exact attributes."""

    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Expire the browser cookie while server-side revocation remains authoritative."""

    response.delete_cookie(
        SESSION_COOKIE_NAME,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


class SessionAuthenticationMiddleware:
    """Resolve the opaque cookie from PostgreSQL on every request, without caching."""

    def __init__(self, app: ASGIApp, store_factory: SessionStoreFactory) -> None:
        self._app = app
        self._store_factory = store_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        token = request.cookies.get(SESSION_COOKIE_NAME)
        invalid_cookie = False
        if token:
            with self._store_factory() as store:
                session = store.authenticate(token, request_device(request))
            if session is None:
                invalid_cookie = True
            else:
                state = scope.setdefault("state", {})
                state["session"] = session

        async def send_with_cookie_cleanup(message: Message) -> None:
            if invalid_cookie and message["type"] == "http.response.start":
                response = Response()
                clear_session_cookie(response)
                headers = list(message.get("headers", []))
                headers.extend(
                    header for header in response.raw_headers if header[0] == b"set-cookie"
                )
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_cookie_cleanup)


class BrowserSecurityHeadersMiddleware:
    """Replace all seven required browser-protection headers on every response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = set(SECURITY_HEADERS)
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in names
                ]
                headers.extend(SECURITY_HEADERS.items())
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_security_headers)


def install_session_authentication(app: object, store_factory: SessionStoreFactory) -> None:
    """Install database-backed session resolution."""

    add_middleware = getattr(app, "add_middleware", None)
    if add_middleware is None:
        raise TypeError("session authentication requires a Starlette-compatible application")
    add_middleware(SessionAuthenticationMiddleware, store_factory=store_factory)


def install_browser_security(app: object) -> None:
    """Install response headers outermost, after problem-details middleware."""

    add_middleware = getattr(app, "add_middleware", None)
    if add_middleware is None:
        raise TypeError("browser security requires a Starlette-compatible application")
    add_middleware(BrowserSecurityHeadersMiddleware)
