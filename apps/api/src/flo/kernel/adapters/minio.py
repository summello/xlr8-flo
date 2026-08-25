"""Local MinIO adapter for the same private S3 storage contract."""

from __future__ import annotations

from flo.kernel.adapters.r2 import R2Storage, _configured_s3_client
from flo.kernel.config import Settings


class MinioStorage(R2Storage):
    """Use MinIO locally without exposing an application-specific surface."""


def create_minio_storage(settings: Settings) -> MinioStorage:
    """Build the local S3-compatible adapter from external configuration."""

    return MinioStorage(_configured_s3_client(settings), settings.storage_bucket)
