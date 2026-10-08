"""Root-project request and response contracts."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError

type ProjectStatus = Literal[
    "draft", "approval_pending", "active", "deferred", "completed", "abandoned"
]
type ProjectSort = Literal["number", "name", "status", "created_at"]
type Direction = Literal["asc", "desc"]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bu_id: UUID
    parent_id: UUID | None = None
    name: str = Field(min_length=1)
    description: str | None = None
    department_code: str
    ledger_account_code: str
    currency: str
    planned_start: date | None = None
    planned_end: date | None = None
    owner_id: UUID | None = None
    sponsor_id: UUID | None = None


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    owner_id: UUID | None = None
    sponsor_id: UUID | None = None
    planned_start: date | None = None
    planned_end: date | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_immutable(cls, value: object) -> object:
        immutable = {
            "number",
            "bu_id",
            "parent_id",
            "currency",
            "department_code",
            "ledger_account_code",
        }
        if isinstance(value, dict) and (fields := immutable.intersection(value)):
            raise ProblemError(
                ErrorCode.VALIDATION_FAILED,
                detail="Structural project fields cannot change. Edit only project details.",
                checks={"problem": "immutable_field"},
                errors=tuple(
                    ProblemFieldError(field=f, message="This field is immutable.")
                    for f in sorted(fields)
                ),
            )
        return value


class ProjectRead(BaseModel):
    id: UUID
    number: str
    name: str
    description: str | None
    status: ProjectStatus
    bu_id: UUID
    parent_id: UUID | None
    owner_id: UUID
    sponsor_id: UUID | None
    department_code: str
    ledger_account_code: str
    currency: str
    planned_start: date | None
    planned_end: date | None
    version: int
    created_at: datetime


class ProjectPage(BaseModel):
    rows: list[ProjectRead]
    next_cursor: str | None


class ProjectRef(BaseModel):
    id: UUID
    parent_id: UUID | None
    root_id: UUID
    number: str
    name: str
    status: ProjectStatus
    depth: int


class PathRead(BaseModel):
    up: list[UUID]
    lca: UUID | None
    down: list[UUID]


class ChildrenPage(BaseModel):
    rows: list[ProjectRef]
    next_cursor: str | None


class TreeNode(BaseModel):
    id: UUID
    number: str
    name: str
    status: ProjectStatus
    depth: int
    children: list["TreeNode"] = Field(default_factory=list)


class TreeRead(BaseModel):
    tree: TreeNode
    truncated: bool


class TransitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: ProjectStatus
    reason: str | None = None
    override: bool = False


class TransitionAvailable(BaseModel):
    to: ProjectStatus
    allowed: bool
    blocked_reasons: list[str]
