"""Link releases to their original entries and expose reservation remaining funds."""

from typing import Protocol

revision = "20260826_0022"
down_revision = "20260826_0021"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM ledger_entry WHERE entry_type = 'release') THEN
  RAISE EXCEPTION 'Cannot migrate existing unlinked releases';
 END IF;
END $$;
ALTER TABLE ledger_entry ADD COLUMN releases_entry_id bigint REFERENCES ledger_entry(id);
ALTER TABLE ledger_entry ADD CONSTRAINT ledger_release_link CHECK
 ((entry_type = 'release') = (releases_entry_id IS NOT NULL)) NOT VALID;
ALTER TABLE ledger_entry VALIDATE CONSTRAINT ledger_release_link;
CREATE INDEX ledger_releases_entry_idx ON ledger_entry(releases_entry_id);
CREATE VIEW reservation_remaining WITH (security_invoker = true) AS
SELECT r.id AS reservation_entry_id, r.org_id, r.amount AS reserved_amount,
 COALESCE((SELECT SUM(l.amount) FROM ledger_entry l
  WHERE l.org_id = r.org_id AND (l.releases_entry_id = r.id OR l.reverses_entry_id IN
   (SELECT d.id FROM ledger_entry d WHERE d.org_id = r.org_id AND d.releases_entry_id = r.id))), 0) AS released_amount,
 CASE WHEN EXISTS (SELECT 1 FROM ledger_entry v
  WHERE v.org_id = r.org_id AND v.reverses_entry_id = r.id) THEN 0
 ELSE r.amount + COALESCE((SELECT SUM(l.amount) FROM ledger_entry l
  WHERE l.org_id = r.org_id AND (l.releases_entry_id = r.id OR l.reverses_entry_id IN
   (SELECT d.id FROM ledger_entry d WHERE d.org_id = r.org_id AND d.releases_entry_id = r.id))), 0) END AS remaining
FROM ledger_entry r WHERE r.entry_type = 'reservation';
"""
DOWNGRADE_SQL = """
DROP VIEW reservation_remaining;
DROP INDEX ledger_releases_entry_idx;
ALTER TABLE ledger_entry DROP CONSTRAINT ledger_release_link;
ALTER TABLE ledger_entry DROP COLUMN releases_entry_id;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
