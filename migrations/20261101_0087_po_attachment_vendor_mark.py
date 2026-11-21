"""Per-file vendor-facing mark on PO attachments: intent only, nothing is sent (E13-S25)."""

from flo.kernel.migrate import MigrationConnection

revision = "20261101_0087"
down_revision = "20261031_0086"

UPGRADE_SQL = """
CREATE TABLE po_attachment_vendor_mark (
 org_id uuid NOT NULL REFERENCES organization(id),
 attachment_id uuid NOT NULL,
 po_id uuid NOT NULL,
 attachment_version_id uuid NOT NULL REFERENCES attachment_version(id),
 vendor_facing boolean NOT NULL DEFAULT false,
 marked_by uuid NOT NULL REFERENCES identity(id),
 marked_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (attachment_id),
 FOREIGN KEY (org_id, attachment_id) REFERENCES attachment(org_id, id),
 FOREIGN KEY (org_id, po_id) REFERENCES purchase_order(org_id, id)
);
ALTER TABLE po_attachment_vendor_mark ENABLE ROW LEVEL SECURITY;
ALTER TABLE po_attachment_vendor_mark FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON po_attachment_vendor_mark
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = """
DROP TABLE po_attachment_vendor_mark;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: drops vendor-facing mark; the intent recorded since upgrade has no earlier home.
    connection.execute(DOWNGRADE_SQL)
