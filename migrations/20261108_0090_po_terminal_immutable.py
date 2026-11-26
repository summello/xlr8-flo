"""An issued purchase order stays immutable once it is closed or cancelled (E13-S26)."""

from flo.kernel.migrate import MigrationConnection

revision = "20261108_0090"
down_revision = "20261102_0089"

# Replaces the two 0084 functions in place (the triggers keep pointing at them). A closed or
# cancelled status is terminal; an issued row only leaves for one of them stamped; a terminal
# row that was issued (`issued_at` set) keeps every 0084 protection. A PO cancelled before issue
# is unprotected as before.
UPGRADE_SQL = """
CREATE OR REPLACE FUNCTION purchase_order_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'UPDATE' AND OLD.status IN ('closed','cancelled')
  AND NEW.status IS DISTINCT FROM OLD.status THEN
  RAISE EXCEPTION 'issued_is_immutable: a closed or cancelled purchase order cannot change status'
   USING ERRCODE = 'check_violation';
 END IF;
 IF NOT (OLD.status IN ('issued','superseded')
  OR (OLD.status IN ('closed','cancelled') AND OLD.issued_at IS NOT NULL)) THEN
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
  OR (NEW.status IN ('closed','cancelled') AND OLD.issued_at IS NULL)
  OR NEW.version <> OLD.version + 1
  OR NEW.issued_at IS DISTINCT FROM OLD.issued_at OR NEW.issued_by IS DISTINCT FROM OLD.issued_by
 THEN
  RAISE EXCEPTION 'issued_is_immutable: that status change is not a lifecycle transition'
   USING ERRCODE = 'check_violation';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION po_line_parent_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE frozen boolean;
BEGIN
 SELECT EXISTS (SELECT 1 FROM purchase_order p
  WHERE p.org_id = COALESCE(NEW.org_id, OLD.org_id)
   AND (p.status IN ('issued','superseded')
    OR (p.status IN ('closed','cancelled') AND p.issued_at IS NOT NULL))
   AND p.id IN (CASE WHEN TG_OP <> 'INSERT' THEN OLD.po_id END,
                CASE WHEN TG_OP <> 'DELETE' THEN NEW.po_id END)) INTO frozen;
 IF frozen THEN
  RAISE EXCEPTION 'issued_is_immutable: the lines of an issued purchase order cannot change'
   USING ERRCODE = 'check_violation';
 END IF;
 RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
"""

DOWNGRADE_SQL = """
CREATE OR REPLACE FUNCTION purchase_order_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
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

CREATE OR REPLACE FUNCTION po_line_parent_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
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
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
