"""Tenant writes and internal SKIP LOCKED claims for the outbox."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from flo.kernel.audit.writer import CorrelationMissing
from flo.kernel.db.repo import ScopedRepo
from flo.kernel.logging import current_correlation_id
from flo.kernel.tenancy.context import Scope

type DatabaseRow = Sequence[object] | Mapping[str, object]
type JsonObject = dict[str, object]


class OutboxState(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: UUID
    org_id: UUID
    topic: str
    payload: JsonObject
    state: OutboxState
    attempts: int
    max_attempts: int
    run_after: datetime
    locked_at: datetime | None
    last_error: str | None
    provider_message_id: str | None
    idempotency_key: str
    correlation_id: str


class QueryResult(Protocol):
    rowcount: int

    def fetchone(self) -> DatabaseRow | None: ...

    def fetchall(self) -> list[DatabaseRow]: ...


class OutboxConnection(Protocol):
    def execute(
        self,
        query: str,
        params: Mapping[str, object] | None = None,
    ) -> QueryResult: ...


_RETURNING = """
id, org_id, topic, payload, state, attempts, max_attempts, run_after,
locked_at, last_error, provider_message_id, idempotency_key, correlation_id
"""


def _value(row: DatabaseRow, index: int, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]


def _record(row: DatabaseRow) -> OutboxRecord:
    return OutboxRecord(
        id=cast(UUID, _value(row, 0, "id")),
        org_id=cast(UUID, _value(row, 1, "org_id")),
        topic=cast(str, _value(row, 2, "topic")),
        payload=cast(JsonObject, _value(row, 3, "payload")),
        state=OutboxState(cast(str, _value(row, 4, "state"))),
        attempts=cast(int, _value(row, 5, "attempts")),
        max_attempts=cast(int, _value(row, 6, "max_attempts")),
        run_after=cast(datetime, _value(row, 7, "run_after")),
        locked_at=cast(datetime | None, _value(row, 8, "locked_at")),
        last_error=cast(str | None, _value(row, 9, "last_error")),
        provider_message_id=cast(str | None, _value(row, 10, "provider_message_id")),
        idempotency_key=cast(str, _value(row, 11, "idempotency_key")),
        correlation_id=cast(str, _value(row, 12, "correlation_id")),
    )


class OutboxStore(ScopedRepo[OutboxRecord]):
    """Append external effects on the caller's active business transaction."""

    def __init__(self, connection: OutboxConnection, scope: Scope) -> None:
        super().__init__(connection, scope)
        self._connection = connection

    def add(
        self,
        topic: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        max_attempts: int = 5,
    ) -> OutboxRecord:
        correlation_id = current_correlation_id()
        if not correlation_id:
            raise CorrelationMissing("outbox writes require a correlation id")
        if not topic:
            raise ValueError("outbox topic must be non-empty")
        if not idempotency_key:
            raise ValueError("outbox idempotency key must be non-empty")
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("outbox max_attempts must be between 1 and 5")
        row = self._connection.execute(
            f"""
            INSERT INTO outbox (
                id, org_id, topic, payload, max_attempts,
                idempotency_key, correlation_id
            ) VALUES (
                %(id)s, %(org_id)s, %(topic)s, %(payload)s, %(max_attempts)s,
                %(idempotency_key)s, %(correlation_id)s
            )
            RETURNING {_RETURNING}
            """,
            self.scoped_params(
                {
                    "id": uuid4(),
                    "topic": topic,
                    "payload": Jsonb(dict(payload)),
                    "max_attempts": max_attempts,
                    "idempotency_key": idempotency_key,
                    "correlation_id": correlation_id,
                }
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("outbox insert did not return its row")
        return _record(row)

    def add_email(
        self,
        *,
        to: str,
        template: str,
        context: Mapping[str, object],
        idempotency_key: str,
    ) -> OutboxRecord:
        return self.add(
            "email",
            {"to": to, "template": template, "context": dict(context)},
            idempotency_key=idempotency_key,
        )

    def list_dead(self, limit: int = 100) -> list[OutboxRecord]:
        if limit < 1 or limit > 1000:
            raise ValueError("dead-outbox limit must be between 1 and 1000")
        rows = self._connection.execute(
            f"""
            SELECT {_RETURNING}
            FROM outbox
            WHERE org_id = %(org_id)s AND state = 'dead'
            ORDER BY run_after DESC, id
            LIMIT %(limit)s
            """,
            self.scoped_params({"limit": limit}),
        ).fetchall()
        return [_record(row) for row in rows]


class WorkerOutboxStore:
    """Cross-tenant claim surface reserved for the authenticated cron worker."""

    def __init__(self, connection: OutboxConnection) -> None:
        self._connection = connection

    def claim(self) -> OutboxRecord | None:
        # See WorkerJobQueue: the marker is local to this authenticated cron
        # transaction and grants only SELECT/UPDATE on durable work tables.
        self._connection.execute(
            "SET LOCAL app.org_id = '00000000-0000-0000-0000-000000000000'"
        )
        self._connection.execute("SET LOCAL app.worker = 'jobs'")
        row = self._connection.execute(
            f"""
            UPDATE outbox
            SET locked_at = CURRENT_TIMESTAMP, attempts = attempts + 1,
                last_error = NULL
            WHERE id = (
                SELECT id FROM outbox
                WHERE state IN ('pending', 'failed')
                  AND run_after <= CURRENT_TIMESTAMP
                  AND (
                      locked_at IS NULL
                      OR locked_at < CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                  )
                ORDER BY run_after, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING {_RETURNING}
            """
        ).fetchone()
        return None if row is None else _record(row)

    def sent(self, row: OutboxRecord, provider_message_id: str) -> None:
        if not provider_message_id:
            raise ValueError("provider message id must be non-empty")
        self._transition(
            row,
            "state = 'sent', locked_at = NULL, provider_message_id = %(message_id)s, "
            "sent_at = CURRENT_TIMESTAMP",
            {"message_id": provider_message_id},
        )

    def retry(self, row: OutboxRecord, *, delay_seconds: float, last_error: str) -> None:
        self._transition(
            row,
            "state = 'failed', locked_at = NULL, "
            "run_after = CURRENT_TIMESTAMP + %(delay_seconds)s * INTERVAL '1 second', "
            "last_error = %(last_error)s",
            {"delay_seconds": delay_seconds, "last_error": last_error},
        )

    def dead(self, row: OutboxRecord, *, last_error: str) -> None:
        self._transition(
            row,
            "state = 'dead', locked_at = NULL, last_error = %(last_error)s",
            {"last_error": last_error},
        )

    def _transition(
        self,
        row: OutboxRecord,
        assignment: str,
        params: Mapping[str, object],
    ) -> None:
        updated = self._connection.execute(
            f"UPDATE outbox SET {assignment} "
            "WHERE id = %(id)s AND org_id = %(org_id)s "
            "AND state IN ('pending', 'failed') AND locked_at IS NOT NULL",
            {"id": row.id, "org_id": row.org_id, **params},
        )
        if updated.rowcount != 1:
            raise RuntimeError("claimed outbox row changed before delivery completed")
