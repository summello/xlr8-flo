import asyncio
from collections.abc import Iterator

import httpx
import pytest

from flo.api.health import HealthProbe, app, get_readiness_probes
from flo.kernel.config import Settings


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


async def _ok(_: Settings) -> None:
    return None


def _override_probes(**probes: HealthProbe) -> None:
    app.dependency_overrides[get_readiness_probes] = lambda: probes


def _get(path: str) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(request())


def test_healthz_is_live_without_touching_dependencies() -> None:
    async def fail_if_called(_: Settings) -> None:
        raise AssertionError("liveness touched a dependency")

    _override_probes(database=fail_if_called, storage=fail_if_called)

    response = _get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_each_healthy_dependency() -> None:
    _override_probes(database=_ok, storage=_ok)

    response = _get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "storage": "ok"},
    }


def test_database_failure_degrades_readiness_but_not_liveness() -> None:
    async def database_down(_: Settings) -> None:
        raise ConnectionError("postgres is stopped")

    _override_probes(database=database_down, storage=_ok)

    health_response = _get("/healthz")
    readiness_response = _get("/readyz")

    assert health_response.status_code == 200
    assert readiness_response.status_code == 503
    assert readiness_response.json() == {
        "status": "degraded",
        "checks": {"database": "error", "storage": "ok"},
    }


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
        "status": "degraded",
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
