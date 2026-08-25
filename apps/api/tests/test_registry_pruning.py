from __future__ import annotations

import importlib.util
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "apps" / "api" / "scripts" / "prune_registry.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prune_registry", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


class FakeGcloud:
    def __init__(self, *, delete_works: bool = True) -> None:
        self.versions: list[str] = []
        self.delete_works = delete_works

    def deploy(self, version: str) -> None:
        self.versions.insert(0, version)

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert capture_output is True
        assert text is True
        if command[4] == "list":
            return subprocess.CompletedProcess(command, 0, "\n".join(self.versions) + "\n", "")
        assert command[4] == "delete"
        if self.delete_works:
            version = command[-2].rpartition("@")[2]
            self.versions.remove(version)
        return subprocess.CompletedProcess(command, 0, "", "")


def test_four_deploys_leave_exactly_three_images() -> None:
    module = _load_script()
    gcloud = FakeGcloud()
    image = "us-central1-docker.pkg.dev/project/flo/flo-api"

    for number in range(1, 5):
        current = _digest(number)
        gcloud.deploy(current)
        remaining = module.prune_registry(
            image,
            f"{image}@{current}",
            runner=gcloud,
        )

    assert remaining == (_digest(4), _digest(3), _digest(2))
    assert gcloud.versions == [_digest(4), _digest(3), _digest(2)]


def test_prune_guard_bites_when_delete_reports_success_but_does_nothing() -> None:
    module = _load_script()
    gcloud = FakeGcloud(delete_works=False)
    image = "us-central1-docker.pkg.dev/project/flo/flo-api"
    for number in range(1, 5):
        gcloud.deploy(_digest(number))

    with pytest.raises(module.RegistryPruneError, match="prune verification failed"):
        module.prune_registry(
            image,
            f"{image}@{_digest(4)}",
            runner=gcloud,
        )


def test_prune_fails_as_missing_when_current_digest_is_absent() -> None:
    module = _load_script()
    gcloud = FakeGcloud()
    image = "us-central1-docker.pkg.dev/project/flo/flo-api"
    gcloud.deploy(_digest(1))

    with pytest.raises(module.RegistryPruneError, match="MISSING"):
        module.prune_registry(
            image,
            f"{image}@{_digest(2)}",
            runner=gcloud,
        )
