"""Purchase order drafts from awarded lines; ordered counter on award lines (E13-S01)."""

from typing import Protocol

revision = "20261027_0078"
down_revision = "20261022_0077"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE award_line ADD COLUMN ordered_qty NUMERIC(18,4) NOT NULL DEFAULT 0
 CHECK (ordered_qty >= 0 AND ordered_qty <= quantity);

CREATE TABLE purchase_order (
 id uuid PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 number text NOT NULL,
 unit_id uuid NOT NULL,
 vendor_id uuid NOT NULL,
 requisition_id uuid NOT NULL REFERENCES requisition(id),
 vendor_snapshot jsonb NOT NULL,
 award_id uuid NOT NULL,
 currency char(3) NOT NULL,
 rate jsonb NOT NULL,
 bill_to jsonb NOT NULL,
 ship_to_address_id uuid,
 recipients jsonb NOT NULL,
 requester_id uuid NOT NULL REFERENCES identity(id),
 terms text,
 needed_by date,
 status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','deleted')),
 version int NOT NULL DEFAULT 1,
 created_by uuid NOT NULL REFERENCES identity(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (org_id, unit_id, number), UNIQUE (org_id, id),
 FOREIGN KEY (org_id, unit_id) REFERENCES org_unit(org_id, id),
 FOREIGN KEY (org_id, award_id) REFERENCES award(org_id, id)
);
CREATE INDEX purchase_order_award ON purchase_order(org_id, award_id);
ALTER TABLE purchase_order ENABLE ROW LEVEL SECURITY;
ALTER TABLE purchase_order FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON purchase_order
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TABLE po_line (
 org_id uuid NOT NULL REFERENCES organization(id),
 po_id uuid NOT NULL,
 line_no int NOT NULL CHECK (line_no >= 1),
 award_id uuid NOT NULL,
 rfq_line_id uuid NOT NULL,
 vendor_id uuid NOT NULL,
 requisition_id uuid NOT NULL REFERENCES requisition(id),
 requisition_line_id uuid NOT NULL REFERENCES requisition_line(id),
 description text NOT NULL,
 uom_code text NOT NULL,
 quantity NUMERIC(18,4) NOT NULL CHECK (quantity > 0),
 unit_price NUMERIC(18,4) NOT NULL,
 project_id uuid NOT NULL,
 department_code text,
 ledger_account_code text,
 PRIMARY KEY (po_id, line_no),
 FOREIGN KEY (org_id, po_id) REFERENCES purchase_order(org_id, id),
 FOREIGN KEY (award_id, rfq_line_id, vendor_id)
  REFERENCES award_line(award_id, rfq_line_id, vendor_id)
);
CREATE INDEX po_line_award_line ON po_line(award_id, rfq_line_id, vendor_id);
ALTER TABLE po_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE po_line FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON po_line
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = """
DROP TABLE po_line;
DROP TABLE purchase_order;
ALTER TABLE award_line DROP COLUMN ordered_qty;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: purchase orders have no earlier home; down drops them and the counter.
    connection.execute(DOWNGRADE_SQL)
