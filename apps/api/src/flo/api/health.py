"""Unauthenticated process and dependency health endpoints."""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated

import psycopg
import uvicorn
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from flo.kernel.config import Settings

HealthProbe = Callable[[Settings], Awaitable[None]]

app = FastAPI(
    title="XLR8 FLO API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


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


def _touch_storage(endpoint_url: str, timeout_seconds: float) -> None:
    request = urllib.request.Request(endpoint_url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds):  # noqa: S310
            pass
    except urllib.error.HTTPError:
        # Authentication and bucket checks belong to the storage adapter. Any HTTP
        # response proves the configured S3-compatible endpoint is reachable.
        pass


async def check_storage(settings: Settings) -> None:
    """Reach the configured S3-compatible HTTP endpoint without exposing its URL."""

    if settings.storage_endpoint_url is None:
        raise RuntimeError("storage is not configured")

    await asyncio.to_thread(
        _touch_storage,
        settings.storage_endpoint_url.get_secret_value(),
        settings.readiness_timeout_seconds,
    )


def get_readiness_probes() -> Mapping[str, HealthProbe]:
    """Return dependency probes in stable response order."""

    return {"database": check_database, "storage": check_storage}


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


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Report only that the API process can serve requests."""

    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
async def readyz(
    settings: Annotated[Settings, Depends(get_settings)],
    probes: Annotated[Mapping[str, HealthProbe], Depends(get_readiness_probes)],
) -> JSONResponse:
    """Report bounded, per-dependency readiness without leaking internal details."""

    names = list(probes)
    results = await asyncio.gather(*(_probe(probes[name], settings) for name in names))
    checks = dict(zip(names, results, strict=True))
    ready = all(result == "ok" for result in results)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ok" if ready else "degraded", "checks": checks},
    )


def main() -> None:
    """Run the API server as the container's signal-receiving process."""

    settings = Settings()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.port,
        server_header=False,
        timeout_graceful_shutdown=settings.graceful_shutdown_timeout_seconds,
    )


if __name__ == "__main__":
    main()
