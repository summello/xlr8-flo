"""Provider-independent identity boundary and Phase-1 construction factory."""

from flo.kernel.identity.local import IdentityConnection, build_local_identity_provider
from flo.kernel.identity.port import (
    AuthenticationStatus,
    AuthResult,
    IdentityAlreadyExistsError,
    IdentityId,
    IdentityNotFoundError,
    IdentityProvider,
    PasswordPolicyError,
    PolicyResult,
    PolicyViolation,
)

__all__ = [
    "AuthenticationStatus",
    "AuthResult",
    "IdentityAlreadyExistsError",
    "IdentityConnection",
    "IdentityId",
    "IdentityNotFoundError",
    "IdentityProvider",
    "PasswordPolicyError",
    "PolicyResult",
    "PolicyViolation",
    "build_local_identity_provider",
]
