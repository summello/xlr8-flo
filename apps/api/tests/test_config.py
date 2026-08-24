import pytest

from flo.kernel.config import Settings


class ExampleSettings(Settings):
    message: str


def test_settings_read_from_the_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLO_MESSAGE", "configured externally")

    assert ExampleSettings().message == "configured externally"
