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

    assert len(images) == 4
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in images)
    assert all(":latest" not in image for image in images)
    for service in ("postgres", "minio", "mailpit"):
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
        "127.0.0.1:9001:9001",
        "127.0.0.1:1025:1025",
        "127.0.0.1:8025:8025",
    ]


def test_up_waits_for_health_and_prints_all_four_urls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    compose_calls = []
    waited = []
    monkeypatch.setitem(FLO_GLOBALS, "_run_compose", lambda *args: compose_calls.append(args))
    monkeypatch.setitem(FLO_GLOBALS, "_wait_for_stack", lambda: waited.append(True))

    FLO["cmd_up"]([])

    assert compose_calls == [("up", "-d")]
    assert waited == [True]
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 4
    assert "postgresql://localhost:5432/flo_dev" in output
    assert "http://localhost:9000" in output
    assert "http://localhost:9001" in output
    assert "http://localhost:8025" in output


def test_up_reset_destroys_volumes_before_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    compose_calls = []
    monkeypatch.setitem(FLO_GLOBALS, "_run_compose", lambda *args: compose_calls.append(args))
    monkeypatch.setitem(FLO_GLOBALS, "_wait_for_stack", lambda: None)

    FLO["cmd_up"](["--reset"])

    assert compose_calls == [
        ("down", "--volumes", "--remove-orphans"),
        ("up", "-d"),
    ]


def test_down_stops_the_compose_project(monkeypatch: pytest.MonkeyPatch) -> None:
    compose_calls = []
    monkeypatch.setitem(FLO_GLOBALS, "_run_compose", lambda *args: compose_calls.append(args))

    FLO["cmd_down"]([])

    assert compose_calls == [("down",)]


def test_wait_requires_all_services_and_bucket_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def healthy_status(service: str, include_stopped: bool = False) -> tuple[str, str, int]:
        assert include_stopped is (service == "minio-init")
        if service == "minio-init":
            return "exited", "", 0
        return "running", "healthy", 0

    monkeypatch.setitem(FLO_GLOBALS, "_container_status", healthy_status)

    FLO["_wait_for_stack"](1)


def test_environment_example_exposes_dev_and_test_connections() -> None:
    variables = {
        line.split("=", maxsplit=1)[0]
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    }

    assert {
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_BUCKET",
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_BUCKET",
        "S3_REGION",
        "SMTP_HOST",
        "SMTP_PORT",
        "MAILPIT_URL",
    } <= variables
