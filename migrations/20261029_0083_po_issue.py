"""Issued quantity on requisition lines and the issue stamp on purchase orders (E13-S05)."""

from flo.kernel.migrate import MigrationConnection

revision = "20261029_0083"
down_revision = "20261028_0082"

UPGRADE_SQL = """
ALTER TABLE requisition_line ADD COLUMN issued_qty NUMERIC(18,4) NOT NULL DEFAULT 0;
ALTER TABLE requisition_line ADD CONSTRAINT line_issued_nonneg CHECK (issued_qty >= 0);
ALTER TABLE requisition_line ADD CONSTRAINT line_issued_ceiling CHECK (issued_qty <= ordered_qty);
ALTER TABLE purchase_order ADD COLUMN issued_at timestamptz;
ALTER TABLE purchase_order ADD COLUMN issued_by uuid REFERENCES identity(id);
"""

DOWNGRADE_SQL = """
ALTER TABLE purchase_order DROP COLUMN issued_by;
ALTER TABLE purchase_order DROP COLUMN issued_at;
ALTER TABLE requisition_line DROP CONSTRAINT line_issued_ceiling;
ALTER TABLE requisition_line DROP CONSTRAINT line_issued_nonneg;
ALTER TABLE requisition_line DROP COLUMN issued_qty;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
