"""Effective-dated unit address history."""

from typing import Protocol

revision = "20260826_0014"
down_revision = "20260826_0013"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE org_address (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 unit_id uuid NOT NULL, kind text NOT NULL CHECK (kind IN ('bill_to','ship_to')),
 line1 text NOT NULL, line2 text, city text NOT NULL, region text,
 postal_code text, country char(2) NOT NULL,
 effective_from date NOT NULL, effective_to date,
 created_by uuid NOT NULL REFERENCES identity(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id, unit_id) REFERENCES org_unit(org_id, id),
 CHECK (effective_to IS NULL OR effective_to >= effective_from),
 EXCLUDE USING gist (unit_id WITH =, kind WITH =,
 daterange(effective_from, effective_to, '[]') WITH &&)
);
ALTER TABLE org_address ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_address FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON org_address
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # btree_gist can be shared by other tables; retain the extension.
    connection.execute("DROP TABLE org_address")
