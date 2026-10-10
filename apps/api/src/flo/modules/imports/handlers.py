"""Template-specific read-only planning and transactional application ports."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.imports.templates import REGISTRY


@dataclass(frozen=True)
class Issue:
    column: str | None
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"


@dataclass(frozen=True)
class RowPlan:
    action: Literal["create", "update", "skip", "error"]
    issues: tuple[Issue, ...]
    preview: dict[str, str]
    state_token: str | None


@dataclass
class RowContext:
    connection: psycopg.Connection[tuple[object, ...]]
    scope: Scope
    actor_id: UUID
    can: Callable[[str, AuthorizationTarget], bool]
    scratch: dict[str, object]


class TemplateHandler(Protocol):
    name: str

    def plan_row(self, context: RowContext, row_no: int, values: dict[str, object]) -> RowPlan: ...

    def apply_row(
        self, context: RowContext, plan: RowPlan, values: dict[str, object]
    ) -> tuple[str, UUID]: ...


HANDLERS: dict[str, TemplateHandler] = {}


def register_handler(handler: TemplateHandler) -> None:
    if handler.name not in REGISTRY:
        raise ValueError("template is not registered")
    if handler.name in HANDLERS:
        raise ValueError("handler already registered")
    HANDLERS[handler.name] = handler


def get_handler(name: str) -> TemplateHandler:
    if name not in HANDLERS:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return HANDLERS[name]
