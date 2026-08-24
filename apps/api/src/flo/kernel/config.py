"""Environment-backed application configuration and runtime resource guards."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

CGROUP_V2_MEMORY_LIMIT = Path("/sys/fs/cgroup/memory.max")
ARGON2_PROCESS_BASELINE_MIB = 256
_BYTES_PER_MIB = 1024 * 1024


class Settings(BaseSettings):
    """Base for configuration loaded exclusively from the process environment."""

    model_config = SettingsConfigDict(env_prefix="FLO_", extra="ignore")

    port: int = Field(default=8080, ge=1, le=65535, validation_alias="PORT")
    database_url: SecretStr | None = Field(default=None, validation_alias="DATABASE_URL")
    storage_endpoint_url: SecretStr | None = Field(
        default=None,
        validation_alias="STORAGE_ENDPOINT_URL",
    )
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
