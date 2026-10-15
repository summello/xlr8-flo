"""Immutable, tenant-scoped approval attribution and rationale."""
from typing import Protocol

revision = "20261009_0044"
down_revision = "20261009_0043"

class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...

UPGRADE_SQL = """
CREATE TABLE approval_decision (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 instance_id uuid NOT NULL, task_id uuid NULL,
 actor_id uuid NOT NULL, on_behalf_of uuid NULL,
 authority text NOT NULL CHECK(authority IN ('member','delegate','admin','requester')),
 outcome text NOT NULL CHECK(outcome IN ('approve','reject','return','comment','withdraw')),
 reject_mode text NULL, comment text NULL, document_version int NOT NULL,
 members_at_decision jsonb NOT NULL,
 decided_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(org_id,instance_id) REFERENCES approval_instance(org_id,id)
);
CREATE INDEX approval_decision_trail ON approval_decision(org_id,instance_id,decided_at,id);
CREATE UNIQUE INDEX approval_decision_actor_task ON approval_decision(org_id,task_id,actor_id)
 WHERE outcome IN ('approve','reject','return');
CREATE TRIGGER approval_decision_append_only BEFORE UPDATE OR DELETE ON approval_decision
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER approval_decision_append_only_truncate BEFORE TRUNCATE ON approval_decision
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
ALTER TABLE approval_decision ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_decision FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON approval_decision
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
"""
DOWNGRADE_SQL = "DROP TABLE approval_decision;"

def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)

def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
