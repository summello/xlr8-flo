"""Create expiring, single-use password resets and identity-scoped delivery.

Revision ID: 20260825_0010
Revises: 20260825_0009
"""

from __future__ import annotations

from typing import Protocol

revision = "20260825_0010"
down_revision = "20260825_0009"


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE password_reset (
    id uuid PRIMARY KEY,
    identity_id uuid NOT NULL REFERENCES identity(id) ON DELETE RESTRICT,
    token_hash char(64) NOT NULL UNIQUE,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    CONSTRAINT password_reset_token_hash_sha256
        CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT password_reset_expiry_order
        CHECK (created_at < expires_at),
    CONSTRAINT password_reset_use_order
        CHECK (used_at IS NULL OR used_at >= created_at)
);
CREATE INDEX password_reset_identity_outstanding_idx
    ON password_reset (identity_id, expires_at DESC)
    WHERE used_at IS NULL;

CREATE TABLE password_reset_rate_limit (
    id bigserial PRIMARY KEY,
    dimension text NOT NULL CHECK (dimension IN ('email', 'ip')),
    key_hash char(64) NOT NULL,
    occurred_at timestamptz NOT NULL,
    CONSTRAINT password_reset_rate_key_sha256
        CHECK (key_hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX password_reset_rate_window_idx
    ON password_reset_rate_limit (dimension, key_hash, occurred_at DESC);
CREATE INDEX password_reset_rate_cleanup_idx
    ON password_reset_rate_limit (occurred_at);

ALTER TABLE session_security_event
    ALTER COLUMN session_id DROP NOT NULL;
ALTER TABLE session_security_event
    DROP CONSTRAINT session_security_event_event_type_check;
ALTER TABLE session_security_event
    ADD CONSTRAINT session_security_event_event_type_check
    CHECK (event_type IN (
        'revoked_token_reuse',
        'password_reset_requested',
        'password_reset_completed'
    ));

ALTER TABLE outbox
    ALTER COLUMN org_id DROP NOT NULL,
    ADD COLUMN identity_id uuid REFERENCES identity(id) ON DELETE RESTRICT,
    ADD COLUMN expires_at timestamptz;
ALTER TABLE outbox
    ADD CONSTRAINT outbox_scope_valid
        CHECK (num_nonnulls(org_id, identity_id) = 1),
    ADD CONSTRAINT outbox_identity_expiry_valid
        CHECK (
            (identity_id IS NULL AND expires_at IS NULL)
            OR (identity_id IS NOT NULL AND expires_at IS NOT NULL)
        ),
    ADD CONSTRAINT outbox_expiry_order
        CHECK (expires_at IS NULL OR expires_at > created_at),
    ADD CONSTRAINT outbox_identity_idempotency_unique
        UNIQUE (identity_id, idempotency_key);
CREATE INDEX outbox_identity_pending_idx
    ON outbox (identity_id, run_after, id)
    WHERE identity_id IS NOT NULL AND state IN ('pending', 'failed');
CREATE POLICY outbox_identity_insert ON outbox FOR INSERT
    WITH CHECK (
        org_id IS NULL
        AND identity_id = NULLIF(current_setting('app.identity_id', true), '')::uuid
    );
CREATE POLICY outbox_identity_delete ON outbox FOR DELETE
    USING (
        org_id IS NULL
        AND identity_id = NULLIF(current_setting('app.identity_id', true), '')::uuid
    );
CREATE POLICY outbox_worker_delete ON outbox FOR DELETE
    USING (current_setting('app.worker', true) = 'jobs');
"""

DOWNGRADE_SQL = """
DROP POLICY outbox_worker_delete ON outbox;
DROP POLICY outbox_identity_delete ON outbox;
DROP POLICY outbox_identity_insert ON outbox;
DELETE FROM outbox WHERE identity_id IS NOT NULL;
DROP INDEX outbox_identity_pending_idx;
ALTER TABLE outbox
    DROP CONSTRAINT outbox_identity_idempotency_unique,
    DROP CONSTRAINT outbox_expiry_order,
    DROP CONSTRAINT outbox_identity_expiry_valid,
    DROP CONSTRAINT outbox_scope_valid,
    DROP COLUMN expires_at,
    DROP COLUMN identity_id,
    ALTER COLUMN org_id SET NOT NULL;

DROP TRIGGER session_security_event_append_only ON session_security_event;
DELETE FROM session_security_event
 WHERE event_type IN ('password_reset_requested', 'password_reset_completed');
ALTER TABLE session_security_event
    DROP CONSTRAINT session_security_event_event_type_check;
ALTER TABLE session_security_event
    ADD CONSTRAINT session_security_event_event_type_check
    CHECK (event_type = 'revoked_token_reuse');
ALTER TABLE session_security_event
    ALTER COLUMN session_id SET NOT NULL;
CREATE TRIGGER session_security_event_append_only
    BEFORE UPDATE OR DELETE ON session_security_event
    FOR EACH ROW EXECUTE FUNCTION reject_session_security_event_mutation();

DROP TABLE password_reset_rate_limit;
DROP TABLE password_reset;
"""


def upgrade(connection: MigrationConnection) -> None:
    """Add identity-scoped reset state without fabricating a tenant."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove reset-only state while preserving all pre-existing rows."""

    connection.execute(DOWNGRADE_SQL)
