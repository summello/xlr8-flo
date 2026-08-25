"""Cloudflare R2 adapter using its private S3-compatible API."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from flo.kernel.config import Settings
from flo.kernel.ports.storage import Storage, require_opaque_storage_key


class _StreamingBody(Protocol):
    def read(self) -> bytes: ...

    def close(self) -> None: ...


class _S3Paginator(Protocol):
    def paginate(self, **kwargs: str) -> Iterable[Mapping[str, object]]: ...


class S3Client(Protocol):
    """Typed subset shared by the R2 and MinIO S3 clients."""

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
    """Store only opaque objects in a bucket with public access disabled."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        require_opaque_storage_key(key)
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def get(self, key: str) -> bytes:
        require_opaque_storage_key(key)
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = cast(_StreamingBody, response["Body"])
        try:
            return body.read()
        finally:
            body.close()

    def presign_get(self, key: str, ttl_seconds: int) -> str:
        require_opaque_storage_key(key)
        if ttl_seconds < 1:
            raise ValueError("presigned URL TTL must be positive")
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )

    def delete(self, key: str) -> None:
        require_opaque_storage_key(key)
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


def _configured_s3_client(settings: Settings) -> S3Client:
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
    return cast(S3Client, client)


def create_r2_storage(settings: Settings) -> R2Storage:
    """Build the configured R2 adapter without exposing credentials."""

    return R2Storage(_configured_s3_client(settings), settings.storage_bucket)
