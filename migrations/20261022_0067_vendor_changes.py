"""Vendor approval requests and configured unique identifiers (E11-S06)."""

from typing import Protocol

revision = "20261022_0067"
down_revision = "20261022_0066"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE vendor_change (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 org_id uuid NOT NULL REFERENCES organization(id),
 vendor_id uuid NOT NULL,
 kind text NOT NULL CHECK (kind IN ('create','availability','suspend','reactivate')),
 payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 reason text CHECK (char_length(reason) BETWEEN 1 AND 500),
 status text NOT NULL DEFAULT 'pending'
  CHECK (status IN ('pending','approved','rejected','withdrawn')),
 requested_by uuid NOT NULL REFERENCES identity(id),
 requested_at timestamptz NOT NULL DEFAULT now(),
 decided_at timestamptz,
 CHECK (kind = 'create' OR reason IS NOT NULL),
 UNIQUE (org_id, id),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id)
);
CREATE UNIQUE INDEX vendor_change_one_pending ON vendor_change(org_id, vendor_id)
 WHERE status = 'pending';
ALTER TABLE vendor_change ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_change FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON vendor_change
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);

CREATE TABLE vendor_identifier (
 org_id uuid NOT NULL REFERENCES organization(id),
 vendor_id uuid NOT NULL,
 type_code text NOT NULL,
 value_normalised text NOT NULL CHECK (char_length(value_normalised) BETWEEN 1 AND 64),
 PRIMARY KEY (org_id, vendor_id, type_code),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id)
);
CREATE INDEX vendor_identifier_lookup ON vendor_identifier(org_id, type_code, value_normalised);
ALTER TABLE vendor_identifier ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_identifier FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON vendor_identifier
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);

-- the normalised comparison of vendors/repo.py: upper case, spaces, dots and hyphens stripped
CREATE INDEX vendor_sensitive_tax_normalised ON vendor_sensitive
 (org_id, tax_id_country, (upper(regexp_replace(tax_id, '[ .-]', '', 'g'))));
"""

# irreversible: down drops vendor_change and vendor_identifier with the requests and identifiers.
DOWNGRADE_SQL = """
DROP INDEX vendor_sensitive_tax_normalised;
DROP TABLE vendor_identifier;
DROP TABLE vendor_change;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
