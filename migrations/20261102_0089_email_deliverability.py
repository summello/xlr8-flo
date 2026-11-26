"""Email deliverability: domain status, webhook routing, events and the suppression list (E13-S10)."""

from flo.kernel.migrate import MigrationConnection

revision = "20261102_0089"
down_revision = "20261102_0088"

UPGRADE_SQL = """
CREATE TABLE email_domain_status (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 checked_at timestamptz NOT NULL DEFAULT now(),
 spf text NOT NULL CHECK (spf IN ('pass','fail','unknown')),
 dkim text NOT NULL CHECK (dkim IN ('pass','fail','unknown')),
 detail text NOT NULL
);
CREATE UNIQUE INDEX vendor_contact_org_id ON vendor_contact (org_id, id);
CREATE UNIQUE INDEX po_transmission_org_id ON po_transmission (org_id, id);
ALTER TABLE po_transmission ADD COLUMN result_reason text;
ALTER TABLE po_transmission ADD COLUMN vendor_contact_id uuid;
ALTER TABLE po_transmission ADD FOREIGN KEY (org_id, vendor_contact_id)
 REFERENCES vendor_contact (org_id, id) ON DELETE SET NULL (vendor_contact_id);

CREATE TABLE email_message_route (
 provider_message_id text PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 outbox_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE email_message_route ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_message_route FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON email_message_route
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE POLICY email_route_webhook_select ON email_message_route FOR SELECT
 USING (current_setting('app.worker', true) = 'webhook');

CREATE TABLE email_event (
 id uuid PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 provider_event_id text NOT NULL,
 type text NOT NULL,
 transmission_id uuid NOT NULL,
 vendor_contact_id uuid,
 received_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (provider_event_id),
 FOREIGN KEY (org_id, transmission_id) REFERENCES po_transmission (org_id, id)
);
ALTER TABLE email_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON email_event
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TABLE email_suppression (
 id uuid PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 email_normalised text NOT NULL,
 reason text NOT NULL CHECK (reason IN ('bounced','complained')),
 source_event_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (org_id, email_normalised)
);
ALTER TABLE email_suppression ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_suppression FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON email_suppression
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""

# irreversible: downgrade loses every stored provider event, webhook route and suppression.
DOWNGRADE_SQL = """
DROP TABLE email_suppression;
DROP TABLE email_event;
DROP TABLE email_message_route;
ALTER TABLE po_transmission DROP COLUMN vendor_contact_id;
ALTER TABLE po_transmission DROP COLUMN result_reason;
DROP INDEX po_transmission_org_id;
DROP INDEX vendor_contact_org_id;
DROP TABLE email_domain_status;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
