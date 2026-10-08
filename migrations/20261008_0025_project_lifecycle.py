"""Append-only project lifecycle history and transition permissions."""

from typing import Protocol

revision = "20261008_0025"
down_revision = "20261008_0024"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE project_status_event (
 id bigserial PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 project_id uuid NOT NULL, from_status text NOT NULL, to_status text NOT NULL,
 actor_id uuid NOT NULL REFERENCES identity(id), reason text,
 override bool NOT NULL DEFAULT false, blocked_by jsonb NOT NULL DEFAULT '{}',
 at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (org_id, project_id) REFERENCES project(org_id, id)
);
CREATE INDEX project_status_event_project_idx ON project_status_event(org_id, project_id, id);
ALTER TABLE project_status_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_status_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON project_status_event
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TRIGGER project_status_event_append_only BEFORE UPDATE OR DELETE ON project_status_event
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER project_status_event_append_only_truncate BEFORE TRUNCATE ON project_status_event
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
INSERT INTO permission(code) VALUES
 ('project.submit'), ('project.approve'), ('project.complete'), ('project.close.override')
 ON CONFLICT (code) DO NOTHING;
"""
# project.approve is part of the original RBAC catalog; preserve it and its grants.
DOWNGRADE_SQL = """
DROP TABLE project_status_event;
DELETE FROM role_permission WHERE permission_code IN
 ('project.submit','project.complete','project.close.override');
DELETE FROM permission WHERE code IN
 ('project.submit','project.complete','project.close.override');
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
