"""Vendor contracts. People are emails (D-M2-16)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    @field_validator("name", "email", "phone")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None


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
