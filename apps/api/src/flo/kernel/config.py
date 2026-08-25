"""Environment-backed application configuration and runtime resource guards."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CGROUP_V2_MEMORY_LIMIT = Path("/sys/fs/cgroup/memory.max")
ARGON2_PROCESS_BASELINE_MIB = 256
_BYTES_PER_MIB = 1024 * 1024


class Settings(BaseSettings):
    """Base for configuration loaded exclusively from the process environment."""

    model_config = SettingsConfigDict(
        env_prefix="FLO_",
        extra="ignore",
        populate_by_name=True,
    )

    port: int = Field(default=8080, ge=1, le=65535, validation_alias="PORT")
    database_url: SecretStr | None = Field(default=None, validation_alias="DATABASE_URL")
    storage_endpoint_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("S3_ENDPOINT_URL", "STORAGE_ENDPOINT_URL"),
    )
    storage_access_key_id: SecretStr | None = Field(
        default=None,
        validation_alias="S3_ACCESS_KEY_ID",
    )
    storage_secret_access_key: SecretStr | None = Field(
        default=None,
        validation_alias="S3_SECRET_ACCESS_KEY",
    )
    origin_shared_secret: SecretStr | None = Field(
        default=None,
        min_length=32,
        validation_alias="ORIGIN_SHARED_SECRET",
    )
    storage_bucket: Literal["flo-attachments"] = Field(
        default="flo-attachments",
        validation_alias="S3_BUCKET",
    )
    storage_region: str = Field(
        default="auto",
        min_length=1,
        validation_alias="S3_REGION",
    )
    gcp_project: str | None = Field(
        default=None,
        min_length=6,
        validation_alias="GCP_PROJECT",
    )
    cloud_run_service: Literal["flo-api"] = Field(
        default="flo-api",
        validation_alias="CLOUD_RUN_SERVICE",
    )
    artifact_registry_location: Literal["us-central1"] = Field(
        default="us-central1",
        validation_alias="ARTIFACT_REGISTRY_LOCATION",
    )
    artifact_registry_repository: Literal["flo"] = Field(
        default="flo",
        validation_alias="ARTIFACT_REGISTRY_REPOSITORY",
    )
    quota_timeout_seconds: float = Field(default=5.0, gt=0, le=15)
    readiness_timeout_seconds: float = Field(default=1.0, gt=0, le=5)
    readiness_cache_ttl_seconds: float = Field(default=2.0, gt=0, le=5)
    graceful_shutdown_timeout_seconds: int = Field(default=30, ge=1, le=300)
    identity_argon2_time_cost: int = Field(default=3, ge=1, le=10)
    identity_argon2_memory_cost_kib: int = Field(
        default=64 * 1024,
        ge=8 * 1024,
        le=256 * 1024,
    )
    identity_argon2_parallelism: int = Field(default=4, ge=1, le=16)
    identity_argon2_max_concurrency: int = Field(default=4, ge=1, le=32)
    session_idle_timeout_seconds: int = Field(default=8 * 60 * 60, ge=60)
    session_absolute_timeout_seconds: int = Field(default=12 * 60 * 60, ge=60)

    @model_validator(mode="after")
    def require_ordered_session_timeouts(self) -> Settings:
        """Keep the sliding expiry at or before the fixed absolute deadline."""

        if self.session_absolute_timeout_seconds < self.session_idle_timeout_seconds:
            raise ValueError("session absolute timeout must be at least the idle timeout")
        return self

    @field_validator("database_url")
    @classmethod
    def require_neon_transaction_pooler(cls, value: SecretStr | None) -> SecretStr | None:
        """Reject Neon's session endpoint while allowing local PostgreSQL URLs."""

        if value is None:
            return None
        hostname = urlsplit(value.get_secret_value()).hostname
        if hostname is not None and hostname.endswith(".neon.tech"):
            endpoint_name = hostname.partition(".")[0]
            if not endpoint_name.endswith("-pooler"):
                raise ValueError("DATABASE_URL must use the Neon pooled endpoint")
        return value

    @property
    def required_argon2_instance_memory_mib(self) -> int:
        """Return hashing memory plus the process baseline, rounded up to MiB."""

        hashing_kib = (
            self.identity_argon2_memory_cost_kib * self.identity_argon2_max_concurrency
        )
        hashing_mib = (hashing_kib + 1023) // 1024
        return hashing_mib + ARGON2_PROCESS_BASELINE_MIB


def enforce_argon2_memory_limit(
    settings: Settings,
    cgroup_limit_path: Path = CGROUP_V2_MEMORY_LIMIT,
) -> None:
    """Refuse startup when a readable cgroup limit cannot contain configured hashing."""

    try:
        raw_limit = cgroup_limit_path.read_text(encoding="ascii").strip()
    except OSError:
        return
    if raw_limit == "max":
        return

    try:
        limit_bytes = int(raw_limit)
    except ValueError as exc:
        raise RuntimeError(
            f"invalid cgroup memory limit in {cgroup_limit_path}: expected bytes or 'max'"
        ) from exc

    required_mib = settings.required_argon2_instance_memory_mib
    if limit_bytes < required_mib * _BYTES_PER_MIB:
        raise RuntimeError(
            "cgroup memory limit is below the Argon2 safety requirement: "
            f"need at least {required_mib} MiB"
        )
