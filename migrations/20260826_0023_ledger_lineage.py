"""Conversion lineage and commitment remaining, including reversed releases."""

from typing import Protocol

revision = "20260826_0023"
down_revision = "20260826_0022"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE ledger_entry ADD COLUMN converts_entry_id bigint REFERENCES ledger_entry(id);
CREATE VIEW commitment_remaining WITH (security_invoker = true) AS
SELECT r.id AS commitment_entry_id, r.org_id, r.amount AS committed_amount,
 COALESCE((SELECT SUM(l.amount) FROM ledger_entry l
  WHERE l.org_id = r.org_id AND (l.releases_entry_id = r.id OR l.reverses_entry_id IN
   (SELECT d.id FROM ledger_entry d WHERE d.org_id = r.org_id AND d.releases_entry_id = r.id))), 0) AS released_amount,
 CASE WHEN EXISTS (SELECT 1 FROM ledger_entry v
  WHERE v.org_id = r.org_id AND v.reverses_entry_id = r.id) THEN 0
 ELSE r.amount + COALESCE((SELECT SUM(l.amount) FROM ledger_entry l
  WHERE l.org_id = r.org_id AND (l.releases_entry_id = r.id OR l.reverses_entry_id IN
   (SELECT d.id FROM ledger_entry d WHERE d.org_id = r.org_id AND d.releases_entry_id = r.id))), 0) END AS remaining
FROM ledger_entry r WHERE r.entry_type = 'commitment';
CREATE VIEW ledger_lineage WITH (security_invoker = true) AS
WITH RECURSIVE chain AS (
 SELECT id AS entry_id, org_id, project_id, source_type, source_id, id AS chain_root_id,
  0 AS depth, COALESCE(reverses_entry_id, releases_entry_id, converts_entry_id) AS parent_id,
  ARRAY[id] AS visited
 FROM ledger_entry
 UNION ALL
 SELECT c.entry_id, c.org_id, c.project_id, p.source_type, p.source_id, p.id,
  c.depth + 1, COALESCE(p.reverses_entry_id, p.releases_entry_id, p.converts_entry_id),
  c.visited || p.id
 FROM chain c JOIN ledger_entry p ON p.id = c.parent_id AND p.org_id = c.org_id
 WHERE NOT p.id = ANY(c.visited)
)
SELECT entry_id, org_id, project_id, source_type, source_id, chain_root_id, depth,
 NULL::uuid AS approval_instance_id FROM chain;
"""
DOWNGRADE_SQL = """
DROP VIEW ledger_lineage;
DROP VIEW commitment_remaining;
ALTER TABLE ledger_entry DROP COLUMN converts_entry_id;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
