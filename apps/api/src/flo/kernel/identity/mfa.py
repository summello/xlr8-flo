"""Encrypted RFC 6238 factors, single-use recovery codes, and MFA access gates."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID, uuid4

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity.hashing import run_argon2
from flo.kernel.identity.port import IdentityId
from flo.kernel.session.store import RequestDevice, SessionId

TOTP_DIGITS = 6
TOTP_INTERVAL_SECONDS = 30
TOTP_DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10
RECOVERY_WARNING_AT = 3
_CIPHER_VERSION = "v1"
_AES_GCM_NONCE_BYTES = 12

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MfaAccessRequirement(StrEnum):
    """The only incomplete MFA states an authenticated session may enter."""

    NONE = "none"
    ENROLL = "enroll"
    VERIFY = "verify"


@dataclass(frozen=True, slots=True)
class MfaEnrollment:
    """One-time enrollment material returned only to its authenticated identity."""

    secret: str
    otpauth_uri: str
    recovery_codes: tuple[str, ...]

    def __repr__(self) -> str:
        return "MfaEnrollment(secret=<redacted>, recovery_codes=<redacted>)"


@dataclass(frozen=True, slots=True)
class MfaVerification:
    """A successful factor result with an optional low-code warning count."""

    used_recovery_code: bool
    recovery_codes_remaining: int | None = None


class SecretCipher:
    """Authenticated encryption boundary for decryptable standing TOTP secrets."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("MFA encryption key must contain exactly 32 bytes")
        self._cipher = AESGCM(key)

    @classmethod
    def from_urlsafe_base64(cls, encoded: str) -> SecretCipher:
        """Decode one unpadded URL-safe 256-bit key without retaining its text."""

        try:
            key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (ValueError, TypeError) as exc:
            raise ValueError("MFA encryption key is not valid URL-safe base64") from exc
        return cls(key)

    def encrypt(self, identity_id: IdentityId, secret: str) -> str:
        """Encrypt with an identity-bound AAD value and a fresh 96-bit nonce."""

        nonce = secrets.token_bytes(_AES_GCM_NONCE_BYTES)
        ciphertext = self._cipher.encrypt(
            nonce, secret.encode("ascii"), cast(UUID, identity_id).bytes
        )
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii").rstrip("=")
        return f"{_CIPHER_VERSION}.{payload}"

    def decrypt(self, identity_id: IdentityId, ciphertext: str) -> str:
        """Decrypt an identity-bound secret, failing without exposing its payload."""

        version, separator, encoded = ciphertext.partition(".")
        if separator != "." or version != _CIPHER_VERSION:
            raise RuntimeError("stored MFA credential uses an unsupported cipher version")
        try:
            payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            plaintext = self._cipher.decrypt(
                payload[:_AES_GCM_NONCE_BYTES],
                payload[_AES_GCM_NONCE_BYTES:],
                cast(UUID, identity_id).bytes,
            )
            return plaintext.decode("ascii")
        except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError("stored MFA credential could not be decrypted") from exc


class MfaQueryResult(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Sequence[object] | Mapping[str, object] | None: ...


class MfaConnection(Protocol):
    """Small synchronous PostgreSQL surface used by the MFA credential service."""

    def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> MfaQueryResult: ...

    def transaction(self) -> AbstractContextManager[object]: ...


def _value(row: Sequence[object] | Mapping[str, object], index: int, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]


def _datetime(value: object) -> datetime:
    timestamp = cast(datetime, value)
    return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)


def _totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(
        secret,
        digits=TOTP_DIGITS,
        interval=TOTP_INTERVAL_SECONDS,
        digest=hashlib.sha1,
    )


def _matched_time_step(secret: str, code: str, now: datetime) -> int | None:
    if len(code) != TOTP_DIGITS or not code.isascii() or not code.isdigit():
        return None
    current = int(now.timestamp()) // TOTP_INTERVAL_SECONDS
    generator = _totp(secret)
    for offset in range(-TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS + 1):
        candidate = current + offset
        if candidate >= 0 and hmac.compare_digest(
            generator.at(candidate * TOTP_INTERVAL_SECONDS), code
        ):
            return candidate
    return None


def _recovery_code() -> tuple[UUID, str]:
    selector = uuid4()
    return selector, f"{selector.hex}-{secrets.token_urlsafe(12)}"


def _recovery_selector(code: str) -> UUID | None:
    selector, separator, secret = code.partition("-")
    if separator != "-" or not secret or len(selector) != 32:
        return None
    try:
        return UUID(hex=selector)
    except ValueError:
        return None


class MfaService:
    """Manage a local TOTP factor without exposing its standing secret to other layers."""

    def __init__(
        self,
        connection: MfaConnection,
        cipher: SecretCipher,
        hasher: PasswordHasher,
        *,
        argon2_max_concurrency: int,
        max_failed_attempts: int,
        failure_window: timedelta,
        lock_duration: timedelta,
        clock: Clock = _utc_now,
    ) -> None:
        if max_failed_attempts < 1:
            raise ValueError("MFA failure threshold must be positive")
        if failure_window <= timedelta(0) or lock_duration <= timedelta(0):
            raise ValueError("MFA failure and lock durations must be positive")
        self._connection = connection
        self._cipher = cipher
        self._hasher = hasher
        self._argon2_max_concurrency = argon2_max_concurrency
        self._max_failed_attempts = max_failed_attempts
        self._failure_window = failure_window
        self._lock_duration = lock_duration
        self._clock = clock

    def access_requirement(self, identity_id: IdentityId) -> MfaAccessRequirement:
        """Resolve the global credential gate without reading tenant-owned role rows."""

        row = self._connection.execute(
            """
            SELECT identity.privileged_role_grants,
                   mfa_factor.secret_ciphertext,
                   mfa_factor.pending_secret_ciphertext
              FROM identity
              LEFT JOIN mfa_factor ON mfa_factor.identity_id = identity.id
             WHERE identity.id = %s
            """,
            (identity_id,),
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.UNAUTHORIZED)
        privileged = cast(int, _value(row, 0, "privileged_role_grants")) > 0
        active = _value(row, 1, "secret_ciphertext") is not None
        if active:
            return MfaAccessRequirement.VERIFY
        if privileged:
            return MfaAccessRequirement.ENROLL
        return MfaAccessRequirement.NONE

    def is_privileged(self, identity_id: IdentityId) -> bool:
        """Return whether RBAC currently requires the identity to retain MFA."""

        row = self._connection.execute(
            "SELECT privileged_role_grants FROM identity WHERE id = %s",
            (identity_id,),
        ).fetchone()
        return row is not None and cast(int, _value(row, 0, "privileged_role_grants")) > 0

    async def enroll(self, identity_id: IdentityId) -> MfaEnrollment:
        """Create inactive enrollment material while preserving any active factor."""

        identity = self._connection.execute(
            "SELECT email FROM identity WHERE id = %s", (identity_id,)
        ).fetchone()
        if identity is None:
            raise ProblemError(ErrorCode.UNAUTHORIZED)
        email = cast(str, _value(identity, 0, "email"))
        secret = pyotp.random_base32()
        enrollment_id = uuid4()
        recovery_material = [_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
        recovery_hashes = await asyncio.gather(
            *(
                run_argon2(
                    self._argon2_max_concurrency,
                    self._hasher.hash,
                    code,
                )
                for _, code in recovery_material
            )
        )
        now = self._clock()
        encrypted = self._cipher.encrypt(identity_id, secret)
        with self._connection.transaction():
            self._connection.execute(
                """
                INSERT INTO mfa_factor (
                    identity_id, pending_secret_ciphertext, pending_enrollment_id, updated_at
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (identity_id) DO UPDATE
                    SET pending_secret_ciphertext = EXCLUDED.pending_secret_ciphertext,
                        pending_enrollment_id = EXCLUDED.pending_enrollment_id,
                        updated_at = EXCLUDED.updated_at
                """,
                (identity_id, encrypted, enrollment_id, now),
            )
            self._connection.execute(
                """
                DELETE FROM mfa_recovery_code
                 WHERE identity_id = %s
                   AND recovery_set_id <> COALESCE(
                       (SELECT active_recovery_set_id FROM mfa_factor WHERE identity_id = %s),
                       %s
                   )
                """,
                (identity_id, identity_id, enrollment_id),
            )
            for (selector, _), code_hash in zip(recovery_material, recovery_hashes, strict=True):
                self._connection.execute(
                    """
                    INSERT INTO mfa_recovery_code (
                        selector, identity_id, recovery_set_id, code_hash, created_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (selector, identity_id, enrollment_id, code_hash, now),
                )

        # ponytail: add a phishing-resistant provider factor here in Phase 2; keep
        # access_requirement provider-neutral so recent-auth callers do not change.
        return MfaEnrollment(
            secret=secret,
            otpauth_uri=_totp(secret).provisioning_uri(
                name=email,
                issuer_name="XLR8 FLO",
            ),
            recovery_codes=tuple(code for _, code in recovery_material),
        )

    async def confirm(
        self,
        identity_id: IdentityId,
        code: str,
        *,
        session_id: SessionId,
        device: RequestDevice,
    ) -> None:
        """Activate only a pending secret proven by a fresh real TOTP value."""

        now = self._clock()
        failure: ProblemError | None = None
        with self._connection.transaction():
            row = self._locked_factor(identity_id)
            if row is None or _value(row, 4, "pending_secret_ciphertext") is None:
                failure = ProblemError(ErrorCode.BAD_REQUEST)
            else:
                failure = self._locked_problem(row, identity_id, session_id, device, now)
                if failure is None:
                    secret = self._cipher.decrypt(
                        identity_id,
                        cast(str, _value(row, 4, "pending_secret_ciphertext")),
                    )
                    time_step = _matched_time_step(secret, code, now)
                    if time_step is None or not self._consume_time_step(
                        identity_id, time_step, now
                    ):
                        failure = self._record_failure(row, identity_id, session_id, device, now)
                    else:
                        enrollment_id = cast(UUID, _value(row, 5, "pending_enrollment_id"))
                        self._connection.execute(
                            """
                            UPDATE mfa_factor
                               SET secret_ciphertext = pending_secret_ciphertext,
                                   active_recovery_set_id = pending_enrollment_id,
                                   activated_at = %s,
                                   pending_secret_ciphertext = NULL,
                                   pending_enrollment_id = NULL,
                                   failed_attempts = 0,
                                   failure_window_started_at = NULL,
                                   locked_until = NULL,
                                   updated_at = %s
                             WHERE identity_id = %s
                            """,
                            (now, now, identity_id),
                        )
                        self._connection.execute(
                            """
                            DELETE FROM mfa_recovery_code
                             WHERE identity_id = %s AND recovery_set_id <> %s
                            """,
                            (identity_id, enrollment_id),
                        )
        if failure is not None:
            raise failure

    async def verify(
        self,
        identity_id: IdentityId,
        credential: str,
        *,
        session_id: SessionId,
        device: RequestDevice,
    ) -> MfaVerification:
        """Consume one fresh TOTP time step or one Argon2id recovery code."""

        now = self._clock()
        failure: ProblemError | None = None
        verification: MfaVerification | None = None
        with self._connection.transaction():
            row = self._locked_factor(identity_id)
            if row is None or _value(row, 1, "secret_ciphertext") is None:
                failure = ProblemError(ErrorCode.UNAUTHORIZED)
            else:
                failure = self._locked_problem(row, identity_id, session_id, device, now)
                if failure is None:
                    time_step = _matched_time_step(
                        self._cipher.decrypt(
                            identity_id, cast(str, _value(row, 1, "secret_ciphertext"))
                        ),
                        credential,
                        now,
                    )
                    if time_step is not None and self._consume_time_step(
                        identity_id, time_step, now
                    ):
                        verification = MfaVerification(False)
                    else:
                        verification = await self._consume_recovery(
                            identity_id,
                            cast(UUID, _value(row, 2, "active_recovery_set_id")),
                            credential,
                            now,
                            session_id,
                            device,
                        )
                    if verification is None:
                        failure = self._record_failure(row, identity_id, session_id, device, now)
                    else:
                        self._reset_failures(identity_id, now)
        if failure is not None:
            raise failure
        if verification is None:
            raise RuntimeError("successful MFA verification produced no result")
        return verification

    async def disable(
        self,
        identity_id: IdentityId,
        credential: str,
        *,
        session_id: SessionId,
        device: RequestDevice,
    ) -> MfaVerification:
        """Remove a non-mandatory factor only after consuming its current proof."""

        if self.is_privileged(identity_id):
            raise ProblemError(
                ErrorCode.FORBIDDEN,
                detail="MFA is required while this identity holds a privileged role.",
            )
        verification = await self.verify(
            identity_id,
            credential,
            session_id=session_id,
            device=device,
        )
        with self._connection.transaction():
            removed = self._connection.execute(
                "DELETE FROM mfa_factor WHERE identity_id = %s",
                (identity_id,),
            )
        if removed.rowcount != 1:
            raise ProblemError(ErrorCode.CONFLICT)
        return verification

    def _locked_factor(
        self, identity_id: IdentityId
    ) -> Sequence[object] | Mapping[str, object] | None:
        return self._connection.execute(
            """
            SELECT identity_id, secret_ciphertext, active_recovery_set_id, activated_at,
                   pending_secret_ciphertext, pending_enrollment_id, failed_attempts,
                   failure_window_started_at, locked_until
              FROM mfa_factor
             WHERE identity_id = %s
             FOR UPDATE
            """,
            (identity_id,),
        ).fetchone()

    def _locked_problem(
        self,
        row: Sequence[object] | Mapping[str, object],
        identity_id: IdentityId,
        session_id: SessionId,
        device: RequestDevice,
        now: datetime,
    ) -> ProblemError | None:
        locked_value = _value(row, 8, "locked_until")
        if locked_value is None or _datetime(locked_value) <= now:
            return None
        self._security_event(identity_id, session_id, "factor_failure", device, now)
        remaining = max(1, int((_datetime(locked_value) - now).total_seconds()))
        return ProblemError(
            ErrorCode.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(remaining)},
        )

    def _record_failure(
        self,
        row: Sequence[object] | Mapping[str, object],
        identity_id: IdentityId,
        session_id: SessionId,
        device: RequestDevice,
        now: datetime,
    ) -> ProblemError:
        started_value = _value(row, 7, "failure_window_started_at")
        started = None if started_value is None else _datetime(started_value)
        previous = cast(int, _value(row, 6, "failed_attempts"))
        if started is None or now - started >= self._failure_window:
            started = now
            attempts = 1
        else:
            attempts = previous + 1
        locked_until = now + self._lock_duration if attempts >= self._max_failed_attempts else None
        self._connection.execute(
            """
            UPDATE mfa_factor
               SET failed_attempts = %s,
                   failure_window_started_at = %s,
                   locked_until = %s,
                   updated_at = %s
             WHERE identity_id = %s
            """,
            (attempts, started, locked_until, now, identity_id),
        )
        self._security_event(identity_id, session_id, "factor_failure", device, now)
        if locked_until is not None:
            self._security_event(identity_id, session_id, "factor_locked", device, now)
            return ProblemError(
                ErrorCode.TOO_MANY_REQUESTS,
                headers={"Retry-After": str(int(self._lock_duration.total_seconds()))},
            )
        return ProblemError(ErrorCode.UNAUTHORIZED)

    def _consume_time_step(self, identity_id: IdentityId, time_step: int, now: datetime) -> bool:
        result = self._connection.execute(
            """
            INSERT INTO mfa_totp_consumption (identity_id, time_step, consumed_at)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (identity_id, time_step, now),
        )
        return result.rowcount == 1

    async def _consume_recovery(
        self,
        identity_id: IdentityId,
        recovery_set_id: UUID,
        credential: str,
        now: datetime,
        session_id: SessionId,
        device: RequestDevice,
    ) -> MfaVerification | None:
        selector = _recovery_selector(credential)
        if selector is None:
            return None
        row = self._connection.execute(
            """
            SELECT code_hash
              FROM mfa_recovery_code
             WHERE selector = %s
               AND identity_id = %s
               AND recovery_set_id = %s
               AND used_at IS NULL
             FOR UPDATE
            """,
            (selector, identity_id, recovery_set_id),
        ).fetchone()
        if row is None:
            return None
        try:
            await run_argon2(
                self._argon2_max_concurrency,
                self._hasher.verify,
                cast(str, _value(row, 0, "code_hash")),
                credential,
            )
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return None
        used = self._connection.execute(
            """
            UPDATE mfa_recovery_code SET used_at = %s
             WHERE selector = %s AND used_at IS NULL
            """,
            (now, selector),
        )
        if used.rowcount != 1:
            return None
        remaining_row = self._connection.execute(
            """
            SELECT count(*)
              FROM mfa_recovery_code
             WHERE identity_id = %s AND recovery_set_id = %s AND used_at IS NULL
            """,
            (identity_id, recovery_set_id),
        ).fetchone()
        if remaining_row is None:
            raise RuntimeError("recovery-code count query returned no row")
        remaining = cast(int, _value(remaining_row, 0, "count"))
        if remaining <= RECOVERY_WARNING_AT:
            self._security_event(identity_id, session_id, "recovery_codes_low", device, now)
        return MfaVerification(True, remaining)

    def _reset_failures(self, identity_id: IdentityId, now: datetime) -> None:
        self._connection.execute(
            """
            UPDATE mfa_factor
               SET failed_attempts = 0,
                   failure_window_started_at = NULL,
                   locked_until = NULL,
                   updated_at = %s
             WHERE identity_id = %s
            """,
            (now, identity_id),
        )

    def _security_event(
        self,
        identity_id: IdentityId,
        session_id: SessionId,
        event_type: str,
        device: RequestDevice,
        now: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO mfa_security_event (
                identity_id, session_id, event_type, occurred_at, ip_prefix, user_agent
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                identity_id,
                session_id,
                event_type,
                now,
                device.ip_prefix,
                device.user_agent,
            ),
        )
