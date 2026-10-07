"""Required-scope base class for tenant repositories."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from flo.kernel.tenancy.context import Scope


class Session(Protocol):
    """Marker protocol for a repository database session."""


class ScopedRepo[T]:
    """Base repository that cannot be constructed without a tenant scope."""

    def __init__(self, session: Session, scope: Scope) -> None:
        self._session = session
        self._scope = scope

    @property
    def session(self) -> Session:
        """Return the database session for a concrete repository implementation."""

        return self._session

    @property
    def scope(self) -> Scope:
        """Return the immutable tenant scope required at construction."""

        return self._scope

    def scoped_params(self, params: Mapping[str, object] | None = None) -> dict[str, object]:
        """Return query parameters with an unoverrideable organization identifier."""

        scoped = dict(params or {})
        scoped["org_id"] = self._scope.org_id
        return scoped
