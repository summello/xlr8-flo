"""A project carries the budget it asks for until approval allocates it."""
from typing import Protocol

revision = "20261015_0057"
down_revision = "20261014_0056"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


def upgrade(connection: MigrationConnection) -> None:
    connection.execute("""
ALTER TABLE project ADD COLUMN requested_budget NUMERIC(18,4)
 CHECK (requested_budget IS NULL OR requested_budget > 0);
""")


def downgrade(connection: MigrationConnection) -> None:
    connection.execute("ALTER TABLE project DROP COLUMN requested_budget;")
