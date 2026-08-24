"""Environment-backed application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Base for configuration loaded exclusively from the process environment."""

    model_config = SettingsConfigDict(env_prefix="FLO_", extra="ignore")
