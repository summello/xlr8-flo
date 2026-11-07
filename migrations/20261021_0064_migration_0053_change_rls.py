"""Tenant-isolate migration 0053's tracking table, which is kept so 0053 can still be reversed (E22-S04)."""

from typing import Protocol

revision = "20261021_0064"
down_revision = "20261015_0063"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE migration_0053_change ENABLE ROW LEVEL SECURITY;
ALTER TABLE migration_0053_change FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON migration_0053_change
 USING(org_id=current_setting('app.org_id', true)::uuid)
 WITH CHECK(org_id=current_setting('app.org_id', true)::uuid);
"""

DOWNGRADE_SQL = """
DROP POLICY tenant_isolation ON migration_0053_change;
ALTER TABLE migration_0053_change NO FORCE ROW LEVEL SECURITY;
ALTER TABLE migration_0053_change DISABLE ROW LEVEL SECURITY;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # down restores the unprotected table so 0053's downgrade, which reads rows of every org
    # (including the org-less permission rows), runs exactly as before.
    connection.execute(DOWNGRADE_SQL)
