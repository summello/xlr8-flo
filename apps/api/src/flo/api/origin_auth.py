"""Authenticate requests forwarded from the single Cloudflare origin."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header

from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError

ORIGIN_SECRET_HEADER = "X-FLO-Origin-Secret"


def get_origin_settings() -> Settings:
    """Load the shared origin secret from the process environment."""

    return Settings()


def require_origin_secret(
    settings: Annotated[Settings, Depends(get_origin_settings)],
    presented_secret: Annotated[str | None, Header(alias=ORIGIN_SECRET_HEADER)] = None,
) -> None:
    """Hide every API path from callers that did not traverse the Worker."""

    configured_secret = settings.origin_shared_secret
    presented = (presented_secret or "").encode()
    configured = (
        configured_secret.get_secret_value().encode()
        if configured_secret is not None
        else b""
    )
    if configured_secret is None or not hmac.compare_digest(presented, configured):
        raise ProblemError(ErrorCode.NOT_FOUND)
