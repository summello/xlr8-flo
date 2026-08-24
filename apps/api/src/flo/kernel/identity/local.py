"""Argon2id-backed local implementation of the identity-provider port."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID, uuid4

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from psycopg.errors import UniqueViolation

from flo.kernel.config import Settings
from flo.kernel.identity.policy import PasswordPolicy, normalize_password
from flo.kernel.identity.port import (
    AuthResult,
    IdentityAlreadyExistsError,
    IdentityId,
    IdentityNotFoundError,
    IdentityProvider,
    PasswordPolicyError,
    PolicyResult,
)

_DUMMY_PASSWORD = "dummy credential used only to equalize authentication work"


def _normalize_email(email: str) -> str:
    # RFC 5321 makes the domain case-insensitive and leaves the local part to the
    # provider. Every provider this product will meet treats both case-insensitively,
    # and allowing two accounts for one human is the worse failure.
    return email.casefold()


@dataclass(frozen=True, slots=True)
class _StoredIdentity:
    identity_id: IdentityId
    password_hash: str


class _IdentityStore(Protocol):
    def find_by_email(self, email: str) -> _StoredIdentity | None: ...

    def insert(self, identity_id: IdentityId, email: str, password_hash: str) -> None: ...

    def update_password(self, identity_id: IdentityId, password_hash: str) -> bool: ...

    def rehash_password(
        self,
        identity_id: IdentityId,
        previous_hash: str,
        password_hash: str,
    ) -> None: ...


class _QueryResult(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Sequence[object] | None: ...


class IdentityConnection(Protocol):
    """Small synchronous Postgres surface required by the local adapter."""

    def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> _QueryResult: ...

    def transaction(self) -> AbstractContextManager[object]: ...


class _PostgresIdentityStore:
    def __init__(self, connection: IdentityConnection) -> None:
        self._connection = connection

    def find_by_email(self, email: str) -> _StoredIdentity | None:
        row = self._connection.execute(
            "SELECT id, password_hash FROM identity WHERE email = %s",
            (email,),
        ).fetchone()
        if row is None:
            return None
        return _StoredIdentity(IdentityId(cast(UUID, row[0])), cast(str, row[1]))

    def insert(self, identity_id: IdentityId, email: str, password_hash: str) -> None:
        try:
            with self._connection.transaction():
                self._connection.execute(
                    "INSERT INTO identity (id, email, password_hash) VALUES (%s, %s, %s)",
                    (identity_id, email, password_hash),
                )
        except UniqueViolation as exc:
            raise IdentityAlreadyExistsError from exc

    def update_password(self, identity_id: IdentityId, password_hash: str) -> bool:
        with self._connection.transaction():
            result = self._connection.execute(
                "UPDATE identity SET password_hash = %s, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s",
                (password_hash, identity_id),
            )
        return result.rowcount == 1

    def rehash_password(
        self,
        identity_id: IdentityId,
        previous_hash: str,
        password_hash: str,
    ) -> None:
        with self._connection.transaction():
            self._connection.execute(
                "UPDATE identity SET password_hash = %s, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s AND password_hash = %s",
                (password_hash, identity_id, previous_hash),
            )


class LocalIdentityProvider:
    """Authenticate local identities while exposing only the provider protocol."""

    def __init__(
        self,
        store: _IdentityStore,
        settings: Settings,
        *,
        policy: PasswordPolicy | None = None,
    ) -> None:
        self._store = store
        self._policy = policy or PasswordPolicy()
        self._hasher = PasswordHasher(
            time_cost=settings.identity_argon2_time_cost,
            memory_cost=settings.identity_argon2_memory_cost_kib,
            parallelism=settings.identity_argon2_parallelism,
            type=Type.ID,
        )
        self._dummy_hash = self._hasher.hash(_DUMMY_PASSWORD)

    def authenticate(self, email: str, password: str) -> AuthResult:
        """Verify one credential hash without exposing the failure reason."""

        stored = self._store.find_by_email(_normalize_email(email))
        if stored is None:
            self._verify_dummy(password)
            return AuthResult.invalid_credentials()

        normalized = normalize_password(password)
        try:
            self._hasher.verify(stored.password_hash, normalized)
        except VerifyMismatchError:
            return AuthResult.invalid_credentials()
        except (InvalidHashError, VerificationError):
            return AuthResult.invalid_credentials()

        # ponytail: after Argon2 parameters change, timing distinguishes known from
        # unknown emails only for identities with a pre-change hash, and only until
        # their next successful sign-in; operations must keep that rehash window short.
        if self._hasher.check_needs_rehash(stored.password_hash):
            replacement = self._hasher.hash(normalized)
            self._store.rehash_password(
                stored.identity_id,
                stored.password_hash,
                replacement,
            )
        return AuthResult.success(stored.identity_id)

    def create_identity(self, email: str, password: str) -> IdentityId:
        """Validate and store a newly salted Argon2id credential."""

        self._require_policy(password)
        identity_id = IdentityId(uuid4())
        password_hash = self._hasher.hash(normalize_password(password))
        self._store.insert(identity_id, _normalize_email(email), password_hash)
        return identity_id

    def change_password(self, identity_id: IdentityId, new: str) -> None:
        """Validate and replace an identity's local credential."""

        self._require_policy(new)
        password_hash = self._hasher.hash(normalize_password(new))
        if not self._store.update_password(identity_id, password_hash):
            raise IdentityNotFoundError

    def verify_password_policy(self, password: str) -> PolicyResult:
        """Evaluate password-only policy without retaining the password."""

        return self._policy.verify(password)

    def _require_policy(self, password: str) -> None:
        result = self.verify_password_policy(password)
        if not result.accepted:
            raise PasswordPolicyError(result)

    def _verify_dummy(self, password: str) -> None:
        try:
            self._hasher.verify(self._dummy_hash, normalize_password(password))
        except VerifyMismatchError:
            pass


def build_local_identity_provider(
    connection: IdentityConnection,
    settings: Settings,
) -> IdentityProvider:
    """Build the Phase-1 adapter while returning only the provider port type."""

    return LocalIdentityProvider(_PostgresIdentityStore(connection), settings)
