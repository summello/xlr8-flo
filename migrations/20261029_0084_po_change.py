"""Numbered change orders, PO revisions and the issued-PO immutability triggers (E13-S06)."""

from flo.kernel.migrate import MigrationConnection

revision = "20261029_0084"
down_revision = "20261029_0083"

# `purchase_order_immutable` freezes every column of an issued or superseded row except: the
# lifecycle transitions `issued -> superseded | cancelled | closed` (status changes, version + 1),
# and, once, the `issued_at` / `issued_by` stamp written right after the issue transition.
# Nothing leaves `superseded`. Later stories that write other columns after issue (PDF,
# transmission) name them in the column list here.
UPGRADE_SQL = """
ALTER TABLE purchase_order ADD COLUMN revision int NOT NULL DEFAULT 0;
ALTER TABLE purchase_order ADD COLUMN supersedes_id uuid;
ALTER TABLE purchase_order ADD FOREIGN KEY (org_id, supersedes_id)
 REFERENCES purchase_order(org_id, id);
CREATE UNIQUE INDEX purchase_order_one_successor ON purchase_order(org_id, supersedes_id)
 WHERE supersedes_id IS NOT NULL;
ALTER TABLE purchase_order DROP CONSTRAINT purchase_order_org_id_unit_id_number_key;
ALTER TABLE purchase_order ADD CONSTRAINT purchase_order_number_revision_key
 UNIQUE (org_id, unit_id, number, revision);

CREATE TABLE po_change (
 org_id uuid NOT NULL REFERENCES organization(id),
 id uuid PRIMARY KEY,
 po_id uuid NOT NULL,
 root_po_id uuid NOT NULL,
 number int NOT NULL CHECK (number >= 1),
 version int NOT NULL DEFAULT 1,
 from_version int NOT NULL,
 draft jsonb NOT NULL DEFAULT '{}',
 diff jsonb NOT NULL DEFAULT '[]',
 budget_effect jsonb NOT NULL DEFAULT '[]',
 material boolean NOT NULL DEFAULT false,
 total NUMERIC(18,4) NOT NULL DEFAULT 0,
 budget_total_project NUMERIC(18,4) NOT NULL DEFAULT 0,
 status text NOT NULL DEFAULT 'draft'
  CHECK (status IN ('draft','approval_pending','approved','rejected','withdrawn')),
 reason text,
 created_by uuid NOT NULL REFERENCES identity(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 result_po_id uuid,
 UNIQUE (org_id, id), UNIQUE (org_id, root_po_id, number),
 FOREIGN KEY (org_id, po_id) REFERENCES purchase_order(org_id, id),
 FOREIGN KEY (org_id, root_po_id) REFERENCES purchase_order(org_id, id),
 FOREIGN KEY (org_id, result_po_id) REFERENCES purchase_order(org_id, id)
);
CREATE UNIQUE INDEX po_change_one_open ON po_change(org_id, root_po_id)
 WHERE status IN ('draft','approval_pending');
ALTER TABLE po_change ENABLE ROW LEVEL SECURITY;
ALTER TABLE po_change FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON po_change
 USING (org_id = current_setting('app.org_id')::uuid)
 WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE FUNCTION purchase_order_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.status NOT IN ('issued','superseded') THEN
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
 END IF;
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'issued_is_immutable: an issued purchase order cannot be deleted'
   USING ERRCODE = 'check_violation';
 END IF;
 IF (to_jsonb(NEW) - ARRAY['status','version','issued_at','issued_by'])
  IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','version','issued_at','issued_by']) THEN
  RAISE EXCEPTION 'issued_is_immutable: an issued purchase order cannot be edited'
   USING ERRCODE = 'check_violation';
 END IF;
 IF NEW.status = OLD.status THEN
  -- no transition: the version stays, and the issue stamp is written once, whole: both columns
  -- empty before, both set after (a partial, clearing or repeated write is refused)
  IF NEW.version <> OLD.version
   OR ((NEW.issued_at IS DISTINCT FROM OLD.issued_at OR NEW.issued_by IS DISTINCT FROM OLD.issued_by)
    AND NOT (OLD.issued_at IS NULL AND OLD.issued_by IS NULL
     AND NEW.issued_at IS NOT NULL AND NEW.issued_by IS NOT NULL))
  THEN
   RAISE EXCEPTION 'issued_is_immutable: an issued purchase order cannot be edited'
    USING ERRCODE = 'check_violation';
  END IF;
 ELSIF OLD.status <> 'issued' OR NEW.status NOT IN ('superseded','cancelled','closed')
  OR NEW.version <> OLD.version + 1
  OR NEW.issued_at IS DISTINCT FROM OLD.issued_at OR NEW.issued_by IS DISTINCT FROM OLD.issued_by
 THEN
  RAISE EXCEPTION 'issued_is_immutable: that status change is not a lifecycle transition'
   USING ERRCODE = 'check_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER purchase_order_immutable BEFORE UPDATE OR DELETE ON purchase_order
 FOR EACH ROW EXECUTE FUNCTION purchase_order_immutable();

CREATE FUNCTION po_line_parent_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE frozen boolean;
BEGIN
 SELECT EXISTS (SELECT 1 FROM purchase_order p
  WHERE p.org_id = COALESCE(NEW.org_id, OLD.org_id) AND p.status IN ('issued','superseded')
   AND p.id IN (CASE WHEN TG_OP <> 'INSERT' THEN OLD.po_id END,
                CASE WHEN TG_OP <> 'DELETE' THEN NEW.po_id END)) INTO frozen;
 IF frozen THEN
  RAISE EXCEPTION 'issued_is_immutable: the lines of an issued purchase order cannot change'
   USING ERRCODE = 'check_violation';
 END IF;
 RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER po_line_parent_immutable BEFORE INSERT OR UPDATE OR DELETE ON po_line
 FOR EACH ROW EXECUTE FUNCTION po_line_parent_immutable();

ALTER TABLE workflow_definition DROP CONSTRAINT workflow_definition_document_type_check;
ALTER TABLE workflow_definition ADD CONSTRAINT workflow_definition_document_type_check
 CHECK (document_type IN ('project','requisition','project_request','budget_change','vendor',
  'rfq','award','po','po_change'));
"""

DOWNGRADE_SQL = """
ALTER TABLE workflow_definition DROP CONSTRAINT workflow_definition_document_type_check;
ALTER TABLE workflow_definition ADD CONSTRAINT workflow_definition_document_type_check
 CHECK (document_type IN ('project','requisition','project_request','budget_change','vendor',
  'rfq','award','po'));
DROP TRIGGER po_line_parent_immutable ON po_line;
DROP FUNCTION po_line_parent_immutable();
DROP TRIGGER purchase_order_immutable ON purchase_order;
DROP FUNCTION purchase_order_immutable();
DROP TABLE po_change;
ALTER TABLE purchase_order DROP CONSTRAINT purchase_order_number_revision_key;
ALTER TABLE purchase_order ADD UNIQUE (org_id, unit_id, number);
DROP INDEX purchase_order_one_successor;
ALTER TABLE purchase_order DROP COLUMN supersedes_id;
ALTER TABLE purchase_order DROP COLUMN revision;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: refuses (CHECK / unique violation) while a `po_change` workflow or a second
    # revision of a purchase order exists; every other row survives untouched.
    connection.execute(DOWNGRADE_SQL)
