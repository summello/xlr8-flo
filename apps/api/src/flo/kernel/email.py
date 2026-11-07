"""Configuration-only composition for the EmailSender port, and the PO sending-domain gate."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from flo.kernel.adapters.resend import create_resend_domain_checker, create_resend_sender
from flo.kernel.adapters.smtp import SmtpDomainChecker, create_smtp_sender
from flo.kernel.config import Settings
from flo.kernel.ports.email import DomainChecker, DomainResult, DomainStatus, EmailSender

DOMAIN_NOT_VERIFIED = "Sending domain not verified"
DOMAIN_CHECK_JOB = "email-domain-check"
DOMAIN_CHECK_INTERVAL_SECONDS = 60


def create_email_sender(settings: Settings) -> EmailSender:
    """Swap SMTP and Resend without changing application modules."""

    if settings.email_provider == "resend":
        return create_resend_sender(settings)
    return create_smtp_sender(settings)


def create_domain_checker(settings: Settings) -> DomainChecker:
    if settings.email_provider == "resend":
        return create_resend_domain_checker(settings)
    return SmtpDomainChecker()


@dataclass(frozen=True)
class DomainCheck:
    status: DomainStatus
    checked_at: datetime
    recent: bool  # stored less than DOMAIN_CHECK_INTERVAL_SECONDS ago, by the database clock


def latest_domain_check(connection: Any) -> DomainCheck | None:
    """The newest stored check (global table, no tenant), or None before the first one."""
    row = connection.execute(
        "SELECT spf, dkim, detail, checked_at, "
        "checked_at > now() - make_interval(secs => %(interval)s) "
        "FROM email_domain_status ORDER BY checked_at DESC, id DESC LIMIT 1",
        {"interval": DOMAIN_CHECK_INTERVAL_SECONDS},
    ).fetchone()
    if row is None:
        return None
    status = DomainStatus(cast(DomainResult, row[0]), cast(DomainResult, row[1]), str(row[2]))
    return DomainCheck(status, cast(datetime, row[3]), bool(row[4]))


def latest_domain_status(connection: Any) -> DomainStatus | None:
    check = latest_domain_check(connection)
    return None if check is None else check.status


def po_domain_gate(settings: Settings, status: DomainStatus | None) -> Callable[[str], str | None]:
    """In production, refuse a `po-` email until SPF and DKIM both pass (EML-001)."""

    def gate(template: str) -> str | None:
        if settings.email_provider != "resend" or not template.startswith("po-"):
            return None
        if status is None or status.spf != "pass" or status.dkim != "pass":
            return DOMAIN_NOT_VERIFIED
        return None

    return gate
