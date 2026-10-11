"""Governed tenant master data and read/manage permissions."""

from typing import Protocol

revision = "20260826_0015"
down_revision = "20260826_0014"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE master_record (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 kind text NOT NULL, code text NOT NULL, name text NOT NULL,
 attributes jsonb NOT NULL DEFAULT '{}',
 effective_from date NOT NULL DEFAULT current_date, effective_to date,
 active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (org_id, kind, code)
);
CREATE INDEX master_record_prefix ON master_record (org_id, kind, code text_pattern_ops);
ALTER TABLE master_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE master_record FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON master_record
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
INSERT INTO permission(code) VALUES ('master.manage'), ('master.read');
"""
DOWNGRADE_SQL = """
DELETE FROM role_permission WHERE permission_code IN ('master.manage', 'master.read');
DELETE FROM permission WHERE code IN ('master.manage', 'master.read');
DROP TABLE master_record;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
