"""Organization HTTP contracts."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


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


class OrgAddressCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["bill_to", "ship_to"]
    line1: str = Field(min_length=1)
    line2: str | None = None
    city: str = Field(min_length=1)
    region: str | None = None
    postal_code: str | None = None
    country: str = Field(min_length=2, max_length=2)
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def valid_range(self) -> "OrgAddressCreate":
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        return self


class OrgAddressClose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effective_to: date = Field(description="Close an open address; closed history cannot change.")


class OrgAddressRead(OrgAddressCreate):
    id: UUID
    unit_id: UUID
    created_by: UUID
    created_at: datetime


class MasterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, description="Immutable; trimmed and stored uppercase.")
    name: str = Field(min_length=1)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    effective_from: date = Field(default_factory=date.today)
    effective_to: date | None = None
    active: bool = True

    @field_validator("code", "name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Must not be blank")
        return value.strip()


class MasterPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1)
    attributes: dict[str, JsonValue] | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def nonnull(self) -> "MasterPatch":
        for key in self.model_fields_set - {"effective_to"}:
            if getattr(self, key) is None:
                raise ValueError(f"{key} must not be null")
        if self.name is not None:
            if not self.name.strip():
                raise ValueError("name must not be blank")
            self.name = self.name.strip()
        return self


class MasterRef(MasterCreate):
    id: UUID
    kind: str
    created_at: datetime
    updated_at: datetime


class MasterPage(BaseModel):
    rows: list[MasterRef]
    next_cursor: UUID | None


class BaseCurrencyPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class FxRateRead(BaseModel):
    base: str
    quote: str
    rate: str
    source: str
    effective_date: date


class FxLastRun(BaseModel):
    status: Literal["ok", "failed"]
    finished_at: datetime
    rows_inserted: int
    error_class: str | None


class FxStatus(BaseModel):
    last_run: FxLastRun | None
    newest_rate_date: date | None


class FxIngestReport(BaseModel):
    status: Literal["ok"]
    rows_inserted: int
    newest_effective_date: date | None


class CurrencyRead(BaseModel):
    code: str
    exponent: int
