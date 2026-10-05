from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def assert_build_context_policy(source: str) -> None:
    rules = [line.strip() for line in source.splitlines() if line and not line.startswith("#")]
    assert rules == [
        "**",
        "!apps/",
        "!apps/api/",
        "!apps/api/pyproject.toml",
        "!apps/api/src/",
        "!apps/api/src/**",
        "!migrations/",
        "!migrations/**",
        "**/.env",
        "**/.env.*",
        "**/.git",
        "**/.git/**",
    ], "build context must deny all content except the API inputs, excluding secrets and Git"

    def included(path: str) -> bool:
        allowed = True
        for rule in rules:
            pattern = rule.removeprefix("!").rstrip("/")
            patterns = [pattern]
            if pattern.startswith("**/"):
                patterns.append(pattern[3:])
            if any(fnmatchcase(path, candidate) for candidate in patterns):
                allowed = rule.startswith("!")
        return allowed

    for path in (
        ".env",
        ".env.local",
        ".git",
        ".git/config",
        "apps/web/.env.production",
        "apps/api/src/.env",
        "apps/api/src/flo/.env.local",
        "migrations/.env",
        "migrations/.git/config",
        "apps/web/node_modules/package/index.js",
    ):
        assert not included(path), f"unsafe build context input: {path}"
    for path in (
        "apps/api/pyproject.toml",
        "apps/api/src/flo/kernel/migrate.py",
        "migrations/20260824_0001_kernel.py",
    ):
        assert included(path), f"missing build input: {path}"


@pytest.mark.parametrize("violation", ("!.env", "!.git/**", "!apps/web/**"))
def test_build_context_rejects_secrets_and_git_then_passes(violation: str) -> None:
    source = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    with pytest.raises(AssertionError):
        assert_build_context_policy(source + violation + "\n")
    assert_build_context_policy(source)
