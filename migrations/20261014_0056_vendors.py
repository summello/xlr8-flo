"""Vendor record, restricted-availability units and local disables (E11-S01)."""

from typing import Protocol

revision = "20261014_0056"
down_revision = "20261014_0055"

DOC_TYPES = "'project','requisition','rfq','award','po','asset'"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


def _rls(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {table}
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
"""


UPGRADE_SQL = f"""
ALTER TABLE numbering_format DROP CONSTRAINT numbering_format_doc_type_check;
ALTER TABLE numbering_format ADD CONSTRAINT numbering_format_doc_type_check
 CHECK (doc_type IN ({DOC_TYPES},'vendor'));
CREATE TABLE vendor (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 number text NOT NULL,
 trading_name text NOT NULL CHECK (char_length(trading_name) BETWEEN 1 AND 200),
 legal_name text NOT NULL CHECK (char_length(legal_name) BETWEEN 1 AND 200),
 status text NOT NULL DEFAULT 'draft'
  CHECK (status IN ('draft','pending_approval','active','suspended','inactive','rejected')),
 availability text NOT NULL CHECK (availability IN ('global','restricted')),
 created_by uuid NOT NULL REFERENCES identity(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (org_id, number), UNIQUE (org_id, id)
);
CREATE INDEX vendor_org_status ON vendor(org_id, status);
CREATE INDEX vendor_trading_name_lower ON vendor(org_id, lower(trading_name));
CREATE INDEX vendor_legal_name_lower ON vendor(org_id, lower(legal_name));
{_rls("vendor")}
CREATE TABLE vendor_unit (
 org_id uuid NOT NULL REFERENCES organization(id),
 vendor_id uuid NOT NULL, unit_id uuid NOT NULL,
 PRIMARY KEY (vendor_id, unit_id),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id),
 FOREIGN KEY (org_id, unit_id) REFERENCES org_unit(org_id, id)
);
{_rls("vendor_unit")}
CREATE TABLE vendor_local_disable (
 org_id uuid NOT NULL REFERENCES organization(id),
 vendor_id uuid NOT NULL, unit_id uuid NOT NULL,
 disabled_by uuid NOT NULL REFERENCES identity(id),
 disabled_at timestamptz NOT NULL DEFAULT now(),
 reason text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 500),
 PRIMARY KEY (vendor_id, unit_id),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id),
 FOREIGN KEY (org_id, unit_id) REFERENCES org_unit(org_id, id)
);
{_rls("vendor_local_disable")}
"""

DOWNGRADE_SQL = f"""
DROP TABLE vendor_local_disable;
DROP TABLE vendor_unit;
DROP TABLE vendor;
DELETE FROM numbering_counter WHERE doc_type = 'vendor';
DELETE FROM numbering_format WHERE doc_type = 'vendor';
ALTER TABLE numbering_format DROP CONSTRAINT numbering_format_doc_type_check;
ALTER TABLE numbering_format ADD CONSTRAINT numbering_format_doc_type_check
 CHECK (doc_type IN ({DOC_TYPES}));
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: vendors created since upgrade have no earlier home; down drops them.
    connection.execute(DOWNGRADE_SQL)
