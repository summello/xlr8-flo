#!/usr/bin/env python3
"""Reject float annotations and construction in money modules."""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable
from pathlib import Path

MONEY_MODULES = ("budget", "purchasing", "sourcing", "requisitions")


def annotation_contains_float(annotation: ast.expr) -> bool:
    """Return whether an annotation contains the name ``float``."""
    if any(isinstance(node, ast.Name) and node.id == "float" for node in ast.walk(annotation)):
        return True

    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            parsed = ast.parse(annotation.value, mode="eval")
        except SyntaxError:
            return False
        return any(isinstance(node, ast.Name) and node.id == "float" for node in ast.walk(parsed))

    return False


def annotation_nodes(tree: ast.AST) -> Iterable[ast.expr]:
    """Yield every expression used as an annotation in a Python syntax tree."""
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            yield node.annotation
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            yield node.returns
        elif isinstance(node, ast.TypeAlias):
            yield node.value


def is_float_call(node: ast.Call) -> bool:
    """Return whether a call directly constructs a built-in float."""
    if isinstance(node.func, ast.Name):
        return node.func.id == "float"
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "builtins"
        and node.func.attr == "float"
    )


def check_file(path: Path) -> list[str]:
    """Return diagnostics for prohibited float usage in one Python file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        return [f"{path}: unable to inspect money module: {error}"]

    diagnostics: list[str] = []
    for annotation in annotation_nodes(tree):
        if annotation_contains_float(annotation):
            diagnostics.append(
                f"{path}:{annotation.lineno}:{annotation.col_offset + 1}: "
                "float is banned in money-module annotations; use Decimal"
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and is_float_call(node):
            diagnostics.append(
                f"{path}:{node.lineno}:{node.col_offset + 1}: "
                "float() is banned in money modules; use Decimal"
            )

    return diagnostics


def python_files(paths: Iterable[Path]) -> Iterable[Path]:
    """Yield Python files under explicit files or module directories."""
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            yield path
        elif path.is_dir():
            yield from sorted(path.rglob("*.py"))


def default_money_modules() -> list[Path]:
    """Return the money-module paths that currently exist in the repository."""
    repository = Path(__file__).resolve().parents[3]
    modules = repository / "apps" / "api" / "src" / "flo" / "modules"
    existing: list[Path] = []
    for name in MONEY_MODULES:
        path = modules / name
        if path.is_dir():
            existing.append(path)
        else:
            print(f"notice: module {name} not built yet")
    return existing


def main(argv: list[str] | None = None) -> int:
    """Check configured paths and return a process exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    arguments = parser.parse_args(argv)
    paths = arguments.paths or default_money_modules()

    diagnostics = [diagnostic for path in python_files(paths) for diagnostic in check_file(path)]
    if diagnostics:
        print("\n".join(diagnostics), file=sys.stderr)
        print("float is banned in money modules (AGENTS.md 3.1)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
