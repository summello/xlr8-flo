"""Purchase order contracts. Quantities and amounts are decimal strings (E13-S01)."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

type PoTab = Literal["all", "draft"]


def _qty(value: object) -> object:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,14}(?:\.[0-9]{1,4})?", value):
        raise ValueError("Enter a quantity as a decimal string with at most 4 decimals.")
    if Decimal(value) <= 0:
        raise ValueError("Enter a quantity above zero.")
    return value


class PoLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rfq_line_id: UUID
    quantity: str

    _quantity = field_validator("quantity", mode="before", json_schema_input_type=str)(
        lambda cls, v: _qty(v)
    )


class PoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    award_id: UUID
    vendor_id: UUID
    lines: list[PoLineIn] = Field(min_length=1, max_length=500)
    override_reason: str | None = Field(default=None, max_length=500)

    @field_validator("override_reason", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return (v.strip() or None) if isinstance(v, str) else v


class PoLinePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_no: int = Field(ge=1)
    quantity: str

    _quantity = field_validator("quantity", mode="before", json_schema_input_type=str)(
        lambda cls, v: _qty(v)
    )


class PoPatch(BaseModel):
    """A field left out is unchanged; `terms` and `needed_by` may be set to null. A given
    `ship_to_address_id` must be an active Ship To (null is refused: 422 `ship_to_invalid`)."""

    model_config = ConfigDict(extra="forbid")
    terms: str | None = Field(default=None, max_length=4000)
    needed_by: date | None = None
    ship_to_address_id: UUID | None = None
    lines: list[PoLinePatch] | None = Field(default=None, max_length=500)


class PoLineRead(BaseModel):
    line_no: int
    rfq_line_id: UUID
    requisition_line_id: UUID
    description: str
    uom_code: str
    quantity: str
    unit_price: str
    project_id: UUID
    department_code: str | None
    ledger_account_code: str | None
    value: str
    value_converted: str


class PoRead(BaseModel):
    id: UUID
    number: str
    unit_id: UUID
    vendor_id: UUID
    vendor_snapshot: dict[str, Any]
    requisition_id: UUID
    award_id: UUID
    currency: str
    converted_currency: str
    rate: dict[str, Any]
    bill_to: dict[str, Any]
    ship_to_address_id: UUID | None
    recipients: list[dict[str, Any]]
    requester_id: UUID
    terms: str | None
    needed_by: date | None
    status: str
    version: int
    created_at: datetime
    lines: list[PoLineRead]
    total: str
    total_converted: str


class PoListRow(BaseModel):
    id: UUID
    number: str
    unit_id: UUID
    vendor_id: UUID
    vendor_name: str
    requisition_id: UUID
    currency: str
    total: str
    status: str
    created_at: datetime


class PoPage(BaseModel):
    rows: list[PoListRow]
    counts: dict[str, int]
    next_cursor: str | None


class AwardedLineRead(BaseModel):
    rfq_line_id: UUID
    line_no: int
    vendor_id: UUID
    requisition_id: UUID
    requisition_line_id: UUID
    description: str
    uom_code: str
    currency: str
    unit_price: str
    awarded_qty: str
    ordered_qty: str
    open_qty: str
    ordered_value: str


class AwardedLinesRead(BaseModel):
    award_id: UUID
    lines: list[AwardedLineRead]
