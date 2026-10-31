"""Provider-neutral email delivery port."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


class TransientEmailError(RuntimeError):
    """A provider failure that may succeed when retried with the same key."""


class PermanentEmailError(RuntimeError):
    """A provider rejection that must not be retried."""


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content_type: str
    content: bytes


class EmailSender(Protocol):
    """Deliver one template while preserving provider idempotency."""

    def send(
        self,
        to: str,
        template: str,
        context: Mapping[str, object],
        idempotency_key: str,
        attachments: Sequence[EmailAttachment] = (),
    ) -> str:
        """Return the provider's stable message identifier."""
