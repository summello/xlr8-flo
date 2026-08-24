import pytest
from pydantic import ValidationError

from flo.kernel.config import Settings


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

    monkeypatch.setenv("FLO_IDENTITY_ARGON2_TIME_COST", "4")

    assert Settings().identity_argon2_time_cost == 4


def test_argon2_memory_cost_cannot_consume_the_whole_instance() -> None:
    assert Settings(identity_argon2_memory_cost_kib=256 * 1024).identity_argon2_memory_cost_kib == (
        256 * 1024
    )

    with pytest.raises(ValidationError):
        Settings(identity_argon2_memory_cost_kib=256 * 1024 + 1)
