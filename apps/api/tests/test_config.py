from pathlib import Path

import pytest
from pydantic import ValidationError

from flo.kernel.config import Settings, enforce_argon2_memory_limit


class ExampleSettings(Settings):
    message: str


def test_settings_read_from_the_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLO_MESSAGE", "configured externally")

    assert ExampleSettings().message == "configured externally"


def test_argon2_cost_defaults_and_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = Settings()
    assert defaults.identity_argon2_time_cost == 3
    assert defaults.identity_argon2_memory_cost_kib == 64 * 1024
    assert defaults.identity_argon2_parallelism == 4
    assert defaults.identity_argon2_max_concurrency == 4
    assert defaults.required_argon2_instance_memory_mib == 512

    monkeypatch.setenv("FLO_IDENTITY_ARGON2_TIME_COST", "4")

    assert Settings().identity_argon2_time_cost == 4


def test_session_timeout_defaults_are_configurable_and_ordered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = Settings()
    assert defaults.session_idle_timeout_seconds == 8 * 60 * 60
    assert defaults.session_absolute_timeout_seconds == 12 * 60 * 60

    monkeypatch.setenv("FLO_SESSION_IDLE_TIMEOUT_SECONDS", "3600")
    monkeypatch.setenv("FLO_SESSION_ABSOLUTE_TIMEOUT_SECONDS", "7200")
    configured = Settings()
    assert configured.session_idle_timeout_seconds == 3600
    assert configured.session_absolute_timeout_seconds == 7200

    with pytest.raises(
        ValidationError,
        match="session absolute timeout must be at least the idle timeout",
    ):
        Settings(
            session_idle_timeout_seconds=7200,
            session_absolute_timeout_seconds=3600,
        )


def test_argon2_memory_cost_cannot_consume_the_whole_instance() -> None:
    assert Settings(identity_argon2_memory_cost_kib=256 * 1024).identity_argon2_memory_cost_kib == (
        256 * 1024
    )

    with pytest.raises(ValidationError):
        Settings(identity_argon2_memory_cost_kib=256 * 1024 + 1)


def test_argon2_concurrency_is_bounded_by_configuration() -> None:
    assert Settings(identity_argon2_max_concurrency=32).identity_argon2_max_concurrency == 32

    with pytest.raises(ValidationError):
        Settings(identity_argon2_max_concurrency=0)
    with pytest.raises(ValidationError):
        Settings(identity_argon2_max_concurrency=33)


def test_neon_database_url_requires_the_transaction_pooler_without_leaking_it() -> None:
    direct_url = "postgresql://flo:do-not-log@ep-example.us-east-2.aws.neon.tech/flo"

    with pytest.raises(ValidationError) as failure:
        Settings(database_url=direct_url)

    rendered = str(failure.value)
    assert "DATABASE_URL must use the Neon pooled endpoint" in rendered
    assert direct_url not in rendered
    assert "do-not-log" not in rendered


def test_neon_pooled_and_local_database_urls_are_accepted() -> None:
    pooled = "postgresql://flo:secret@ep-example-pooler.us-east-2.aws.neon.tech/flo"
    local = "postgresql://flo:flo-local@127.0.0.1:5432/flo_test"

    assert Settings(database_url=pooled).database_url is not None
    assert Settings(database_url=local).database_url is not None


def test_storage_secrets_are_masked_in_settings_output() -> None:
    settings = Settings(
        origin_shared_secret="a-private-origin-secret-at-least-32-bytes",
        storage_access_key_id="private-access",
        storage_secret_access_key="private-secret",
    )

    rendered = repr(settings)
    assert "a-private-origin-secret-at-least-32-bytes" not in rendered
    assert "private-access" not in rendered
    assert "private-secret" not in rendered
    assert rendered.count("**********") >= 3


def test_origin_shared_secret_rejects_short_values() -> None:
    with pytest.raises(ValidationError):
        Settings(origin_shared_secret="too-short")


def test_startup_refuses_a_cgroup_below_the_argon2_requirement(tmp_path: Path) -> None:
    memory_limit = tmp_path / "memory.max"
    memory_limit.write_text(str(511 * 1024 * 1024), encoding="ascii")

    with pytest.raises(RuntimeError, match="need at least 512 MiB"):
        enforce_argon2_memory_limit(Settings(), memory_limit)


@pytest.mark.parametrize("limit", (None, "max"))
def test_startup_skips_an_unreadable_or_unbounded_cgroup(
    tmp_path: Path, limit: str | None
) -> None:
    memory_limit = tmp_path / "memory.max"
    if limit is not None:
        memory_limit.write_text(limit, encoding="ascii")

    enforce_argon2_memory_limit(Settings(), memory_limit)
