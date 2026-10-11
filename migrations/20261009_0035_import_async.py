"""Import replay keys and observable background processing."""

from typing import Protocol

revision = "20261009_0035"
down_revision = "20261009_0034"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE import_batch ADD COLUMN external_key text,
 ADD COLUMN progress jsonb NOT NULL DEFAULT '{}',
 ADD COLUMN cancel_requested boolean NOT NULL DEFAULT false,
 ADD COLUMN error_class text,
 ADD CONSTRAINT import_external_key_length CHECK
 (external_key IS NULL OR length(external_key) BETWEEN 1 AND 128);
CREATE UNIQUE INDEX import_external_key ON import_batch(org_id,template,external_key)
 WHERE external_key IS NOT NULL;
"""
DOWNGRADE_SQL = """
DROP INDEX import_external_key;
ALTER TABLE import_batch DROP CONSTRAINT import_external_key_length,
 DROP COLUMN external_key, DROP COLUMN progress, DROP COLUMN cancel_requested,
 DROP COLUMN error_class;
"""


def upgrade(connection: MigrationConnection) -> None:
    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute(DOWNGRADE_SQL)
