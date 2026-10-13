from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    # Fixtures drop and rebuild schema objects. Without an explicit database they used to fall
    # back to the shared flo_test, which a parallel lane then found half-dropped (13 Oct 2026).
    if not (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")):
        pytest.exit(
            "TEST_DATABASE_URL is not set. Use your own database: "
            'export TEST_DATABASE_URL="$(apps/api/.venv/bin/python agents/scripts/flo db reset)"',
            returncode=2,
        )
