"""Exactly-once provider handoff for claimed transactional outbox rows."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from psycopg import sql

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.jobs.backoff import RandomFraction, backoff_seconds
from flo.kernel.logging import correlation_context
from flo.kernel.outbox.store import OutboxConnection, OutboxRecord, WorkerOutboxStore
from flo.kernel.ports.email import (
    EmailSender,
    PermanentEmailError,
    TransientEmailError,
)
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction


class TransientOutboxError(RuntimeError):
    """A classified provider failure safe to retry with the same key."""


class PermanentOutboxError(RuntimeError):
    """An invalid external effect routed immediately to operator review."""


class Transaction(Protocol):
    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> bool | None: ...


class DispatcherConnection(Protocol):
    def execute(
        self,
        query: str | sql.Composed,
        params: Mapping[str, object] | None = None,
    ) -> object: ...

    def transaction(self) -> Transaction: ...


type OutboxHandler = Callable[[OutboxRecord], str]
type MonotonicClock = Callable[[], float]
REQUIRED_OUTBOX_TOPICS = frozenset({"email"})


@dataclass(frozen=True, slots=True)
class OutboxSummary:
    claimed: int = 0
    sent: int = 0
    retried: int = 0
    dead: int = 0


def email_handler(sender: EmailSender) -> OutboxHandler:
    """Map the closed email payload contract onto the provider-neutral port."""

    def deliver(row: OutboxRecord) -> str:
        required = {"to", "template", "context"}
        missing = required - set(row.payload)
        if missing:
            raise PermanentOutboxError(
                f"MISSING outbox email payload fields: {', '.join(sorted(missing))}"
            )
        to = row.payload["to"]
        template = row.payload["template"]
        context = row.payload["context"]
        if not isinstance(to, str) or not isinstance(template, str) or not isinstance(
            context, Mapping
        ):
            raise PermanentOutboxError("outbox email payload has invalid field types")
        try:
            return sender.send(
                to,
                template,
                cast(Mapping[str, object], context),
                row.idempotency_key,
            )
        except TransientEmailError as error:
            raise TransientOutboxError(str(error)) from None
        except PermanentEmailError as error:
            raise PermanentOutboxError(str(error)) from None

    return deliver


class OutboxDispatcher:
    """Drain outbox rows while preserving provider idempotency on every retry."""

    def __init__(
        self,
        connection: DispatcherConnection,
        handlers: Mapping[str, OutboxHandler],
        *,
        random_fraction: RandomFraction,
        monotonic: MonotonicClock = time.monotonic,
    ) -> None:
        missing = REQUIRED_OUTBOX_TOPICS - set(handlers)
        if missing:
            raise RuntimeError(f"MISSING outbox handlers: {', '.join(sorted(missing))}")
        self._connection = connection
        self._handlers = dict(handlers)
        self._store = WorkerOutboxStore(cast(OutboxConnection, connection))
        self._random_fraction = random_fraction
        self._monotonic = monotonic

    def run(self, time_budget_seconds: float = 50.0) -> OutboxSummary:
        if time_budget_seconds <= 0 or time_budget_seconds > 50:
            raise ValueError("outbox tick budget must be in (0, 50] seconds")
        deadline = self._monotonic() + time_budget_seconds
        claimed = sent = retried = dead = 0
        while self._monotonic() < deadline:
            with self._connection.transaction():
                row = self._store.claim()
            if row is None:
                break
            claimed += 1
            outcome = self._deliver(row)
            if outcome == "sent":
                sent += 1
            elif outcome == "retried":
                retried += 1
            else:
                dead += 1
        return OutboxSummary(claimed=claimed, sent=sent, retried=retried, dead=dead)

    def _deliver(self, row: OutboxRecord) -> str:
        handler = self._handlers.get(row.topic)
        try:
            if handler is None:
                raise PermanentOutboxError("outbox topic has no registered handler")
            provider_message_id = handler(row)
            with correlation_context(row.correlation_id), tenant_transaction(
                cast(RlsSession, self._connection), Scope(row.org_id)
            ):
                self._store.sent(row, provider_message_id)
                self._audit(row, "outbox.sent", Outcome.SUCCESS)
            return "sent"
        except TransientOutboxError as error:
            if row.attempts >= row.max_attempts:
                self._record_dead(row, _safe_error(error))
                return "dead"
            delay = backoff_seconds(row.attempts, self._random_fraction)
            with correlation_context(row.correlation_id), tenant_transaction(
                cast(RlsSession, self._connection), Scope(row.org_id)
            ):
                self._store.retry(row, delay_seconds=delay, last_error=_safe_error(error))
                self._audit(row, "outbox.retry", Outcome.ERROR, _safe_error(error))
            return "retried"
        except Exception as error:
            self._record_dead(row, _safe_error(error))
            return "dead"

    def _record_dead(self, row: OutboxRecord, last_error: str) -> None:
        with correlation_context(row.correlation_id), tenant_transaction(
            cast(RlsSession, self._connection), Scope(row.org_id)
        ):
            self._store.dead(row, last_error=last_error)
            self._audit(row, "outbox.dead", Outcome.ERROR, last_error)

    def _audit(
        self,
        row: OutboxRecord,
        action: str,
        outcome: Outcome,
        reason: str | None = None,
    ) -> None:
        AuditWriter(cast(AuditConnection, self._connection), Scope(row.org_id)).write(
            actor=AuditActor(ActorKind.JOB),
            action=action,
            target_type="outbox",
            target_id=row.id,
            outcome=outcome,
            reason=reason,
        )


def _safe_error(error: BaseException) -> str:
    if isinstance(error, (TransientOutboxError, PermanentOutboxError)) and str(error):
        return str(error)[:500]
    return type(error).__name__
