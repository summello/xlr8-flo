"""RFQ draft contracts. Quantities are decimal strings; people are emails (D-M2-16)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


class RequirementRead(BaseModel):
    id: UUID
    position: int
    text: str
    waived: bool
    waived_by_email: str | None
    waived_reason: str | None


class RfqAttachmentRead(BaseModel):
    id: UUID
    display_name: str
    media_type: str
    size_bytes: int
    scan_status: str
    vendor_visible: bool


class DeliveryRead(BaseModel):
    contact_id: UUID
    version: int
    state: Literal["queued", "sent", "failed", "bounced"]  # follows the outbox row; dead = bounced


class InviteeRead(BaseModel):
    vendor_id: UUID
    contact_ids: list[UUID]
    vendor_snapshot: dict[str, object]
    override_reason: str | None
    invited_by_email: str
    invited_at: datetime
    eligible: bool | None  # live, for the RFQ's unit; None if the vendor can no longer be read
    reasons: list[str]
    deliveries: list[DeliveryRead] = []
    latest_sent_version: int | None = None
    not_told_of: list[int] = []  # versions with no email sent to any contact of this vendor


class RfqLineRead(BaseModel):
    id: UUID
    specification: str | None = None
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
    edit_revision: int
    created_at: datetime
    updated_at: datetime
    lines: list[RfqLineRead]
    specifications: str | None = None
    delivery_location: str | None = None
    delivery_terms: str | None = None
    terms: str | None = None
    deadline_at: datetime
    deadline_tz: str = "UTC"
    requirements: list[RequirementRead] = []
    attachments: list[RfqAttachmentRead] = []
    invitees: list[InviteeRead] = []
    published_at: datetime | None = None
    can_edit: bool = False
    can_approve: bool = False


type RfqTab = Literal["drafts", "open", "evaluating", "awarded", "all"]


class RfqListRow(BaseModel):
    id: UUID
    number: str
    title: str
    status: RfqStatus
    unit_id: UUID
    owner_email: str
    deadline_at: datetime
    deadline_tz: str
    invitee_count: int
    requisition_numbers: list[str]


class RfqCounts(BaseModel):
    drafts: int
    open: int
    evaluating: int
    awarded: int
    all: int


class RfqPage(BaseModel):
    rows: list[RfqListRow]
    next_cursor: str | None
    counts: RfqCounts


class RfqEvent(BaseModel):
    action: str
    at: datetime
    actor_email: str | None
    reason: str | None


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


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("Give the deadline with a UTC offset.")
    return value


def _iana(value: str | None) -> str | None:
    if value is not None:
        try:
            ZoneInfo(value)
        except (ValueError, OSError, ZoneInfoNotFoundError):
            raise ValueError("Use an IANA time zone such as Europe/London.") from None
    return value


class RfqContentPatch(BaseModel):
    """Only the fields sent change; null clears a text field. The deadline is required, not null."""

    model_config = ConfigDict(extra="forbid")
    specifications: str | None = Field(default=None, max_length=20000)
    delivery_location: str | None = Field(default=None, max_length=500)
    delivery_terms: str | None = Field(default=None, max_length=500)
    terms: str | None = Field(default=None, max_length=20000)
    deadline_at: datetime | None = None
    deadline_tz: str | None = None
    line_specifications: dict[UUID, str | None] | None = Field(default=None, max_length=500)

    @field_validator("deadline_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @field_validator("deadline_tz")
    @classmethod
    def iana(cls, value: str | None) -> str | None:
        return _iana(value)

    @field_validator("line_specifications")
    @classmethod
    def short(cls, value: dict[UUID, str | None] | None) -> dict[UUID, str | None] | None:
        if value and any(v is not None and len(v) > 5000 for v in value.values()):
            raise ValueError("Enter at most 5,000 characters per line.")
        return value


class RequirementsPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(max_length=100)


class RequirementsFromTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: UUID


class RequirementWaive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


class VisibilityPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor_visible: bool


class InviteeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor_id: UUID
    contact_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=20)
    override_reason: str | None = Field(default=None, min_length=1, max_length=500)


class InviteePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_ids: list[UUID] = Field(min_length=1, max_length=20)


class RfqAmend(RfqContentPatch):
    """The draft's edits, applied as a new version; `lines` may change quantities only."""

    reason: str = Field(min_length=1, max_length=500)
    lines: list[RfqLineIn] | None = Field(default=None, min_length=1, max_length=500)


class ExtendDeadline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deadline_at: datetime
    deadline_tz: str | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("deadline_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return cast(datetime, _aware(value))

    @field_validator("deadline_tz")
    @classmethod
    def iana(cls, value: str | None) -> str | None:
        return _iana(value)


class RfqVersionRead(BaseModel):
    version: int
    reason: str | None
    diff: dict[str, object]
    snapshot: dict[str, object]
    published_by_email: str
    published_at: datetime
