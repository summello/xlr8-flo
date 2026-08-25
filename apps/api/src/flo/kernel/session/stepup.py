"""Provider-neutral recent-authentication enforcement for high-risk endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import cast

from starlette.requests import Request

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.session.store import SessionRecord

_RECENT_AUTH_MARKER = "__flo_recent_auth_max_age__"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _request(args: tuple[object, ...], kwargs: dict[str, object]) -> Request:
    for value in (*args, *kwargs.values()):
        if isinstance(value, Request):
            return value
    raise RuntimeError("recent-auth endpoints must declare a Request parameter")


def requires_recent_auth[**P, R](
    *,
    max_age: timedelta,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Reject a high-risk endpoint when the provider's last proof is too old."""

    if max_age <= timedelta(0):
        raise ValueError("recent-auth maximum age must be positive")

    def decorate(
        endpoint: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[R]]:
        @wraps(endpoint)
        async def enforce(*args: P.args, **kwargs: P.kwargs) -> R:
            request = _request(cast(tuple[object, ...], args), cast(dict[str, object], kwargs))
            session = getattr(request.state, "session", None)
            if not isinstance(session, SessionRecord):
                raise ProblemError(ErrorCode.UNAUTHORIZED)
            clock = getattr(request.app.state, "recent_auth_clock", _utc_now)
            if not callable(clock):
                raise RuntimeError("recent-auth clock must be callable")
            now = cast(datetime, clock())
            if now.tzinfo is None or now.utcoffset() is None:
                raise RuntimeError("recent-auth clock must return an aware datetime")
            if now - session.last_auth_at > max_age:
                raise ProblemError(
                    ErrorCode.STEP_UP_REQUIRED,
                    headers={"WWW-Authenticate": "step-up"},
                )
            return await endpoint(*args, **kwargs)

        setattr(enforce, _RECENT_AUTH_MARKER, max_age)
        return enforce

    return decorate


def recent_auth_max_age(endpoint: object) -> timedelta | None:
    """Expose the closed decorator marker to guard tests and route introspection."""

    value = getattr(endpoint, _RECENT_AUTH_MARKER, None)
    return value if isinstance(value, timedelta) else None
