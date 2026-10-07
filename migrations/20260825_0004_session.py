"""Create opaque sessions and append-only revoked-token security events.

Revision ID: 20260825_0004
Revises: 20260825_0003
"""

from __future__ import annotations

from typing import Protocol

revision = "20260825_0004"
down_revision = "20260825_0003"


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE auth_session (
    id uuid PRIMARY KEY,
    identity_id uuid NOT NULL REFERENCES identity(id) ON DELETE RESTRICT,
    token_hash char(64) NOT NULL UNIQUE,
    created_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    idle_timeout_seconds integer NOT NULL CHECK (idle_timeout_seconds > 0),
    idle_expires_at timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    ip_prefix cidr,
    user_agent text NOT NULL,
    CONSTRAINT auth_session_token_hash_sha256
        CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT auth_session_expiry_order
        CHECK (created_at <= last_seen_at
           AND last_seen_at <= idle_expires_at
           AND idle_expires_at <= absolute_expires_at),
    CONSTRAINT auth_session_revocation_order
        CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX auth_session_identity_active_idx
    ON auth_session (identity_id, created_at DESC)
    WHERE revoked_at IS NULL;

CREATE TABLE session_security_event (
    id bigserial PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES auth_session(id) ON DELETE RESTRICT,
    identity_id uuid NOT NULL REFERENCES identity(id) ON DELETE RESTRICT,
    event_type text NOT NULL CHECK (event_type = 'revoked_token_reuse'),
    occurred_at timestamptz NOT NULL,
    ip_prefix cidr,
    user_agent text NOT NULL
);
CREATE INDEX session_security_event_identity_time_idx
    ON session_security_event (identity_id, occurred_at DESC);

CREATE FUNCTION reject_session_security_event_mutation() RETURNS trigger
LANGUAGE plpgsql AS $guard$
BEGIN
    RAISE EXCEPTION 'session security events are append-only';
END
$guard$;
CREATE TRIGGER session_security_event_append_only
    BEFORE UPDATE OR DELETE ON session_security_event
    FOR EACH ROW EXECUTE FUNCTION reject_session_security_event_mutation()
"""

DOWNGRADE_SQL = """
DROP TRIGGER session_security_event_append_only ON session_security_event;
DROP FUNCTION reject_session_security_event_mutation();
DROP TABLE session_security_event;
DROP TABLE auth_session
"""


def upgrade(connection: MigrationConnection) -> None:
    """Create hashed sessions and immutable revoked-token reuse evidence."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove only the session tables and their mutation guard."""

    connection.execute(DOWNGRADE_SQL)
