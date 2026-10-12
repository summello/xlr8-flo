"""Preserve the material snapshot that completed approval."""
from typing import Protocol

revision = "20261009_0045"
down_revision = "20261009_0044"

class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...

def upgrade(connection: MigrationConnection) -> None:
    connection.execute("ALTER TABLE approval_instance ADD COLUMN approved_material_digest text NULL, "
        "ADD COLUMN approved_version int NULL")

def downgrade(connection: MigrationConnection) -> None:
    connection.execute("ALTER TABLE approval_instance DROP COLUMN approved_material_digest, "
        "DROP COLUMN approved_version")
