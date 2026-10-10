"""Import template and batch wire contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ColumnType = Literal["text", "integer", "decimal", "date", "code", "enum"]


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    type: ColumnType
    required: bool = False
    accepted_codes_source: str | None = None
    example: str = ""


class Template(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    version: int = Field(ge=1)
    columns: tuple[Column, ...]
    key_columns: tuple[str, ...] = ()


class TemplateColumnRead(BaseModel):
    name: str
    type: ColumnType
    required: bool
    example: str
    accepted_codes_url: str | None = None


class TemplateRead(BaseModel):
    name: str
    version: int
    columns: list[TemplateColumnRead]


class MappingPut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mapping: dict[str, str]


class ImportReport(BaseModel):
    mode: Literal["atomic", "partial"]
    committed: int
    skipped_errors: int
    unchanged: int
    committed_row_numbers: list[int]
    skipped_row_numbers: list[int]
    recovery: str | None


class CommitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["atomic", "partial"]


class ImportIssueRead(BaseModel):
    column: str | None
    code: str
    message: str
    severity: Literal["error", "warning"]


class ImportRowRead(BaseModel):
    row_no: int
    action: Literal["create", "update", "skip", "error"]
    has_warning: bool
    record_preview: dict[str, str]
    issues: list[ImportIssueRead]


class ImportPreview(BaseModel):
    rows: list[ImportRowRead]
    next_cursor: str | None


class ImportBatchRead(BaseModel):
    id: UUID
    template: str
    template_version: int
    uploader_id: UUID
    file_name: str
    file_sha256: str
    file_size: int
    status: Literal[
        "uploaded",
        "validating",
        "validated",
        "failed_validation",
        "committing",
        "committed",
        "failed",
        "cancelled",
    ]
    headers: list[str]
    row_count: int
    mapping: dict[str, str] | None
    counts: dict[str, int]
    created_at: datetime
    validated_at: datetime | None
    committed_at: datetime | None
    result: ImportReport | None = None
