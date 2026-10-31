"""A late bid accepted by the RFQ lead with a reason, stored on the bid itself (E12-S05)."""

from typing import Protocol

revision = "20261022_0073"
down_revision = "20261022_0072"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE bid
 ADD COLUMN late_accepted_by uuid REFERENCES identity(id),
 ADD COLUMN late_accepted_at timestamptz,
 ADD COLUMN late_reason text CHECK (char_length(late_reason) BETWEEN 1 AND 500),
 ADD CONSTRAINT bid_late_all_or_none CHECK (
  (late_accepted_by IS NULL) = (late_accepted_at IS NULL)
  AND (late_accepted_by IS NULL) = (late_reason IS NULL));
"""

DOWNGRADE_SQL = """
ALTER TABLE bid
 DROP CONSTRAINT bid_late_all_or_none,
 DROP COLUMN late_reason,
 DROP COLUMN late_accepted_at,
 DROP COLUMN late_accepted_by;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    # irreversible: the late-acceptance record of already submitted bids is lost on the way down.
    connection.execute(DOWNGRADE_SQL)
