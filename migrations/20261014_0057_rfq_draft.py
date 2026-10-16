"""RFQ draft and its lines, sourced from approved requisition lines (E12-S01)."""

from typing import Protocol

revision = "20261014_0057"
down_revision = "20261014_0056"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE rfq (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 number text NOT NULL,
 title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
 status text NOT NULL DEFAULT 'draft'
  CHECK (status IN ('draft','published','closed','under_evaluation','awarded','cancelled')),
 unit_id uuid NOT NULL, currency char(3) NOT NULL,
 owner_id uuid NOT NULL REFERENCES identity(id),
 version int NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (org_id, number), UNIQUE (org_id, id),
 FOREIGN KEY (org_id, unit_id) REFERENCES org_unit(org_id, id)
);
CREATE INDEX rfq_org_status ON rfq(org_id, status);
ALTER TABLE rfq ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TABLE rfq_line (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL, line_no int NOT NULL,
 requisition_id uuid NOT NULL, requisition_line_id uuid NOT NULL REFERENCES requisition_line(id),
 requisition_version int NOT NULL,
 quantity NUMERIC(18,4) NOT NULL CHECK (quantity > 0),
 description text NOT NULL, uom_code text NOT NULL, needed_by date,
 currency char(3) NOT NULL,
 UNIQUE (rfq_id, requisition_line_id), UNIQUE (rfq_id, line_no),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id),
 FOREIGN KEY (org_id, requisition_id) REFERENCES requisition(org_id, id)
);
CREATE INDEX rfq_line_requisition ON rfq_line(org_id, requisition_id);
ALTER TABLE rfq_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_line FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_line
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = """
UPDATE requisition_line l SET sourced_qty = GREATEST(0, l.sourced_qty - s.q), updated_at = now()
 FROM (SELECT requisition_line_id, sum(quantity) AS q FROM rfq_line GROUP BY 1) s
 WHERE l.id = s.requisition_line_id;
DROP TABLE rfq_line;
DROP TABLE rfq;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: RFQ drafts created since upgrade have no earlier home; down drops them
    # and returns the quantity they held to their requisition lines.
    connection.execute(DOWNGRADE_SQL)
