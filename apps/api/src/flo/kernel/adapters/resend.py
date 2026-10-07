"""Resend adapter with provider idempotency and classified failures."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import cast

from flo.kernel.config import Settings
from flo.kernel.ports.email import (
    EmailSender,
    PermanentEmailError,
    TransientEmailError,
)

_RESEND_EMAILS_URL = "https://api.resend.com/emails"
type HttpRequester = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]


def _request(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout_seconds: float,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class ResendEmailSender(EmailSender):
    """Deliver a provider template through Resend's HTTPS API."""

    def __init__(
        self,
        api_key: str,
        from_address: str,
        *,
        timeout_seconds: float = 10.0,
        requester: HttpRequester = _request,
    ) -> None:
        if not api_key:
            raise ValueError("Resend API key must be configured")
        if not from_address:
            raise ValueError("email from address must be configured")
        self._api_key = api_key
        self._from_address = from_address
        self._timeout_seconds = timeout_seconds
        self._requester = requester

    def send(
        self,
        to: str,
        template: str,
        context: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        _require_header_safe("recipient", to)
        _require_header_safe("idempotency key", idempotency_key)
        if not template:
            raise PermanentEmailError("email template must be non-empty")
        try:
            body = json.dumps(
                {
                    "from": self._from_address,
                    "to": [to],
                    "template": {"id": template, "variables": dict(context)},
                },
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError):
            raise PermanentEmailError("email template context is not JSON serializable") from None

        try:
            status, response_body = self._requester(
                _RESEND_EMAILS_URL,
                {
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": idempotency_key,
                },
                body,
                self._timeout_seconds,
            )
        except (TimeoutError, urllib.error.URLError, OSError):
            raise TransientEmailError("email provider request did not complete") from None

        if status in {408, 425, 429} or status >= 500:
            raise TransientEmailError("email provider is temporarily unavailable")
        if status == 409 and _error_name(response_body) == "concurrent_idempotent_requests":
            raise TransientEmailError("email provider is still processing this idempotency key")
        if status < 200 or status >= 300:
            raise PermanentEmailError("email provider rejected the request")
        try:
            document = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise TransientEmailError("email provider returned an invalid response") from None
        if not isinstance(document, dict):
            raise TransientEmailError("email provider returned an invalid response")
        message_id = cast(object, document.get("id"))
        if not isinstance(message_id, str) or not message_id:
            raise TransientEmailError("email provider returned no message identifier")
        return message_id


def _error_name(response_body: bytes) -> str | None:
    try:
        document = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    name = document.get("name")
    return name if isinstance(name, str) else None


def _require_header_safe(field: str, value: str) -> None:
    if not value or "\r" in value or "\n" in value:
        raise PermanentEmailError(f"email {field} is invalid")


def create_resend_sender(settings: Settings) -> ResendEmailSender:
    """Build Resend only when its secret is present."""

    if settings.resend_api_key is None:
        raise RuntimeError("RESEND_API_KEY is not configured")
    return ResendEmailSender(
        settings.resend_api_key.get_secret_value(),
        settings.email_from,
        timeout_seconds=settings.email_timeout_seconds,
    )
