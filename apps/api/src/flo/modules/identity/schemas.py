"""Invitation wire contracts; bearer tokens are deliberately absent."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flo.modules.identity.models import ScopeType


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role_code: str = Field(min_length=1, max_length=100)
    scope_type: ScopeType
    scope_id: UUID | None = None


class InvitationRecord(BaseModel):
    id: UUID
    org_id: UUID
    email: str
    full_name: str | None
    role_code: str
    scope_type: ScopeType
    scope_id: UUID | None
    invited_by: UUID
    created_at: datetime
    sent_at: datetime
    expires_at: datetime
    resend_counter: int
    used_at: datetime | None
    used_by: UUID | None
    withdrawn_at: datetime | None
    withdrawn_by: UUID | None


class InvitationValid(BaseModel):
    state: Literal["valid"] = "valid"
    org_name: str
    tenant_label: str | None
    inviter_name: str
    inviter_title: str | None
    role_name: str
    access_words: str
    expires_at: datetime
    email: str
    full_name: str | None


class InvitationExpired(BaseModel):
    state: Literal["expired"] = "expired"
    lifetime_days: int


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    password: str | None = Field(default=None, min_length=1, max_length=256)
    accepted_terms: bool = False


class InvitationAccepted(BaseModel):
    next: Literal["enroll", "home"]


class ProfileUpdate(BaseModel):
    """PATCH /me: the caller's own display name, trimmed; null clears it."""

    model_config = ConfigDict(extra="forbid")
    display_name: str | None

    @field_validator("display_name")
    @classmethod
    def trimmed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not 1 <= len(value) <= 200:
            raise ValueError("Use 1 to 200 characters, or clear the name.")
        return value


class Profile(BaseModel):
    email: str
    display_name: str | None
    version: int
