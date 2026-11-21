"""RFQ evaluation: criteria, evaluators, conflict declarations and sealed scores (E12-S07)."""

from typing import Protocol

revision = "20261022_0076"
down_revision = "20261022_0075"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE rfq_criterion (
 id uuid PRIMARY KEY,
 org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL,
 position int NOT NULL CHECK (position >= 1),
 name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
 weight int NOT NULL CHECK (weight BETWEEN 1 AND 100),
 UNIQUE (org_id, rfq_id, id),
 UNIQUE (org_id, rfq_id, position),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id)
);
ALTER TABLE rfq_criterion ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_criterion FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_criterion
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TABLE rfq_evaluator (
 org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL,
 identity_id uuid NOT NULL REFERENCES identity(id),
 added_by uuid NOT NULL REFERENCES identity(id),
 added_at timestamptz NOT NULL DEFAULT now(),
 version int NOT NULL DEFAULT 1,
 PRIMARY KEY (org_id, rfq_id, identity_id),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id)
);
ALTER TABLE rfq_evaluator ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_evaluator FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_evaluator
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TABLE rfq_evaluator_declaration (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL,
 identity_id uuid NOT NULL REFERENCES identity(id),
 conflict boolean NOT NULL,
 detail text CHECK (char_length(detail) <= 2000),
 declared_at timestamptz NOT NULL DEFAULT now(),
 CHECK (NOT conflict OR char_length(btrim(coalesce(detail, ''))) > 0),
 UNIQUE (org_id, rfq_id, identity_id, conflict),
 FOREIGN KEY (org_id, rfq_id) REFERENCES rfq(org_id, id)
);
ALTER TABLE rfq_evaluator_declaration ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_evaluator_declaration FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_evaluator_declaration
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE TRIGGER rfq_evaluator_declaration_append_only BEFORE UPDATE OR DELETE
 ON rfq_evaluator_declaration FOR EACH ROW EXECUTE FUNCTION raise_append_only();
CREATE TRIGGER rfq_evaluator_declaration_append_only_truncate BEFORE TRUNCATE
 ON rfq_evaluator_declaration FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();

CREATE TABLE rfq_score (
 org_id uuid NOT NULL REFERENCES organization(id),
 rfq_id uuid NOT NULL,
 identity_id uuid NOT NULL,
 vendor_id uuid NOT NULL,
 criterion_id uuid NOT NULL,
 score int NOT NULL CHECK (score BETWEEN 0 AND 10),
 comment text CHECK (char_length(comment) <= 2000),
 status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted')),
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (org_id, rfq_id, identity_id, vendor_id, criterion_id),
 FOREIGN KEY (org_id, rfq_id, identity_id)
  REFERENCES rfq_evaluator(org_id, rfq_id, identity_id),
 FOREIGN KEY (org_id, vendor_id) REFERENCES vendor(org_id, id),
 FOREIGN KEY (org_id, rfq_id, criterion_id) REFERENCES rfq_criterion(org_id, rfq_id, id)
);
ALTER TABLE rfq_score ENABLE ROW LEVEL SECURITY;
ALTER TABLE rfq_score FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON rfq_score
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE FUNCTION rfq_score_guard() RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
 IF OLD.status = 'submitted' THEN
  RAISE EXCEPTION 'a submitted score is immutable' USING ERRCODE = '55000';
 END IF;
 IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END
$guard$;
CREATE TRIGGER rfq_score_immutable BEFORE UPDATE OR DELETE ON rfq_score
 FOR EACH ROW EXECUTE FUNCTION rfq_score_guard();
CREATE TRIGGER rfq_score_append_only_truncate BEFORE TRUNCATE ON rfq_score
 FOR EACH STATEMENT EXECUTE FUNCTION raise_append_only();
"""

DOWNGRADE_SQL = """
DROP TABLE rfq_score;
DROP FUNCTION rfq_score_guard();
DROP TABLE rfq_evaluator_declaration;
DROP TABLE rfq_evaluator;
DROP TABLE rfq_criterion;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: criteria, evaluators, declarations and scores have no earlier home; down drops them.
    connection.execute(DOWNGRADE_SQL)
