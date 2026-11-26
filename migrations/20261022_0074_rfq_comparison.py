"""The frozen exchange rates a bid comparison reads, one row per RFQ (E12-S06)."""

from typing import Protocol

revision = "20261022_0074"
down_revision = "20261022_0073"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE rfq_comparison (
 org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL,
 rate_date date NOT NULL,
 frozen_at timestamptz NOT NULL,
 rates jsonb NOT NULL,
 row_version int NOT NULL DEFAULT 1,
 PRIMARY KEY (org_id, rfq_id),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id)
);
ALTER TABLE rfq_comparison ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_comparison FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_comparison
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = """
DROP TABLE rfq_comparison;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: the frozen rates and their refresh history are dropped; the next read re-freezes.
    connection.execute(DOWNGRADE_SQL)
