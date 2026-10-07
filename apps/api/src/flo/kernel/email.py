"""Configuration-only composition for the EmailSender port."""

from flo.kernel.adapters.resend import create_resend_sender
from flo.kernel.adapters.smtp import create_smtp_sender
from flo.kernel.config import Settings
from flo.kernel.ports.email import EmailSender


def create_email_sender(settings: Settings) -> EmailSender:
    """Swap SMTP and Resend without changing application modules."""

    if settings.email_provider == "resend":
        return create_resend_sender(settings)
    return create_smtp_sender(settings)
