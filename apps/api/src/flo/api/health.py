"""Origin-authenticated process and dependency health endpoints."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated

import psycopg
import uvicorn
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from flo.api.internal import router as internal_router
from flo.api.origin_auth import require_origin_secret
from flo.kernel.config import Settings, enforce_argon2_memory_limit
from flo.kernel.errors import ErrorCode, ProblemError, install_problem_details
from flo.kernel.storage import create_storage

HealthProbe = Callable[[Settings], Awaitable[None]]


@dataclass(frozen=True)
class ReadinessResult:
    """Non-sensitive result shared by callers within the configured TTL."""

    ready: bool
    checks: Mapping[str, str]


class ReadinessCache:
    """Coalesce dependency checks and briefly cache their result."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._result: ReadinessResult | None = None
        self._expires_at = 0.0

    async def get(
        self,
        settings: Settings,
        probes: Mapping[str, HealthProbe],
    ) -> ReadinessResult:
        """Return a fresh-enough result, allowing only one check in flight."""

        if self._result is not None and time.monotonic() < self._expires_at:
            return self._result

        async with self._lock:
            if self._result is not None and time.monotonic() < self._expires_at:
                return self._result

            result = await _check_readiness(settings, probes)
            self._result = result
            self._expires_at = time.monotonic() + settings.readiness_cache_ttl_seconds
            return result


_readiness_cache = ReadinessCache()

app = FastAPI(
    title="XLR8 FLO API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    dependencies=[Depends(require_origin_secret)],
)
app.include_router(internal_router)
# Starlette prepends user middleware, so install problem details last to keep
# correlation outermost and able to serialize failures from every other middleware.
install_problem_details(app)


def get_settings() -> Settings:
    """Load current process configuration for a request."""

    return Settings()


async def check_database(settings: Settings) -> None:
    """Open a PostgreSQL connection and execute a minimal query."""

    if settings.database_url is None:
        raise RuntimeError("database is not configured")

    connection = await psycopg.AsyncConnection.connect(settings.database_url.get_secret_value())
    try:
        await connection.execute("SELECT 1")
    finally:
        await connection.close()


async def check_storage(settings: Settings) -> None:
    """Authenticate and list the private bucket without exposing configuration."""

    storage = create_storage(settings)
    await asyncio.to_thread(storage.usage_bytes)


def get_readiness_probes() -> Mapping[str, HealthProbe]:
    """Return dependency probes in stable response order."""

    return {"database": check_database, "storage": check_storage}


def get_readiness_cache() -> ReadinessCache:
    """Return the process-wide readiness single-flight cache."""

    return _readiness_cache


async def _probe(
    probe: HealthProbe,
    settings: Settings,
) -> str:
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            await probe(settings)
    except TimeoutError:
        return "timeout"
    except Exception:
        return "error"
    return "ok"


async def _check_readiness(
    settings: Settings,
    probes: Mapping[str, HealthProbe],
) -> ReadinessResult:
    names = list(probes)
    results = await asyncio.gather(*(_probe(probes[name], settings) for name in names))
    checks = dict(zip(names, results, strict=True))
    return ReadinessResult(
        ready=all(result == "ok" for result in results),
        checks=checks,
    )


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Report only that the API process can serve requests."""

    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
async def readyz(
    settings: Annotated[Settings, Depends(get_settings)],
    probes: Annotated[Mapping[str, HealthProbe], Depends(get_readiness_probes)],
    cache: Annotated[ReadinessCache, Depends(get_readiness_cache)],
) -> JSONResponse:
    """Report bounded, per-dependency readiness without leaking internal details."""

    result = await cache.get(settings, probes)
    if not result.ready:
        raise ProblemError(ErrorCode.SERVICE_UNAVAILABLE, checks=result.checks)

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "checks": result.checks,
        },
    )


def main() -> None:
    """Run the API server as the container's signal-receiving process."""

    settings = Settings()
    enforce_argon2_memory_limit(settings)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.port,
        server_header=False,
        timeout_graceful_shutdown=settings.graceful_shutdown_timeout_seconds,
    )


if __name__ == "__main__":
    main()
