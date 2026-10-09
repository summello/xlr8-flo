"""Resolve visitor addresses only across the authenticated Worker boundary."""

import hmac
import ipaddress
from functools import lru_cache

from starlette.requests import Request

from flo.kernel.config import Settings


@lru_cache(maxsize=1)
def _settings() -> Settings:
    return Settings()


def trusted_client_host(request: Request) -> str | None:
    """Ignore untrusted, missing, and malformed forwarded addresses."""
    secret = _settings().origin_shared_secret
    supplied = request.headers.get("x-flo-origin-secret", "")
    if secret is None or not hmac.compare_digest(
        supplied.encode("utf-8"), secret.get_secret_value().encode("utf-8")
    ):
        return None
    try:
        return str(ipaddress.ip_address(request.headers.get("x-flo-client-ip", "")))
    except ValueError:
        return None


def throttle_client_value(request: Request) -> str:
    """Limit one IPv4 host or IPv6 /64, independently of session privacy prefixes."""
    host = trusted_client_host(request) or (request.client.host if request.client else None)
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:
        return "unknown"
    if address.version == 4:
        return str(address)
    return str(ipaddress.ip_network(f"{address}/64", strict=False))
