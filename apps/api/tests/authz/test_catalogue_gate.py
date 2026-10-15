"""USR-007: every permission code the API checks is catalogued and grantable by a baseline role."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from flo.modules.identity.models import BASELINE_ROLE_PERMISSIONS, PERMISSION_CODES

SRC = Path(__file__).resolve().parents[2] / "src" / "flo"
CHECKERS = {"require", "permitted", "permits", "check"}
CODE = re.compile(r"[a-z_]+(\.[a-z_]+)+")


def _codes_in(node: ast.AST | None) -> list[str]:
    if node is None:
        return []
    return [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and CODE.fullmatch(n.value)
    ]


def _target_names(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def checked_codes(root: Path) -> dict[str, str]:
    """Map each permission code the code under ``root`` checks to where it appears.

    Collected: string literals passed positionally or by keyword to ``require``, ``permitted``,
    ``permits`` or ``check``; any ``permission=`` keyword; values of module-level ``*TRANSITIONS``
    dicts; class attributes named ``permission`` or ``*_permission`` (document adapters); module
    constants named ``*PERMISSION``.
    """

    found: dict[str, str] = {}

    def add(codes: list[str], path: Path, line: int) -> None:
        for code in codes:
            found.setdefault(code, f"{path.name}:{line}")

    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name in CHECKERS:
                    for arg in (*node.args, *(kw.value for kw in node.keywords)):
                        add(
                            _codes_in(arg) if isinstance(arg, ast.Constant) else [],
                            path,
                            node.lineno,
                        )
                for kw in node.keywords:
                    if kw.arg == "permission":
                        add(_codes_in(kw.value), path, node.lineno)
            elif isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    names = _target_names(stmt)
                    if any(n == "permission" or n.endswith("_permission") for n in names):
                        add(_codes_in(getattr(stmt, "value", None)), path, stmt.lineno)
        for stmt in tree.body:
            for name in _target_names(stmt):
                if name.endswith("TRANSITIONS") or name.endswith("PERMISSION"):
                    add(_codes_in(getattr(stmt, "value", None)), path, stmt.lineno)
    return found


def ungrantable(codes: dict[str, str]) -> list[str]:
    catalogue = {str(code) for code in PERMISSION_CODES}
    granted = {str(code) for grants in BASELINE_ROLE_PERMISSIONS.values() for code in grants}
    return sorted(
        f"{code} ({where})" for code, where in codes.items() if code not in catalogue & granted
    )


def test_every_checked_permission_is_catalogued_and_granted_to_a_baseline_role() -> None:
    codes = checked_codes(SRC)
    assert len(codes) > 30, "the scan found almost nothing; the gate is not looking"
    # The indirect forms are seen: adapter attributes, transition tables and module constants.
    assert {"requisition.approve", "project.complete", "audit.read", "requisition.cancel"} <= set(
        codes
    )
    assert ungrantable(codes) == []


@pytest.mark.parametrize(
    "source",
    [
        'x = require("budget.bogus", target)\n',
        'x = require(permission="budget.bogus", target=target)\n',
        'x = context.permitted("budget.bogus", target)\n',
        'x = permits(request, "budget.bogus", target)\n',
        'Link(permission="budget.bogus")\n',
        'TRANSITIONS = {("a", "b"): "budget.bogus"}\n',
        'TRANSITIONS = {("a", "b"): ("budget.bogus", True)}\n',
        'class Adapter:\n    approve_permission = "budget.bogus"\n',
        'class Adapter:\n    permission: str = "budget.bogus"\n',
        'AUDIT_READ_PERMISSION = "budget.bogus"\n',
    ],
)
def test_the_gate_fails_on_a_planted_unknown_permission(tmp_path: Path, source: str) -> None:
    (tmp_path / "planted.py").write_text(source, encoding="utf-8")
    codes = checked_codes(tmp_path)
    assert list(codes) == ["budget.bogus"]
    assert len(ungrantable(codes)) == 1


def test_the_gate_accepts_a_catalogued_code_in_every_form(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text(
        'a = require(permission="budget.allocate", target=t)\n'
        'TRANSITIONS = {("a", "b"): ("project.complete", True)}\n',
        encoding="utf-8",
    )
    assert ungrantable(checked_codes(tmp_path)) == []
