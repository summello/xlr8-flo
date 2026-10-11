"""Bounded, immutable project hierarchy."""

from typing import Protocol

revision = "20260826_0018"
down_revision = "20260826_0017"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE project ADD COLUMN depth smallint NOT NULL DEFAULT 1
 CHECK (depth BETWEEN 1 AND 5), ADD COLUMN root_id uuid;
WITH RECURSIVE tree(id, org_id, depth, root_id) AS (
 SELECT id, org_id, 1, id FROM project WHERE parent_id IS NULL
 UNION
 SELECT child.id, child.org_id, tree.depth + 1, tree.root_id
 FROM project child JOIN tree ON child.parent_id = tree.id AND child.org_id = tree.org_id
 WHERE tree.depth < 6
)
UPDATE project SET depth = tree.depth, root_id = tree.root_id
 FROM tree WHERE project.id = tree.id AND project.org_id = tree.org_id;
CREATE INDEX project_children ON project(org_id, parent_id, number, id);
CREATE FUNCTION enforce_project_hierarchy() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_depth smallint; parent_root uuid;
BEGIN
 IF TG_OP = 'UPDATE' THEN
  IF NEW.parent_id IS DISTINCT FROM OLD.parent_id OR NEW.org_id IS DISTINCT FROM OLD.org_id THEN
   RAISE EXCEPTION 'project hierarchy is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
 END IF;
 IF NEW.parent_id IS NULL THEN
  NEW.depth := 1; NEW.root_id := NEW.id;
 ELSE
  SELECT depth, root_id INTO parent_depth, parent_root FROM project
   WHERE id = NEW.parent_id AND org_id = NEW.org_id;
  IF NOT FOUND THEN
   RAISE EXCEPTION 'parent not found in organization' USING ERRCODE = '23514';
  END IF;
  IF parent_depth >= 5 THEN
   RAISE EXCEPTION 'project depth exceeded' USING ERRCODE = '23514';
  END IF;
  NEW.depth := parent_depth + 1; NEW.root_id := parent_root;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER project_hierarchy BEFORE INSERT OR UPDATE OF parent_id, org_id ON project
 FOR EACH ROW EXECUTE FUNCTION enforce_project_hierarchy();
"""
DOWNGRADE_SQL = """
DROP TRIGGER project_hierarchy ON project;
DROP FUNCTION enforce_project_hierarchy();
DROP INDEX project_children;
ALTER TABLE project DROP COLUMN root_id, DROP COLUMN depth;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
