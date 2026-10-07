"""Create encrypted TOTP factors, recovery hashes, replay evidence, and recent auth.

Revision ID: 20260825_0011
Revises: 20260825_0010
"""

from __future__ import annotations

from typing import Protocol

revision = "20260825_0011"
down_revision = "20260825_0010"


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE identity
    ADD COLUMN privileged_role_grants integer NOT NULL DEFAULT 0,
    ADD CONSTRAINT identity_privileged_role_grants_nonnegative
        CHECK (privileged_role_grants >= 0);

ALTER TABLE auth_session ADD COLUMN last_auth_at timestamptz;
UPDATE auth_session SET last_auth_at = created_at;
ALTER TABLE auth_session
    ALTER COLUMN last_auth_at SET NOT NULL,
    ADD COLUMN mfa_verified_at timestamptz,
    ADD CONSTRAINT auth_session_recent_auth_order
        CHECK (created_at <= last_auth_at AND last_auth_at <= last_seen_at),
    ADD CONSTRAINT auth_session_mfa_verification_order
        CHECK (mfa_verified_at IS NULL
            OR (created_at <= mfa_verified_at AND mfa_verified_at <= last_seen_at));

CREATE TABLE mfa_factor (
    identity_id uuid PRIMARY KEY REFERENCES identity(id) ON DELETE RESTRICT,
    secret_ciphertext text,
    active_recovery_set_id uuid,
    activated_at timestamptz,
    pending_secret_ciphertext text,
    pending_enrollment_id uuid,
    failed_attempts integer NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    failure_window_started_at timestamptz,
    locked_until timestamptz,
    updated_at timestamptz NOT NULL,
    CONSTRAINT mfa_factor_active_complete CHECK (
        (secret_ciphertext IS NULL) = (active_recovery_set_id IS NULL)
        AND (secret_ciphertext IS NULL) = (activated_at IS NULL)
    ),
    CONSTRAINT mfa_factor_pending_complete CHECK (
        (pending_secret_ciphertext IS NULL) = (pending_enrollment_id IS NULL)
    ),
    CONSTRAINT mfa_factor_failure_window_complete CHECK (
        (failed_attempts = 0) = (failure_window_started_at IS NULL)
    ),
    CONSTRAINT mfa_factor_lock_order CHECK (
        locked_until IS NULL OR failure_window_started_at IS NOT NULL
    )
);

CREATE TABLE mfa_recovery_code (
    selector uuid PRIMARY KEY,
    identity_id uuid NOT NULL REFERENCES mfa_factor(identity_id) ON DELETE CASCADE,
    recovery_set_id uuid NOT NULL,
    code_hash text NOT NULL,
    created_at timestamptz NOT NULL,
    used_at timestamptz,
    CONSTRAINT mfa_recovery_code_argon2id CHECK (code_hash LIKE '$argon2id$%'),
    CONSTRAINT mfa_recovery_code_use_order CHECK (used_at IS NULL OR used_at >= created_at)
);
CREATE INDEX mfa_recovery_code_identity_set_idx
    ON mfa_recovery_code (identity_id, recovery_set_id)
    WHERE used_at IS NULL;

CREATE TABLE mfa_totp_consumption (
    identity_id uuid NOT NULL REFERENCES mfa_factor(identity_id) ON DELETE CASCADE,
    time_step bigint NOT NULL CHECK (time_step >= 0),
    consumed_at timestamptz NOT NULL,
    PRIMARY KEY (identity_id, time_step)
);

CREATE TABLE mfa_security_event (
    id bigserial PRIMARY KEY,
    identity_id uuid NOT NULL REFERENCES identity(id) ON DELETE RESTRICT,
    session_id uuid REFERENCES auth_session(id) ON DELETE RESTRICT,
    event_type text NOT NULL CHECK (
        event_type IN ('factor_failure', 'factor_locked', 'recovery_codes_low')
    ),
    occurred_at timestamptz NOT NULL,
    ip_prefix cidr,
    user_agent text NOT NULL
);
CREATE INDEX mfa_security_event_identity_time_idx
    ON mfa_security_event (identity_id, occurred_at DESC);

CREATE FUNCTION reject_mfa_security_event_mutation() RETURNS trigger
LANGUAGE plpgsql AS $guard$
BEGIN
    RAISE EXCEPTION 'MFA security events are append-only';
END
$guard$;
CREATE TRIGGER mfa_security_event_append_only
    BEFORE UPDATE OR DELETE ON mfa_security_event
    FOR EACH ROW EXECUTE FUNCTION reject_mfa_security_event_mutation();
"""

DOWNGRADE_SQL = """
DROP TRIGGER mfa_security_event_append_only ON mfa_security_event;
DROP FUNCTION reject_mfa_security_event_mutation();
DROP TABLE mfa_security_event;
DROP TABLE mfa_totp_consumption;
DROP TABLE mfa_recovery_code;
DROP TABLE mfa_factor;
ALTER TABLE auth_session
    DROP CONSTRAINT auth_session_mfa_verification_order,
    DROP CONSTRAINT auth_session_recent_auth_order,
    DROP COLUMN mfa_verified_at,
    DROP COLUMN last_auth_at;
ALTER TABLE identity
    DROP CONSTRAINT identity_privileged_role_grants_nonnegative,
    DROP COLUMN privileged_role_grants;
"""


def upgrade(connection: MigrationConnection) -> None:
    """Create encrypted MFA state and append-only security evidence."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove MFA state while preserving identities and existing sessions."""

    connection.execute(DOWNGRADE_SQL)
