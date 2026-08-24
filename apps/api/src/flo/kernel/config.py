"""Environment-backed application configuration."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    graceful_shutdown_timeout_seconds: int = Field(default=30, ge=1, le=300)
