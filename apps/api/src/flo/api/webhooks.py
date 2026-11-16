"""Resend delivery webhook (E13-S10): signature, then tenant from the route table."""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Annotated, cast

import psycopg
from fastapi import APIRouter, Depends, Request, Response

from flo.api.internal import get_internal_settings
from flo.kernel.adapters.resend import verify_svix
from flo.kernel.authz import public_route
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.outbox.store import OutboxConnection, email_route
from flo.kernel.tenancy.context import Scope
from flo.modules.purchasing.transmission import apply_email_event

UNROUTED_RETRY_SECONDS = 3600
_log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _early_po_event(document: dict[str, object], data: dict[str, object], event_id: str) -> bool:
    """A PO-tagged event under an hour old: `sent()` may not have written the route yet."""
    tags = data.get("tags")
    if not isinstance(tags, dict) or tags.get("kind") != "po":
        return False
    try:
        age = time.time() - datetime.fromisoformat(str(document["created_at"])).timestamp()
    except (KeyError, ValueError):
        return False
    if age >= UNROUTED_RETRY_SECONDS:
        return False
    _log.warning("webhook event %s arrived before its PO route; asking for a retry", event_id)
    return True


def _apply(settings: Settings, event_id: str, body: bytes) -> bool:
    """True when the provider should retry: a PO event whose route is not written yet."""
    try:
        document = json.loads(body)
        data = document["data"]
        message_id = data["email_id"]
        kind = document["type"]
        hard = isinstance(data.get("bounce"), dict) and data["bounce"].get("type") == "Permanent"
    except (ValueError, KeyError, TypeError, AttributeError):
        return False  # signed but not an event we understand: acknowledged, ignored
    if not isinstance(message_id, str) or not message_id or not isinstance(kind, str):
        return False
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not configured")
    with psycopg.connect(settings.database_url.get_secret_value()) as connection:
        route = email_route(cast(OutboxConnection, connection), message_id)
        if route is None:
            return _early_po_event(document, data, event_id)  # else 200, nothing stored
        org_id, outbox_id = route
        apply_email_event(
            connection, Scope(org_id), outbox_id, event_id=event_id, type=kind, hard=hard
        )
    return False


@router.post("/email", status_code=200, include_in_schema=False)
@public_route
async def email_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_internal_settings)],
) -> Response:
    """Origin secret plus a Svix signature; the org comes only from `email_message_route`."""
    body = await request.body()  # raw bytes: the signature covers them exactly
    secret = settings.resend_webhook_secret
    headers = request.headers
    svix_id = headers.get("svix-id")
    if secret is None or not verify_svix(
        secret.get_secret_value(),
        svix_id,
        headers.get("svix-timestamp"),
        headers.get("svix-signature"),
        body,
        time.time(),
    ):
        raise ProblemError(ErrorCode.UNAUTHORIZED)
    # ponytail: one-hour window; a stored-unrouted-event table is the upgrade if sent() ever fails
    # for good after the email left.
    retry = await asyncio.to_thread(_apply, settings, cast(str, svix_id), body)
    return Response(status_code=503 if retry else 200)
