#!/usr/bin/env python3
"""Forbid session-scoped PostgreSQL SET statements under transaction pooling."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOTS = (ROOT / "apps" / "api" / "src", ROOT / "migrations")
SESSION_SET = re.compile(
    r"(?:^|;)\s*SET\s+(?!LOCAL\b)(?:ROLE\b|SESSION\b|[a-z_][\w.]*\s*(?:=|TO\b))",
    flags=re.IGNORECASE | re.MULTILINE,
)


def _python_strings(path: Path) -> Iterable[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def violations(roots: Iterable[Path]) -> list[str]:
    failures: list[str] = []
    for root in roots:
        if not root.is_dir():
            failures.append(f"MISSING PostgreSQL pooling scan root: {root}")
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix == ".py":
                values = _python_strings(path)
            elif path.suffix == ".sql":
                values = ((1, path.read_text(encoding="utf-8")),)
            else:
                continue
            for line, value in values:
                if SESSION_SET.search(value):
                    failures.append(
                        f"{path}:{line}: session-scoped SET is forbidden; use SET LOCAL"
                    )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="*", type=Path, default=list(DEFAULT_ROOTS))
    args = parser.parse_args(argv)
    try:
        failures = violations(args.roots)
    except (OSError, SyntaxError) as exc:
        print(f"::error::PostgreSQL pooling check could not run: {exc}")
        return 1
    for failure in failures:
        print(f"::error::{failure}")
    if failures:
        return 1
    print("PostgreSQL pooling check passed: every SET is transaction-scoped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
