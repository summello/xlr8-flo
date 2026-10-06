"""Root projects and independently committed document numbering."""

from typing import Protocol

revision = "20260826_0017"
down_revision = "20260826_0016"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE numbering_format (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 scope_id uuid NOT NULL, doc_type text NOT NULL
 CHECK (doc_type IN ('project','requisition','rfq','award','po','asset')),
 prefix text NOT NULL DEFAULT '', include_year boolean NOT NULL DEFAULT true,
 width smallint NOT NULL DEFAULT 4 CHECK (width BETWEEN 1 AND 9),
 effective_from date NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (org_id, scope_id, doc_type, effective_from)
);
ALTER TABLE numbering_format ENABLE ROW LEVEL SECURITY;
ALTER TABLE numbering_format FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON numbering_format
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TABLE numbering_counter (
 org_id uuid NOT NULL REFERENCES organization(id), scope_id uuid NOT NULL,
 doc_type text NOT NULL, year int NOT NULL, last_value bigint NOT NULL DEFAULT 0,
 PRIMARY KEY (org_id, scope_id, doc_type, year)
);
ALTER TABLE numbering_counter ENABLE ROW LEVEL SECURITY;
ALTER TABLE numbering_counter FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON numbering_counter
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TABLE project (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 bu_id uuid NOT NULL, parent_id uuid, number text NOT NULL, name text NOT NULL,
 description text, status text NOT NULL DEFAULT 'draft'
 CHECK (status IN ('draft','approval_pending','active','deferred','completed','abandoned')),
 owner_id uuid NOT NULL, sponsor_id uuid, department_code text NOT NULL,
 ledger_account_code text NOT NULL, currency char(3) NOT NULL,
 planned_start date, planned_end date,
 created_by uuid NOT NULL REFERENCES identity(id),
 created_at timestamptz NOT NULL DEFAULT now(), version bigint NOT NULL DEFAULT 1,
 UNIQUE (org_id, bu_id, number), UNIQUE (org_id, id), UNIQUE (org_id, id, bu_id),
 FOREIGN KEY (org_id, bu_id) REFERENCES org_unit(org_id, id),
 FOREIGN KEY (org_id, parent_id) REFERENCES project(org_id, id),
 CHECK (planned_end IS NULL OR planned_start IS NULL OR planned_end >= planned_start)
);
ALTER TABLE project ENABLE ROW LEVEL SECURITY;
ALTER TABLE project FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON project
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
-- project.create already belongs to the original RBAC catalog; preserve it on downgrade.
INSERT INTO permission(code) VALUES ('project.create'), ('project.read'), ('project.update')
 ON CONFLICT (code) DO NOTHING;
"""
DOWNGRADE_SQL = """
DELETE FROM authorization_scope WHERE scope_type = 'project'
 AND scope_id IN (SELECT id FROM project);
DELETE FROM role_permission WHERE permission_code IN ('project.read', 'project.update');
DELETE FROM permission WHERE code IN ('project.read', 'project.update');
DROP TABLE project;
DROP TABLE numbering_counter;
DROP TABLE numbering_format;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
