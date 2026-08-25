"""Tenant writes and internal SKIP LOCKED claims for background jobs."""

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


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class JobProgress:
    current: int
    total: int
    message: str

    def __post_init__(self) -> None:
        if self.current < 0 or self.total < self.current:
            raise ValueError("job progress must satisfy 0 <= current <= total")
        if not self.message:
            raise ValueError("job progress message must be non-empty")

    def as_json(self) -> JsonObject:
        return {"current": self.current, "total": self.total, "message": self.message}


@dataclass(frozen=True, slots=True)
class Job:
    id: UUID
    org_id: UUID
    kind: str
    payload: JsonObject
    state: JobState
    attempts: int
    max_attempts: int
    run_after: datetime
    locked_at: datetime | None
    last_error: str | None
    correlation_id: str
    progress: JsonObject | None


class QueryResult(Protocol):
    rowcount: int

    def fetchone(self) -> DatabaseRow | None: ...

    def fetchall(self) -> list[DatabaseRow]: ...


class JobConnection(Protocol):
    def execute(
        self,
        query: str,
        params: Mapping[str, object] | None = None,
    ) -> QueryResult: ...


_RETURNING = """
id, org_id, kind, payload, state, attempts, max_attempts, run_after,
locked_at, last_error, correlation_id, progress
"""


def _value(row: DatabaseRow, index: int, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]


def _job(row: DatabaseRow) -> Job:
    return Job(
        id=cast(UUID, _value(row, 0, "id")),
        org_id=cast(UUID, _value(row, 1, "org_id")),
        kind=cast(str, _value(row, 2, "kind")),
        payload=cast(JsonObject, _value(row, 3, "payload")),
        state=JobState(cast(str, _value(row, 4, "state"))),
        attempts=cast(int, _value(row, 5, "attempts")),
        max_attempts=cast(int, _value(row, 6, "max_attempts")),
        run_after=cast(datetime, _value(row, 7, "run_after")),
        locked_at=cast(datetime | None, _value(row, 8, "locked_at")),
        last_error=cast(str | None, _value(row, 9, "last_error")),
        correlation_id=cast(str, _value(row, 10, "correlation_id")),
        progress=cast(JsonObject | None, _value(row, 11, "progress")),
    )


class JobQueue(ScopedRepo[Job]):
    """Write and inspect jobs only inside an authenticated tenant scope."""

    def __init__(self, connection: JobConnection, scope: Scope) -> None:
        super().__init__(connection, scope)
        self._connection = connection

    def enqueue(
        self,
        kind: str,
        payload: Mapping[str, object],
        *,
        max_attempts: int = 5,
        run_after: datetime | None = None,
    ) -> Job:
        correlation_id = current_correlation_id()
        if not correlation_id:
            raise CorrelationMissing("job enqueue requires a correlation id")
        if not kind:
            raise ValueError("job kind must be non-empty")
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("job max_attempts must be between 1 and 5")
        row = self._connection.execute(
            f"""
            INSERT INTO job (
                id, org_id, kind, payload, max_attempts, run_after, correlation_id
            ) VALUES (
                %(id)s, %(org_id)s, %(kind)s, %(payload)s, %(max_attempts)s,
                COALESCE(%(run_after)s, CURRENT_TIMESTAMP), %(correlation_id)s
            )
            RETURNING {_RETURNING}
            """,
            self.scoped_params(
                {
                    "id": uuid4(),
                    "kind": kind,
                    "payload": Jsonb(dict(payload)),
                    "max_attempts": max_attempts,
                    "run_after": run_after,
                    "correlation_id": correlation_id,
                }
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("job insert did not return its row")
        return _job(row)

    def update_progress(self, job_id: UUID, progress: JobProgress) -> None:
        updated = self._connection.execute(
            "UPDATE job SET progress = %(progress)s "
            "WHERE id = %(id)s AND org_id = %(org_id)s AND state = 'running'",
            self.scoped_params({"id": job_id, "progress": Jsonb(progress.as_json())}),
        )
        if updated.rowcount != 1:
            raise RuntimeError("running job was not available for progress update")

    def list_dead(self, limit: int = 100) -> list[Job]:
        if limit < 1 or limit > 1000:
            raise ValueError("dead-job limit must be between 1 and 1000")
        rows = self._connection.execute(
            f"""
            SELECT {_RETURNING}
            FROM job
            WHERE org_id = %(org_id)s AND state = 'dead'
            ORDER BY finished_at DESC, id
            LIMIT %(limit)s
            """,
            self.scoped_params({"limit": limit}),
        ).fetchall()
        return [_job(row) for row in rows]


class WorkerJobQueue:
    """Cross-tenant claim surface reserved for the authenticated cron worker."""

    def __init__(self, connection: JobConnection) -> None:
        self._connection = connection

    def claim(self) -> Job | None:
        # Tenant RLS remains forced for application queries. This transaction-
        # local marker opens only the two worker policies on the queue tables.
        self._connection.execute(
            "SET LOCAL app.org_id = '00000000-0000-0000-0000-000000000000'"
        )
        self._connection.execute("SET LOCAL app.worker = 'jobs'")
        row = self._connection.execute(
            f"""
            UPDATE job
            SET state = 'running', locked_at = CURRENT_TIMESTAMP,
                attempts = attempts + 1, last_error = NULL
            WHERE id = (
                SELECT id FROM job
                WHERE state = 'queued' AND run_after <= CURRENT_TIMESTAMP
                ORDER BY run_after, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING {_RETURNING}
            """
        ).fetchone()
        return None if row is None else _job(row)

    def complete(self, job: Job) -> None:
        self._transition(
            job,
            "state = 'done', locked_at = NULL, finished_at = CURRENT_TIMESTAMP",
            {},
        )

    def retry(self, job: Job, *, delay_seconds: float, last_error: str) -> None:
        self._transition(
            job,
            "state = 'queued', locked_at = NULL, "
            "run_after = CURRENT_TIMESTAMP + %(delay_seconds)s * INTERVAL '1 second', "
            "last_error = %(last_error)s",
            {"delay_seconds": delay_seconds, "last_error": last_error},
        )

    def dead(self, job: Job, *, last_error: str) -> None:
        self._transition(
            job,
            "state = 'dead', locked_at = NULL, finished_at = CURRENT_TIMESTAMP, "
            "last_error = %(last_error)s",
            {"last_error": last_error},
        )

    def _transition(
        self,
        job: Job,
        assignment: str,
        params: Mapping[str, object],
    ) -> None:
        updated = self._connection.execute(
            f"UPDATE job SET {assignment} "
            "WHERE id = %(id)s AND org_id = %(org_id)s AND state = 'running'",
            {"id": job.id, "org_id": job.org_id, **params},
        )
        if updated.rowcount != 1:
            raise RuntimeError("claimed job changed state before completion")
