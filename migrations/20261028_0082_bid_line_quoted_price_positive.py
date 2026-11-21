"""A quoted bid line prices above zero (E12-S23)."""

from flo.kernel.migrate import MigrationConnection

revision = "20261028_0082"
down_revision = "20261028_0081"

# bid_line is append-only, so no row is rewritten; a validated constraint aborts the upgrade if a
# quoted zero-price row exists (reset the development database).
UPGRADE_SQL = """
ALTER TABLE bid_line ADD CONSTRAINT bid_line_quoted_price_positive
 CHECK (NOT quoted OR unit_price > 0);
"""

DOWNGRADE_SQL = "ALTER TABLE bid_line DROP CONSTRAINT bid_line_quoted_price_positive;"


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
