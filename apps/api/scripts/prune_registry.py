#!/usr/bin/env python3
"""Retain and verify exactly the three newest Artifact Registry images."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Sequence

KEEP_IMAGES = 3
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
Runner = Callable[..., subprocess.CompletedProcess[str]]


class RegistryPruneError(RuntimeError):
    """A safe operator-facing registry cleanup failure."""


def _run(command: Sequence[str], runner: Runner) -> str:
    result = runner(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RegistryPruneError("Artifact Registry command failed; inspect the CI step")
    return result.stdout


def _versions(image: str, runner: Runner) -> tuple[str, ...]:
    output = _run(
        (
            "gcloud",
            "artifacts",
            "docker",
            "images",
            "list",
            image,
            "--sort-by=~UPDATE_TIME",
            "--format=value(version)",
        ),
        runner,
    )
    versions = tuple(line.strip() for line in output.splitlines() if line.strip())
    invalid = tuple(version for version in versions if _DIGEST.fullmatch(version) is None)
    if invalid:
        raise RegistryPruneError("Artifact Registry returned a malformed image digest")
    if len(set(versions)) != len(versions):
        raise RegistryPruneError("Artifact Registry returned duplicate image digests")
    return versions


def prune_registry(
    image: str,
    current_image: str,
    *,
    runner: Runner = subprocess.run,
) -> tuple[str, ...]:
    """Delete older versions and prove the expected versions remain."""

    current_digest = current_image.rpartition("@")[2]
    if _DIGEST.fullmatch(current_digest) is None:
        raise RegistryPruneError("current image must be an immutable sha256 digest reference")

    before = _versions(image, runner)
    if current_digest not in before:
        raise RegistryPruneError("the deployed image digest is MISSING from Artifact Registry")
    expected = before[:KEEP_IMAGES]
    for version in before[KEEP_IMAGES:]:
        _run(
            (
                "gcloud",
                "artifacts",
                "docker",
                "images",
                "delete",
                f"{image}@{version}",
                "--quiet",
            ),
            runner,
        )

    remaining = _versions(image, runner)
    if remaining != expected:
        raise RegistryPruneError(
            "registry prune verification failed: expected the three newest available images"
        )
    if current_digest not in remaining:
        raise RegistryPruneError("registry prune removed the deployed image")
    return remaining


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--current-image", required=True)
    args = parser.parse_args(argv)
    try:
        remaining = prune_registry(args.image, args.current_image)
    except RegistryPruneError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(f"registry prune verified: {len(remaining)} image(s), newest {KEEP_IMAGES} retained")
    return 0


if __name__ == "__main__":
    sys.exit(main())
