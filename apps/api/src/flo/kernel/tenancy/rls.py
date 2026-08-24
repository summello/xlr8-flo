"""Transaction-scoped Postgres row-level-security context."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Protocol
from uuid import UUID

from psycopg import sql

from flo.kernel.tenancy.context import Scope


class RlsSession(Protocol):
    """Minimum synchronous database session required by the RLS boundary."""

    def execute(self, query: sql.Composed) -> object: ...

    def transaction(self) -> AbstractTransaction: ...


class AbstractTransaction(Protocol):
    """Context manager returned by a database session transaction."""

    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None: ...


def set_local_org(session: RlsSession, org_id: UUID) -> None:
    """Set the tenant only for the active transaction.

    A literal ``SET LOCAL`` is intentional. A session-level ``SET`` can leak a
    tenant through a transaction-pooled connection.
    """

    statement = sql.SQL("SET LOCAL app.org_id = {}").format(sql.Literal(str(org_id)))
    session.execute(statement)


@contextmanager
def tenant_transaction(session: RlsSession, scope: Scope) -> Generator[RlsSession, None, None]:
    """Start a transaction and install its RLS tenant before any caller query."""

    with session.transaction():
        set_local_org(session, scope.org_id)
        yield session
