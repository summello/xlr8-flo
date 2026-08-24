"""Create storage for the Phase-1 local identity provider.

Revision ID: 20260824_0002
Revises: 20260824_0001
"""

from __future__ import annotations

from typing import Protocol

revision = "20260824_0002"
down_revision = "20260824_0001"


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE identity (
    id uuid PRIMARY KEY,
    email text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT identity_password_hash_argon2id
        CHECK (password_hash LIKE '$argon2id$%')
)
"""

DOWNGRADE_SQL = "DROP TABLE identity"


def upgrade(connection: MigrationConnection) -> None:
    """Create local identity storage without exposing password plaintext."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove the local identity table."""

    connection.execute(DOWNGRADE_SQL)
