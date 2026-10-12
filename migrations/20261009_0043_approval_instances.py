"""Tenant approval execution with immutable pins and one running instance."""
from typing import Protocol

revision = "20261009_0043"
down_revision = "20261009_0042"

class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...

UPGRADE_SQL = """
CREATE TABLE approval_instance (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 document_type text NOT NULL, document_id uuid NOT NULL,
 workflow_version_id uuid NOT NULL REFERENCES workflow_version(id),
 document_version int NOT NULL, material_digest text NOT NULL,
 requester_id uuid NOT NULL REFERENCES identity(id),
 status text NOT NULL CHECK(status IN ('running','approved','rejected','returned','withdrawn','superseded')),
 current_node_id text, route jsonb NOT NULL DEFAULT '[]',
 started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(org_id,id)
);
CREATE UNIQUE INDEX approval_one_running ON approval_instance(org_id,document_type,document_id)
 WHERE status='running';
CREATE TABLE approval_task (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 instance_id uuid NOT NULL REFERENCES approval_instance(id), node_id text NOT NULL,
 name text NOT NULL, approver_rule jsonb NOT NULL, mode jsonb NOT NULL,
 allow_repeat boolean NOT NULL, status text NOT NULL CHECK(status IN ('open','completed','cancelled')),
 outcome text, created_at timestamptz NOT NULL DEFAULT now(), due_at timestamptz, completed_at timestamptz,
 UNIQUE(instance_id,node_id), FOREIGN KEY(org_id,instance_id) REFERENCES approval_instance(org_id,id)
);
CREATE FUNCTION approval_instance_pin_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (NEW.workflow_version_id,NEW.document_version,NEW.requester_id,NEW.document_type,NEW.document_id,NEW.org_id)
 IS DISTINCT FROM
 (OLD.workflow_version_id,OLD.document_version,OLD.requester_id,OLD.document_type,OLD.document_id,OLD.org_id)
 THEN RAISE EXCEPTION 'approval instance pin is immutable' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER approval_instance_pin_guard BEFORE UPDATE ON approval_instance
 FOR EACH ROW EXECUTE FUNCTION approval_instance_pin_guard();
ALTER TABLE approval_instance ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_instance FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON approval_instance
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
ALTER TABLE approval_task ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_task FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON approval_task
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
"""
DOWNGRADE_SQL = """
DROP TABLE approval_task, approval_instance;
DROP FUNCTION approval_instance_pin_guard();
"""

def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)

def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
