"""Allocations become governed budget-change requests."""
from typing import Protocol

revision = "20261014_0055"
down_revision = "20261014_0054"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


def upgrade(connection: MigrationConnection) -> None:
    connection.execute("""
ALTER TABLE budget_change_request DROP CONSTRAINT budget_change_request_kind_check;
ALTER TABLE budget_change_request ADD CONSTRAINT budget_change_request_kind_check
 CHECK(kind IN ('adjustment','allocation','transfer'));
""")


def downgrade(connection: MigrationConnection) -> None:
    # Allocation requests have no earlier home: refuse rather than drop them silently.
    connection.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM budget_change_request WHERE kind='allocation') THEN
  RAISE EXCEPTION 'cannot downgrade: allocation requests exist';
 END IF;
END $$;
ALTER TABLE budget_change_request DROP CONSTRAINT budget_change_request_kind_check;
ALTER TABLE budget_change_request ADD CONSTRAINT budget_change_request_kind_check
 CHECK(kind IN ('adjustment','transfer'));
""")
