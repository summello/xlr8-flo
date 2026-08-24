#!/usr/bin/env python3
"""Run CI guards for tenant-safe API inputs and database migrations."""

from __future__ import annotations

import sys
from pathlib import Path

from flo.api.health import app
from flo.kernel.tenancy.guards import forbidden_org_id_operations, unprotected_tenant_tables

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "migrations"


def main() -> int:
    """Run all tenancy guards against the checked-out application."""

    failures = [
        f"OpenAPI operation accepts org_id: {operation}"
        for operation in forbidden_org_id_operations(app.openapi())
    ]
    for migration in sorted(MIGRATIONS.glob("*.py")):
        for table in unprotected_tenant_tables(migration.read_text()):
            failures.append(f"{migration}: tenant table {table} is missing mandatory RLS")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("tenancy guards passed: OpenAPI inputs and tenant-table migrations are scoped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
