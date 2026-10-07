"""Provider-independent identity boundary and Phase-1 construction factory."""

from flo.kernel.identity.local import IdentityConnection, build_local_identity_provider
from flo.kernel.identity.mfa import (
    MfaAccessRequirement,
    MfaConnection,
    MfaEnrollment,
    MfaService,
    MfaVerification,
    SecretCipher,
)
from flo.kernel.identity.mfa_middleware import (
    MfaServiceFactory,
    install_mfa_access_gate,
)
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
    "MfaAccessRequirement",
    "MfaConnection",
    "MfaEnrollment",
    "MfaService",
    "MfaServiceFactory",
    "MfaVerification",
    "PasswordPolicyError",
    "PolicyResult",
    "PolicyViolation",
    "SecretCipher",
    "build_local_identity_provider",
    "install_mfa_access_gate",
]
