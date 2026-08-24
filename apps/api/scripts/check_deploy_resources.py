#!/usr/bin/env python3
"""Fail CI when Cloud Run flags cannot contain the configured Argon2 workload."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DEFAULT_CONFIG = ROOT / "apps" / "api" / "src" / "flo" / "kernel" / "config.py"
BASELINE_MEMORY_MIB = 256


def _integer_expression(node: ast.expr) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult)):
        left = _integer_expression(node.left)
        right = _integer_expression(node.right)
        return left + right if isinstance(node.op, ast.Add) else left * right
    raise ValueError("configuration default must be an integer literal expression")


def _settings_defaults(config_path: Path) -> dict[str, int]:
    tree = ast.parse(config_path.read_text(encoding="utf-8"), filename=str(config_path))
    wanted = {
        "identity_argon2_memory_cost_kib",
        "identity_argon2_max_concurrency",
    }
    defaults: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Settings":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign):
                continue
            if not isinstance(statement.target, ast.Name) or statement.target.id not in wanted:
                continue
            if not isinstance(statement.value, ast.Call):
                raise ValueError(f"{statement.target.id} must use Field(default=...)")
            default = next(
                (keyword.value for keyword in statement.value.keywords if keyword.arg == "default"),
                None,
            )
            if default is None:
                raise ValueError(f"{statement.target.id} has no explicit default")
            defaults[statement.target.id] = _integer_expression(default)
    missing = wanted - defaults.keys()
    if missing:
        raise ValueError(f"missing Settings defaults: {', '.join(sorted(missing))}")
    return defaults


def _cloud_run_deploy_command(workflow_path: Path) -> str:
    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if "gcloud run deploy flo-api" not in line:
            continue
        command_parts = [line.strip().removesuffix("\\").strip()]
        while line.rstrip().endswith("\\"):
            index += 1
            if index >= len(lines):
                raise ValueError("Cloud Run deploy command ends with an unfinished continuation")
            line = lines[index]
            command_parts.append(line.strip().removesuffix("\\").strip())
        return " ".join(command_parts)
    raise ValueError("workflow has no 'gcloud run deploy flo-api' command")


def _flag(command: str, name: str) -> str:
    match = re.search(rf"(?:^|\s)--{re.escape(name)}(?:=|\s+)([^\s]+)", command)
    if match is None:
        raise ValueError(f"Cloud Run deploy is missing --{name}")
    return match.group(1)


def _memory_mib(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(Mi|Gi)", value)
    if match is None:
        raise ValueError("--memory must be an integer Mi or Gi quantity")
    amount = int(match.group(1))
    return amount * 1024 if match.group(2) == "Gi" else amount


def validate(workflow_path: Path, config_path: Path) -> list[str]:
    defaults = _settings_defaults(config_path)
    command = _cloud_run_deploy_command(workflow_path)
    deployed_memory_mib = _memory_mib(_flag(command, "memory"))
    max_concurrency = defaults["identity_argon2_max_concurrency"]
    hashing_kib = defaults["identity_argon2_memory_cost_kib"] * max_concurrency
    required_memory_mib = (hashing_kib + 1023) // 1024 + BASELINE_MEMORY_MIB
    failures: list[str] = []

    if deployed_memory_mib < required_memory_mib:
        failures.append(
            "Cloud Run --memory is below the Argon2 invariant: "
            f"configured {deployed_memory_mib} MiB, require {required_memory_mib} MiB"
        )

    try:
        cpu = Decimal(_flag(command, "cpu"))
    except InvalidOperation as exc:
        raise ValueError("--cpu must be numeric") from exc
    if cpu < 2:
        failures.append("Cloud Run --cpu must be at least 2 for Argon2 parallelism")

    try:
        request_concurrency = int(_flag(command, "concurrency"))
    except ValueError as exc:
        raise ValueError("--concurrency must be an integer") from exc
    if request_concurrency < max_concurrency:
        failures.append(
            "Cloud Run --concurrency must not be lower than "
            "identity_argon2_max_concurrency"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    try:
        failures = validate(args.workflow, args.config)
    except (OSError, SyntaxError, ValueError) as exc:
        print(f"::error::deploy resource check could not run: {exc}")
        return 1
    for failure in failures:
        print(f"::error::{failure}")
    if failures:
        return 1
    print("deploy resource check passed: Cloud Run contains the configured Argon2 workload")
    return 0


if __name__ == "__main__":
    sys.exit(main())
