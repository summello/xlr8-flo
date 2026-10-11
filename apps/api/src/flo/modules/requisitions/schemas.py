"""Draft header contracts; currency and estimate are derived."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
