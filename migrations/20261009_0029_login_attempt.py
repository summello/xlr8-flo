"""Global pre-authentication login evidence; tenant is unknown at login."""
from typing import Protocol

revision = "20261009_0029"
down_revision = "20261008_0028"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


def upgrade(connection: MigrationConnection) -> None:
    connection.execute("""
        CREATE TABLE login_attempt (
            id bigserial PRIMARY KEY,
            kind text NOT NULL CHECK (kind IN ('identity', 'client')),
            key_hash char(64) NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
            outcome text NOT NULL CHECK (outcome IN ('failed', 'throttled', 'succeeded')),
            occurred_at timestamptz NOT NULL
        );
        CREATE INDEX login_attempt_key_window ON login_attempt (kind, key_hash, occurred_at DESC);
        CREATE INDEX login_attempt_retention ON login_attempt (occurred_at);
    """)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute("DROP TABLE login_attempt")
