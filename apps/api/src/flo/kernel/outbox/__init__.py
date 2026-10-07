"""Transactional external-effect handoff and delivery."""

from flo.kernel.outbox.dispatcher import (
    OutboxDispatcher,
    OutboxSummary,
    PermanentOutboxError,
    TransientOutboxError,
    email_handler,
)
from flo.kernel.outbox.store import IdentityOutboxStore, OutboxRecord, OutboxState, OutboxStore

__all__ = [
    "IdentityOutboxStore",
    "OutboxDispatcher",
    "OutboxRecord",
    "OutboxState",
    "OutboxStore",
    "OutboxSummary",
    "PermanentOutboxError",
    "TransientOutboxError",
    "email_handler",
]
