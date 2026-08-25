"""Single-use password reset issuance and atomic credential replacement."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol, cast
from urllib.parse import urlencode
from uuid import UUID, uuid4

from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity.port import IdentityId, IdentityProvider, PasswordPolicyError
from flo.kernel.outbox import IdentityOutboxStore
from flo.kernel.outbox.store import OutboxConnection
from flo.kernel.session.store import RequestDevice, SessionStore

PASSWORD_RESET_TEMPLATE = "password-reset-v1"
PASSWORD_RESET_ORIGIN = "https://xlr8flo.summello.com"

type Clock = Callable[[], datetime]
type QueryParameters = tuple[object, ...] | Mapping[str, object]


class ResetQueryResult(Protocol):
    """Result surface used by reset persistence."""

    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...


class ResetConnection(Protocol):
    """Synchronous transaction surface shared by identity, sessions, and outbox."""

    def execute(
        self,
        query: str,
        params: QueryParameters = (),
    ) -> ResetQueryResult: ...

    def transaction(self) -> AbstractContextManager[object]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def hash_reset_token(token: str) -> str:
    """Return the only reset-token representation stored outside the expiring outbox."""

    return sha256(token.encode("ascii"), usedforsecurity=True).hexdigest()


def _rate_key(value: str) -> str:
    return sha256(value.encode("utf-8"), usedforsecurity=True).hexdigest()


class PasswordResetService:
    """Coordinate reset state without exposing account existence to the HTTP layer."""

    def __init__(
        self,
        connection: ResetConnection,
        settings: Settings,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._connection = connection
        self._ttl = timedelta(seconds=settings.password_reset_ttl_seconds)
        self._rate_window = timedelta(seconds=settings.password_reset_rate_window_seconds)
        self._email_limit = settings.password_reset_email_limit
        self._ip_limit = settings.password_reset_ip_limit
        self._clock = clock

    def request_reset(self, email: str, device: RequestDevice) -> None:
        """Issue one reset and its outbox row, or perform the same private lookup work."""

        normalized_email = email.casefold()
        token = secrets.token_urlsafe(32)
        token_hash = hash_reset_token(token)
        now = self._clock()
        expires_at = now + self._ttl
        email_key = _rate_key(f"email:{normalized_email}")
        ip_key = _rate_key(f"ip:{device.ip_prefix or 'unknown'}")

        with self._connection.transaction():
            limited = self._record_and_check_rate_limit(email_key, ip_key, now)
            identity_row = self._connection.execute(
                "SELECT id, email FROM identity WHERE email = %s",
                (normalized_email,),
            ).fetchone()
            if limited or identity_row is None:
                return

            identity_id = IdentityId(cast(UUID, identity_row[0]))
            destination = cast(str, identity_row[1])
            self._connection.execute(
                """
                UPDATE password_reset
                   SET used_at = %s
                 WHERE identity_id = %s AND used_at IS NULL
                """,
                (now, identity_id),
            )
            reset_id = uuid4()
            self._connection.execute(
                """
                INSERT INTO password_reset
                    (id, identity_id, token_hash, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (reset_id, identity_id, token_hash, now, expires_at),
            )

            outbox = IdentityOutboxStore(
                cast(OutboxConnection, self._connection),
                identity_id,
            )
            outbox.discard_pending_email(PASSWORD_RESET_TEMPLATE)
            query = urlencode({"token": token})
            outbox.add_email(
                to=destination,
                template=PASSWORD_RESET_TEMPLATE,
                context={
                    "reset_url": f"{PASSWORD_RESET_ORIGIN}/reset?{query}",
                    "expires_minutes": int(self._ttl.total_seconds() / 60),
                },
                idempotency_key=f"password-reset:{reset_id}",
                created_at=now,
                expires_at=expires_at,
            )
            self._security_event(identity_id, "password_reset_requested", now, device)

    async def complete_reset(
        self,
        token: str,
        password: str,
        device: RequestDevice,
        provider: IdentityProvider,
        sessions: SessionStore,
    ) -> None:
        """Consume once, replace the password, revoke sessions, and record evidence."""

        policy = provider.verify_password_policy(password)
        if not policy.accepted:
            raise PasswordPolicyError(policy)

        now = self._clock()
        token_hash = hash_reset_token(token)
        with self._connection.transaction():
            row = self._connection.execute(
                """
                UPDATE password_reset
                   SET used_at = %s
                 WHERE token_hash = %s
                   AND used_at IS NULL
                   AND expires_at > %s
                RETURNING identity_id
                """,
                (now, token_hash, now),
            ).fetchone()
            if row is None:
                raise ProblemError(ErrorCode.INVALID_OR_EXPIRED)

            identity_id = IdentityId(cast(UUID, row[0]))
            await provider.change_password(identity_id, password)
            sessions.revoke_all(identity_id)
            self._security_event(identity_id, "password_reset_completed", now, device)

    def _record_and_check_rate_limit(
        self,
        email_key: str,
        ip_key: str,
        now: datetime,
    ) -> bool:
        keys = (("email", email_key), ("ip", ip_key))
        for dimension, key_hash in sorted(keys):
            self._connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{dimension}:{key_hash}",),
            )
        self._connection.execute(
            "DELETE FROM password_reset_rate_limit WHERE occurred_at < %s",
            (now - self._rate_window,),
        )
        self._connection.execute(
            """
            INSERT INTO password_reset_rate_limit (dimension, key_hash, occurred_at)
            VALUES ('email', %s, %s), ('ip', %s, %s)
            """,
            (email_key, now, ip_key, now),
        )
        rows = self._connection.execute(
            """
            SELECT dimension, count(*)
              FROM password_reset_rate_limit
             WHERE ((dimension = 'email' AND key_hash = %s)
                    OR (dimension = 'ip' AND key_hash = %s))
               AND occurred_at >= %s
             GROUP BY dimension
            """,
            (email_key, ip_key, now - self._rate_window),
        ).fetchall()
        counts = {cast(str, row[0]): cast(int, row[1]) for row in rows}
        return counts.get("email", 0) > self._email_limit or counts.get(
            "ip", 0
        ) > self._ip_limit

    def _security_event(
        self,
        identity_id: IdentityId,
        event_type: str,
        occurred_at: datetime,
        device: RequestDevice,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO session_security_event
                (session_id, identity_id, event_type, occurred_at, ip_prefix, user_agent)
            VALUES (NULL, %s, %s, %s, %s, %s)
            """,
            (
                identity_id,
                event_type,
                occurred_at,
                device.ip_prefix,
                device.user_agent,
            ),
        )
