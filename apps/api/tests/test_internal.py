from __future__ import annotations

import asyncio
import logging
import urllib.parse
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI

from flo.api import internal
from flo.api.health import app
from flo.api.internal import (
    QUOTA_POLICIES,
    GoogleCloudUsage,
    QuotaCollector,
    TickComponentSummary,
    TickReport,
    build_quota_report,
    get_internal_settings,
    get_quota_collectors,
    get_tick_processor,
)
from flo.api.origin_auth import ORIGIN_SECRET_HEADER, get_origin_settings
from flo.kernel.config import Settings
from flo.kernel.errors import install_problem_details

_TEST_ORIGIN_SECRET = "test-origin-shared-secret-at-least-32-bytes"


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _collectors(values: Mapping[str, int]) -> dict[str, QuotaCollector]:
    collectors: dict[str, QuotaCollector] = {}
    for metric in QUOTA_POLICIES:
        current = values.get(metric, 0)

        async def collect(value: int = current) -> int:
            return value

        collectors[metric] = collect
    return collectors


def _get_quota(collectors: Mapping[str, QuotaCollector]) -> httpx.Response:
    async def request() -> httpx.Response:
        app.dependency_overrides[get_quota_collectors] = lambda: collectors
        app.dependency_overrides[get_origin_settings] = lambda: Settings(
            origin_shared_secret=_TEST_ORIGIN_SECRET
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={ORIGIN_SECRET_HEADER: _TEST_ORIGIN_SECRET},
        ) as client:
            return await client.get("/internal/health/quota")

    return asyncio.run(request())


def test_quota_endpoint_reports_all_four_required_metrics_with_current_percentages() -> None:
    values = {
        "neon_storage": 256_000_000,
        "cloud_run_requests": 500_000,
        "r2_storage": 1_000_000_000,
        "artifact_registry": 100_000_000,
    }

    response = _get_quota(_collectors(values))

    assert response.status_code == 200
    document = response.json()
    assert set(document["metrics"]) == {
        "neon_storage",
        "cloud_run_requests",
        "r2_storage",
        "artifact_registry",
    }
    assert {
        name: (metric["current"], metric["percentage"])
        for name, metric in document["metrics"].items()
    } == {
        "neon_storage": (256_000_000, 50.0),
        "cloud_run_requests": (500_000, 25.0),
        "r2_storage": (1_000_000_000, 10.0),
        "artifact_registry": (100_000_000, 20.0),
    }
    assert document["alerts"] == []


def test_quota_authentication_rejects_before_opening_a_database_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_connections = 0

    class FakeCursor:
        async def fetchone(self) -> tuple[int]:
            return (256_000_000,)

    class FakeConnection:
        async def execute(self, _: str) -> FakeCursor:
            return FakeCursor()

        async def close(self) -> None:
            return None

    async def connect(_: str) -> FakeConnection:
        nonlocal database_connections
        database_connections += 1
        return FakeConnection()

    class FakeGoogleUsage:
        def cloud_run_requests_this_month(self) -> int:
            return 500_000

        def artifact_registry_bytes(self) -> int:
            return 100_000_000

    class FakeStorage:
        def usage_bytes(self) -> int:
            return 1_000_000_000

    settings = Settings(
        database_url="postgresql://unused",
        gcp_project="flo-test-project",
        origin_shared_secret=_TEST_ORIGIN_SECRET,
    )
    quota_app = FastAPI()
    quota_app.include_router(internal.router)
    install_problem_details(quota_app)
    quota_app.dependency_overrides[get_origin_settings] = lambda: settings
    quota_app.dependency_overrides[get_internal_settings] = lambda: settings
    monkeypatch.setattr(internal.psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(internal, "GoogleCloudUsage", lambda _: FakeGoogleUsage())
    monkeypatch.setattr(internal, "create_storage", lambda _: FakeStorage())

    async def request_all_three_outcomes() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=quota_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.get("/internal/health/quota")
            wrong = await client.get(
                "/internal/health/quota",
                headers={ORIGIN_SECRET_HEADER: "wrong-origin-secret"},
            )
            assert database_connections == 0
            correct = await client.get(
                "/internal/health/quota",
                headers={ORIGIN_SECRET_HEADER: _TEST_ORIGIN_SECRET},
            )
            return missing, wrong, correct

    missing, wrong, correct = asyncio.run(request_all_three_outcomes())

    assert missing.status_code == 404
    assert wrong.status_code == 404
    assert correct.status_code == 200
    assert database_connections == 1


@pytest.mark.parametrize(
    ("metric", "threshold", "action", "cost"),
    (
        (
            "neon_storage",
            60,
            "Archive audit partitions to R2 first; then Neon Launch",
            "$19/mo",
        ),
        (
            "cloud_run_requests",
            80,
            "Still free; set min-instances=1 for latency",
            "~$8–15/mo",
        ),
        ("r2_storage", 90, "Pay-as-you-go", "~$0.15/mo per 10 GB"),
        (
            "artifact_registry",
            80,
            "Prune to 3 images (automate first)",
            "~$0",
        ),
    ),
)
def test_threshold_fixture_emits_the_pre_agreed_action_and_cost(
    metric: str,
    threshold: int,
    action: str,
    cost: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    policy = QUOTA_POLICIES[metric]
    current = policy.limit * threshold // 100

    with caplog.at_level(logging.WARNING, logger="flo.api.internal"):
        report = asyncio.run(build_quota_report(_collectors({metric: current})))

    assert len(report.alerts) == 1
    assert report.alerts[0].model_dump() == {
        "metric": metric,
        "threshold_percent": threshold,
        "action": action,
        "cost": cost,
    }
    assert f"metric={metric}" in caplog.text
    assert f"threshold_percent={threshold}" in caplog.text
    assert f"action={action}" in caplog.text
    assert f"cost={cost}" in caplog.text


def test_quota_contract_fails_as_missing_when_a_metric_mapping_is_deleted() -> None:
    collectors = _collectors({})
    del collectors["r2_storage"]

    with pytest.raises(RuntimeError, match="MISSING quota collectors: r2_storage"):
        asyncio.run(build_quota_report(collectors))


def test_quota_failure_discloses_no_secret_in_body_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "postgresql://flo:private@internal.example/flo"
    collectors = _collectors({})

    async def fail() -> int:
        raise RuntimeError(secret)

    collectors["neon_storage"] = fail
    with caplog.at_level(logging.ERROR):
        response = _get_quota(collectors)

    assert response.status_code == 503
    assert response.json()["checks"] == {"neon_storage": "error"}
    assert secret not in response.text
    assert "private" not in response.text
    assert secret not in caplog.text
    assert "private" not in caplog.text
    assert "quota collection failed metric=neon_storage" in caplog.text


def test_google_usage_reads_all_monitoring_and_registry_pages_without_credentials_in_urls() -> None:
    calls: list[tuple[str, str, float]] = []

    def request_json(url: str, token: str, timeout: float) -> dict[str, object]:
        calls.append((url, token, timeout))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        page_token = query.get("pageToken", [None])[0]
        if "monitoring.googleapis.com" in url:
            if page_token is None:
                return {
                    "timeSeries": [{"points": [{"value": {"int64Value": "17"}}]}],
                    "nextPageToken": "monitoring-next",
                }
            assert page_token == "monitoring-next"
            return {"timeSeries": [{"points": [{"value": {"int64Value": "5"}}]}]}
        if page_token is None:
            return {
                "dockerImages": [{"imageSizeBytes": "120"}],
                "nextPageToken": "registry-next",
            }
        assert page_token == "registry-next"
        return {"dockerImages": [{"imageSizeBytes": "30"}]}

    usage = GoogleCloudUsage(
        Settings(gcp_project="flo-test-project"),
        request_json=request_json,
        token_provider=lambda _: "metadata-access-token",
    )

    assert usage.cloud_run_requests_this_month(datetime(2026, 8, 25, tzinfo=UTC)) == 22
    assert usage.artifact_registry_bytes() == 150
    assert len(calls) == 4
    assert all(token == "metadata-access-token" for _, token, _ in calls)
    assert all("metadata-access-token" not in url for url, _, _ in calls)
    assert all(timeout == 5.0 for _, _, timeout in calls)


def test_jobs_tick_rejects_unauthenticated_calls_before_building_worker() -> None:
    settings = Settings(origin_shared_secret=_TEST_ORIGIN_SECRET)
    tick_app = FastAPI()
    tick_app.include_router(internal.router)
    install_problem_details(tick_app)
    tick_app.dependency_overrides[get_origin_settings] = lambda: settings
    built = 0

    def processor_factory() -> internal.TickProcessor:
        nonlocal built
        built += 1

        async def process() -> TickReport:
            return TickReport(
                jobs=TickComponentSummary(claimed=0, done=0, retried=0, dead=0),
                outbox=TickComponentSummary(claimed=0, done=0, retried=0, dead=0),
            )

        return process

    tick_app.dependency_overrides[get_tick_processor] = processor_factory

    async def request() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=tick_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.post("/internal/jobs/tick")
            wrong = await client.post(
                "/internal/jobs/tick",
                headers={ORIGIN_SECRET_HEADER: "wrong-origin-secret"},
            )
            assert built == 0
            accepted = await client.post(
                "/internal/jobs/tick",
                headers={ORIGIN_SECRET_HEADER: _TEST_ORIGIN_SECRET},
            )
            return missing, wrong, accepted

    missing, wrong, accepted = asyncio.run(request())

    assert missing.status_code == 404
    assert wrong.status_code == 404
    assert accepted.status_code == 200
    assert accepted.json() == {
        "jobs": {"claimed": 0, "done": 0, "retried": 0, "dead": 0},
        "outbox": {"claimed": 0, "done": 0, "retried": 0, "dead": 0},
    }
    assert built == 1


def test_jobs_tick_is_absent_from_public_openapi_even_when_router_is_mounted() -> None:
    documented_app = FastAPI()
    documented_app.include_router(internal.router)

    assert "/internal/jobs/tick" not in documented_app.openapi()["paths"]
