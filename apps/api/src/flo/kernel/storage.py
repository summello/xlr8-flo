"""Compatibility composition surface for the kernel Storage port."""

from __future__ import annotations

import secrets
import sys

from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from flo.kernel.adapters.r2 import R2Storage, _configured_s3_client, create_r2_storage
from flo.kernel.adapters.s3_local import LocalS3Storage, create_local_s3_storage
from flo.kernel.config import Settings
from flo.kernel.ports.storage import Storage, opaque_storage_key

__all__ = [
    "LocalS3Storage",
    "R2Storage",
    "Storage",
    "create_storage",
    "ensure_bucket",
    "opaque_storage_key",
    "verify_storage_round_trip",
]


def create_storage(settings: Settings) -> Storage:
    """Build the configured provider while keeping callers on the port."""

    if settings.storage_provider == "local":
        return create_local_s3_storage(settings)
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


def ensure_bucket(settings: Settings) -> None:
    """Create the configured bucket if it is missing; idempotent, other errors propagate."""

    client = _configured_s3_client(settings)
    try:
        client.head_bucket(Bucket=settings.storage_bucket)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code"))
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        client.create_bucket(Bucket=settings.storage_bucket)


def main() -> int:
    """Run the production storage smoke check without disclosing SDK failures."""

    if sys.argv[1:] == ["ensure-bucket"]:
        ensure_bucket(Settings())
        print("storage bucket ready")
        return 0
    try:
        verify_storage_round_trip(create_storage(Settings()))
    except Exception:
        print("storage round-trip failed; inspect provider audit logs", file=sys.stderr)
        return 1
    print("storage round-trip passed and the smoke object was deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
