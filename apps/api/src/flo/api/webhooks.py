"""Resend delivery webhook (E13-S10): signature, then tenant from the route table."""

import asyncio
import json
import time
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

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _apply(settings: Settings, event_id: str, body: bytes) -> None:
    try:
        document = json.loads(body)
        data = document["data"]
        message_id = data["email_id"]
        kind = document["type"]
        hard = isinstance(data.get("bounce"), dict) and data["bounce"].get("type") == "Permanent"
    except (ValueError, KeyError, TypeError, AttributeError):
        return  # signed but not an event we understand: acknowledged, ignored
    if not isinstance(message_id, str) or not message_id or not isinstance(kind, str):
        return
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not configured")
    with psycopg.connect(settings.database_url.get_secret_value()) as connection:
        route = email_route(cast(OutboxConnection, connection), message_id)
        if route is None:
            return  # unknown message id: 200, nothing stored, no oracle
        org_id, outbox_id = route
        apply_email_event(
            connection, Scope(org_id), outbox_id, event_id=event_id, type=kind, hard=hard
        )


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
    await asyncio.to_thread(_apply, settings, cast(str, svix_id), body)
    return Response(status_code=200)
