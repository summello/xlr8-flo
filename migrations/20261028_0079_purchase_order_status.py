"""Purchase order lifecycle statuses in the status CHECK (E13-S03)."""

from typing import Protocol

revision = "20261028_0079"
down_revision = "20261027_0078"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE purchase_order DROP CONSTRAINT purchase_order_status_check;
ALTER TABLE purchase_order ADD CONSTRAINT purchase_order_status_check CHECK (status IN (
    'draft','approval_pending','approved','issued','partially_delivered','delivered',
    'accepted','closed','cancelled','superseded','deleted'));
"""

DOWNGRADE_SQL = """
ALTER TABLE purchase_order DROP CONSTRAINT purchase_order_status_check;
ALTER TABLE purchase_order ADD CONSTRAINT purchase_order_status_check
    CHECK (status IN ('draft','deleted'));
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: refuses (CHECK violation) while a PO sits in a later status; draft and
    # deleted rows survive untouched.
    connection.execute(DOWNGRADE_SQL)
