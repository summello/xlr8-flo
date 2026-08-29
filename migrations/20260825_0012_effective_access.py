"""Add identity lifecycle state and dated role-grant provenance.

Revision ID: 20260825_0012
Revises: 20260825_0011
"""

from __future__ import annotations

from typing import Protocol

revision = "20260825_0012"
down_revision = "20260825_0011"


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE identity
    ADD COLUMN status text NOT NULL DEFAULT 'active',
    ADD COLUMN deactivated_at timestamptz,
    ADD CONSTRAINT identity_status_valid CHECK (status IN ('active', 'deactivated')),
    ADD CONSTRAINT identity_deactivation_complete CHECK (
        (status = 'active' AND deactivated_at IS NULL)
        OR (status = 'deactivated' AND deactivated_at IS NOT NULL)
    );

ALTER TABLE user_role
    ADD COLUMN granted_by uuid REFERENCES identity(id) ON DELETE RESTRICT,
    ADD COLUMN granted_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN effective_from timestamptz;

INSERT INTO permission (code) VALUES ('admin.access.read');
"""

DOWNGRADE_SQL = """
DELETE FROM role_permission WHERE permission_code = 'admin.access.read';
DELETE FROM permission WHERE code = 'admin.access.read';
ALTER TABLE user_role
    DROP COLUMN effective_from,
    DROP COLUMN granted_at,
    DROP COLUMN granted_by;
ALTER TABLE identity
    DROP CONSTRAINT identity_deactivation_complete,
    DROP CONSTRAINT identity_status_valid,
    DROP COLUMN deactivated_at,
    DROP COLUMN status;
"""


def upgrade(connection: MigrationConnection) -> None:
    """Persist deactivation and grant activation/provenance without rewriting rows."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove explorer state while preserving identities, grants, and audit history."""

    connection.execute(DOWNGRADE_SQL)
