"""Local SMTP adapter used by Mailpit without provider-specific module imports."""

from __future__ import annotations

import hashlib
import json
import smtplib
from collections.abc import Callable, Mapping
from email.message import EmailMessage

from flo.kernel.config import Settings
from flo.kernel.ports.email import (
    EmailSender,
    PermanentEmailError,
    TransientEmailError,
)

type SmtpFactory = Callable[[str, int, float], smtplib.SMTP]


def _smtp_factory(host: str, port: int, timeout: float) -> smtplib.SMTP:
    return smtplib.SMTP(host, port, timeout=timeout)


class SmtpEmailSender(EmailSender):
    """Deliver deterministic development messages to a local SMTP sink."""

    def __init__(
        self,
        host: str,
        port: int,
        from_address: str,
        *,
        timeout_seconds: float = 10.0,
        smtp_factory: SmtpFactory = _smtp_factory,
    ) -> None:
        self._host = host
        self._port = port
        self._from_address = from_address
        self._timeout_seconds = timeout_seconds
        self._smtp_factory = smtp_factory

    def send(
        self,
        to: str,
        template: str,
        context: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        if not to or "\r" in to or "\n" in to:
            raise PermanentEmailError("email recipient is invalid")
        if not template or "\r" in template or "\n" in template:
            raise PermanentEmailError("email template is invalid")
        if not idempotency_key or "\r" in idempotency_key or "\n" in idempotency_key:
            raise PermanentEmailError("email idempotency key is invalid")
        try:
            rendered_context = json.dumps(dict(context), sort_keys=True, indent=2)
        except (TypeError, ValueError):
            raise PermanentEmailError("email template context is not JSON serializable") from None

        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        provider_message_id = f"smtp-{digest}"
        message = EmailMessage()
        message["From"] = self._from_address
        message["To"] = to
        message["Subject"] = template
        message["Message-ID"] = f"<{provider_message_id}@xlr8flo.local>"
        message["Resend-Idempotency-Key"] = idempotency_key
        message.set_content(rendered_context)
        try:
            with self._smtp_factory(
                self._host,
                self._port,
                self._timeout_seconds,
            ) as smtp:
                refused = smtp.send_message(message)
        except (TimeoutError, OSError, smtplib.SMTPServerDisconnected):
            raise TransientEmailError("SMTP delivery did not complete") from None
        except smtplib.SMTPException:
            raise PermanentEmailError("SMTP rejected the message") from None
        if refused:
            raise PermanentEmailError("SMTP rejected the recipient")
        return provider_message_id


def create_smtp_sender(settings: Settings) -> SmtpEmailSender:
    """Build the local Mailpit adapter."""

    return SmtpEmailSender(
        settings.smtp_host,
        settings.smtp_port,
        settings.email_from,
        timeout_seconds=settings.email_timeout_seconds,
    )
