"""Compatibility composition surface for the kernel Storage port."""

from __future__ import annotations

import secrets
import sys

from flo.kernel.adapters.minio import MinioStorage, create_minio_storage
from flo.kernel.adapters.r2 import R2Storage, create_r2_storage
from flo.kernel.config import Settings
from flo.kernel.ports.storage import Storage, opaque_storage_key

__all__ = [
    "MinioStorage",
    "R2Storage",
    "Storage",
    "create_storage",
    "opaque_storage_key",
    "verify_storage_round_trip",
]


def create_storage(settings: Settings) -> Storage:
    """Build the configured provider while keeping callers on the port."""

    if settings.storage_provider == "minio":
        return create_minio_storage(settings)
    return create_r2_storage(settings)


def verify_storage_round_trip(storage: Storage) -> None:
    """Write, read, and remove one opaque smoke object through the port."""

    key = opaque_storage_key()
    payload = secrets.token_bytes(32)
    stored = False
    try:
        storage.put(key, payload, content_type="application/octet-stream")
        stored = True
        if storage.get(key) != payload:
            raise RuntimeError("storage round-trip payload did not match")
    finally:
        if stored:
            storage.delete(key)


def main() -> int:
    """Run the production storage smoke check without disclosing SDK failures."""

    try:
        verify_storage_round_trip(create_storage(Settings()))
    except Exception:
        print("storage round-trip failed; inspect provider audit logs", file=sys.stderr)
        return 1
    print("storage round-trip passed and the smoke object was deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
