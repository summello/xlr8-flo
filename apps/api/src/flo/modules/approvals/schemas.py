"""Frozen workflow graph and wire contracts; condition expressions remain opaque."""

import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError

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


class WorkflowRead(Contract):
    id: UUID
    document_type: DocumentType
    name: str
    scope_type: Literal["org", "bu"]
    scope_id: UUID
    created_by: UUID
    created_at: datetime
    retired_at: datetime | None
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
