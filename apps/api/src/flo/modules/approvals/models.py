"""Explicitly tenant-scoped workflow queries."""

from collections.abc import Mapping
from uuid import UUID

import psycopg

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.tenancy.context import Scope

DEFINITION_FIELDS = (
    "id",
    "document_type",
    "name",
    "scope_type",
    "scope_id",
    "created_by",
    "created_at",
    "retired_at",
)
VERSION_FIELDS = (
    "id",
    "definition_id",
    "version",
    "status",
    "graph",
    "row_version",
    "created_by",
    "created_at",
    "published_by",
    "published_at",
)


class WorkflowRepository(ScopedRepo[object]):
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(connection, scope)
        self.connection = connection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> psycopg.Cursor[tuple[object, ...]]:
        return self.connection.execute(query, self.scoped_params(params))

    def definition(self, id: UUID, *, lock: bool = False) -> dict[str, object] | None:
        row = self.execute(
            f"SELECT {', '.join(DEFINITION_FIELDS)} FROM workflow_definition "
            "WHERE org_id=%(org_id)s AND id=%(id)s" + (" FOR UPDATE" if lock else ""),
            {"id": id},
        ).fetchone()
        return dict(zip(DEFINITION_FIELDS, row, strict=True)) if row else None

    def version(self, id: UUID, version: int) -> dict[str, object] | None:
        row = self.execute(
            f"SELECT {', '.join(VERSION_FIELDS)} FROM workflow_version "
            "WHERE org_id=%(org_id)s AND definition_id=%(id)s AND version=%(version)s",
            {"id": id, "version": version},
        ).fetchone()
        return dict(zip(VERSION_FIELDS, row, strict=True)) if row else None
