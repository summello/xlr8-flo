"""Thin HTTP adapter for identity authentication and browser-session lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Annotated, cast
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityConnection, IdentityProvider, build_local_identity_provider
from flo.kernel.session.csrf import CSRF_COOKIE_NAME, rotate_csrf_cookie
from flo.kernel.session.middleware import clear_session_cookie, set_session_cookie
from flo.kernel.session.store import (
    RotationReason,
    SessionConnection,
    SessionId,
    SessionRecord,
    SessionStore,
    SessionStoreFactory,
    request_device,
)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    """Credentials accepted by the local identity-provider boundary."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class SessionResponse(BaseModel):
    """Privacy-minimized active-session representation."""

    id: UUID
    created_at: datetime
    last_seen_at: datetime
    ip_prefix: str | None
    user_agent: str
    current: bool


def get_auth_settings() -> Settings:
    """Load current authentication configuration for a request."""

    return Settings()


def _database_url(settings: Settings) -> str:
    configured = settings.database_url
    if configured is None:
        raise ProblemError(ErrorCode.SERVICE_UNAVAILABLE)
    return configured.get_secret_value().replace("postgresql+psycopg://", "postgresql://", 1)


def get_auth_connection(
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """Open one request-scoped database connection without exposing its URL."""

    try:
        connection = psycopg.connect(_database_url(settings), autocommit=True)
    except psycopg.Error as exc:
        raise ProblemError(ErrorCode.SERVICE_UNAVAILABLE) from exc
    try:
        yield connection
    finally:
        connection.close()


async def get_identity_provider(
    connection: Annotated[
        psycopg.Connection[tuple[object, ...]], Depends(get_auth_connection)
    ],
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> IdentityProvider:
    """Construct the configured provider behind its protocol boundary."""

    return await build_local_identity_provider(cast(IdentityConnection, connection), settings)


def get_session_store(
    connection: Annotated[
        psycopg.Connection[tuple[object, ...]], Depends(get_auth_connection)
    ],
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> SessionStore:
    """Bind the request connection and configured timeout policy to the store."""

    return SessionStore(
        cast(SessionConnection, connection),
        idle_timeout=timedelta(seconds=settings.session_idle_timeout_seconds),
        absolute_timeout=timedelta(seconds=settings.session_absolute_timeout_seconds),
    )


@contextmanager
def production_session_store() -> Iterator[SessionStore]:
    """Open the short-lived store used by session-authentication middleware."""

    settings = Settings()
    try:
        connection = psycopg.connect(_database_url(settings), autocommit=True)
    except psycopg.Error as exc:
        raise ProblemError(ErrorCode.SERVICE_UNAVAILABLE) from exc
    try:
        yield SessionStore(
            cast(SessionConnection, connection),
            idle_timeout=timedelta(seconds=settings.session_idle_timeout_seconds),
            absolute_timeout=timedelta(seconds=settings.session_absolute_timeout_seconds),
        )
    finally:
        connection.close()


def production_session_store_factory() -> SessionStoreFactory:
    """Expose a typed factory without opening a connection at application import."""

    return production_session_store


def current_session(request: Request) -> SessionRecord:
    """Require the session installed by database-backed middleware."""

    session = getattr(request.state, "session", None)
    if not isinstance(session, SessionRecord):
        raise ProblemError(ErrorCode.UNAUTHORIZED)
    return session


def _delete_auth_cookies(response: Response) -> None:
    clear_session_cookie(response)
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        secure=True,
        httponly=False,
        samesite="lax",
        path="/",
    )


@router.post("/login", status_code=204)
async def login(
    body: LoginRequest,
    request: Request,
    provider: Annotated[IdentityProvider, Depends(get_identity_provider)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> Response:
    """Authenticate credentials and rotate any already authenticated session."""

    result = await provider.authenticate(body.email, body.password)
    if not result.authenticated or result.identity_id is None:
        raise ProblemError(ErrorCode.UNAUTHORIZED)

    device = request_device(request)
    existing = getattr(request.state, "session", None)
    if isinstance(existing, SessionRecord):
        issued = store.rotate(existing, result.identity_id, device, RotationReason.LOGIN)
    else:
        issued = store.issue(result.identity_id, device)

    response = Response(status_code=204)
    set_session_cookie(response, issued.cookie_value())
    rotate_csrf_cookie(response)
    return response


@router.post("/logout", status_code=204)
def logout(
    session: Annotated[SessionRecord, Depends(current_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> Response:
    """Immediately revoke the current server-side session."""

    store.revoke(session.id, session.identity_id)
    response = Response(status_code=204)
    _delete_auth_cookies(response)
    return response


@router.get("/sessions", response_model=list[SessionResponse])
def sessions(
    session: Annotated[SessionRecord, Depends(current_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> list[SessionResponse]:
    """Return active devices without precise IPs or user-agent versions."""

    return [
        SessionResponse(
            id=record.id,
            created_at=record.created_at,
            last_seen_at=record.last_seen_at,
            ip_prefix=record.ip_prefix,
            user_agent=record.user_agent,
            current=is_current,
        )
        for record, is_current in store.list_active(session.identity_id, session.id)
    ]


@router.delete("/sessions", status_code=204)
def revoke_other_sessions(
    session: Annotated[SessionRecord, Depends(current_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> Response:
    """Leave exactly the current session active."""

    store.revoke_others(session.identity_id, session.id)
    return Response(status_code=204)


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: UUID,
    session: Annotated[SessionRecord, Depends(current_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> Response:
    """Revoke one owned session, concealing foreign or unknown identifiers."""

    if not store.revoke(SessionId(session_id), session.identity_id):
        raise ProblemError(ErrorCode.NOT_FOUND)
    response = Response(status_code=204)
    if session.id == session_id:
        _delete_auth_cookies(response)
    return response
