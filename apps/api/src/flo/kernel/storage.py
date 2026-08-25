"""Private S3-compatible object storage behind the kernel Storage port."""

from __future__ import annotations

import secrets
import sys
from collections.abc import Iterable, Mapping
from typing import Protocol, cast
from uuid import uuid4

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from flo.kernel.config import Settings


class Storage(Protocol):
    """The only object-storage surface available to application modules."""

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Store a private object under an application-generated key."""

    def get(self, key: str) -> bytes:
        """Read an object's bytes."""

    def presign_get(self, key: str, ttl_seconds: int) -> str:
        """Create a short-lived private download URL."""

    def delete(self, key: str) -> None:
        """Delete an object."""

    def usage_bytes(self) -> int:
        """Return the total bytes currently stored in the private bucket."""


class _StreamingBody(Protocol):
    def read(self) -> bytes: ...

    def close(self) -> None: ...


class _S3Paginator(Protocol):
    def paginate(self, **kwargs: str) -> Iterable[Mapping[str, object]]: ...


class _S3Client(Protocol):
    def put_object(self, **kwargs: object) -> object: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> object: ...

    def generate_presigned_url(
        self,
        client_method: str,
        *,
        Params: Mapping[str, str],
        ExpiresIn: int,
    ) -> str: ...

    def get_paginator(self, operation_name: str) -> _S3Paginator: ...


class R2Storage(Storage):
    """Cloudflare R2 adapter using its S3-compatible API."""

    def __init__(self, client: _S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def get(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = cast(_StreamingBody, response["Body"])
        try:
            return body.read()
        finally:
            body.close()

    def presign_get(self, key: str, ttl_seconds: int) -> str:
        if ttl_seconds < 1:
            raise ValueError("presigned URL TTL must be positive")
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def usage_bytes(self) -> int:
        total = 0
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket):
            contents = page.get("Contents", ())
            if not isinstance(contents, list):
                raise RuntimeError("storage listing returned an invalid Contents value")
            for item in contents:
                if not isinstance(item, Mapping):
                    raise RuntimeError("storage listing returned an invalid object")
                size = item.get("Size")
                if not isinstance(size, int) or size < 0:
                    raise RuntimeError("storage listing returned an invalid object size")
                total += size
        return total


def create_storage(settings: Settings) -> Storage:
    """Build the configured R2 adapter without exposing credentials in failures."""

    if settings.storage_endpoint_url is None:
        raise RuntimeError("S3_ENDPOINT_URL is not configured")
    if settings.storage_access_key_id is None:
        raise RuntimeError("S3_ACCESS_KEY_ID is not configured")
    if settings.storage_secret_access_key is None:
        raise RuntimeError("S3_SECRET_ACCESS_KEY is not configured")

    client = boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url.get_secret_value(),
        aws_access_key_id=settings.storage_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.storage_secret_access_key.get_secret_value(),
        region_name=settings.storage_region,
        config=Config(
            signature_version="s3v4",
            connect_timeout=settings.quota_timeout_seconds,
            read_timeout=settings.quota_timeout_seconds,
            retries={"total_max_attempts": 3, "mode": "standard"},
        ),
    )
    return R2Storage(cast(_S3Client, client), settings.storage_bucket)


def verify_storage_round_trip(storage: Storage) -> None:
    """Write, read, and remove one opaque smoke object through the port."""

    key = f"operator-smoke/{uuid4()}"
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
