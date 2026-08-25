"""PostgreSQL storage for revocable opaque browser sessions."""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import NewType, Protocol, cast
from uuid import UUID, uuid4

from starlette.requests import Request

from flo.kernel.identity.port import IdentityId

SessionId = NewType("SessionId", UUID)
Clock = Callable[[], datetime]


class RotationReason(StrEnum):
    """Privilege changes that must replace, rather than reuse, a session."""

    LOGIN = "login"
    MFA_COMPLETION = "mfa_completion"
    PASSWORD_CHANGE = "password_change"


@dataclass(frozen=True, slots=True)
class RequestDevice:
    """Privacy-minimized request metadata stored with a session."""

    ip_prefix: str | None
    user_agent: str


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """Authenticated server-side state; it intentionally contains no raw token."""

    id: SessionId
    identity_id: IdentityId
    created_at: datetime
    last_seen_at: datetime
    last_auth_at: datetime
    mfa_verified_at: datetime | None
    idle_expires_at: datetime
    absolute_expires_at: datetime
    ip_prefix: str | None
    user_agent: str


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A newly persisted session plus its one-time plaintext cookie value."""

    session: SessionRecord
    _cookie_value: str = field(repr=False)

    def cookie_value(self) -> str:
        """Reveal the token only at the response-cookie boundary."""

        return self._cookie_value


class SessionQueryResult(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...


class SessionConnection(Protocol):
    """Small synchronous PostgreSQL surface used by the session store."""

    def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> SessionQueryResult: ...

    def transaction(self) -> AbstractContextManager[object]: ...


type SessionStoreFactory = Callable[[], AbstractContextManager["SessionStore"]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def hash_session_token(token: str) -> str:
    """Return the only representation of a session token allowed in storage."""

    return sha256(token.encode("ascii"), usedforsecurity=True).hexdigest()


def _ip_prefix(address: str | None) -> str | None:
    if not address:
        return None
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    prefix = 24 if parsed.version == 4 else 48
    return str(ipaddress.ip_network(f"{parsed}/{prefix}", strict=False))


def coarse_user_agent(user_agent: str | None) -> str:
    """Reduce a user-agent to browser and platform families without versions."""

    value = user_agent or ""
    if "Edg/" in value:
        browser = "Edge"
    elif "Firefox/" in value:
        browser = "Firefox"
    elif "Chrome/" in value or "CriOS/" in value:
        browser = "Chrome"
    elif "Safari/" in value:
        browser = "Safari"
    else:
        browser = "Other browser"

    if "iPhone" in value or "iPad" in value:
        platform = "iOS"
    elif "Android" in value:
        platform = "Android"
    elif "Windows" in value:
        platform = "Windows"
    elif "Macintosh" in value or "Mac OS X" in value:
        platform = "macOS"
    elif "Linux" in value:
        platform = "Linux"
    else:
        platform = "unknown platform"
    return f"{browser} on {platform}"


def request_device(request: Request) -> RequestDevice:
    """Derive the deliberately coarse device description for one request."""

    address = request.client.host if request.client is not None else None
    return RequestDevice(
        ip_prefix=_ip_prefix(address),
        user_agent=coarse_user_agent(request.headers.get("user-agent")),
    )


def _as_datetime(value: object) -> datetime:
    result = cast(datetime, value)
    return result if result.tzinfo is not None else result.replace(tzinfo=UTC)


def _record(row: Sequence[object]) -> SessionRecord:
    return SessionRecord(
        id=SessionId(cast(UUID, row[0])),
        identity_id=IdentityId(cast(UUID, row[1])),
        created_at=_as_datetime(row[2]),
        last_seen_at=_as_datetime(row[3]),
        last_auth_at=_as_datetime(row[4]),
        mfa_verified_at=None if row[5] is None else _as_datetime(row[5]),
        idle_expires_at=_as_datetime(row[6]),
        absolute_expires_at=_as_datetime(row[7]),
        ip_prefix=None if row[8] is None else str(row[8]),
        user_agent=cast(str, row[9]),
    )


_RETURNING_COLUMNS = (
    "id, identity_id, created_at, last_seen_at, last_auth_at, mfa_verified_at, "
    "idle_expires_at, absolute_expires_at, ip_prefix, user_agent"
)


class SessionStore:
    """Issue, resolve, rotate, list, and immediately revoke sessions."""

    def __init__(
        self,
        connection: SessionConnection,
        *,
        idle_timeout: timedelta,
        absolute_timeout: timedelta,
        clock: Clock = _utc_now,
    ) -> None:
        if idle_timeout <= timedelta(0):
            raise ValueError("idle timeout must be positive")
        if absolute_timeout < idle_timeout:
            raise ValueError("absolute timeout must be at least the idle timeout")
        self._connection = connection
        self._idle_timeout = idle_timeout
        self._absolute_timeout = absolute_timeout
        self._clock = clock

    def issue(
        self,
        identity_id: IdentityId,
        device: RequestDevice,
        *,
        mfa_verified: bool = False,
    ) -> IssuedSession:
        """Persist one 256-bit opaque token while retaining only its SHA-256 hash."""

        token = secrets.token_urlsafe(32)
        now = self._clock()
        session_id = SessionId(uuid4())
        idle_expires_at = now + self._idle_timeout
        absolute_expires_at = now + self._absolute_timeout
        with self._connection.transaction():
            row = self._connection.execute(
                f"""
                INSERT INTO auth_session
                    (id, identity_id, token_hash, created_at, last_seen_at,
                     last_auth_at, mfa_verified_at, idle_timeout_seconds,
                     idle_expires_at, absolute_expires_at, ip_prefix, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_RETURNING_COLUMNS}
                """,
                (
                    session_id,
                    identity_id,
                    hash_session_token(token),
                    now,
                    now,
                    now,
                    now if mfa_verified else None,
                    int(self._idle_timeout.total_seconds()),
                    idle_expires_at,
                    absolute_expires_at,
                    device.ip_prefix,
                    device.user_agent,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("session insert did not return the inserted row")
        return IssuedSession(_record(row), token)

    def authenticate(self, token: str, device: RequestDevice) -> SessionRecord | None:
        """Resolve and refresh an active session atomically, auditing revoked reuse."""

        token_hash = hash_session_token(token)
        now = self._clock()
        with self._connection.transaction():
            row = self._connection.execute(
                f"""
                UPDATE auth_session
                   SET last_seen_at = %s,
                       idle_expires_at = LEAST(
                           %s + idle_timeout_seconds * INTERVAL '1 second',
                           absolute_expires_at
                       )
                 WHERE token_hash = %s
                   AND revoked_at IS NULL
                   AND idle_expires_at > %s
                   AND absolute_expires_at > %s
                RETURNING {_RETURNING_COLUMNS}
                """,
                (now, now, token_hash, now, now),
            ).fetchone()
            if row is not None:
                return _record(row)

            rejected = self._connection.execute(
                "SELECT id, identity_id, revoked_at FROM auth_session WHERE token_hash = %s",
                (token_hash,),
            ).fetchone()
            if rejected is not None and rejected[2] is not None:
                self._connection.execute(
                    """
                    INSERT INTO session_security_event
                        (session_id, identity_id, event_type, occurred_at,
                         ip_prefix, user_agent)
                    VALUES (%s, %s, 'revoked_token_reuse', %s, %s, %s)
                    """,
                    (
                        rejected[0],
                        rejected[1],
                        now,
                        device.ip_prefix,
                        device.user_agent,
                    ),
                )
        return None

    def rotate(
        self,
        current: SessionRecord,
        identity_id: IdentityId,
        device: RequestDevice,
        reason: RotationReason,
        *,
        mfa_verified: bool = False,
    ) -> IssuedSession:
        """Revoke the old row and issue a distinct row for a privilege change."""

        del reason  # The enum closes call sites; the token or reason is never persisted.
        now = self._clock()
        token = secrets.token_urlsafe(32)
        replacement_id = SessionId(uuid4())
        idle_expires_at = now + self._idle_timeout
        absolute_expires_at = now + self._absolute_timeout
        with self._connection.transaction():
            revoked = self._connection.execute(
                "UPDATE auth_session SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL",
                (now, current.id),
            )
            if revoked.rowcount != 1:
                raise LookupError("current session is no longer active")
            row = self._connection.execute(
                f"""
                INSERT INTO auth_session
                    (id, identity_id, token_hash, created_at, last_seen_at,
                     last_auth_at, mfa_verified_at, idle_timeout_seconds,
                     idle_expires_at, absolute_expires_at, ip_prefix, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_RETURNING_COLUMNS}
                """,
                (
                    replacement_id,
                    identity_id,
                    hash_session_token(token),
                    now,
                    now,
                    now,
                    now if mfa_verified else None,
                    int(self._idle_timeout.total_seconds()),
                    idle_expires_at,
                    absolute_expires_at,
                    device.ip_prefix,
                    device.user_agent,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("session rotation did not return the replacement row")
        return IssuedSession(_record(row), token)

    def list_active(
        self,
        identity_id: IdentityId,
        current_id: SessionId,
    ) -> list[tuple[SessionRecord, bool]]:
        """List only sessions that are active according to the server clock."""

        now = self._clock()
        rows = self._connection.execute(
            f"""
            SELECT {_RETURNING_COLUMNS}
              FROM auth_session
             WHERE identity_id = %s
               AND revoked_at IS NULL
               AND idle_expires_at > %s
               AND absolute_expires_at > %s
             ORDER BY created_at DESC, id
            """,
            (identity_id, now, now),
        ).fetchall()
        return [(record := _record(row), record.id == current_id) for row in rows]

    def revoke(self, session_id: SessionId, identity_id: IdentityId) -> bool:
        """Revoke one owned session without revealing another identity's row."""

        now = self._clock()
        with self._connection.transaction():
            result = self._connection.execute(
                """
                UPDATE auth_session
                   SET revoked_at = %s
                 WHERE id = %s AND identity_id = %s AND revoked_at IS NULL
                """,
                (now, session_id, identity_id),
            )
        return result.rowcount == 1

    def revoke_others(self, identity_id: IdentityId, current_id: SessionId) -> int:
        """Revoke every session for an identity except the current row."""

        now = self._clock()
        with self._connection.transaction():
            result = self._connection.execute(
                """
                UPDATE auth_session
                   SET revoked_at = %s
                 WHERE identity_id = %s AND id <> %s AND revoked_at IS NULL
                """,
                (now, identity_id, current_id),
            )
        return result.rowcount

    def revoke_all(self, identity_id: IdentityId) -> int:
        """Revoke every active session after an identity credential reset."""

        now = self._clock()
        with self._connection.transaction():
            result = self._connection.execute(
                """
                UPDATE auth_session
                   SET revoked_at = %s
                 WHERE identity_id = %s AND revoked_at IS NULL
                """,
                (now, identity_id),
            )
        return result.rowcount
