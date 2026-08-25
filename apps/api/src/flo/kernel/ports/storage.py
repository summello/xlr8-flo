"""Private object-storage port exposed to application modules."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4


def opaque_storage_key() -> str:
    """Return a storage key that cannot disclose a user-supplied filename."""

    return str(uuid4())


def require_opaque_storage_key(key: str) -> None:
    """Fail closed unless ``key`` is a canonical application-generated UUID."""

    try:
        parsed = UUID(key)
    except ValueError:
        raise ValueError("storage key must be an opaque UUID") from None
    if str(parsed) != key:
        raise ValueError("storage key must be a canonical opaque UUID")


class Storage(Protocol):
    """The only object-storage surface available to application modules."""

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Store a private object under an application-generated key."""

    def get(self, key: str) -> bytes:
        """Read an object's bytes through authenticated provider access."""

    def presign_get(self, key: str, ttl_seconds: int) -> str:
        """Create a time-limited private download URL."""

    def delete(self, key: str) -> None:
        """Delete an object."""

    def usage_bytes(self) -> int:
        """Return total private-bucket bytes for the quota monitor."""
