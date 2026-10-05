"""Scoped query path for organization data."""

from collections.abc import Mapping

import psycopg

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.tenancy.context import Scope


class OrgRepository(ScopedRepo[object]):
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(connection, scope)
        self.connection = connection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> psycopg.Cursor[tuple[object, ...]]:
        return self.connection.execute(query, self.scoped_params(params))

    def lock_organization(self) -> None:
        # Serialize structure and override mutations, including absent setting rows.
        self.execute("SELECT id FROM organization WHERE org_id = %(org_id)s FOR UPDATE")

    def unit(self, unit_id: object) -> dict[str, object] | None:
        row = self.execute(
            """SELECT id, parent_id, code, name, kind, active, created_at
            FROM org_unit WHERE org_id = %(org_id)s AND id = %(id)s""",
            {"id": unit_id},
        ).fetchone()
        if row is None:
            return None
        return dict(
            zip(
                ("id", "parent_id", "code", "name", "kind", "active", "created_at"),
                row,
                strict=True,
            )
        )
