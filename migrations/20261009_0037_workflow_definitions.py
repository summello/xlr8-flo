"""Tenant workflow definitions and immutable published routing graphs."""

from typing import Protocol

revision = "20261009_0037"
down_revision = "20261009_0036"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE workflow_definition (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 document_type text NOT NULL CHECK (document_type IN
 ('project','requisition','budget_change','vendor','rfq','award','po')),
 name text NOT NULL, scope_type text NOT NULL CHECK (scope_type IN ('org','bu')),
 scope_id uuid NOT NULL, created_by uuid NOT NULL REFERENCES identity(id),
 created_at timestamptz NOT NULL DEFAULT now(), retired_at timestamptz,
 CHECK (scope_type <> 'org' OR scope_id = org_id), UNIQUE(org_id,id)
);
CREATE UNIQUE INDEX workflow_live_scope ON workflow_definition
 (org_id,document_type,scope_type,scope_id) WHERE retired_at IS NULL;
CREATE TABLE workflow_version (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 definition_id uuid NOT NULL REFERENCES workflow_definition(id),
 version int NOT NULL CHECK (version >= 1),
 status text NOT NULL CHECK (status IN ('draft','published','superseded')),
 graph jsonb NOT NULL, row_version int NOT NULL DEFAULT 1 CHECK (row_version >= 1),
 created_by uuid NOT NULL REFERENCES identity(id), created_at timestamptz NOT NULL DEFAULT now(),
 published_by uuid REFERENCES identity(id), published_at timestamptz,
 FOREIGN KEY(org_id,definition_id) REFERENCES workflow_definition(org_id,id),
 UNIQUE(definition_id,version)
);
CREATE UNIQUE INDEX workflow_one_published ON workflow_version(definition_id)
 WHERE status = 'published';
CREATE UNIQUE INDEX workflow_one_draft ON workflow_version(definition_id) WHERE status = 'draft';
CREATE FUNCTION workflow_version_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
  IF OLD.status = 'draft' THEN RETURN OLD; END IF;
 ELSIF OLD.status = 'draft' THEN
  IF NEW.row_version > OLD.row_version THEN RETURN NEW; END IF;
 ELSIF OLD.status = 'published' AND NEW.status = 'superseded'
  AND (to_jsonb(NEW) - 'status') = (to_jsonb(OLD) - 'status') THEN
  RETURN NEW;
 END IF;
 RAISE EXCEPTION 'workflow version is immutable' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER workflow_version_immutable BEFORE UPDATE OR DELETE ON workflow_version
 FOR EACH ROW EXECUTE FUNCTION workflow_version_immutable();
ALTER TABLE workflow_definition ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_definition FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workflow_definition
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
ALTER TABLE workflow_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_version FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workflow_version
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
"""
DOWNGRADE_SQL = """
DROP TABLE workflow_version;
DROP FUNCTION workflow_version_immutable();
DROP TABLE workflow_definition;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
