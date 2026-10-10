"""Scoped queries for project records."""

from collections.abc import Mapping
from uuid import UUID

import psycopg

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.tenancy.context import Scope

FIELDS = (
    "external_ref",
    "id",
    "number",
    "name",
    "description",
    "status",
    "bu_id",
    "parent_id",
    "owner_id",
    "sponsor_id",
    "department_code",
    "ledger_account_code",
    "currency",
    "planned_start",
    "planned_end",
    "health",
    "percent_complete",
    "actual_start",
    "actual_end",
    "version",
    "created_at",
)
COLUMNS = ", ".join(FIELDS)


class ProjectRepository(ScopedRepo[object]):
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(connection, scope)
        self.connection = connection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> psycopg.Cursor[tuple[object, ...]]:
        return self.connection.execute(query, self.scoped_params(params))

    def get(self, project_id: UUID, *, lock: bool = False) -> dict[str, object] | None:
        row = self.execute(
            f"SELECT {COLUMNS} FROM project WHERE org_id = %(org_id)s AND id = %(id)s"
            + (" FOR UPDATE" if lock else ""),
            {"id": project_id},
        ).fetchone()
        return dict(zip(FIELDS, row, strict=True)) if row else None
