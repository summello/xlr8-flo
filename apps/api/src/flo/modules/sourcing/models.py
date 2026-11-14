"""Only tenant-scoped RFQ queries."""

from collections.abc import Mapping

import psycopg

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.tenancy.context import Scope


class RfqRepository(ScopedRepo[object]):
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(connection, scope)
        self.connection = connection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> psycopg.Cursor[tuple[object, ...]]:
        return self.connection.execute(query, self.scoped_params(params))
