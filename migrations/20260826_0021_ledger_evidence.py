"""Append-only supporting evidence for authorized manual ledger actions."""

from typing import Protocol

revision = "20260826_0021"
down_revision = "20260826_0020"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE ledger_entry ADD CONSTRAINT ledger_entry_org_id_id UNIQUE (org_id, id);
CREATE TABLE ledger_evidence (
 entry_id bigint PRIMARY KEY REFERENCES ledger_entry(id), org_id uuid NOT NULL,
 ref text NOT NULL CHECK (length(btrim(ref)) > 0),
 FOREIGN KEY (org_id, entry_id) REFERENCES ledger_entry(org_id, id)
);
ALTER TABLE ledger_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ledger_evidence
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TRIGGER ledger_evidence_append_only BEFORE UPDATE OR DELETE ON ledger_evidence
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER ledger_evidence_append_only_truncate BEFORE TRUNCATE ON ledger_evidence
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
"""

DOWNGRADE_SQL = """
DROP TABLE ledger_evidence;
ALTER TABLE ledger_entry DROP CONSTRAINT ledger_entry_org_id_id;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
