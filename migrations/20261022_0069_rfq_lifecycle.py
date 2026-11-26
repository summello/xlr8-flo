"""RFQ publication, immutable versions and who was told of each (E12-S03)."""

from typing import Protocol

revision = "20261022_0069"
down_revision = "20261022_0068"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE rfq ADD COLUMN published_at timestamptz;
CREATE INDEX rfq_due ON rfq(deadline_at) WHERE status = 'published';
CREATE TABLE rfq_version (
 org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL, version int NOT NULL CHECK (version >= 1),
 snapshot jsonb NOT NULL, reason text, diff jsonb NOT NULL,
 published_by uuid NOT NULL REFERENCES identity(id),
 published_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (rfq_id, version),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id)
);
ALTER TABLE rfq_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_version FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_version
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TRIGGER rfq_version_append_only BEFORE UPDATE OR DELETE ON rfq_version
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER rfq_version_append_only_truncate BEFORE TRUNCATE ON rfq_version
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
CREATE TABLE rfq_invitee_version (
 org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL, vendor_id uuid NOT NULL, contact_id uuid NOT NULL,
 version int NOT NULL, outbox_id uuid NOT NULL PRIMARY KEY,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (rfq_id, version) REFERENCES rfq_version(rfq_id, version),
 FOREIGN KEY (rfq_id, vendor_id) REFERENCES rfq_invitee(rfq_id, vendor_id)
);
CREATE INDEX rfq_invitee_version_rfq ON rfq_invitee_version(rfq_id, vendor_id);
ALTER TABLE rfq_invitee_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_invitee_version FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_invitee_version
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TRIGGER rfq_invitee_version_append_only BEFORE UPDATE OR DELETE ON rfq_invitee_version
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER rfq_invitee_version_append_only_truncate BEFORE TRUNCATE ON rfq_invitee_version
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
"""

DOWNGRADE_SQL = """
DROP TABLE rfq_invitee_version;
DROP TABLE rfq_version;
DROP INDEX rfq_due;
ALTER TABLE rfq DROP COLUMN published_at;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: versions and the record of who was told have no earlier home; down drops
    # them and leaves each RFQ's status and version number as they were.
    connection.execute(DOWNGRADE_SQL)
