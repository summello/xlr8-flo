"""Transactional project ledger rollup."""

from typing import Protocol

revision = "20260826_0020"
down_revision = "20260826_0019"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE project_balance (
 project_id uuid PRIMARY KEY, org_id uuid NOT NULL, bu_id uuid NOT NULL,
 currency char(3) NOT NULL,
 allocated numeric(18,4) NOT NULL DEFAULT 0,
 reserved numeric(18,4) NOT NULL DEFAULT 0,
 committed numeric(18,4) NOT NULL DEFAULT 0,
 actual numeric(18,4) NOT NULL DEFAULT 0,
 available numeric(18,4) GENERATED ALWAYS AS
 (allocated - reserved - committed - actual) STORED,
 version bigint NOT NULL DEFAULT 0,
 updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id, project_id, bu_id) REFERENCES project(org_id, id, bu_id),
 CONSTRAINT project_balance_nonnegative_buckets CHECK
 (reserved >= 0 AND committed >= 0 AND actual >= 0)
);
INSERT INTO project_balance
 (project_id, org_id, bu_id, currency, allocated, reserved, committed, actual)
SELECT p.id, p.org_id, p.bu_id, p.currency,
 COALESCE(SUM(l.amount) FILTER (WHERE l.bucket = 'allocated'), 0),
 COALESCE(SUM(l.amount) FILTER (WHERE l.bucket = 'reserved'), 0),
 COALESCE(SUM(l.amount) FILTER (WHERE l.bucket = 'committed'), 0),
 COALESCE(SUM(l.amount) FILTER (WHERE l.bucket = 'actual'), 0)
FROM project p LEFT JOIN ledger_entry l ON l.project_id = p.id AND l.org_id = p.org_id
GROUP BY p.id;
ALTER TABLE project_balance ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_balance FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON project_balance
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = "DROP TABLE project_balance;"


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
