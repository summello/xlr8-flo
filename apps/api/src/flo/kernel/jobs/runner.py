"""Bounded job runner with classified retry and transactional audit writes."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from psycopg import sql

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.jobs.backoff import RandomFraction, backoff_seconds
from flo.kernel.jobs.queue import Job, JobConnection, WorkerJobQueue
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction


class TransientJobError(RuntimeError):
    """A classified failure that is safe to retry."""


class PermanentJobError(RuntimeError):
    """Invalid work that must go directly to operator review."""


class Transaction(Protocol):
    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None: ...


class RunnerConnection(Protocol):
    def execute(
        self,
        query: str | sql.Composed,
        params: Mapping[str, object] | None = None,
    ) -> object: ...

    def transaction(self) -> Transaction: ...


type JobHandler = Callable[[Job], None]
type MonotonicClock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class RunnerSummary:
    claimed: int = 0
    done: int = 0
    retried: int = 0
    dead: int = 0


class JobRunner:
    """Drain available jobs without exceeding the cron request's budget."""

    def __init__(
        self,
        connection: RunnerConnection,
        handlers: Mapping[str, JobHandler],
        *,
        random_fraction: RandomFraction,
        monotonic: MonotonicClock = time.monotonic,
    ) -> None:
        self._connection = connection
        self._handlers = dict(handlers)
        self._queue = WorkerJobQueue(cast(JobConnection, connection))
        self._random_fraction = random_fraction
        self._monotonic = monotonic

    def run(self, time_budget_seconds: float = 50.0) -> RunnerSummary:
        if time_budget_seconds <= 0 or time_budget_seconds > 50:
            raise ValueError("job tick budget must be in (0, 50] seconds")
        deadline = self._monotonic() + time_budget_seconds
        claimed = done = retried = dead = 0
        while self._monotonic() < deadline:
            with self._connection.transaction():
                job = self._queue.claim()
            if job is None:
                break
            claimed += 1
            outcome = self._execute(job)
            if outcome == "done":
                done += 1
            elif outcome == "retried":
                retried += 1
            else:
                dead += 1
        return RunnerSummary(claimed=claimed, done=done, retried=retried, dead=dead)

    def _execute(self, job: Job) -> str:
        handler = self._handlers.get(job.kind)
        try:
            if handler is None:
                raise PermanentJobError("job kind has no registered handler")
            with correlation_context(job.correlation_id), tenant_transaction(
                cast(RlsSession, self._connection), Scope(job.org_id)
            ):
                handler(job)
                self._queue.complete(job)
                self._audit(job, "job.done", Outcome.SUCCESS)
            return "done"
        except TransientJobError as error:
            if job.attempts >= job.max_attempts:
                self._record_dead(job, _safe_error(error))
                return "dead"
            delay = backoff_seconds(job.attempts, self._random_fraction)
            with correlation_context(job.correlation_id), tenant_transaction(
                cast(RlsSession, self._connection), Scope(job.org_id)
            ):
                self._queue.retry(job, delay_seconds=delay, last_error=_safe_error(error))
                self._audit(job, "job.retry", Outcome.ERROR, _safe_error(error))
            return "retried"
        except Exception as error:
            self._record_dead(job, _safe_error(error))
            return "dead"

    def _record_dead(self, job: Job, last_error: str) -> None:
        with correlation_context(job.correlation_id), tenant_transaction(
            cast(RlsSession, self._connection), Scope(job.org_id)
        ):
            self._queue.dead(job, last_error=last_error)
            self._audit(job, "job.dead", Outcome.ERROR, last_error)

    def _audit(
        self,
        job: Job,
        action: str,
        outcome: Outcome,
        reason: str | None = None,
    ) -> None:
        AuditWriter(cast(AuditConnection, self._connection), Scope(job.org_id)).write(
            actor=AuditActor(ActorKind.JOB),
            action=action,
            target_type="job",
            target_id=job.id,
            outcome=outcome,
            reason=reason,
        )


def _safe_error(error: BaseException) -> str:
    if isinstance(error, (TransientJobError, PermanentJobError)) and str(error):
        return str(error)[:500]
    return type(error).__name__
