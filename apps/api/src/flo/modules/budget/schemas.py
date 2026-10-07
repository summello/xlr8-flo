"""Internal balance read contract."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flo.modules.budget.models import LedgerEntry


@dataclass(frozen=True, slots=True)
class BalanceRead:
    project_id: UUID
    org_id: UUID
    bu_id: UUID
    currency: str
    allocated: Decimal
    reserved: Decimal
    committed: Decimal
    actual: Decimal
    available: Decimal
    version: int
    updated_at: datetime


class AdjustmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(max_digits=18, decimal_places=4, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    effective_date: date
    reason: str = Field(min_length=10)
    evidence_ref: str | None = Field(default=None, min_length=1)

    @field_validator("amount", mode="before", json_schema_input_type=str)
    @classmethod
    def decimal_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("Supply the amount as a decimal string.")
        return value

    @field_validator("amount")
    @classmethod
    def nonzero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("Supply a non-zero amount.")
        return value

    @field_validator("reason", "evidence_ref")
    @classmethod
    def meaningful_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Supply non-blank text.")
        return value


class AllocationCreate(AdjustmentCreate):
    @field_validator("amount")
    @classmethod
    def positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("Supply a positive allocation.")
        return value


@dataclass(frozen=True, slots=True)
class LedgerEntryRead(LedgerEntry):
    """Wire view of the immutable ledger row."""


class AllocationResult(BaseModel):
    entries: list[LedgerEntryRead]
    balance: BalanceRead


class BalanceRange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    start: date | None = Field(alias="from")
    end: date | None = Field(alias="to")


class BalanceQueryRead(BaseModel):
    project_id: UUID
    currency: str
    as_of: date
    range: BalanceRange
    allocated: str
    reserved: str
    committed: str
    actual: str
    available: str
    consumption_pct: str | None
    variance: str
    reconciles: bool | None


class ReconciliationRead(BaseModel):
    balance: BalanceQueryRead
    entries_total_by_bucket: dict[str, str]
    difference_by_bucket: dict[str, str]


class LedgerPage(BaseModel):
    entries: list[LedgerEntryRead]
    next_cursor: str | None


class TransferCreate(AllocationCreate):
    from_project_id: UUID
    to_project_id: UUID


class TransferRead(BaseModel):
    transfer_group_id: UUID
    entries: list[LedgerEntryRead]
