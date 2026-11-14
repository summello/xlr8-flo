"""Local S3-compatible (SeaweedFS) adapter for the same private S3 storage contract."""

from __future__ import annotations

from flo.kernel.adapters.r2 import R2Storage, _configured_s3_client
from flo.kernel.config import Settings


class LocalS3Storage(R2Storage):
    """Use SeaweedFS locally without exposing an application-specific surface."""


def create_local_s3_storage(settings: Settings) -> LocalS3Storage:
    """Build the local S3-compatible adapter from external configuration."""

    return LocalS3Storage(_configured_s3_client(settings), settings.storage_bucket)
