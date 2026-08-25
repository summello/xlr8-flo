"""Create the monthly partitioned, append-only business audit log.

Revision ID: 20260825_0005
Revises: 20260825_0004
"""

from __future__ import annotations

from typing import Protocol

revision = "20260825_0005"
down_revision = "20260825_0004"


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE FUNCTION raise_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $append_only$
BEGIN
    RAISE EXCEPTION '% is append-only: % is forbidden', TG_TABLE_NAME, TG_OP
        USING ERRCODE = '55000';
END
$append_only$;

CREATE TABLE audit_log (
    id bigserial NOT NULL,
    org_id uuid NOT NULL,
    bu_id uuid,
    occurred_at timestamptz(6) NOT NULL DEFAULT clock_timestamp(),
    actor_id uuid,
    actor_kind text NOT NULL,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id uuid,
    outcome text NOT NULL,
    reason text,
    correlation_id text NOT NULL,
    before jsonb,
    after jsonb,
    ip_prefix text,
    user_agent text,
    PRIMARY KEY (id, occurred_at),
    CONSTRAINT audit_log_actor_kind_valid
        CHECK (actor_kind IN ('user', 'system', 'job', 'import')),
    CONSTRAINT audit_log_actor_id_valid
        CHECK (actor_kind <> 'user' OR actor_id IS NOT NULL),
    CONSTRAINT audit_log_outcome_valid
        CHECK (outcome IN ('success', 'denied', 'error'))
) PARTITION BY RANGE (occurred_at);

CREATE INDEX audit_log_org_occurred_at_idx
    ON audit_log (org_id, occurred_at DESC, id DESC);

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_log
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE TRIGGER audit_log_immutable
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION raise_append_only();

REVOKE UPDATE, DELETE ON TABLE audit_log FROM PUBLIC;
"""

DOWNGRADE_SQL = """
DROP TABLE audit_log;
DROP FUNCTION raise_append_only();
"""


def upgrade(connection: MigrationConnection) -> None:
    """Create append-only, tenant-isolated audit storage."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove the audit table and its dedicated immutability function."""

    connection.execute(DOWNGRADE_SQL)
