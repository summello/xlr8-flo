"""Opaque, server-side browser sessions and their request protections."""

from flo.kernel.session.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    install_csrf_protection,
    rotate_csrf_cookie,
)
from flo.kernel.session.middleware import (
    SECURITY_HEADERS,
    SESSION_COOKIE_NAME,
    SessionAuthenticationMiddleware,
    install_browser_security,
    install_session_authentication,
)
from flo.kernel.session.stepup import recent_auth_max_age, requires_recent_auth
from flo.kernel.session.store import (
    IssuedSession,
    RequestDevice,
    RotationReason,
    SessionRecord,
    SessionStore,
    SessionStoreFactory,
    coarse_user_agent,
    hash_session_token,
    request_device,
)

__all__ = [
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "IssuedSession",
    "RequestDevice",
    "RotationReason",
    "SECURITY_HEADERS",
    "SESSION_COOKIE_NAME",
    "SessionAuthenticationMiddleware",
    "SessionRecord",
    "SessionStore",
    "SessionStoreFactory",
    "coarse_user_agent",
    "hash_session_token",
    "install_browser_security",
    "install_csrf_protection",
    "install_session_authentication",
    "request_device",
    "recent_auth_max_age",
    "requires_recent_auth",
    "rotate_csrf_cookie",
]
