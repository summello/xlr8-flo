"""RFQ draft contracts. Quantities are decimal strings; people are emails (D-M2-16)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError

type RfqStatus = Literal["draft", "published", "closed", "under_evaluation", "awarded", "cancelled"]
type NotSelectable = Literal["another_unit", "different_currency", "amendment_pending", "on_rfq"]


def invalid(field: str, message: str, problem: str = "invalid_rfq") -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail=message,
        errors=(ProblemFieldError(field=field, message=message),),
        checks={"problem": problem},
    )


class RfqLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requisition_line_id: UUID
    requisition_version: int = Field(ge=1)
    quantity: str

    @field_validator("quantity", mode="before", json_schema_input_type=str)
    @classmethod
    def decimal_string(cls, value: object) -> object:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,14}(?:\.[0-9]{1,4})?", value):
            raise ValueError("Enter a quantity as a decimal string with at most 4 decimals.")
        if Decimal(value) <= 0:
            raise ValueError("Enter a quantity above zero.")
        return value


def _distinct(lines: list[RfqLineIn]) -> list[RfqLineIn]:
    ids = [line.requisition_line_id for line in lines]
    if len(set(ids)) != len(ids):
        raise ValueError("Each requisition line may appear once.")
    return lines


class RfqCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    lines: list[RfqLineIn] = Field(min_length=1, max_length=500)

    @field_validator("title")
    @classmethod
    def trimmed(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 200:
            raise ValueError("Enter 1 to 200 characters.")
        return value

    @field_validator("lines")
    @classmethod
    def distinct(cls, value: list[RfqLineIn]) -> list[RfqLineIn]:
        return _distinct(value)


class RfqLinesPatch(BaseModel):
    """The complete line list wanted: new lines added, absent ones removed, others requantified."""

    model_config = ConfigDict(extra="forbid")
    lines: list[RfqLineIn] = Field(max_length=500)

    @field_validator("lines")
    @classmethod
    def distinct(cls, value: list[RfqLineIn]) -> list[RfqLineIn]:
        return _distinct(value)


class RfqLineRead(BaseModel):
    id: UUID
    line_no: int
    requisition_id: UUID
    requisition_number: str
    requisition_line_id: UUID
    requisition_line_no: int
    requisition_version: int
    quantity: str
    description: str
    uom_code: str
    needed_by: date | None
    currency: str
    open_qty: str


class RfqRead(BaseModel):
    id: UUID
    number: str
    title: str
    status: RfqStatus
    unit_id: UUID
    currency: str
    owner_email: str
    version: int
    created_at: datetime
    updated_at: datetime
    lines: list[RfqLineRead]


class SourceableLine(BaseModel):
    line_id: UUID
    requisition_id: UUID
    requisition_number: str
    requisition_version: int
    line_no: int
    unit_id: UUID
    currency: str
    description: str
    uom_code: str
    needed_by: date | None
    requested_qty: str
    sourced_qty: str
    awarded_qty: str
    ordered_qty: str
    cancelled_qty: str
    open_qty: str
    selectable: bool
    reason: NotSelectable | None


class SourceablePage(BaseModel):
    rows: list[SourceableLine]
    next_cursor: str | None
