"""Private import provenance and structural validation state."""

from typing import Protocol

revision = "20261008_0027"
down_revision = "20261008_0026"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE import_batch (
 id uuid PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 template text NOT NULL,
 template_version int NOT NULL,
 uploader_id uuid NOT NULL REFERENCES identity(id),
 file_name text,
 file_sha256 char(64) NOT NULL,
 file_size int,
 status text NOT NULL DEFAULT 'uploaded' CHECK (status IN
 ('uploaded','validating','validated','failed_validation','committing','committed','failed','cancelled')),
 mapping jsonb,
 counts jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 validated_at timestamptz,
 committed_at timestamptz
);
ALTER TABLE import_batch ENABLE ROW LEVEL SECURITY;
ALTER TABLE import_batch FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON import_batch
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
INSERT INTO permission(code) VALUES ('import.run'), ('import.read');
"""
DOWNGRADE_SQL = """
DELETE FROM role_permission WHERE permission_code IN ('import.run','import.read');
DELETE FROM permission WHERE code IN ('import.run','import.read');
DROP TABLE import_batch;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
