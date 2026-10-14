"""Frozen workflow graph and wire contracts; condition expressions remain opaque."""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

import psycopg
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictStr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from flo.kernel.authz import AuthorizationTarget
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.money import Money, currency_exponent
from flo.kernel.tenancy.context import Scope
from flo.modules.org.schemas import CodesRead

DocumentType = Literal["project", "requisition", "budget_change", "vendor", "rfq", "award", "po"]
VersionStatus = Literal["draft", "published", "superseded"]


def invalid(field: str, message: str) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail="Correct the workflow graph and retry.",
        errors=(ProblemFieldError(field=field, message=message),),
    )


def check_graph_size(value: object) -> None:
    if (
        len(json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":")).encode())
        > 256 * 1024
    ):
        raise invalid("/", "Graph JSON exceeds 256 KiB.")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleApprovers(Contract):
    type: Literal["role"]
    role_code: str = Field(min_length=1)


class UserApprovers(Contract):
    type: Literal["users"]
    user_ids: list[UUID] = Field(min_length=1, max_length=50)


class PermissionApprovers(Contract):
    type: Literal["permission"]


Approvers = Annotated[
    RoleApprovers | UserApprovers | PermissionApprovers, Field(discriminator="type")
]


class Quorum(Contract):
    quorum: int = Field(ge=1, strict=True)


class Node(Contract):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    kind: Literal["start", "condition", "approval", "end"]
    name: str | None = None
    approvers: Approvers | None = None
    mode: Literal["any", "all"] | Quorum | None = None
    due_days: int | None = Field(default=None, ge=1, le=365, strict=True)
    escalate_after_days: int | None = Field(default=None, ge=1, strict=True)
    escalate_to: Approvers | None = None
    allow_repeat: bool = False

    @model_validator(mode="after")
    def approval_fields(self) -> Self:
        if self.kind == "approval" and (self.approvers is None or self.mode is None):
            raise ValueError("Approval nodes require approvers and mode.")
        return self


class Edge(Contract):
    from_: str = Field(alias="from")
    to: str
    when: dict[str, JsonValue] | None = Field(default=None, exclude_if=lambda value: value is None)
    default: bool = False

    @field_validator("when", mode="before")
    @classmethod
    def condition_object(cls, value: object) -> object:
        if value is None:
            raise ValueError("A condition expression must be a JSON object.")
        return value


class Position(Contract):
    x: int | float
    y: int | float


class WorkflowGraph(Contract):
    nodes: list[Node] = Field(max_length=200)
    edges: list[Edge] = Field(max_length=400)
    layout: dict[str, Position] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def byte_limit(cls, value: object) -> object:
        if isinstance(value, cls):
            return value
        check_graph_size(value)
        return value

    @model_validator(mode="after")
    def structure(self) -> Self:
        if sum(n.kind == "start" for n in self.nodes) != 1:
            raise invalid("/nodes", "Provide exactly one start node.")
        if not any(n.kind == "end" for n in self.nodes):
            raise invalid("/nodes", "Provide at least one end node.")
        ids: set[str] = set()
        for i, node in enumerate(self.nodes):
            if node.id in ids:
                raise invalid(f"/nodes/{i}/id", "Node ids must be unique.")
            ids.add(node.id)
        for i, edge in enumerate(self.edges):
            for field, endpoint in (("from", edge.from_), ("to", edge.to)):
                if endpoint not in ids:
                    raise invalid(f"/edges/{i}/{field}", "Endpoint must name a node.")
        # Enforce the persisted representation too: defaults can enlarge the input JSON.
        check_graph_size(self.model_dump(mode="json", by_alias=True, exclude_none=True))
        return self


def graph_pointer(location: tuple[str | int, ...]) -> str:
    parts: list[str] = []
    for component in location:
        # Pydantic adds union branch names; these are not keys in the graph JSON.
        if (
            parts
            and parts[-1] in {"approvers", "escalate_to"}
            and component in {"role", "users", "permission"}
        ):
            continue
        if component == "Quorum" or str(component).startswith("literal["):
            continue
        parts.append(str(component).replace("~", "~0").replace("/", "~1"))
    return "/" + "/".join(parts)


def parse_graph(value: object) -> WorkflowGraph:
    """Convert Pydantic errors to JSON pointers without changing the kernel handler."""
    try:
        return WorkflowGraph.model_validate(value)
    except ValidationError as exc:
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED,
            detail="Correct the workflow graph and retry.",
            errors=tuple(
                ProblemFieldError(
                    field=graph_pointer(error["loc"]),
                    message=str(error["msg"]),
                )
                for error in exc.errors()
            ),
        ) from exc


class WorkflowCreate(Contract):
    document_type: DocumentType
    name: str = Field(min_length=1)
    scope_type: Literal["org", "bu"]
    scope_id: UUID | None = None

    @model_validator(mode="after")
    def scope_shape(self) -> Self:
        if self.scope_type == "org" and "scope_id" in self.model_fields_set:
            raise invalid("/scope_id", "Organization scope must omit scope_id.")
        if self.scope_type == "bu" and self.scope_id is None:
            raise invalid("/scope_id", "BU scope requires scope_id.")
        return self


class WorkflowVersionWrite(Contract):
    graph: WorkflowGraph

    @model_validator(mode="before")
    @classmethod
    def graph_pointers(cls, value: object) -> object:
        if isinstance(value, dict) and "graph" in value:
            return {**value, "graph": parse_graph(value["graph"])}
        return value


class WorkflowVersionSummary(Contract):
    version: int
    status: VersionStatus
    created_at: datetime
    published_at: datetime | None
    author_name: str = "Unknown"
    in_flight: int | None = 0


class WorkflowRead(Contract):
    id: UUID
    document_type: DocumentType
    name: str
    scope_type: Literal["org", "bu"]
    scope_id: UUID
    created_by: UUID
    created_at: datetime
    retired_at: datetime | None
    applies_to: str = ""
    published_version: int | None = None
    draft_version: int | None = None
    in_flight: int | None = 0
    edited_at: datetime | None = None
    edited_by_name: str = "Unknown"
    versions: list[WorkflowVersionSummary] = Field(default_factory=list)


class WorkflowVersionRead(WorkflowVersionSummary):
    id: UUID
    definition_id: UUID
    graph: WorkflowGraph
    row_version: int
    created_by: UUID
    published_by: UUID | None


class WorkflowPage(Contract):
    rows: list[WorkflowRead]
    next_cursor: str | None


# Conditions are parsed separately at publish time; draft graphs remain opaque.
type AttributeValue = str | Decimal | date | bool | None | tuple[str, ...] | Money
FieldType = Literal["text", "code", "decimal", "date", "boolean", "money", "code_list"]
Operator = Literal[
    "eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "contains", "is_null", "not_null"
]


class MoneyValue(Contract):
    amount: str = Field(strict=True, pattern=r"^-?[0-9]{1,14}(\.[0-9]{1,4})?$")
    currency: StrictStr

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        currency_exponent(value)
        return value


class LeafCondition(Contract):
    field: str = Field(strict=True, min_length=1)
    op: Operator
    value: StrictStr | StrictBool | MoneyValue | list[StrictStr] | None = None

    @model_validator(mode="after")
    def value_shape(self) -> Self:
        if self.op in {"is_null", "not_null"}:
            if "value" in self.model_fields_set:
                raise ValueError("Null operators must omit value")
        elif self.value is None:
            raise ValueError("Operator requires value")
        elif self.op in {"in", "not_in"}:
            if not isinstance(self.value, list) or not 1 <= len(self.value) <= 100:
                raise ValueError("Membership requires 1..100 strings")
        elif isinstance(self.value, list):
            raise ValueError("Only membership accepts lists")
        elif self.op == "contains" and not isinstance(self.value, str):
            raise ValueError("Contains requires a string")
        if self.op in {"lt", "le", "gt", "ge"} and isinstance(self.value, str):
            if re.fullmatch(r"-?[0-9]{1,14}(\.[0-9]{1,4})?", self.value) is None:
                if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", self.value) is None:
                    raise ValueError("Ordered strings must be decimal amounts or ISO dates")
                date.fromisoformat(self.value)
        return self


class AllCondition(Contract):
    all: list["Condition"] = Field(min_length=1, max_length=20)


class AnyCondition(Contract):
    any: list["Condition"] = Field(min_length=1, max_length=20)


class NotCondition(Contract):
    not_: "Condition" = Field(alias="not")


type Condition = LeafCondition | AllCondition | AnyCondition | NotCondition
for _model in (AllCondition, AnyCondition, NotCondition):
    _model.model_rebuild()
_condition_adapter: TypeAdapter[Condition] = TypeAdapter(Condition)


def parse_condition(value: object) -> Condition:
    """Parse the frozen grammar, returning JSON-pointer validation errors."""
    leaves = 0

    def limits(node: object, depth: int, pointer: str) -> None:
        nonlocal leaves
        if depth > 6:
            raise invalid(pointer or "/", "Condition depth exceeds 6")
        if not isinstance(node, dict):
            return
        if "field" in node:
            leaves += 1
            if leaves > 50:
                raise invalid(pointer or "/", "Condition exceeds 50 leaves")
        for key in ("all", "any"):
            children = node.get(key)
            if isinstance(children, list):
                for index, child in enumerate(children):
                    limits(child, depth + 1, f"{pointer}/{key}/{index}")
        if "not" in node:
            limits(node["not"], depth + 1, f"{pointer}/not")

    limits(value, 1, "")
    try:
        return _condition_adapter.validate_python(value)
    except ValidationError as exc:
        branch_names = {
            "LeafCondition",
            "AllCondition",
            "AnyCondition",
            "NotCondition",
            "MoneyValue",
        }
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED,
            detail="Correct the condition and retry.",
            errors=tuple(
                ProblemFieldError(
                    field=graph_pointer(
                        tuple(part for part in error["loc"] if part not in branch_names)
                    ),
                    message=str(error["msg"]),
                )
                for error in exc.errors()
            ),
        ) from exc


# Execution contracts shared by every document adapter.


class Outcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    RETURNED = "returned"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True)
class OutcomeContext:
    actor_id: UUID
    decision_id: UUID | None
    assign_project_id: UUID | None
    permitted: Callable[[str, AuthorizationTarget], bool]
    reject_mode: Literal["return_to_draft", "permanent"] | None = None


@dataclass(frozen=True)
class DocumentSnapshot:
    version: int
    requester_id: UUID
    target: AuthorizationTarget
    attributes: dict[str, AttributeValue]
    material: dict[str, str]


@dataclass(frozen=True)
class DocumentSummary:
    number: str
    title: str
    amount: Money | None
    requester_id: UUID
    href: str
    note: str | None
    codes: CodesRead | None = None


class DocumentAdapter(Protocol):
    document_type: str
    approve_permission: str
    read_permission: str
    accepts_project_assignment: bool

    def fields(self) -> Mapping[str, FieldType]: ...
    def snapshot(
        self, conn: psycopg.Connection[tuple[object, ...]], scope: Scope, document_id: UUID
    ) -> DocumentSnapshot: ...
    def summaries(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_ids: Sequence[UUID],
    ) -> Mapping[UUID, DocumentSummary]: ...
    def on_outcome(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_id: UUID,
        outcome: Outcome,
        context: OutcomeContext,
    ) -> None: ...


class RouteStepState(StrEnum):
    UPCOMING = "upcoming"
    OPEN = "open"
    COMPLETED = "completed"
    CARRIED = "carried"


class ExcludedApprover(Contract):
    email: str
    reason: str


class RouteStep(Contract):
    reached_via: str | None = None
    node_id: str
    label: str
    mode: Literal["any", "all", "quorum"]
    quorum: int | None
    rule: str
    approver_emails: list[str]
    excluded: list[ExcludedApprover]
    state: RouteStepState
    due_at: datetime | None


class RouteProblem(Contract):
    code: Literal["no_workflow", "no_eligible_approver", "route_blocked"]
    step_label: str | None
    message: str


class RoutePreview(Contract):
    path_edge_indices: list[int] = Field(default_factory=list)
    separation_of_duty_applied: bool = True
    workflow_version_id: UUID | None
    workflow_version: int | None
    steps: list[RouteStep]
    problems: list[RouteProblem]


class TaskRead(Contract):
    id: UUID
    node_id: str
    label: str
    mode: Literal["any", "all", "quorum"]
    quorum: int | None
    status: Literal["open", "completed", "cancelled"]
    outcome: str | None
    approver_emails: list[str]
    due_at: datetime | None
    completed_at: datetime | None


class InstanceRead(Contract):
    separation_of_duty_applied: bool = True
    id: UUID
    document_type: str
    document_id: UUID
    workflow_version: int
    workflow_version_id: UUID
    document_version: int
    status: Literal["running", "approved", "rejected", "returned", "withdrawn", "superseded"]
    requester_email: str
    current_node_id: str | None
    route: list[RouteStep]
    tasks: list[TaskRead]
    started_at: datetime
    finished_at: datetime | None


class CodeSubstitutions(Contract):
    department_code: str | None = Field(default=None, min_length=1, max_length=64)
    ledger_account_code: str | None = Field(default=None, min_length=1, max_length=64)


class DecisionWrite(Contract):
    outcome: Literal["approve", "reject", "return", "comment"]
    reject_mode: Literal["return_to_draft", "permanent"] | None = None
    comment: str | None = Field(default=None, min_length=1, max_length=4000)
    document_version: int
    assign_project_id: UUID | None = None
    substitutions: CodeSubstitutions | None = None

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if self.comment is not None:
            self.comment = self.comment.strip()
            if not self.comment:
                raise decision_invalid("comment", "Supply a rationale for this action.")
        if (self.outcome == "reject") != (self.reject_mode is not None):
            raise decision_invalid("reject_mode", "Choose a rejection mode only when rejecting.")
        if self.outcome in {"reject", "return", "comment"} and self.comment is None:
            raise decision_invalid("comment", "Supply a rationale for this action.")
        if self.assign_project_id is not None and self.outcome != "approve":
            raise decision_invalid("assign_project_id", "Assign a project only when approving.")
        return self


def decision_invalid(field: str, message: str) -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail="Correct the decision and retry.",
        errors=(ProblemFieldError(field=field, message=message),),
    )


class WithdrawWrite(Contract):
    comment: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_comment(self) -> Self:
        if self.comment is not None:
            self.comment = self.comment.strip()
            if not self.comment:
                raise decision_invalid("comment", "Supply a rationale for this action.")
        return self


class DecisionRead(Contract):
    id: UUID
    instance_id: UUID
    task_id: UUID | None
    node_id: str | None
    step_label: str | None
    actor_email: str
    on_behalf_of_email: str | None
    authority: Literal["member", "delegate", "admin", "requester"]
    outcome: Literal["approve", "reject", "return", "comment", "withdraw"]
    reject_mode: Literal["return_to_draft", "permanent"] | None
    comment: str | None
    document_version: int
    members_at_decision: list[str]
    decided_at: datetime


class DecisionResult(Contract):
    decision: DecisionRead
    instance: InstanceRead


class DelegationCreate(Contract):
    delegate_id: UUID
    starts_at: datetime
    ends_at: datetime
    document_types: list[DocumentType] | None = Field(default=None, min_length=1, max_length=7)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        self.reason = self.reason.strip()
        if not self.reason:
            raise decision_invalid("reason", "Say why you are delegating.")
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise decision_invalid("starts_at", "Send times with a time zone.")
        if self.starts_at >= self.ends_at:
            raise decision_invalid("ends_at", "The end must be after the start.")
        if (self.ends_at - self.starts_at).days > 366:
            raise decision_invalid("ends_at", "A delegation can last at most 366 days.")
        return self


class DelegationRead(Contract):
    id: UUID
    delegator_email: str
    delegate_email: str
    starts_at: datetime
    ends_at: datetime
    document_types: list[str] | None
    reason: str
    created_at: datetime
    revoked_at: datetime | None
    state: Literal["scheduled", "active", "ended", "revoked"]


class DelegationPage(Contract):
    rows: list[DelegationRead]
    next_cursor: str | None


class ReassignWrite(Contract):
    user_ids: list[UUID] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def reason_present(self) -> Self:
        self.reason = self.reason.strip()
        if not self.reason:
            raise decision_invalid("reason", "Say why this task is being reassigned.")
        return self


class ReminderReport(Contract):
    organizations: int
    digests: int
    escalated: int


class ApprovalState(Contract):
    status: Literal[
        "none", "running", "approved", "rejected", "returned", "withdrawn", "superseded"
    ]
    instance_id: UUID | None = None
    approved_version: int | None = None
    approved_material_digest: str | None = None
    running_instance_id: UUID | None = None
