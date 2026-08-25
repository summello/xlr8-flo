#!/usr/bin/env python3
"""Fail CI when any FastAPI route omits an explicit RBAC classification."""

from __future__ import annotations

import sys

from flo.api.health import app
from flo.kernel.authz import route_authorization_failures


def main() -> int:
    """Check the complete production route table rather than selected modules."""

    failures = route_authorization_failures(app)
    if failures:
        print(
            "unguarded API routes (add public_route or require(permission, target)):",
            file=sys.stderr,
        )
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("route authorization guard passed: every API route is classified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
