"""Request-scoped tenant context."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import UUID


class TenantScopeMissing(RuntimeError):
    """Raised when tenant-scoped code runs outside an authenticated scope."""


@dataclass(frozen=True, slots=True)
class Scope:
    """Organization and optional business-unit scope from an authenticated session."""

    org_id: UUID
    bu_ids: frozenset[UUID] = field(default_factory=frozenset)


_scope: ContextVar[Scope | None] = ContextVar("tenant_scope", default=None)


def current_scope() -> Scope:
    """Return the active request scope, failing closed when none was established."""

    scope = _scope.get()
    if scope is None:
        raise TenantScopeMissing("tenant scope is unavailable")
    return scope


@contextmanager
def use_scope(scope: Scope) -> Iterator[None]:
    """Bind a scope for one request or explicitly bounded operation."""

    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)
