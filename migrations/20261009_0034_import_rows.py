"""Tenant-scoped dry-run rows and committed identifiers."""

from typing import Protocol

revision = "20261009_0034"
down_revision = "20261009_0033"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE import_batch ADD CONSTRAINT import_batch_org_id_id_key UNIQUE (org_id, id);
ALTER TABLE import_batch ADD COLUMN result jsonb;
CREATE TABLE import_row (
 org_id uuid NOT NULL,
 batch_id uuid NOT NULL,
 row_no int NOT NULL,
 action text NOT NULL CHECK (action IN ('create','update','skip','error')),
 has_warning boolean NOT NULL DEFAULT false,
 raw jsonb NOT NULL,
 parsed jsonb,
 preview jsonb,
 issues jsonb NOT NULL DEFAULT '[]',
 state_token text,
 PRIMARY KEY (batch_id, row_no),
 FOREIGN KEY (org_id, batch_id) REFERENCES import_batch(org_id, id)
);
CREATE INDEX import_row_action ON import_row(batch_id, action, row_no);
CREATE INDEX import_row_warning ON import_row(batch_id, has_warning, row_no);
CREATE TABLE import_result (
 org_id uuid NOT NULL,
 batch_id uuid NOT NULL,
 row_no int NOT NULL,
 record_type text NOT NULL,
 record_id uuid NOT NULL,
 PRIMARY KEY (batch_id, row_no),
 FOREIGN KEY (org_id, batch_id) REFERENCES import_batch(org_id, id)
);
ALTER TABLE import_row ENABLE ROW LEVEL SECURITY;
ALTER TABLE import_row FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON import_row
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE import_result ENABLE ROW LEVEL SECURITY;
ALTER TABLE import_result FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON import_result
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""
DOWNGRADE_SQL = """
DROP TABLE import_result;
DROP TABLE import_row;
ALTER TABLE import_batch DROP COLUMN result;
ALTER TABLE import_batch DROP CONSTRAINT import_batch_org_id_id_key;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
