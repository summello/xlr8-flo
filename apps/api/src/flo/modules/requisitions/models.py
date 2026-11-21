"""Only tenant-scoped header queries."""

from collections.abc import Mapping

import psycopg

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.tenancy.context import Scope

FIELDS = (
    "id",
    "number",
    "title",
    "status",
    "standalone",
    "project_id",
    "bu_id",
    "requester_id",
    "justification",
    "needed_by",
    "department_code",
    "ledger_account_code",
    "delivery_address_id",
    "estimate",
    "currency",
    "version",
    "submitted_at",
    "decided_at",
    "created_at",
    "updated_at",
)
COLUMNS = ", ".join("r." + field + ("::text" if field == "estimate" else "") for field in FIELDS)
READ_SQL = f"""SELECT {COLUMNS}, p.number, p.name, u.name, i.email, i.display_name
 FROM requisition r
 LEFT JOIN project p ON p.org_id=r.org_id AND p.id=r.project_id
 JOIN org_unit u ON u.org_id=r.org_id AND u.id=r.bu_id
 JOIN identity i ON i.id=r.requester_id"""
READ_FIELDS = (
    *FIELDS,
    "project_number",
    "project_name",
    "bu_name",
    "requester_email",
    "requester_name",
)


class RequisitionRepository(ScopedRepo[object]):
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(connection, scope)
        self.connection = connection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> psycopg.Cursor[tuple[object, ...]]:
        return self.connection.execute(query, self.scoped_params(params))


def doc_type(connection: psycopg.Connection[tuple[object, ...]], scope: Scope, id: object) -> str:
    """The approval document type: a standalone request is a Project Request (D-M2-18)."""
    row = (
        RequisitionRepository(connection, scope)
        .execute(
            "SELECT standalone FROM requisition WHERE org_id=%(org_id)s AND id=%(id)s", {"id": id}
        )
        .fetchone()
    )
    return "project_request" if row and row[0] else "requisition"
