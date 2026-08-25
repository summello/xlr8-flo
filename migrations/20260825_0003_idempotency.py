"""Create tenant-scoped storage for idempotent request outcomes.

Revision ID: 20260825_0003
Revises: 20260824_0002
"""

from __future__ import annotations

from typing import Protocol

revision = "20260825_0003"
down_revision = "20260824_0002"


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE idempotency_key (
    org_id uuid NOT NULL,
    key text NOT NULL,
    endpoint text NOT NULL,
    request_hash text NOT NULL,
    state text NOT NULL,
    status_code integer,
    response_body jsonb,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamptz,
    PRIMARY KEY (org_id, key),
    CONSTRAINT idempotency_key_state_valid
        CHECK (state IN ('in_progress', 'completed')),
    CONSTRAINT idempotency_key_completion_valid
        CHECK (
            (state = 'in_progress' AND status_code IS NULL AND response_body IS NULL
                AND completed_at IS NULL)
            OR
            (state = 'completed' AND status_code IS NOT NULL AND response_body IS NOT NULL
                AND completed_at IS NOT NULL)
        )
);
CREATE INDEX idempotency_key_created_at_idx ON idempotency_key (created_at);
ALTER TABLE idempotency_key ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_key FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON idempotency_key
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid)
"""

DOWNGRADE_SQL = "DROP TABLE idempotency_key"


def upgrade(connection: MigrationConnection) -> None:
    """Create idempotency storage, retention index, and tenant policy."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove the idempotency table without touching other application data."""

    connection.execute(DOWNGRADE_SQL)
