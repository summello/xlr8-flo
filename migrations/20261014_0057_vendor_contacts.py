"""Typed vendor contacts (E11-S02)."""

from typing import Protocol

revision = "20261014_0057"
down_revision = "20261014_0056"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE vendor_contact (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 vendor_id uuid NOT NULL,
 type text NOT NULL CHECK (char_length(type) BETWEEN 1 AND 100),
 name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
 email text NOT NULL CHECK (char_length(email) BETWEEN 3 AND 254),
 phone text CHECK (char_length(phone) <= 40),
 receives_documents boolean NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id)
);
CREATE INDEX vendor_contact_vendor ON vendor_contact(org_id, vendor_id);
CREATE UNIQUE INDEX vendor_contact_required_type ON vendor_contact(vendor_id, type)
 WHERE type IN ('billing','service_delivery');
ALTER TABLE vendor_contact ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_contact FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON vendor_contact
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = """
DROP TABLE vendor_contact;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: contacts created since upgrade have no earlier home; down drops them.
    connection.execute(DOWNGRADE_SQL)
