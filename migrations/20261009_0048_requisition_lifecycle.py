"""Requisition funding rounds and immutable status history."""

from typing import Protocol

revision = "20261009_0048"
down_revision = "20261009_0047"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE requisition ADD COLUMN reservation_entry_id bigint REFERENCES ledger_entry(id),
 ADD COLUMN funding_round int NOT NULL DEFAULT 0;
CREATE TABLE requisition_status_event (
 id bigserial PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 requisition_id uuid NOT NULL, from_status text NOT NULL, to_status text NOT NULL,
 actor_id uuid, reason text, funding_round int NOT NULL, ledger_entry_id bigint REFERENCES ledger_entry(id),
 decision_id uuid, at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(org_id,requisition_id) REFERENCES requisition(org_id,id)
);
CREATE INDEX requisition_status_event_history ON requisition_status_event(org_id,requisition_id,id);
ALTER TABLE requisition_status_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE requisition_status_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON requisition_status_event
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
CREATE TRIGGER requisition_status_event_append_only BEFORE UPDATE OR DELETE ON requisition_status_event
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER requisition_status_event_append_only_truncate BEFORE TRUNCATE ON requisition_status_event
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
"""
DOWNGRADE_SQL = """
DROP TABLE requisition_status_event; ALTER TABLE requisition DROP COLUMN reservation_entry_id, DROP COLUMN funding_round;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
