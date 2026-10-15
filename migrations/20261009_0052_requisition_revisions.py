"""Immutable requisition revisions and the pointer to a running amendment."""

from typing import Protocol

revision = "20261009_0052"
down_revision = "20261009_0051"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE requisition_revision (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 requisition_id uuid NOT NULL, revision_no int NOT NULL CHECK (revision_no > 0),
 kind text NOT NULL CHECK (kind IN ('submission','amendment')),
 status text NOT NULL CHECK (status IN ('draft','pending','applied','rejected','returned','withdrawn')),
 snapshot jsonb NOT NULL, material_digest char(64) NOT NULL,
 version int NOT NULL DEFAULT 1, created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), decided_at timestamptz,
 note text,
 FOREIGN KEY (org_id,requisition_id) REFERENCES requisition(org_id,id),
 UNIQUE (requisition_id, revision_no),
 UNIQUE (org_id, id)
);
CREATE UNIQUE INDEX requisition_revision_one_open ON requisition_revision (requisition_id)
 WHERE kind = 'amendment' AND status IN ('draft','pending');
ALTER TABLE requisition_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE requisition_revision FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON requisition_revision
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
CREATE FUNCTION requisition_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
  IF OLD.kind = 'amendment' AND OLD.status = 'draft' THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'requisition_revision rows are immutable' USING ERRCODE = '23000';
 END IF;
 IF OLD.status = 'draft' THEN RETURN NEW; END IF;
 IF OLD.status = 'pending' AND NEW.status IN ('applied','rejected','returned','withdrawn')
  AND (to_jsonb(NEW) - 'status' - 'decided_at') = (to_jsonb(OLD) - 'status' - 'decided_at')
 THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'requisition_revision rows are immutable' USING ERRCODE = '23000';
END $$;
CREATE TRIGGER requisition_revision_guard BEFORE UPDATE OR DELETE ON requisition_revision
 FOR EACH ROW EXECUTE FUNCTION requisition_revision_guard();
ALTER TABLE requisition ADD COLUMN pending_amendment_id uuid REFERENCES requisition_revision(id);
"""
DOWNGRADE_SQL = """
ALTER TABLE requisition DROP COLUMN pending_amendment_id;
DROP TABLE requisition_revision;
DROP FUNCTION requisition_revision_guard();
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
