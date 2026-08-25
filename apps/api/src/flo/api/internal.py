"""Operational endpoints consumed by the authenticated cron worker."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, cast

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from flo.api.origin_auth import require_origin_secret
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.storage import create_storage

_logger = logging.getLogger(__name__)
_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/token"
)
_DECIMAL_MB = 1_000_000
_DECIMAL_GB = 1_000_000_000

QuotaCollector = Callable[[], Awaitable[int]]
JsonObject = dict[str, object]
JsonRequester = Callable[[str, str, float], JsonObject]
TokenProvider = Callable[[float], str]


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    limit: int
    unit: Literal["bytes", "requests"]
    thresholds: tuple[int, ...]
    action: str
    cost: str


QUOTA_POLICIES: dict[str, QuotaPolicy] = {
    "neon_storage": QuotaPolicy(
        limit=512 * _DECIMAL_MB,
        unit="bytes",
        thresholds=(60, 80, 90),
        action="Archive audit partitions to R2 first; then Neon Launch",
        cost="$19/mo",
    ),
    "cloud_run_requests": QuotaPolicy(
        limit=2_000_000,
        unit="requests",
        thresholds=(60, 80, 90),
        action="Still free; set min-instances=1 for latency",
        cost="~$8–15/mo",
    ),
    "r2_storage": QuotaPolicy(
        limit=10 * _DECIMAL_GB,
        unit="bytes",
        thresholds=(60, 80, 90),
        action="Pay-as-you-go",
        cost="~$0.15/mo per 10 GB",
    ),
    "artifact_registry": QuotaPolicy(
        limit=_DECIMAL_GB // 2,
        unit="bytes",
        thresholds=(80,),
        action="Prune to 3 images (automate first)",
        cost="~$0",
    ),
}


class QuotaAlert(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str
    threshold_percent: int = Field(ge=1, le=100)
    action: str
    cost: str


class QuotaMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current: int = Field(ge=0)
    limit: int = Field(gt=0)
    unit: Literal["bytes", "requests"]
    percentage: float = Field(ge=0)
    alert: QuotaAlert | None = None


class QuotaReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metrics: dict[str, QuotaMetric]
    alerts: tuple[QuotaAlert, ...]


def _json_object(raw: bytes) -> JsonObject:
    value = json.loads(raw)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RuntimeError("provider returned an invalid JSON document")
    return cast(JsonObject, value)


def _metadata_token(timeout_seconds: float) -> str:
    request = urllib.request.Request(
        _METADATA_TOKEN_URL,
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        document = _json_object(response.read())
    token = document.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("metadata server returned no access token")
    return token


def _authorized_json(url: str, access_token: str, timeout_seconds: float) -> JsonObject:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        return _json_object(response.read())


def _objects(value: object, field: str) -> list[Mapping[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise RuntimeError(f"provider returned an invalid {field} collection")
    return cast(list[Mapping[str, object]], value)


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"provider returned an invalid {field} object")
    return cast(Mapping[str, object], value)


class GoogleCloudUsage:
    """Read Cloud Run and Artifact Registry usage with the runtime identity."""

    def __init__(
        self,
        settings: Settings,
        *,
        request_json: JsonRequester = _authorized_json,
        token_provider: TokenProvider = _metadata_token,
    ) -> None:
        if settings.gcp_project is None:
            raise RuntimeError("GCP_PROJECT is not configured")
        self._project = settings.gcp_project
        self._service = settings.cloud_run_service
        self._location = settings.artifact_registry_location
        self._repository = settings.artifact_registry_repository
        self._timeout_seconds = settings.quota_timeout_seconds
        self._request_json = request_json
        self._access_token = token_provider(self._timeout_seconds)

    def _pages(self, base_url: str, query: Mapping[str, str]) -> list[JsonObject]:
        pages: list[JsonObject] = []
        page_token: str | None = None
        while True:
            parameters = dict(query)
            if page_token is not None:
                parameters["pageToken"] = page_token
            url = f"{base_url}?{urllib.parse.urlencode(parameters)}"
            page = self._request_json(url, self._access_token, self._timeout_seconds)
            pages.append(page)
            raw_token = page.get("nextPageToken")
            if raw_token is None:
                return pages
            if not isinstance(raw_token, str) or not raw_token:
                raise RuntimeError("provider returned an invalid next-page token")
            page_token = raw_token

    def cloud_run_requests_this_month(self, now: datetime | None = None) -> int:
        end = now or datetime.now(UTC)
        if end.tzinfo is None:
            raise ValueError("quota clock must be timezone-aware")
        start = end.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        base_url = (
            "https://monitoring.googleapis.com/v3/projects/"
            f"{urllib.parse.quote(self._project, safe='')}/timeSeries"
        )
        query = {
            "filter": (
                'metric.type="run.googleapis.com/request_count" '
                'AND resource.type="cloud_run_revision" '
                f'AND resource.labels.service_name="{self._service}"'
            ),
            "interval.startTime": start.isoformat().replace("+00:00", "Z"),
            "interval.endTime": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "view": "FULL",
            "pageSize": "100000",
        }
        total = 0
        for page in self._pages(base_url, query):
            for series in _objects(page.get("timeSeries"), "timeSeries"):
                for point in _objects(series.get("points"), "points"):
                    value = _mapping(point.get("value"), "point value")
                    raw_count = value.get("int64Value")
                    if not isinstance(raw_count, str) or not raw_count.isdecimal():
                        raise RuntimeError("Cloud Monitoring returned an invalid request count")
                    total += int(raw_count)
        return total

    def artifact_registry_bytes(self) -> int:
        base_url = (
            "https://artifactregistry.googleapis.com/v1/projects/"
            f"{urllib.parse.quote(self._project, safe='')}/locations/{self._location}/"
            f"repositories/{self._repository}/dockerImages"
        )
        total = 0
        for page in self._pages(base_url, {"pageSize": "1000"}):
            for image in _objects(page.get("dockerImages"), "dockerImages"):
                raw_size = image.get("imageSizeBytes")
                if not isinstance(raw_size, str) or not raw_size.isdecimal():
                    raise RuntimeError("Artifact Registry returned an invalid image size")
                total += int(raw_size)
        return total


async def _neon_storage_bytes(settings: Settings) -> int:
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not configured")
    connection = await psycopg.AsyncConnection.connect(settings.database_url.get_secret_value())
    try:
        cursor = await connection.execute("SELECT pg_database_size(current_database())")
        row = await cursor.fetchone()
    finally:
        await connection.close()
    if row is None or not isinstance(row[0], int) or row[0] < 0:
        raise RuntimeError("PostgreSQL returned an invalid database size")
    return row[0]


def get_internal_settings() -> Settings:
    return Settings()


def get_quota_collectors(
    settings: Annotated[Settings, Depends(get_internal_settings)],
) -> Mapping[str, QuotaCollector]:
    google = GoogleCloudUsage(settings)

    async def r2_storage_bytes() -> int:
        storage = create_storage(settings)
        return await asyncio.to_thread(storage.usage_bytes)

    async def cloud_run_requests() -> int:
        return await asyncio.to_thread(google.cloud_run_requests_this_month)

    async def artifact_registry_bytes() -> int:
        return await asyncio.to_thread(google.artifact_registry_bytes)

    return {
        "neon_storage": lambda: _neon_storage_bytes(settings),
        "cloud_run_requests": cloud_run_requests,
        "r2_storage": r2_storage_bytes,
        "artifact_registry": artifact_registry_bytes,
    }


def _alert(metric: str, current: int, policy: QuotaPolicy) -> QuotaAlert | None:
    crossed = tuple(
        threshold
        for threshold in policy.thresholds
        if current * 100 >= policy.limit * threshold
    )
    if not crossed:
        return None
    return QuotaAlert(
        metric=metric,
        threshold_percent=max(crossed),
        action=policy.action,
        cost=policy.cost,
    )


async def build_quota_report(collectors: Mapping[str, QuotaCollector]) -> QuotaReport:
    """Collect every required metric and fail loudly if a mapping disappears."""

    expected = set(QUOTA_POLICIES)
    actual = set(collectors)
    if missing := expected - actual:
        raise RuntimeError(f"MISSING quota collectors: {', '.join(sorted(missing))}")
    if unexpected := actual - expected:
        raise RuntimeError(f"unexpected quota collectors: {', '.join(sorted(unexpected))}")

    async def collect(metric: str) -> int:
        try:
            value = await collectors[metric]()
        except Exception:
            _logger.error("quota collection failed metric=%s", metric)
            raise ProblemError(
                ErrorCode.SERVICE_UNAVAILABLE,
                checks={metric: "error"},
            ) from None
        if value < 0:
            raise RuntimeError(f"quota collector returned a negative value for {metric}")
        return value

    names = tuple(QUOTA_POLICIES)
    values = await asyncio.gather(*(collect(name) for name in names))
    metrics: dict[str, QuotaMetric] = {}
    alerts: list[QuotaAlert] = []
    for name, current in zip(names, values, strict=True):
        policy = QUOTA_POLICIES[name]
        alert = _alert(name, current, policy)
        if alert is not None:
            alerts.append(alert)
            _logger.warning(
                "quota threshold crossed metric=%s threshold_percent=%d action=%s cost=%s",
                alert.metric,
                alert.threshold_percent,
                alert.action,
                alert.cost,
            )
        metrics[name] = QuotaMetric(
            current=current,
            limit=policy.limit,
            unit=policy.unit,
            percentage=round(current * 100 / policy.limit, 2),
            alert=alert,
        )
    return QuotaReport(metrics=metrics, alerts=tuple(alerts))


router = APIRouter(dependencies=[Depends(require_origin_secret)])


@router.get(
    "/internal/health/quota",
    response_model=QuotaReport,
    include_in_schema=False,
)
async def quota_health(
    collectors: Annotated[Mapping[str, QuotaCollector], Depends(get_quota_collectors)],
) -> QuotaReport:
    """Report current free-tier usage and any pre-agreed graduation action."""

    return await build_quota_report(collectors)
