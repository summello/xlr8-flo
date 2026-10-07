"""Thin HTTP adapter for identity authentication and browser-session lifecycle."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Annotated, cast
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from flo.kernel.authz import public_route
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.identity import (
    IdentityConnection,
    IdentityProvider,
    MfaConnection,
    MfaEnrollment,
    MfaService,
    MfaServiceFactory,
    PasswordPolicyError,
    SecretCipher,
    build_local_identity_provider,
)
from flo.kernel.identity.hashing import build_argon2_hasher
from flo.kernel.identity.reset import PasswordResetService, ResetConnection
from flo.kernel.session.csrf import CSRF_COOKIE_NAME, rotate_csrf_cookie
from flo.kernel.session.middleware import clear_session_cookie, set_session_cookie
from flo.kernel.session.stepup import requires_recent_auth
from flo.kernel.session.store import (
    RotationReason,
    SessionConnection,
    SessionId,
    SessionIssueDenied,
    SessionRecord,
    SessionStore,
    SessionStoreFactory,
    request_device,
)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
_RESET_REQUEST_MIN_SECONDS = 0.05


class LoginRequest(BaseModel):
    """Credentials accepted by the local identity-provider boundary."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class PasswordResetRequest(BaseModel):
    """The sole account locator accepted before authentication."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)


class PasswordResetCompletion(BaseModel):
    """One opaque token and the replacement credential."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=256)


class SessionResponse(BaseModel):
    """Privacy-minimized active-session representation."""

    id: UUID
    created_at: datetime
    last_seen_at: datetime
    ip_prefix: str | None
    user_agent: str
    current: bool


class MfaEnrollRequest(BaseModel):
    """Current password required before generating standing MFA credentials."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=256)


class MfaEnrollmentResponse(BaseModel):
    """One-time enrollment material that is never persisted in plaintext."""

    secret: str
    otpauth_uri: str
    recovery_codes: tuple[str, ...]


class MfaCodeRequest(BaseModel):
    """A current TOTP or one complete recovery credential."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=80)


class MfaDeleteRequest(BaseModel):
    """Exactly one current factor proof accepted before disabling MFA."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=6, max_length=80)
    recovery: str | None = Field(default=None, min_length=6, max_length=80)

    @model_validator(mode="after")
    def require_exactly_one_factor(self) -> MfaDeleteRequest:
        if (self.code is None) == (self.recovery is None):
            raise ValueError("provide exactly one of code or recovery")
        return self

    def credential(self) -> str:
        value = self.code if self.code is not None else self.recovery
        if value is None:
            raise RuntimeError("validated MFA delete request has no credential")
        return value


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
    connection: Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_auth_connection)],
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> IdentityProvider:
    """Construct the configured provider behind its protocol boundary."""

    return await build_local_identity_provider(cast(IdentityConnection, connection), settings)


def get_session_store(
    connection: Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_auth_connection)],
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> SessionStore:
    """Bind the request connection and configured timeout policy to the store."""

    return SessionStore(
        cast(SessionConnection, connection),
        idle_timeout=timedelta(seconds=settings.session_idle_timeout_seconds),
        absolute_timeout=timedelta(seconds=settings.session_absolute_timeout_seconds),
    )


def get_password_reset_service(
    connection: Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_auth_connection)],
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> PasswordResetService:
    """Bind password-reset state to the request transaction connection."""

    return PasswordResetService(cast(ResetConnection, connection), settings)


def _mfa_service(
    connection: MfaConnection,
    settings: Settings,
) -> MfaService:
    configured_key = settings.mfa_encryption_key
    if configured_key is None:
        raise ProblemError(ErrorCode.SERVICE_UNAVAILABLE)
    try:
        cipher = SecretCipher.from_urlsafe_base64(configured_key.get_secret_value())
    except ValueError as exc:
        raise ProblemError(ErrorCode.SERVICE_UNAVAILABLE) from exc
    return MfaService(
        connection,
        cipher,
        build_argon2_hasher(settings),
        argon2_max_concurrency=settings.identity_argon2_max_concurrency,
        max_failed_attempts=settings.mfa_max_failed_attempts,
        failure_window=timedelta(seconds=settings.mfa_failure_window_seconds),
        lock_duration=timedelta(seconds=settings.mfa_lock_duration_seconds),
    )


def get_mfa_service(
    connection: Annotated[psycopg.Connection[tuple[object, ...]], Depends(get_auth_connection)],
    settings: Annotated[Settings, Depends(get_auth_settings)],
) -> MfaService:
    """Bind encrypted MFA credential operations to the request connection."""

    return _mfa_service(cast(MfaConnection, connection), settings)


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


@contextmanager
def production_mfa_service() -> Iterator[MfaService]:
    """Open the short-lived service used by global MFA route enforcement."""

    settings = Settings()
    try:
        connection = psycopg.connect(_database_url(settings), autocommit=True)
    except psycopg.Error as exc:
        raise ProblemError(ErrorCode.SERVICE_UNAVAILABLE) from exc
    try:
        yield _mfa_service(cast(MfaConnection, connection), settings)
    finally:
        connection.close()


def production_mfa_service_factory() -> MfaServiceFactory:
    """Expose a typed MFA service factory without opening a connection at import."""

    return production_mfa_service


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


@router.post("/reset-request", status_code=202)
@public_route
async def request_password_reset(
    body: PasswordResetRequest,
    request: Request,
    service: Annotated[PasswordResetService, Depends(get_password_reset_service)],
) -> Response:
    """Accept every syntactically valid account locator without revealing existence."""

    started_at = time.perf_counter()
    service.request_reset(body.email, request_device(request))
    remaining = _RESET_REQUEST_MIN_SECONDS - (time.perf_counter() - started_at)
    if remaining > 0:
        await asyncio.sleep(remaining)
    return Response(status_code=202)


@router.post("/reset", status_code=204)
@public_route
async def reset_password(
    body: PasswordResetCompletion,
    request: Request,
    service: Annotated[PasswordResetService, Depends(get_password_reset_service)],
    provider: Annotated[IdentityProvider, Depends(get_identity_provider)],
    sessions: Annotated[SessionStore, Depends(get_session_store)],
) -> Response:
    """Replace one credential after atomically consuming its reset token."""

    try:
        await service.complete_reset(
            body.token,
            body.password,
            request_device(request),
            provider,
            sessions,
        )
    except PasswordPolicyError as error:
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED,
            errors=(
                ProblemFieldError(
                    field="password",
                    message="The password does not meet the password policy.",
                ),
            ),
        ) from error
    return Response(status_code=204)


@router.post("/login", status_code=204)
@public_route
async def login(
    body: LoginRequest,
    request: Request,
    provider: Annotated[IdentityProvider, Depends(get_identity_provider)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    mfa: Annotated[MfaService, Depends(get_mfa_service)],
) -> Response:
    """Authenticate credentials and rotate any already authenticated session."""

    result = await provider.authenticate(body.email, body.password)
    if not result.authenticated or result.identity_id is None:
        raise ProblemError(ErrorCode.UNAUTHORIZED)

    device = request_device(request)
    requires_mfa = mfa.access_requirement(result.identity_id).value != "none"
    existing = getattr(request.state, "session", None)
    try:
        if isinstance(existing, SessionRecord):
            issued = store.rotate(
                existing,
                result.identity_id,
                device,
                RotationReason.LOGIN,
                mfa_verified=not requires_mfa,
            )
        else:
            issued = store.issue(result.identity_id, device, mfa_verified=not requires_mfa)
    except SessionIssueDenied as exc:
        raise ProblemError(ErrorCode.UNAUTHORIZED) from exc

    response = Response(status_code=204)
    set_session_cookie(response, issued.cookie_value())
    rotate_csrf_cookie(response)
    return response


def _mfa_completion_response(
    request: Request,
    session: SessionRecord,
    store: SessionStore,
    *,
    warning_remaining: int | None = None,
) -> Response:
    try:
        issued = store.rotate(
            session,
            session.identity_id,
            request_device(request),
            RotationReason.MFA_COMPLETION,
            mfa_verified=True,
        )
    except SessionIssueDenied as exc:
        raise ProblemError(ErrorCode.UNAUTHORIZED) from exc
    response = Response(status_code=204)
    set_session_cookie(response, issued.cookie_value())
    rotate_csrf_cookie(response)
    if warning_remaining is not None and warning_remaining <= 3:
        response.headers["Warning"] = f'299 XLR8-FLO "{warning_remaining} recovery codes remain"'
    return response


@router.post("/mfa/enroll", response_model=MfaEnrollmentResponse)
@public_route
async def enroll_mfa(
    body: MfaEnrollRequest,
    session: Annotated[SessionRecord, Depends(current_session)],
    provider: Annotated[IdentityProvider, Depends(get_identity_provider)],
    mfa: Annotated[MfaService, Depends(get_mfa_service)],
) -> MfaEnrollmentResponse:
    """Generate one inactive TOTP secret after current-password re-authentication."""

    if not await provider.verify_current_password(session.identity_id, body.password):
        raise ProblemError(ErrorCode.UNAUTHORIZED)
    enrollment: MfaEnrollment = await mfa.enroll(session.identity_id)
    return MfaEnrollmentResponse(
        secret=enrollment.secret,
        otpauth_uri=enrollment.otpauth_uri,
        recovery_codes=enrollment.recovery_codes,
    )


@router.post("/mfa/confirm", status_code=204)
@public_route
async def confirm_mfa(
    body: MfaCodeRequest,
    request: Request,
    session: Annotated[SessionRecord, Depends(current_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    mfa: Annotated[MfaService, Depends(get_mfa_service)],
) -> Response:
    """Activate a pending factor and rotate the authenticated session identifier."""

    await mfa.confirm(
        session.identity_id,
        body.code,
        session_id=session.id,
        device=request_device(request),
    )
    return _mfa_completion_response(request, session, store)


@router.post("/mfa/verify", status_code=204)
@public_route
async def verify_mfa(
    body: MfaCodeRequest,
    request: Request,
    session: Annotated[SessionRecord, Depends(current_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    mfa: Annotated[MfaService, Depends(get_mfa_service)],
) -> Response:
    """Complete login or step-up, refresh recent auth, and rotate the session."""

    verified = await mfa.verify(
        session.identity_id,
        body.code,
        session_id=session.id,
        device=request_device(request),
    )
    return _mfa_completion_response(
        request,
        session,
        store,
        warning_remaining=verified.recovery_codes_remaining,
    )


@router.delete("/mfa", status_code=204)
@public_route
@requires_recent_auth(max_age=timedelta(minutes=15))
async def disable_mfa(
    body: MfaDeleteRequest,
    request: Request,
    session: Annotated[SessionRecord, Depends(current_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    mfa: Annotated[MfaService, Depends(get_mfa_service)],
) -> Response:
    """Disable a non-mandatory factor after a current proof and rotate the session."""

    await mfa.disable(
        session.identity_id,
        body.credential(),
        session_id=session.id,
        device=request_device(request),
    )
    try:
        issued = store.rotate(
            session,
            session.identity_id,
            request_device(request),
            RotationReason.MFA_COMPLETION,
        )
    except SessionIssueDenied as exc:
        raise ProblemError(ErrorCode.UNAUTHORIZED) from exc
    response = Response(status_code=204)
    set_session_cookie(response, issued.cookie_value())
    rotate_csrf_cookie(response)
    return response


@router.post("/logout", status_code=204)
@public_route
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
@public_route
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
@public_route
def revoke_other_sessions(
    session: Annotated[SessionRecord, Depends(current_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> Response:
    """Leave exactly the current session active."""

    store.revoke_others(session.identity_id, session.id)
    return Response(status_code=204)


@router.delete("/sessions/{session_id}", status_code=204)
@public_route
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
