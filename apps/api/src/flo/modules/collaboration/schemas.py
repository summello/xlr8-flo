"""Document collaboration wire contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Visibility = Literal["internal", "shareable"]
ScanStatus = Literal["pending", "basic_checks_passed", "clean", "quarantined", "rejected"]


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=10000)
    visibility: Visibility


class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str | None = Field(default=None, min_length=1, max_length=10000)
    visibility: Visibility | None = None


class NoteRead(BaseModel):
    id: UUID
    author_id: UUID
    author_email: str
    author_name: str | None
    body: str
    visibility: Visibility
    locked: bool
    created_at: datetime
    edited_at: datetime | None
    version: int


class NoteRevisionRead(BaseModel):
    id: int
    body: str
    visibility: Visibility
    edited_by: UUID
    edited_at: datetime


class AttachmentRead(BaseModel):
    id: UUID
    document_type: str
    document_id: UUID
    version: int
    versions: int
    original_filename: str
    display_name: str
    media_type: str
    size_bytes: int
    sha256: str
    scan_status: ScanStatus
    uploaded_by: UUID
    uploaded_by_email: str
    uploaded_by_name: str | None
    uploaded_at: datetime
    deleted_at: datetime | None
