"""Draft header contracts; currency and estimate are derived."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.modules.approvals.schemas import RoutePreview

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
    reservation: ReservationRead | None = None
    approval: ApprovalRead | None = None


class RequisitionPage(BaseModel):
    rows: list[RequisitionRead]
    total: int
    next_cursor: str | None


class LineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_type: Literal["item", "service"] | None = None
    description: str | None = Field(default=None, min_length=1, max_length=500)
    quantity: str | None = None
    uom_code: str | None = None
    unit_price: str | None = None
    category_code: str | None = None
    catalogue_item_id: UUID | None = None

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

    @model_validator(mode="after")
    def catalogue_or_typed(self) -> LineCreate:
        typed = ("line_type", "description", "uom_code", "category_code")
        if self.catalogue_item_id is not None:
            if any(getattr(self, field) is not None for field in typed):
                raise invalid(
                    "catalogue_item_id",
                    "A catalogue line takes its type, description and unit from the item.",
                    "catalogue_exclusive",
                )
        elif any(
            getattr(self, field) is None
            for field in ("line_type", "description", "uom_code", "quantity", "unit_price")
        ):
            raise invalid(
                "catalogue_item_id",
                "Choose a catalogue item or enter the line type, description, quantity, "
                "unit and price.",
                "line_incomplete",
            )
        return self

    @field_validator("quantity", "unit_price")
    @classmethod
    def decimal_string(cls, value: str | None) -> str | None:
        if value is None:
            return value
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
    catalogue_item_id: UUID | None = None
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


class CatalogueItemRead(BaseModel):
    id: UUID
    line_type: Literal["item", "service"]
    description: str
    uom_code: str
    category_code: str | None
    last_unit_price: str
    currency: str
    last_event: Literal["requested", "sourced", "purchased", "supplied", "serviced"]
    last_event_at: datetime
    last_vendor_id: UUID | None
    last_vendor_name: str | None
    bu_id: UUID


class CatalogueRows(BaseModel):
    rows: list[CatalogueItemRead]


class LookupOption(BaseModel):
    value: str
    label: str
    disabled: bool = False
    note: str | None = None


class LookupRows(BaseModel):
    rows: list[LookupOption]


class SubmissionProblem(BaseModel):
    category: Literal["required", "master_data", "project", "lines", "funds", "routing"]
    field: str
    message: str


class FundingCheck(BaseModel):
    available: str
    this_requisition: str
    already_reserved: str
    available_after: str
    currency: str
    advisory: bool


class SubmissionCheck(BaseModel):
    ready: bool
    problems: list[SubmissionProblem]
    funding: FundingCheck | None
    route: RoutePreview | None = None


class SubmissionInvalid(ProblemError):
    def __init__(self, problems: list[SubmissionProblem]) -> None:
        self.problems = problems
        super().__init__(
            ErrorCode.VALIDATION_FAILED,
            detail=f"Fix {len(problems)} things before you submit. Nothing was submitted. "
            "Everything you entered is kept.",
            checks={"problem": "submission_invalid"},
            errors=tuple(ProblemFieldError(field=p.field, message=p.message) for p in problems),
        )


class RequisitionTransitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: RequisitionStatus
    reason: str | None = None
    note: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def submission_note(self) -> RequisitionTransitionCreate:
        if self.note is not None and self.to != "approval_pending":
            raise invalid("note", "Notes are only accepted when submitting.")
        if self.note is not None and not self.note.strip():
            raise invalid("note", "Enter a plain-text note.")
        return self


class RequisitionTransitionAvailable(BaseModel):
    to: RequisitionStatus
    allowed: bool
    blocked_reasons: list[str]


class StatusEventRead(BaseModel):
    at: datetime
    from_status: RequisitionStatus
    to_status: RequisitionStatus
    actor_email: str | None
    system: bool
    reason: str | None
    funding_round: int
    ledger_entry_id: int | None
    decision_id: UUID | None


class ReservationRead(BaseModel):
    entry_id: int
    reserved: str
    released: str
    remaining: str
    currency: str
    funding_round: int
    project_available: str | None


class ApprovalRead(BaseModel):
    instance_id: UUID
    status: str


RequisitionRead.model_rebuild()
