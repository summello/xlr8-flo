"""Append-only vendor performance scores and disputes (E11-S07, VEN-007, VEN-015)."""

from typing import Protocol

revision = "20261014_0061"
down_revision = "20261014_0060"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE vendor_score (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 vendor_id uuid NOT NULL, category_code text NOT NULL,
 score int NOT NULL CHECK (score BETWEEN 1 AND 5),
 period date NOT NULL CHECK (period = date_trunc('month', period)::date),
 source_type text NOT NULL CHECK (source_type IN ('po','manual')),
 source_ref text NOT NULL CHECK (char_length(source_ref) BETWEEN 1 AND 200),
 rationale text NOT NULL CHECK (char_length(rationale) BETWEEN 1 AND 1000),
 scored_by uuid NOT NULL REFERENCES identity(id),
 scored_at timestamptz NOT NULL DEFAULT now(),
 supersedes uuid,
 UNIQUE (org_id, id),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id),
 FOREIGN KEY (org_id, supersedes) REFERENCES vendor_score(org_id, id)
);
-- One adjustment per original.
CREATE UNIQUE INDEX vendor_score_one_adjustment ON vendor_score(org_id, supersedes)
 WHERE supersedes IS NOT NULL;
CREATE INDEX vendor_score_window ON vendor_score(org_id, vendor_id, period DESC, id DESC);
CREATE TRIGGER vendor_score_append_only BEFORE UPDATE OR DELETE ON vendor_score
 FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER vendor_score_append_only_truncate BEFORE TRUNCATE ON vendor_score
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
ALTER TABLE vendor_score ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_score FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON vendor_score
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);

CREATE TABLE vendor_score_dispute (
 id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES organization(id),
 score_id uuid NOT NULL,
 raised_by uuid NOT NULL REFERENCES identity(id),
 raised_at timestamptz NOT NULL DEFAULT now(),
 reason text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 1000),
 resolution text CHECK (resolution IN ('upheld','dismissed')),
 resolved_by uuid REFERENCES identity(id), resolved_at timestamptz,
 resolution_note text CHECK (char_length(resolution_note) BETWEEN 1 AND 1000),
 CHECK ((resolution IS NULL) = (resolved_by IS NULL)
  AND (resolution IS NULL) = (resolved_at IS NULL)
  AND (resolution IS NULL) = (resolution_note IS NULL)),
 FOREIGN KEY (org_id, score_id) REFERENCES vendor_score(org_id, id)
);
CREATE UNIQUE INDEX vendor_score_dispute_one_open ON vendor_score_dispute(org_id, score_id)
 WHERE resolution IS NULL;
-- The one UPDATE: resolve an open dispute, every other column untouched. Nothing is deleted.
CREATE FUNCTION vendor_score_dispute_guard() RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
 IF TG_OP = 'UPDATE' AND OLD.resolution IS NULL AND NEW.resolution IS NOT NULL
  AND to_jsonb(OLD) - 'resolution' - 'resolved_by' - 'resolved_at' - 'resolution_note'
    = to_jsonb(NEW) - 'resolution' - 'resolved_by' - 'resolved_at' - 'resolution_note' THEN
  RETURN NEW;
 END IF;
 RAISE EXCEPTION 'vendor_score_dispute is append-only: % is forbidden', TG_OP
  USING ERRCODE = '55000';
END
$guard$;
CREATE TRIGGER vendor_score_dispute_append_only BEFORE UPDATE OR DELETE ON vendor_score_dispute
 FOR EACH ROW EXECUTE FUNCTION vendor_score_dispute_guard();
CREATE TRIGGER vendor_score_dispute_append_only_truncate BEFORE TRUNCATE ON vendor_score_dispute
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
ALTER TABLE vendor_score_dispute ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_score_dispute FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON vendor_score_dispute
 USING(org_id=current_setting('app.org_id')::uuid) WITH CHECK(org_id=current_setting('app.org_id')::uuid);
"""

DOWNGRADE_SQL = """
DROP TABLE vendor_score_dispute;
DROP TABLE vendor_score;
DROP FUNCTION vendor_score_dispute_guard();
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: scores and disputes recorded since upgrade are dropped.
    connection.execute(DOWNGRADE_SQL)
