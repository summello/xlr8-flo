"""Provider-neutral ports for every external effect."""

from flo.kernel.ports.email import (
    EmailSender,
    PermanentEmailError,
    TransientEmailError,
)
from flo.kernel.ports.storage import Storage, opaque_storage_key

__all__ = [
    "EmailSender",
    "PermanentEmailError",
    "Storage",
    "TransientEmailError",
    "opaque_storage_key",
]
