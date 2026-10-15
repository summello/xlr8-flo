"""Requisition lines, quantity guards and stable numbering."""

from typing import Protocol

revision = "20261009_0040"
down_revision = "20261009_0039"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


# Retain the high-water mark even when the last (or every) line is deleted.
UPGRADE_SQL = """
ALTER TABLE requisition ADD COLUMN last_line_no int NOT NULL DEFAULT 0;
CREATE TABLE requisition_line (
 id uuid PRIMARY KEY, org_id uuid NOT NULL, requisition_id uuid NOT NULL,
 line_no int NOT NULL, line_type text NOT NULL CHECK (line_type IN ('item','service')),
 description text NOT NULL, quantity NUMERIC(18,4) NOT NULL CHECK (quantity > 0),
 uom_code text NOT NULL, unit_price NUMERIC(18,4) NOT NULL CHECK (unit_price > 0),
 amount NUMERIC(18,4) NOT NULL CHECK (amount >= 0), currency char(3) NOT NULL,
 category_code text, sourced_qty NUMERIC(18,4) NOT NULL DEFAULT 0,
 awarded_qty NUMERIC(18,4) NOT NULL DEFAULT 0, ordered_qty NUMERIC(18,4) NOT NULL DEFAULT 0,
 cancelled_qty NUMERIC(18,4) NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id,requisition_id) REFERENCES requisition(org_id,id) ON DELETE RESTRICT,
 UNIQUE(requisition_id,line_no),
 CHECK (sourced_qty >= 0 AND awarded_qty >= 0 AND ordered_qty >= 0 AND cancelled_qty >= 0),
 CONSTRAINT line_award_ceiling CHECK (awarded_qty <= quantity),
 CONSTRAINT line_order_ceiling CHECK (ordered_qty + cancelled_qty <= quantity)
);
ALTER TABLE requisition_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE requisition_line FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON requisition_line
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(
        "DROP TABLE requisition_line; ALTER TABLE requisition DROP COLUMN last_line_no;"
    )
