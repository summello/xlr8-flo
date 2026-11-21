"""RFQ content, compliance requirements, attachment visibility and invitees (E12-S02)."""

from typing import Protocol

revision = "20261022_0068"
down_revision = "20261022_0067"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE rfq
 ADD COLUMN specifications text CHECK (char_length(specifications) <= 20000),
 ADD COLUMN delivery_location text CHECK (char_length(delivery_location) <= 500),
 ADD COLUMN delivery_terms text CHECK (char_length(delivery_terms) <= 500),
 ADD COLUMN terms text CHECK (char_length(terms) <= 20000),
 ADD COLUMN deadline_at timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
 ADD COLUMN deadline_tz text NOT NULL DEFAULT 'UTC';
ALTER TABLE rfq_line ADD COLUMN specification text CHECK (char_length(specification) <= 5000);
CREATE TABLE rfq_requirement (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL, position int NOT NULL,
 text text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 500),
 waived boolean NOT NULL DEFAULT false,
 waived_by uuid REFERENCES identity(id), waived_reason text,
 CHECK (NOT waived OR (waived_by IS NOT NULL AND char_length(waived_reason) >= 1)),
 UNIQUE (rfq_id, position),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id)
);
ALTER TABLE rfq_requirement ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_requirement FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_requirement
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TABLE rfq_attachment_visibility (
 org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL, attachment_id uuid NOT NULL,
 vendor_visible boolean NOT NULL DEFAULT false,
 PRIMARY KEY (rfq_id, attachment_id),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id),
 FOREIGN KEY (org_id, attachment_id) REFERENCES attachment(org_id, id)
);
ALTER TABLE rfq_attachment_visibility ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_attachment_visibility FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_attachment_visibility
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TABLE rfq_invitee (
 org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL, vendor_id uuid NOT NULL,
 contact_ids uuid[] NOT NULL CHECK (cardinality(contact_ids) >= 1),
 vendor_snapshot jsonb NOT NULL,
 override_reason text, overridden_by uuid REFERENCES identity(id),
 invited_by uuid NOT NULL REFERENCES identity(id),
 invited_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (rfq_id, vendor_id),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id)
);
ALTER TABLE rfq_invitee ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_invitee FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_invitee
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = """
DROP TABLE rfq_invitee;
DROP TABLE rfq_attachment_visibility;
DROP TABLE rfq_requirement;
ALTER TABLE rfq_line DROP COLUMN specification;
ALTER TABLE rfq DROP COLUMN specifications, DROP COLUMN delivery_location,
 DROP COLUMN delivery_terms, DROP COLUMN terms, DROP COLUMN deadline_at,
 DROP COLUMN deadline_tz;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: content, requirements, visibility and invitations entered since upgrade
    # have no earlier home; down drops them.
    connection.execute(DOWNGRADE_SQL)
