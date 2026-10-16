"""Optimistic-concurrency version on vendors and vendor contacts (E22-S04, convention O)."""

from typing import Protocol

revision = "20261014_0063"
down_revision = "20261014_0062"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE vendor ADD COLUMN version integer NOT NULL DEFAULT 1;
ALTER TABLE vendor_contact ADD COLUMN version integer NOT NULL DEFAULT 1;
"""

DOWNGRADE_SQL = """
ALTER TABLE vendor_contact DROP COLUMN version;
ALTER TABLE vendor DROP COLUMN version;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
