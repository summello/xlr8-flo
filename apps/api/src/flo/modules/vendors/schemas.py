"""Vendor contracts. People are emails (D-M2-16)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError

type VendorStatus = Literal[
    "draft", "pending_approval", "active", "suspended", "inactive", "rejected"
]
type Availability = Literal["global", "restricted"]


def invalid(field: str, message: str, problem: str = "invalid_vendor") -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail=message,
        errors=(ProblemFieldError(field=field, message=message),),
        checks={"problem": problem},
    )


def conflict(problem: str, detail: str) -> ProblemError:
    return ProblemError(ErrorCode.CONFLICT, detail=detail, checks={"problem": problem})


class VendorNames(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trading_name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("trading_name", "legal_name")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not 1 <= len(value) <= 200:
            raise ValueError("Enter 1 to 200 characters.")
        return value


class VendorCreate(VendorNames):
    trading_name: str = Field(min_length=1, max_length=200)
    legal_name: str = Field(min_length=1, max_length=200)
    availability: Availability
    unit_ids: list[UUID] = Field(default_factory=list, max_length=500)


class VendorPatch(VendorNames):
    availability: Availability | None = None
    unit_ids: list[UUID] | None = Field(default=None, max_length=500)


class DisableCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Give a reason.")
        return value


class SensitiveMasked(BaseModel):
    """Tax and banking data as the list and detail show it: last four characters only."""

    tax_id: str | None = None
    tax_id_country: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    bank_routing: str | None = None
    version: int = 0  # the ETag of the sensitive record; 0 until one is written


class VendorRead(BaseModel):
    id: UUID
    number: str
    trading_name: str
    legal_name: str
    status: VendorStatus
    availability: Availability
    created_by_email: str
    created_at: datetime
    updated_at: datetime
    sensitive: SensitiveMasked = SensitiveMasked()


class VendorUnit(BaseModel):
    unit_id: UUID
    unit_code: str


class VendorDisabled(VendorUnit):
    by_email: str
    at: datetime
    reason: str


class VendorDetail(VendorRead):
    units: list[VendorUnit] = []
    disabled_in: list[VendorDisabled] = []


class VendorPage(BaseModel):
    rows: list[VendorRead]
    next_cursor: str | None = None


REQUIRED_CONTACT_TYPES = ("billing", "service_delivery")
EMAIL = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"  # the invitation rule


class ContactFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=254, pattern=EMAIL)
    phone: str | None = Field(default=None, max_length=40)
    receives_documents: bool | None = None

    @field_validator("name", "email", "phone", mode="before")
    @classmethod
    def _trimmed(cls, value: object) -> object:
        """Trim before the length rules run, so a blank required field is a 422."""
        return value.strip() if isinstance(value, str) else value

    @field_validator("phone")
    @classmethod
    def _blank_phone_is_none(cls, value: str | None) -> str | None:
        return value or None


class ContactCreate(ContactFields):
    type: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL)


class ContactPatch(ContactFields):
    """No `type`: it is immutable, and `extra="forbid"` answers 422 if one is sent."""


class ContactRead(BaseModel):
    id: UUID
    type: str
    name: str
    email: str
    phone: str | None
    receives_documents: bool
    created_at: datetime
    updated_at: datetime


class Recipient(BaseModel):
    name: str
    email: str


type DocStateName = Literal["missing", "rejected", "expired", "expiring", "unverified", "ok"]
type Verification = Literal["unverified", "verified", "rejected"]


class DocumentDates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_date: date | None = None
    expiry_date: date | None = None


class DocumentCreate(DocumentDates):
    type_code: str = Field(min_length=1, max_length=100)
    attachment_id: UUID


class DocumentReplace(DocumentDates):
    attachment_version_id: UUID


class DocumentReject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Give a reason.")
        return value


class DocState(BaseModel):
    """One row per required type plus every supplied document; `missing` has no document."""

    type_code: str
    required: bool
    state: DocStateName
    days_left: int | None = None
    document_id: UUID | None = None
    attachment_id: UUID | None = None
    attachment_version: int | None = None
    latest_version_id: UUID | None = None
    scan_status: str | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    verification: Verification | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None
    created_at: datetime | None = None


type RevealField = Literal["tax_id", "bank"]
type RevealReason = Literal["verify_payment", "vendor_request", "audit_support", "other"]
DIGIT_RUN = re.compile(r"\d{6,}")


class SensitiveReveal(BaseModel):
    """A reason code from a closed list; the note is free text that must hold no figures."""

    model_config = ConfigDict(extra="forbid")
    fields: list[RevealField] = Field(min_length=1, max_length=2)
    reason_code: RevealReason
    note: str | None = Field(default=None, max_length=200)

    @field_validator("note")
    @classmethod
    def _no_figures(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip() or None
        if value and DIGIT_RUN.search(re.sub(r"[\s\-._/]", "", value)):
            raise ValueError("Leave account and tax numbers out of the note.")
        return value


class SensitivePut(BaseModel):
    """The whole record: a field left out is cleared. Values are stored as typed."""

    model_config = ConfigDict(extra="forbid")
    tax_id: str | None = Field(default=None, min_length=1, max_length=64)
    tax_id_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    bank_name: str | None = Field(default=None, min_length=1, max_length=200)
    bank_account: str | None = Field(default=None, min_length=1, max_length=64)
    bank_routing: str | None = Field(default=None, min_length=1, max_length=64)


class SensitiveValues(SensitivePut):
    """Clear values, returned only by the audited reveal."""

    version: int = 0


class SensitiveWritten(BaseModel):
    changed: list[str]
    version: int


# -- onboarding questionnaire (E11-S05) -------------------------------------------------------

type QuestionType = Literal[
    "text", "number", "yes_no", "single_choice", "multi_choice", "date", "file"
]
type QuestionnaireStatus = Literal["draft", "published", "retired"]


class QuestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=40)
    prompt: str = Field(min_length=1, max_length=500)
    type: QuestionType
    required: bool = False
    weight: int = Field(ge=0, le=100)
    options: JsonValue = None


class QuestionPatch(BaseModel):
    """The type is fixed once a question exists: delete it and add another to change it."""

    model_config = ConfigDict(extra="forbid")
    prompt: str | None = Field(default=None, min_length=1, max_length=500)
    required: bool | None = None
    weight: int | None = Field(default=None, ge=0, le=100)
    options: JsonValue = None


class VersionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: int = Field(ge=0, le=100)


class QuestionMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["up", "down"]


class QuestionAdmin(BaseModel):
    """The administrator's view: weights and points included."""

    key: str
    position: int
    prompt: str
    type: QuestionType
    required: bool
    weight: int
    options: JsonValue
    row_version: int


class VersionRead(BaseModel):
    id: UUID
    number: int
    status: QuestionnaireStatus
    threshold: int
    row_version: int
    published_at: datetime | None
    question_count: int
    weight_total: int


class VersionDetail(VersionRead):
    questions: list[QuestionAdmin]


class Choice(BaseModel):
    value: str
    label: str


class QuestionPublic(BaseModel):
    """What a requester sees: never a weight and never points."""

    key: str
    position: int
    prompt: str
    type: QuestionType
    required: bool
    choices: list[Choice] | None = None


class AnswersPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, JsonValue]


class OnboardingRead(BaseModel):
    vendor_id: UUID
    vendor_number: str
    vendor_status: VendorStatus
    version_number: int
    questions: list[QuestionPublic]
    answers: dict[str, JsonValue]
    row_version: int


class ScoreLine(BaseModel):
    key: str
    prompt: str
    type: QuestionType
    weight: int
    answered: bool
    points: int
    contribution: str


class ScoreResult(BaseModel):
    """A score below the threshold is a fact for the approver, never a refusal (decision 6h-6)."""

    score: int
    threshold: int
    breakdown: list[ScoreLine]


class ScoreCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=100)
    score: int = Field(ge=1, le=5)
    period: date
    source_ref: str = Field(min_length=1, max_length=200)
    source_type: Literal["po", "manual"] = "po"
    rationale: str = Field(min_length=1, max_length=1000)

    @field_validator("category", "source_ref", "rationale")
    @classmethod
    def _stripped(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a value.")
        return value


class DisputeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def _stripped(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a reason.")
        return value


class DisputeResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution: Literal["upheld", "dismissed"]
    note: str = Field(min_length=1, max_length=1000)
    adjusted_score: int | None = Field(default=None, ge=1, le=5)
    adjusted_rationale: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("note", "adjusted_rationale")
    @classmethod
    def _stripped(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Enter a value.")
        return value


class DisputeRead(BaseModel):
    id: UUID
    score_id: UUID
    raised_by_email: str
    raised_at: datetime
    reason: str
    resolution: Literal["upheld", "dismissed"] | None
    resolved_by_email: str | None
    resolved_at: datetime | None
    resolution_note: str | None


class ScoreRead(BaseModel):
    id: UUID
    category_code: str
    score: int
    period: date
    source_type: Literal["po", "manual"]
    source_ref: str
    rationale: str
    scored_by_email: str
    scored_at: datetime
    supersedes: UUID | None
    superseded_by: UUID | None
    disputes: list[DisputeRead]


class ScorePage(BaseModel):
    rows: list[ScoreRead]
    next_cursor: str | None


class CategoryAverage(BaseModel):
    category_code: str
    average: str
    count: int


class PerformanceRead(BaseModel):
    """Figures are computed here from current rows only, never in the browser (VEN-007)."""

    categories: list[CategoryAverage]
    overall: str | None
    count: int
