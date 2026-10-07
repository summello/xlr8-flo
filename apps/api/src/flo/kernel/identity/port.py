"""Provider-independent authentication contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType, Protocol
from uuid import UUID

IdentityId = NewType("IdentityId", UUID)


class AuthenticationStatus(StrEnum):
    """Provider-neutral authentication outcomes."""

    AUTHENTICATED = "authenticated"
    INVALID_CREDENTIALS = "invalid_credentials"


class PolicyViolation(StrEnum):
    """Stable password-policy rejection reasons that never contain the password."""

    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    COMPROMISED = "compromised"


@dataclass(frozen=True, slots=True)
class AuthResult:
    """An authentication result with one generic failure outcome."""

    status: AuthenticationStatus
    identity_id: IdentityId | None = None

    @property
    def authenticated(self) -> bool:
        """Return whether the provider authenticated an identity."""

        return self.status is AuthenticationStatus.AUTHENTICATED

    @classmethod
    def success(cls, identity_id: IdentityId) -> AuthResult:
        """Create a successful result."""

        return cls(AuthenticationStatus.AUTHENTICATED, identity_id)

    @classmethod
    def invalid_credentials(cls) -> AuthResult:
        """Create the sole externally observable credential failure."""

        return cls(AuthenticationStatus.INVALID_CREDENTIALS)


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """A password-policy decision containing only non-sensitive reason codes."""

    violations: tuple[PolicyViolation, ...] = ()

    @property
    def accepted(self) -> bool:
        """Return whether the password satisfies the policy."""

        return not self.violations


class PasswordPolicyError(ValueError):
    """Raised when a password is rejected without retaining its value."""

    def __init__(self, result: PolicyResult) -> None:
        super().__init__("password does not meet policy")
        self.result = result


class IdentityNotFoundError(LookupError):
    """Raised when a requested identity identifier does not exist."""


class IdentityAlreadyExistsError(ValueError):
    """Raised when an identity already exists for an email address."""

    def __init__(self) -> None:
        super().__init__("identity already exists")


class IdentityProvider(Protocol):
    """Authentication boundary implemented by local credentials or future SSO."""

    async def authenticate(self, email: str, password: str) -> AuthResult: ...

    async def create_identity(self, email: str, password: str) -> IdentityId: ...

    async def change_password(self, identity_id: IdentityId, new: str) -> None: ...

    async def verify_current_password(
        self,
        identity_id: IdentityId,
        password: str,
    ) -> bool: ...

    def verify_password_policy(self, password: str) -> PolicyResult: ...
