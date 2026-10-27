"""Awards: split by line and quantity, with justification and the evaluation record (E12-S08)."""

from typing import Protocol

revision = "20261022_0077"
down_revision = "20261022_0076"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE award (
 id uuid PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 number text NOT NULL,
 rfq_id uuid NOT NULL,
 basis text NOT NULL CHECK (basis IN ('lowest_cost_supplier','best_average_cost','manual')),
 status text NOT NULL DEFAULT 'draft'
  CHECK (status IN ('draft','approval_pending','approved','superseded','cancelled')),
 rationale text CHECK (char_length(rationale) <= 2000),
 rates jsonb NOT NULL,
 evaluator_results jsonb,
 exceptions jsonb,
 created_by uuid NOT NULL REFERENCES identity(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 submitted_at timestamptz,
 approved_at timestamptz,
 version int NOT NULL DEFAULT 1,
 UNIQUE (org_id, number), UNIQUE (org_id, id),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id)
);
CREATE UNIQUE INDEX award_one_live ON award(rfq_id)
 WHERE status IN ('draft','approval_pending','approved');
ALTER TABLE award ENABLE ROW LEVEL SECURITY;
ALTER TABLE award FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON award
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TABLE award_line (
 org_id uuid NOT NULL REFERENCES organization(id),
 award_id uuid NOT NULL,
 rfq_line_id uuid NOT NULL REFERENCES rfq_line(id),
 vendor_id uuid NOT NULL,
 bid_id uuid NOT NULL,
 quantity NUMERIC(18,4) NOT NULL CHECK (quantity > 0),
 unit_price NUMERIC(18,4) NOT NULL,
 currency char(3) NOT NULL,
 amount_vendor NUMERIC(18,4) NOT NULL CHECK (amount_vendor >= 0),
 amount_rfq NUMERIC(18,4) NOT NULL CHECK (amount_rfq >= 0),
 not_lowest boolean NOT NULL DEFAULT false,
 justification text CHECK (char_length(justification) BETWEEN 1 AND 500),
 tie_override_reason text CHECK (char_length(tie_override_reason) BETWEEN 1 AND 500),
 PRIMARY KEY (award_id, rfq_line_id, vendor_id),
 FOREIGN KEY (org_id, award_id) REFERENCES award(org_id, id),
 FOREIGN KEY (bid_id, rfq_line_id) REFERENCES bid_line(bid_id, rfq_line_id)
);
ALTER TABLE award_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE award_line FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON award_line
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = """
DROP TABLE award_line;
DROP TABLE award;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: awards and their lines have no earlier home; down drops them.
    connection.execute(DOWNGRADE_SQL)
