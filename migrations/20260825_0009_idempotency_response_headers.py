"""Persist replay-safe headers with completed idempotency responses.

Revision ID: 20260825_0009
Revises: 20260825_0008
"""

from __future__ import annotations

from typing import Protocol

revision = "20260825_0009"
down_revision = "20260825_0008"


class MigrationConnection(Protocol):
    """Database connection surface used by this reversible migration."""

    def execute(self, query: str) -> object: ...


UPGRADE_SQL = """
ALTER TABLE idempotency_key
    ADD COLUMN response_headers jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE idempotency_key
    ADD CONSTRAINT idempotency_key_response_headers_valid
    CHECK (
        jsonb_typeof(response_headers) = 'object'
        AND response_headers - 'location' - 'etag' = '{}'::jsonb
        AND (
            NOT response_headers ? 'location'
            OR jsonb_typeof(response_headers->'location') = 'string'
        )
        AND (
            NOT response_headers ? 'etag'
            OR jsonb_typeof(response_headers->'etag') = 'string'
        )
    );

ALTER TABLE idempotency_key
    DROP CONSTRAINT idempotency_key_completion_valid;
ALTER TABLE idempotency_key
    ADD CONSTRAINT idempotency_key_completion_valid
    CHECK (
        (
            state = 'in_progress'
            AND status_code IS NULL
            AND response_body IS NULL
            AND response_headers = '{}'::jsonb
            AND completed_at IS NULL
        )
        OR
        (
            state = 'completed'
            AND status_code IS NOT NULL
            AND response_body IS NOT NULL
            AND completed_at IS NOT NULL
        )
    );
"""

DOWNGRADE_SQL = """
ALTER TABLE idempotency_key
    DROP CONSTRAINT idempotency_key_completion_valid;
ALTER TABLE idempotency_key
    DROP CONSTRAINT idempotency_key_response_headers_valid;
ALTER TABLE idempotency_key
    DROP COLUMN response_headers;
ALTER TABLE idempotency_key
    ADD CONSTRAINT idempotency_key_completion_valid
    CHECK (
        (
            state = 'in_progress'
            AND status_code IS NULL
            AND response_body IS NULL
            AND completed_at IS NULL
        )
        OR
        (
            state = 'completed'
            AND status_code IS NOT NULL
            AND response_body IS NOT NULL
            AND completed_at IS NOT NULL
        )
    );
"""


def upgrade(connection: MigrationConnection) -> None:
    """Add the closed set of headers that are safe to replay later."""

    connection.execute(UPGRADE_SQL)


def downgrade(connection: MigrationConnection) -> None:
    """Remove replay headers while preserving stored status and response bodies."""

    connection.execute(DOWNGRADE_SQL)
