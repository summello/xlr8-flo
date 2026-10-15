import re
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FLO = runpy.run_path(str(ROOT / "agents" / "scripts" / "flo"))
FLO_GLOBALS = FLO["cmd_up"].__globals__


def test_compose_uses_digest_pinned_images_and_defines_healthchecks() -> None:
    compose = (ROOT / "compose.yaml").read_text()
    images = re.findall(r"^\s+image: (\S+)$", compose, flags=re.MULTILINE)

    assert len(images) == 3
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in images)
    assert all(":latest" not in image for image in images)
    assert "minio" not in compose.lower()
    for service in ("postgres", "seaweedfs", "mailpit"):
        match = re.search(
            rf"^  {service}:\n(?P<body>.*?)(?=^  [a-z][a-z-]*:\n|^volumes:\n)",
            compose,
            flags=re.MULTILINE | re.DOTALL,
        )
        assert match is not None
        block = match.group("body")
        assert "healthcheck:" in block


def test_compose_publishes_ports_on_loopback_only() -> None:
    compose = (ROOT / "compose.yaml").read_text()
    published_ports = re.findall(r'^\s+- "([^\"]+:\d+:\d+)"$', compose, flags=re.MULTILINE)

    assert published_ports == [
        "127.0.0.1:5432:5432",
        "127.0.0.1:9000:9000",
        "127.0.0.1:1025:1025",
        "127.0.0.1:8025:8025",
    ]


def _stub_up(monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple], list[str]]:
    calls: list[tuple] = []
    events: list[str] = []
    monkeypatch.setitem(FLO_GLOBALS, "_run_compose", lambda *a, **_: calls.append(a) or object())
    monkeypatch.setitem(FLO_GLOBALS, "_wait_for_stack", lambda **_: events.append("wait"))
    monkeypatch.setitem(FLO_GLOBALS, "_ensure_bucket", lambda: events.append("bucket"))
    return calls, events


def test_up_starts_all_three_then_ensures_bucket(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls, events = _stub_up(monkeypatch)

    FLO["cmd_up"]([])

    assert calls == [("up", "-d", "postgres", "seaweedfs", "mailpit")]
    assert events == ["wait", "bucket"]
    output = capsys.readouterr().out
    assert "postgresql://localhost:5432/flo_dev" in output
    assert "Object store: http://localhost:9000" in output
    assert "http://localhost:8025" in output
    assert "minio" not in output.lower()


def test_up_reset_destroys_volumes_before_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _ = _stub_up(monkeypatch)

    FLO["cmd_up"](["--reset"])

    assert calls == [
        ("down", "--volumes", "--remove-orphans"),
        ("up", "-d", "postgres", "seaweedfs", "mailpit"),
    ]


def test_up_fails_loudly_when_object_store_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*args, **_):
        raise SystemExit("seaweedfs pull failed")

    monkeypatch.setitem(FLO_GLOBALS, "_run_compose", failing)

    with pytest.raises(SystemExit):
        FLO["cmd_up"]([])


def test_down_stops_the_compose_project(monkeypatch: pytest.MonkeyPatch) -> None:
    compose_calls = []
    monkeypatch.setitem(FLO_GLOBALS, "_run_compose", lambda *args: compose_calls.append(args))

    FLO["cmd_down"]([])

    assert compose_calls == [("down",)]


def test_wait_requires_all_services_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def healthy_status(service: str) -> tuple[str, str, int]:
        seen.append(service)
        return "running", "healthy", 0

    monkeypatch.setitem(FLO_GLOBALS, "_container_status", healthy_status)

    FLO["_wait_for_stack"](1)

    assert seen == ["postgres", "seaweedfs", "mailpit"]


def test_environment_example_exposes_dev_and_test_connections() -> None:
    variables = {
        line.split("=", maxsplit=1)[0]
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    }

    assert {
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_BUCKET",
        "S3_REGION",
        "SMTP_HOST",
        "SMTP_PORT",
        "MAILPIT_URL",
    } <= variables
