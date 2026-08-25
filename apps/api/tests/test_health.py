import asyncio
from collections.abc import Iterator

import httpx
import pytest

from flo.api import health
from flo.api.health import (
    HealthProbe,
    ReadinessCache,
    app,
    get_readiness_cache,
    get_readiness_probes,
    get_settings,
)
from flo.api.origin_auth import (
    ORIGIN_SECRET_HEADER,
    get_origin_settings,
)
from flo.kernel.config import Settings

_TEST_ORIGIN_SECRET = "test-origin-shared-secret-at-least-32-bytes"


class FakeMonotonicClock:
    def __init__(self) -> None:
        self.current_time = 0.0

    def monotonic(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    cache = ReadinessCache()
    app.dependency_overrides[get_readiness_cache] = lambda: cache
    app.dependency_overrides[get_origin_settings] = lambda: Settings(
        origin_shared_secret=_TEST_ORIGIN_SECRET
    )
    yield
    app.dependency_overrides.clear()


async def _ok(_: Settings) -> None:
    return None


def _override_probes(**probes: HealthProbe) -> None:
    app.dependency_overrides[get_readiness_probes] = lambda: probes


def _get(path: str, origin_secret: str | None = _TEST_ORIGIN_SECRET) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        headers = {} if origin_secret is None else {ORIGIN_SECRET_HEADER: origin_secret}
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=headers,
        ) as client:
            return await client.get(path)

    return asyncio.run(request())


def test_healthz_is_live_without_touching_dependencies() -> None:
    async def fail_if_called(_: Settings) -> None:
        raise AssertionError("liveness touched a dependency")

    _override_probes(database=fail_if_called, storage=fail_if_called)

    response = _get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cloud_run_origin_authentication_hides_every_api_path() -> None:
    assert _get("/healthz", None).status_code == 404
    assert _get("/healthz", "wrong-origin-secret").status_code == 404
    assert _get("/healthz", _TEST_ORIGIN_SECRET).status_code == 200


def test_readyz_reports_each_healthy_dependency() -> None:
    _override_probes(database=_ok, storage=_ok)

    response = _get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "storage": "ok"},
    }


def test_api_emits_no_cors_headers_or_middleware() -> None:
    async def request_every_response_shape() -> list[httpx.Response]:
        _override_probes(database=_ok, storage=_ok)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={ORIGIN_SECRET_HEADER: _TEST_ORIGIN_SECRET},
        ) as client:
            return [
                await client.get("/healthz"),
                await client.get("/readyz"),
                await client.options(
                    "/healthz",
                    headers={
                        "Origin": "https://cross-origin.example",
                        "Access-Control-Request-Method": "GET",
                    },
                ),
            ]

    responses = asyncio.run(request_every_response_shape())

    assert all(
        "cors" not in middleware.cls.__module__.casefold()
        for middleware in app.user_middleware
    )
    for response in responses:
        assert "access-control-allow-origin" not in response.headers


def test_concurrent_readyz_requests_share_one_database_connection_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_attempts = 0
    connection_started = asyncio.Event()
    release_connection = asyncio.Event()
    clock = FakeMonotonicClock()

    class FakeConnection:
        async def execute(self, _: str) -> None:
            return None

        async def close(self) -> None:
            return None

    async def connect(_: str) -> FakeConnection:
        nonlocal connection_attempts
        connection_attempts += 1
        connection_started.set()
        await release_connection.wait()
        return FakeConnection()

    async def request_burst() -> None:
        settings = Settings(
            database_url="postgresql://unused",
            readiness_cache_ttl_seconds=5,
        )
        app.dependency_overrides[get_settings] = lambda: settings
        _override_probes(database=health.check_database, storage=_ok)
        monkeypatch.setattr(health, "time", clock)
        monkeypatch.setattr(health.psycopg.AsyncConnection, "connect", connect)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={ORIGIN_SECRET_HEADER: _TEST_ORIGIN_SECRET},
        ) as client:
            requests = [asyncio.create_task(client.get("/readyz")) for _ in range(20)]
            await connection_started.wait()
            await asyncio.sleep(0)
            release_connection.set()
            responses = await asyncio.gather(*requests)

            assert {response.status_code for response in responses} == {200}
            assert connection_attempts == 1

    asyncio.run(request_burst())


def test_readyz_cache_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    probe_attempts = 0
    clock = FakeMonotonicClock()
    settings = Settings(readiness_cache_ttl_seconds=1)

    async def database_probe(_: Settings) -> None:
        nonlocal probe_attempts
        probe_attempts += 1

    async def request_before_and_after_expiry() -> None:
        app.dependency_overrides[get_settings] = lambda: settings
        _override_probes(database=database_probe, storage=_ok)
        monkeypatch.setattr(health, "time", clock)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={ORIGIN_SECRET_HEADER: _TEST_ORIGIN_SECRET},
        ) as client:
            assert (await client.get("/readyz")).status_code == 200
            assert (await client.get("/readyz")).status_code == 200
            assert probe_attempts == 1

            clock.advance(settings.readiness_cache_ttl_seconds + 1)

            assert (await client.get("/readyz")).status_code == 200
            assert probe_attempts == 2

    asyncio.run(request_before_and_after_expiry())


def test_database_failure_degrades_readiness_but_not_liveness() -> None:
    async def database_down(_: Settings) -> None:
        raise ConnectionError("postgres is stopped")

    _override_probes(database=database_down, storage=_ok)

    health_response = _get("/healthz")
    readiness_response = _get("/readyz")

    assert health_response.status_code == 200
    assert readiness_response.status_code == 503
    assert readiness_response.json() == {
        "type": "https://xlr8flo.app/errors/service-unavailable",
        "title": "The service is temporarily unavailable",
        "status": 503,
        "detail": "The request could not be completed. No data was changed.",
        "instance": "/readyz",
        "correlation_id": readiness_response.headers["x-correlation-id"],
        "recovery": "Try again in a few minutes. If the problem continues, contact support.",
        "checks": {"database": "error", "storage": "ok"},
    }
    assert readiness_response.headers["content-type"] == "application/problem+json"


def test_readyz_bounds_a_slow_dependency_and_names_its_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_returns(_: Settings) -> None:
        await asyncio.sleep(1)

    monkeypatch.setenv("FLO_READINESS_TIMEOUT_SECONDS", "0.01")
    _override_probes(database=_ok, storage=never_returns)

    response = _get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "type": "https://xlr8flo.app/errors/service-unavailable",
        "title": "The service is temporarily unavailable",
        "status": 503,
        "detail": "The request could not be completed. No data was changed.",
        "instance": "/readyz",
        "correlation_id": response.headers["x-correlation-id"],
        "recovery": "Try again in a few minutes. If the problem continues, contact support.",
        "checks": {"database": "ok", "storage": "timeout"},
    }


def test_readyz_does_not_disclose_dependency_error_details() -> None:
    sensitive = "postgresql://user:password@db.internal/v17/flo"

    async def leaking_failure(_: Settings) -> None:
        raise RuntimeError(sensitive)

    _override_probes(database=leaking_failure, storage=_ok)

    response = _get("/readyz")
    body = response.text

    assert response.status_code == 503
    assert sensitive not in body
    assert "password" not in body
    assert "db.internal" not in body
    assert "/v17/flo" not in body
