"""Organization, unit tree and replacing setting overrides."""

from typing import Protocol

revision = "20260826_0013"
down_revision = "20260825_0012"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE organization (
 id uuid PRIMARY KEY, org_id uuid GENERATED ALWAYS AS (id) STORED,
 name text NOT NULL, base_currency char(3),
 created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO organization (id, name)
 SELECT org_id, 'Organization' FROM (
 SELECT org_id FROM role UNION SELECT org_id FROM user_role
 UNION SELECT org_id FROM authorization_scope) AS tenants;
CREATE TABLE org_unit (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 parent_id uuid, code text NOT NULL, name text NOT NULL,
 kind text NOT NULL CHECK (kind IN ('bu','ou')),
 active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (org_id, code), UNIQUE (org_id, id),
 FOREIGN KEY (org_id, parent_id) REFERENCES org_unit(org_id, id)
);
CREATE TABLE org_setting (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 unit_id uuid, key text NOT NULL, value jsonb NOT NULL,
 updated_by uuid NOT NULL REFERENCES identity(id),
 updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (org_id, unit_id) REFERENCES org_unit(org_id, id),
 UNIQUE NULLS NOT DISTINCT (org_id, unit_id, key)
);
ALTER TABLE organization ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON organization
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE org_unit ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_unit FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON org_unit
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE org_setting ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_setting FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON org_setting
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
INSERT INTO permission(code) VALUES
 ('org.unit.manage'), ('org.unit.read'), ('org.setting.manage');
"""
DOWNGRADE_SQL = """
DELETE FROM role_permission WHERE permission_code IN
 ('org.unit.manage', 'org.unit.read', 'org.setting.manage');
DELETE FROM permission WHERE code IN
 ('org.unit.manage', 'org.unit.read', 'org.setting.manage');
DROP TABLE org_setting;
DROP TABLE org_unit;
DROP TABLE organization;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
