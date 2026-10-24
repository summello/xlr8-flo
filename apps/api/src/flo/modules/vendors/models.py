"""Only tenant-scoped vendor queries."""

from collections.abc import Mapping

import psycopg

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.tenancy.context import Scope

# The one implementation of the availability rule (E11-S01): global and not disabled in the
# unit or any ancestor, or restricted and the unit or any ancestor is listed. Needs the `anc` CTE.
ANCESTORS_CTE = """WITH RECURSIVE anc AS (
 SELECT id, parent_id FROM org_unit WHERE org_id = %(org_id)s AND id = %(unit_id)s
 UNION ALL
 SELECT u.id, u.parent_id FROM org_unit u JOIN anc a ON u.id = a.parent_id
  WHERE u.org_id = %(org_id)s)"""
AVAILABLE_SQL = """((v.availability = 'global' AND NOT EXISTS (
  SELECT 1 FROM vendor_local_disable d JOIN anc ON anc.id = d.unit_id
   WHERE d.org_id = v.org_id AND d.vendor_id = v.id))
 OR (v.availability = 'restricted' AND EXISTS (
  SELECT 1 FROM vendor_unit vu JOIN anc ON anc.id = vu.unit_id
   WHERE vu.org_id = v.org_id AND vu.vendor_id = v.id)))"""
COLUMNS = (
    "v.id, v.number, v.trading_name, v.legal_name, v.status, v.availability, "
    "i.email, i.display_name, v.created_at, v.updated_at, v.version"
)
FIELDS = (
    "id",
    "number",
    "trading_name",
    "legal_name",
    "status",
    "availability",
    "created_by_email",
    "created_by_name",
    "created_at",
    "updated_at",
    "version",
)
FROM = "FROM vendor v JOIN identity i ON i.id = v.created_by"


class VendorRepository(ScopedRepo[object]):
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(connection, scope)
        self.connection = connection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> psycopg.Cursor[tuple[object, ...]]:
        return self.connection.execute(query, self.scoped_params(params))
