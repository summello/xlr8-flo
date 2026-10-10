"""Chosen session organization and global tenant display registry.

# irreversible: restoring single membership is unsafe while any identity has
# multiple memberships; downgrade refuses before changing any schema or data.
"""
from typing import Protocol

revision = "20261009_0030"
down_revision = "20261009_0029"


class MigrationConnection(Protocol):
    def execute(self, query: str) -> object: ...


def upgrade(connection: MigrationConnection) -> None:
    connection.execute("""
        ALTER TABLE identity_membership DROP CONSTRAINT identity_membership_pkey;
        ALTER TABLE identity_membership ADD PRIMARY KEY (identity_id, org_id);
        ALTER TABLE identity_membership ADD COLUMN last_used_at timestamptz;
        ALTER TABLE auth_session ADD COLUMN org_id uuid REFERENCES organization(id);
        UPDATE auth_session s SET org_id = m.org_id FROM identity_membership m
        WHERE s.identity_id = m.identity_id AND
            (SELECT count(*) FROM identity_membership WHERE identity_id = s.identity_id) = 1;
        ALTER TABLE organization_code ADD COLUMN display_name text;
        UPDATE organization_code c SET display_name = o.name FROM organization o WHERE o.id = c.org_id;
        ALTER TABLE organization_code ALTER COLUMN display_name SET NOT NULL;
        ALTER TABLE organization_code ADD COLUMN tenant_label text
            CHECK (tenant_label IS NULL OR (length(btrim(tenant_label)) BETWEEN 1 AND 60
                AND tenant_label !~ '[[:cntrl:]]'));
        ALTER TABLE session_security_event DROP CONSTRAINT session_security_event_event_type_check;
        ALTER TABLE session_security_event ADD CONSTRAINT session_security_event_event_type_check
            CHECK (event_type IN ('revoked_token_reuse', 'password_reset_requested',
                'password_reset_completed', 'organization_switched'));
    """)


def downgrade(connection: MigrationConnection) -> None:
    connection.execute("""
        DO $guard$ BEGIN
            IF EXISTS (SELECT 1 FROM identity_membership GROUP BY identity_id HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'Cannot downgrade: an identity has multiple organization memberships';
            END IF;
        END $guard$;
        ALTER TABLE identity_membership DROP CONSTRAINT identity_membership_pkey;
        ALTER TABLE identity_membership ADD PRIMARY KEY (identity_id);
        ALTER TABLE identity_membership DROP COLUMN last_used_at;
        ALTER TABLE auth_session DROP COLUMN org_id;
        ALTER TABLE organization_code DROP COLUMN display_name, DROP COLUMN tenant_label;
        ALTER TABLE session_security_event DROP CONSTRAINT session_security_event_event_type_check;
        ALTER TABLE session_security_event ADD CONSTRAINT session_security_event_event_type_check
            CHECK (event_type IN ('revoked_token_reuse',
                'password_reset_requested', 'password_reset_completed')) NOT VALID;
    """)
