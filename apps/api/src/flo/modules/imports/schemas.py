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
