"""Root-project request and response contracts."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError

type ProjectStatus = Literal[
    "draft", "approval_pending", "active", "deferred", "completed", "abandoned"
]
type ProjectSort = Literal["number", "name", "status", "created_at"]
type ProjectHealth = Literal["unknown", "on_track", "at_risk", "off_track"]
type PhaseStatus = Literal["planned", "in_progress", "done", "skipped"]
type ProjectGroup = Literal["status", "bu"]
type Direction = Literal["asc", "desc"]


class ExternalReference(BaseModel):
    external_ref: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:-]{1,64}$")

    @field_validator("external_ref", mode="before")
    @classmethod
    def trim_reference(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ProjectCreate(ExternalReference):
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


class ProjectPatch(ExternalReference):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    owner_id: UUID | None = None
    sponsor_id: UUID | None = None
    planned_start: date | None = None
    planned_end: date | None = None

    health: ProjectHealth | None = None
    percent_complete: int | None = Field(default=None, ge=0, le=100)
    actual_start: date | None = None
    actual_end: date | None = None

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
    external_ref: str | None
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
    health: ProjectHealth
    percent_complete: int
    actual_start: date | None
    actual_end: date | None
    schedule_variance_days: int | None
    version: int
    created_at: datetime
    bu_name: str | None = None
    allocated: str | None = None
    available: str | None = None


class ProjectPage(BaseModel):
    total: int
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
    reachable: bool
    reason_required: bool
    override_available: bool
    to: ProjectStatus
    allowed: bool
    blocked_reasons: list[str]


class PhaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    sequence: int | None = Field(default=None, ge=-32768, le=32767)
    sub_project_id: UUID | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    actual_start: date | None = None
    actual_end: date | None = None
    percent_complete: int = Field(default=0, ge=0, le=100)
    status: PhaseStatus = "planned"


class PhasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1)
    sequence: int | None = Field(default=None, ge=-32768, le=32767)
    sub_project_id: UUID | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    actual_start: date | None = None
    actual_end: date | None = None
    percent_complete: int | None = Field(default=None, ge=0, le=100)
    status: PhaseStatus | None = None


class PhaseRead(PhaseCreate):
    id: UUID
    project_id: UUID
    sequence: int
    created_at: datetime
    schedule_variance_days: int | None


class MilestoneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    due_date: date
    phase_id: UUID | None = None
    completed_on: date | None = None


class MilestonePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1)
    due_date: date | None = None
    phase_id: UUID | None = None
    completed_on: date | None = None


class MilestoneRead(MilestoneCreate):
    id: UUID
    project_id: UUID
    created_at: datetime


type RiskStatus = Literal["open", "mitigating", "closed", "accepted"]


class RiskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    likelihood: int = Field(ge=1, le=5, strict=True)
    impact: int = Field(ge=1, le=5, strict=True)
    owner_id: UUID
    mitigation: str | None = Field(default=None, max_length=4000)
    due_date: date | None = None
    status: RiskStatus = "open"
    closed_reason: str | None = None


class RiskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    likelihood: int | None = Field(default=None, ge=1, le=5, strict=True)
    impact: int | None = Field(default=None, ge=1, le=5, strict=True)
    owner_id: UUID | None = None
    mitigation: str | None = Field(default=None, max_length=4000)
    due_date: date | None = None
    status: RiskStatus | None = None
    closed_reason: str | None = None


class RiskRead(RiskCreate):
    id: UUID
    project_id: UUID
    score: int
    overdue: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    version: int


class RiskPage(BaseModel):
    rows: list[RiskRead]
    next_cursor: str | None
