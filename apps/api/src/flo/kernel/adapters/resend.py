"""Resend adapter with provider idempotency and classified failures."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import cast

from flo.kernel.config import Settings
from flo.kernel.ports.email import (
    DomainChecker,
    DomainResult,
    DomainStatus,
    EmailAttachment,
    EmailSender,
    PermanentEmailError,
    TransientEmailError,
)

_RESEND_EMAILS_URL = "https://api.resend.com/emails"
_RESEND_DOMAINS_URL = "https://api.resend.com/domains"
type HttpRequester = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]
type GetRequester = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]


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


def _get_request(url: str, headers: Mapping[str, str], timeout_seconds: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
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
        attachments: Sequence[EmailAttachment] = (),
    ) -> str:
        _require_header_safe("recipient", to)
        _require_header_safe("idempotency key", idempotency_key)
        if not template:
            raise PermanentEmailError("email template must be non-empty")
        request_body: dict[str, object] = {
            "from": self._from_address,
            "to": [to],
            "template": {"id": template, "variables": dict(context)},
        }
        if attachments:
            request_body["attachments"] = [
                {
                    "filename": a.filename,
                    "content": base64.b64encode(a.content).decode("ascii"),
                    "content_type": a.content_type,
                }
                for a in attachments
            ]
        try:
            body = json.dumps(request_body, separators=(",", ":")).encode()
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


def _record_result(records: list[dict[str, object]], kind: str) -> DomainResult:
    states = [r.get("status") for r in records if r.get("record") == kind]
    if "failed" in states:
        return "fail"
    return "pass" if states and all(x == "verified" for x in states) else "unknown"


class ResendDomainChecker(DomainChecker):
    """Read SPF and DKIM state of one Resend domain with the full-access domain key."""

    def __init__(
        self,
        api_key: str,
        domain_id: str,
        from_address: str,
        *,
        timeout_seconds: float = 10.0,
        requester: GetRequester = _get_request,
    ) -> None:
        if not api_key or not domain_id:
            raise ValueError("Resend domain check is not configured")
        self._api_key = api_key
        self._domain_id = domain_id
        self._from_domain = from_address.rpartition("@")[2].lower()
        self._timeout_seconds = timeout_seconds
        self._requester = requester

    def domain_status(self) -> DomainStatus:
        try:
            status, body = self._requester(
                f"{_RESEND_DOMAINS_URL}/{self._domain_id}",
                {"Authorization": f"Bearer {self._api_key}"},
                self._timeout_seconds,
            )
        except (TimeoutError, urllib.error.URLError, OSError):
            raise TransientEmailError("email provider request did not complete") from None
        if status in {408, 425, 429} or status >= 500:
            raise TransientEmailError("email provider is temporarily unavailable")
        if status < 200 or status >= 300:
            raise PermanentEmailError("email provider rejected the request")
        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise PermanentEmailError("email provider returned an invalid response") from None
        if not isinstance(document, dict):
            raise PermanentEmailError("email provider returned an invalid response")
        raw = cast(object, document.get("records"))
        records = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
        spf, dkim = _record_result(records, "SPF"), _record_result(records, "DKIM")
        name = str(document.get("name", ""))
        notes = [
            f"{r.get('record')} {r.get('type')} {r.get('name')}: {r.get('status')}"
            for r in records
            if r.get("record") in ("SPF", "DKIM") and r.get("status") != "verified"
        ]
        if name.lower() != self._from_domain:
            spf = dkim = "fail"
            notes.append(f"EMAIL_FROM is not on {name}")
        return DomainStatus(spf, dkim, "; ".join(notes)[:500])


def create_resend_domain_checker(settings: Settings) -> ResendDomainChecker:
    if settings.resend_domain_api_key is None or settings.resend_domain_id is None:
        raise RuntimeError("Resend domain check is not configured")
    return ResendDomainChecker(
        settings.resend_domain_api_key.get_secret_value(),
        settings.resend_domain_id,
        settings.email_from,
        timeout_seconds=settings.email_timeout_seconds,
    )


def verify_svix(
    secret: str,
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
    body: bytes,
    now: float,
    tolerance_seconds: int = 300,
) -> bool:
    """Svix webhook signature: HMAC-SHA256 over `id.timestamp.body`, any `v1,` entry may match."""
    if not (svix_id and svix_timestamp and svix_signature):
        return False
    try:
        issued = int(svix_timestamp)
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (ValueError, binascii.Error):
        return False
    if abs(now - issued) > tolerance_seconds:
        return False
    signed = f"{svix_id}.{svix_timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(
        hmac.compare_digest(expected, entry[3:])
        for entry in svix_signature.split()
        if entry.startswith("v1,")
    )
