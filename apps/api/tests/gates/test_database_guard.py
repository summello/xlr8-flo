from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[2]


def test_pytest_refuses_to_run_without_a_database_url() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("TEST_DATABASE_URL", "DATABASE_URL")}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_config.py"],
        cwd=API,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2
    assert "TEST_DATABASE_URL is not set" in result.stdout + result.stderr
