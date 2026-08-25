"""Create the tenant job queue and transactional outbox.

Revision ID: 20260825_0007
Revises: 20260825_0006
"""

from __future__ import annotations

from typing import Protocol

revision = "20260825_0007"
down_revision = "20260825_0006"


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
CREATE TABLE job (
    id uuid PRIMARY KEY,
    org_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind <> ''),
    payload jsonb NOT NULL,
    state text NOT NULL DEFAULT 'queued',
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    run_after timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_at timestamptz,
    last_error text,
    correlation_id text NOT NULL CHECK (correlation_id <> ''),
    progress jsonb,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at timestamptz,
    CONSTRAINT job_state_valid
        CHECK (state IN ('queued', 'running', 'done', 'failed', 'dead')),
    CONSTRAINT job_lock_state_valid
        CHECK ((state = 'running') = (locked_at IS NOT NULL)),
    CONSTRAINT job_progress_valid
        CHECK (
            progress IS NULL OR (
                jsonb_typeof(progress) = 'object'
                AND progress ?& ARRAY['current', 'total', 'message']
                AND jsonb_typeof(progress->'current') = 'number'
                AND jsonb_typeof(progress->'total') = 'number'
                AND jsonb_typeof(progress->'message') = 'string'
                AND (progress->>'current')::integer >= 0
                AND (progress->>'total')::integer >= (progress->>'current')::integer
            )
        )
);
CREATE INDEX job_ready_idx ON job (run_after, id)
    WHERE state = 'queued';
CREATE INDEX job_dead_operator_idx ON job (org_id, finished_at DESC, id)
    WHERE state = 'dead';
ALTER TABLE job ENABLE ROW LEVEL SECURITY;
ALTER TABLE job FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON job
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE POLICY job_worker_select ON job FOR SELECT
    USING (current_setting('app.worker', true) = 'jobs');
CREATE POLICY job_worker_update ON job FOR UPDATE
    USING (current_setting('app.worker', true) = 'jobs')
    WITH CHECK (current_setting('app.worker', true) = 'jobs');

CREATE TABLE outbox (
    id uuid PRIMARY KEY,
    org_id uuid NOT NULL,
    topic text NOT NULL CHECK (topic <> ''),
    payload jsonb NOT NULL,
    state text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    run_after timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_at timestamptz,
    last_error text,
    provider_message_id text,
    idempotency_key text NOT NULL CHECK (idempotency_key <> ''),
    correlation_id text NOT NULL CHECK (correlation_id <> ''),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at timestamptz,
    CONSTRAINT outbox_state_valid
        CHECK (state IN ('pending', 'sent', 'failed', 'dead')),
    CONSTRAINT outbox_provider_result_valid
        CHECK (
            (state = 'sent' AND provider_message_id IS NOT NULL AND sent_at IS NOT NULL)
            OR (state <> 'sent' AND provider_message_id IS NULL AND sent_at IS NULL)
        ),
    CONSTRAINT outbox_idempotency_unique UNIQUE (org_id, idempotency_key)
);
CREATE INDEX outbox_ready_idx ON outbox (run_after, id)
    WHERE state IN ('pending', 'failed');
CREATE INDEX outbox_dead_operator_idx ON outbox (org_id, run_after DESC, id)
    WHERE state = 'dead';
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON outbox
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);
CREATE POLICY outbox_worker_select ON outbox FOR SELECT
    USING (current_setting('app.worker', true) = 'jobs');
CREATE POLICY outbox_worker_update ON outbox FOR UPDATE
    USING (current_setting('app.worker', true) = 'jobs')
    WITH CHECK (current_setting('app.worker', true) = 'jobs');
"""

DOWNGRADE_SQL = """
DROP TABLE outbox;
DROP TABLE job;
"""


def upgrade(connection: MigrationConnection) -> None:
    """Create durable, tenant-isolated asynchronous work storage."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove only the queue and outbox tables."""

    connection.execute(DOWNGRADE_SQL)
