"""The frozen vendor copy of each issued purchase order row (E13-S08)."""

from flo.kernel.migrate import MigrationConnection

revision = "20261030_0085"
down_revision = "20261029_0084"

UPGRADE_SQL = """
CREATE TABLE po_version (
 org_id uuid NOT NULL REFERENCES organization(id),
 po_id uuid NOT NULL,
 version int NOT NULL CHECK (version >= 1),
 template_version text NOT NULL,
 snapshot jsonb NOT NULL,
 pdf_sha256 text NOT NULL CHECK (pdf_sha256 ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (org_id, po_id),
 FOREIGN KEY (org_id, po_id) REFERENCES purchase_order(org_id, id)
);
ALTER TABLE po_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE po_version FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON po_version
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TRIGGER po_version_append_only BEFORE UPDATE OR DELETE ON po_version
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER po_version_append_only_truncate BEFORE TRUNCATE ON po_version
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
"""

# irreversible: dropping po_version loses every stored snapshot and PDF hash.
DOWNGRADE_SQL = """
DROP TRIGGER po_version_append_only_truncate ON po_version;
DROP TRIGGER po_version_append_only ON po_version;
DROP TABLE po_version;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
