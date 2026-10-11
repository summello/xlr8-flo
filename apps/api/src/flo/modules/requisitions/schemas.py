"""Draft header contracts; currency and estimate are derived."""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError

type RequisitionStatus = Literal[
    "draft",
    "approval_pending",
    "sourcing_in_progress",
    "sourced",
    "awarded",
    "open_for_purchase",
    "completed",
    "rejected",
    "withdrawn",
    "cancelled",
    "expired",
]
type RequisitionSort = Literal["number", "title", "estimate", "needed_by", "updated_at"]
type Direction = Literal["asc", "desc"]


def invalid(field: str, message: str, problem: str = "invalid_requisition") -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail=message,
        errors=(ProblemFieldError(field=field, message=message),),
        checks={"problem": problem},
    )


class HeaderFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    justification: str | None = Field(default=None, max_length=4000)
    needed_by: date | None = None
    department_code: str | None = None
    ledger_account_code: str | None = None
    delivery_address_id: UUID | None = None


class RequisitionCreate(HeaderFields):
    title: str = Field(min_length=1, max_length=200)
    project_id: UUID | None = None
    bu_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_derived(cls, value: object) -> object:
        if isinstance(value, dict):
            for field in ("currency", "estimate"):
                if field in value:
                    raise invalid(
                        field, "This field is derived and cannot be entered.", "derived_field"
                    )
            if bool(value.get("project_id")) == bool(value.get("bu_id")):
                raise invalid("project_id", "Choose exactly one project or business unit.")
        return value


class RequisitionPatch(HeaderFields):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    project_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_immutable(cls, value: object) -> object:
        if isinstance(value, dict):
            for field in (
                "standalone",
                "bu_id",
                "currency",
                "estimate",
                "number",
                "status",
                "requester_id",
            ):
                if field in value:
                    raise invalid(field, "This field is immutable.", "immutable_field")
        return value


class RequisitionRead(BaseModel):
    id: UUID
    number: str
    title: str
    status: RequisitionStatus
    standalone: bool
    project_id: UUID | None
    project_number: str | None
    project_name: str | None
    bu_id: UUID
    bu_name: str
    requester_id: UUID
    requester_email: str
    justification: str | None
    needed_by: date | None
    department_code: str | None
    ledger_account_code: str | None
    delivery_address_id: UUID | None
    estimate: str
    currency: str
    version: int
    submitted_at: datetime | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RequisitionPage(BaseModel):
    rows: list[RequisitionRead]
    total: int
    next_cursor: str | None


class LineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_type: Literal["item", "service"]
    description: str = Field(min_length=1, max_length=500)
    quantity: str
    uom_code: str
    unit_price: str
    category_code: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_derived(cls, value: object) -> object:
        if isinstance(value, dict):
            for field in ("amount", "currency", "line_no"):
                if field in value:
                    raise invalid(
                        field, "This field is derived and cannot be entered.", "derived_field"
                    )
        return value

    @field_validator("quantity", "unit_price")
    @classmethod
    def decimal_string(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9]{1,14}(?:\.[0-9]{1,4})?", value) or Decimal(value) <= 0:
            raise ValueError(
                "Enter a positive decimal string with at most 14 integer digits and 4 decimals."
            )
        return value


LinePut = LineCreate


class ReplacementLine(LineCreate):
    line_id: UUID | None = None


class LinesReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[ReplacementLine] = Field(max_length=500)


class LineRead(BaseModel):
    id: UUID
    line_no: int
    line_type: Literal["item", "service"]
    description: str
    quantity: str
    uom_code: str
    unit_price: str
    amount: str
    currency: str
    category_code: str | None
    requested_qty: str
    sourced_qty: str
    awarded_qty: str
    ordered_qty: str
    cancelled_qty: str
    remaining_qty: str


class LinesRead(BaseModel):
    rows: list[LineRead]
    estimate: str
    currency: str
    version: int


class LineWriteResult(BaseModel):
    line: LineRead | None
    estimate: str
    currency: str
    version: int
