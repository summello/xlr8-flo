"""Organization HTTP contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class OrgUnitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_id: UUID | None = Field(
        default=None,
        description="Parent for settings precedence; authorization does not inherit from it.",
    )
    code: str = Field(
        min_length=1, description="Immutable code, stored uppercase and unique per organization."
    )
    name: str = Field(min_length=1)
    kind: Literal["bu", "ou"]

    @field_validator("code", "name")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Must not be blank")
        return value.strip()


class OrgUnitPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1)
    active: bool | None = None

    @field_validator("name", "active")
    @classmethod
    def nonnull(cls, value: str | bool | None) -> str | bool:
        if value is None or isinstance(value, str) and not value.strip():
            raise ValueError("Must not be null or blank")
        return value.strip() if isinstance(value, str) else value


class OrgUnitRead(BaseModel):
    id: UUID
    parent_id: UUID | None
    code: str
    name: str
    kind: Literal["bu", "ou"]
    active: bool
    created_at: datetime


class OrgUnitPage(BaseModel):
    rows: list[OrgUnitRead]
    next_cursor: UUID | None


class SettingPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: UUID | None = None
    value: JsonValue = Field(
        description="Replaces the whole inherited value; values are never merged."
    )


class SettingSource(BaseModel):
    scope: Literal["unit", "org", "default"]
    unit_id: UUID | None = None
    unit_code: str | None = None


class EffectiveSetting(BaseModel):
    value: JsonValue
    source: SettingSource
