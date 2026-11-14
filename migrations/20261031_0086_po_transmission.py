"""One row per email send of an issued purchase order (E13-S09)."""

from flo.kernel.migrate import MigrationConnection

revision = "20261031_0086"
down_revision = "20261030_0085"

UPGRADE_SQL = """
CREATE TABLE po_transmission (
 org_id uuid NOT NULL REFERENCES organization(id),
 id uuid PRIMARY KEY,
 po_id uuid NOT NULL,
 version int NOT NULL,
 attempt int NOT NULL CHECK (attempt >= 1),
 recipient_name text NOT NULL,
 recipient_email text NOT NULL,
 template_version text NOT NULL,
 outbox_id uuid,
 result text CHECK (result IN
  ('address_suppressed','delivered','bounced','complained','failed')),
 result_at timestamptz,
 created_by uuid NOT NULL REFERENCES identity(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id, po_id) REFERENCES purchase_order(org_id, id),
 CHECK ((outbox_id IS NULL) = (result IS NOT DISTINCT FROM 'address_suppressed'))
);
CREATE UNIQUE INDEX po_transmission_once
 ON po_transmission (org_id, po_id, attempt, lower(recipient_email));
ALTER TABLE po_transmission ENABLE ROW LEVEL SECURITY;
ALTER TABLE po_transmission FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON po_transmission
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""

# irreversible: dropping po_transmission loses the send log of every issued purchase order.
DOWNGRADE_SQL = """
DROP TABLE po_transmission;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
