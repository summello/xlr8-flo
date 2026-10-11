"""Stable tenant-local project import identities."""

from typing import Protocol

revision = "20261009_0036"
down_revision = "20261009_0035"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE import_result ALTER COLUMN record_id TYPE text USING record_id::text;
ALTER TABLE project ADD COLUMN external_ref text
 CHECK (external_ref IS NULL OR external_ref ~ '^[A-Za-z0-9._:-]{1,64}$');
CREATE UNIQUE INDEX project_external_ref ON project(org_id, external_ref)
 WHERE external_ref IS NOT NULL;
CREATE FUNCTION project_external_ref_locked() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.external_ref IS NOT NULL AND NEW.external_ref IS DISTINCT FROM OLD.external_ref THEN
  RAISE EXCEPTION 'external_ref_locked' USING ERRCODE = '23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER project_external_ref_locked BEFORE UPDATE ON project
 FOR EACH ROW EXECUTE FUNCTION project_external_ref_locked();
"""

# irreversible: import_result rows naming integer ledger ids cannot be represented as uuid
DOWNGRADE_SQL = """
DELETE FROM import_result WHERE record_id !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
ALTER TABLE import_result ALTER COLUMN record_id TYPE uuid USING record_id::uuid;
DROP TRIGGER project_external_ref_locked ON project;
DROP FUNCTION project_external_ref_locked();
DROP INDEX project_external_ref;
ALTER TABLE project DROP COLUMN external_ref;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
