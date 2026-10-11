from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).parents[1] / "scripts" / "check_money_floats.py"


def run_checker(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    module = tmp_path / "budget"
    module.mkdir()
    (module / "amounts.py").write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(CHECKER), str(module)],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "source",
    [
        "def total() -> float:\n    return 0.0\n",
        "amounts: list[float] = []\n",
        "def f(x: int | float) -> None:\n    pass\n",
        "from typing import Optional\namount: Optional[float] = None\n",
        "amounts: tuple[float, ...] = ()\n",
        "amounts: dict[str, float] = {}\n",
        "amount = float('1.25')\n",
    ],
    ids=[
        "return-annotation",
        "list-parameter",
        "union-member",
        "optional-parameter",
        "tuple-parameter",
        "dict-value",
        "direct-call",
    ],
)
def test_rejects_float_in_money_module(tmp_path: Path, source: str) -> None:
    result = run_checker(tmp_path, source)

    assert result.returncode == 1
    assert "float is banned in money modules" in result.stderr


def test_accepts_decimal_in_money_module(tmp_path: Path) -> None:
    result = run_checker(
        tmp_path,
        "from decimal import Decimal\n"
        "def total(amounts: list[Decimal]) -> Decimal:\n"
        "    return sum(amounts, start=Decimal('0'))\n",
    )

    assert result.returncode == 0
    assert result.stderr == ""


def test_fx_file_is_default_scope_and_float_plant_fails(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("money_checker", CHECKER)
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert any(
        path.as_posix().endswith("modules/org/fx.py") for path in checker.default_money_modules()
    )
    target = tmp_path / "modules" / "org" / "fx.py"
    target.parent.mkdir(parents=True)
    source = next(path for path in checker.default_money_modules() if path.name == "fx.py")
    target.write_text(source.read_text() + "\namount = float(1)\n")
    assert checker.main([str(target)]) == 1
