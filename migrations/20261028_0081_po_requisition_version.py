"""The requisition version a PO was built on, checked again at submit (E13-S04)."""

from flo.kernel.migrate import MigrationConnection

revision = "20261028_0081"
down_revision = "20261028_0080"

# Existing rows take the requisition's current version (1 when the requisition is gone), the
# closest honest value: none of these orders has been submitted, so no check has used it yet.
UPGRADE_SQL = """
ALTER TABLE purchase_order ADD COLUMN requisition_version int;
DO $$
DECLARE org uuid;
BEGIN
 FOR org IN SELECT id FROM organization LOOP
  PERFORM set_config('app.org_id', org::text, true);
  UPDATE purchase_order p SET requisition_version = COALESCE(
   (SELECT r.version FROM requisition r WHERE r.org_id = p.org_id AND r.id = p.requisition_id), 1)
  WHERE p.org_id = org;
 END LOOP;
END $$;
ALTER TABLE purchase_order ALTER COLUMN requisition_version SET NOT NULL;
"""

DOWNGRADE_SQL = "ALTER TABLE purchase_order DROP COLUMN requisition_version;"


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
