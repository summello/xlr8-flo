"""Vendor documents with issue and expiry, verification and expiry notices (E11-S03)."""

from typing import Protocol

revision = "20261014_0058"
down_revision = "20261014_0057"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE vendor_document (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 vendor_id uuid NOT NULL,
 type_code text NOT NULL CHECK (char_length(type_code) BETWEEN 1 AND 100),
 attachment_id uuid NOT NULL,
 attachment_version_id uuid NOT NULL REFERENCES attachment_version(id),
 issue_date date, expiry_date date,
 verification text NOT NULL DEFAULT 'unverified'
  CHECK (verification IN ('unverified','verified','rejected')),
 reviewed_by uuid REFERENCES identity(id), reviewed_at timestamptz,
 rejection_reason text CHECK (char_length(rejection_reason) BETWEEN 1 AND 500),
 notified_expiring_at timestamptz, notified_expired_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK (issue_date IS NULL OR expiry_date IS NULL OR expiry_date >= issue_date),
 CHECK (verification <> 'rejected' OR rejection_reason IS NOT NULL),
 UNIQUE (org_id, id),
 UNIQUE (org_id, vendor_id, type_code),
 UNIQUE (attachment_id),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id),
 FOREIGN KEY (org_id, attachment_id) REFERENCES attachment(org_id, id)
);
CREATE INDEX vendor_document_expiry ON vendor_document(org_id, expiry_date)
 WHERE expiry_date IS NOT NULL;
ALTER TABLE vendor_document ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_document FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON vendor_document
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = """
DROP TABLE vendor_document;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: document metadata created since upgrade has no earlier home; down drops it.
    # The attachments themselves stay readable through the collaboration routes.
    connection.execute(DOWNGRADE_SQL)
