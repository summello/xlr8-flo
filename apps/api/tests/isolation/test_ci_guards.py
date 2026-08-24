from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from flo.api.health import app
from flo.kernel.tenancy.guards import forbidden_org_id_operations, unprotected_tenant_tables

ROOT = Path(__file__).resolve().parents[4]


def test_no_openapi_operation_accepts_org_id() -> None:
    assert forbidden_org_id_operations(app.openapi()) == []


def test_openapi_guard_rejects_org_id_parameters_and_request_bodies() -> None:
    document: dict[str, object] = {
        "paths": {
            "/bad-query": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/OrganizationHeader"}]
                },
            },
            "/bad-body": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/BadBody"}
                            }
                        }
                    }
                }
            },
        },
        "components": {
            "parameters": {"OrganizationHeader": {"name": "Org-Id", "in": "header"}},
            "schemas": {"BadBody": {"properties": {"org_id": {}}}},
        },
    }

    assert forbidden_org_id_operations(document) == ["GET /bad-query", "POST /bad-body"]


def test_every_tenant_table_migration_enables_and_forces_rls() -> None:
    for migration in (ROOT / "migrations").glob("*.py"):
        assert unprotected_tenant_tables(migration.read_text()) == []


def test_migration_guard_detects_money_table_and_rejects_missing_rls() -> None:
    unsafe = """
    def upgrade(connection):
        connection.execute(
            'CREATE TABLE invoice (id uuid, amount NUMERIC(18,4), org_id uuid NOT NULL)'
        )
    """

    assert unprotected_tenant_tables(unsafe) == ["invoice"]


def test_migration_guard_rejects_a_tenant_table_without_force_rls() -> None:
    protected = """
    def upgrade(connection):
        connection.execute('CREATE TABLE invoice (id uuid, org_id uuid NOT NULL)')
        connection.execute('ALTER TABLE invoice ENABLE ROW LEVEL SECURITY')
        connection.execute('ALTER TABLE invoice FORCE ROW LEVEL SECURITY')
        connection.execute(
            "CREATE POLICY tenant_isolation ON invoice "
            "USING (org_id = current_setting('app.org_id')::uuid)"
        )
    """

    assert unprotected_tenant_tables(protected) == []


def test_migration_guard_rejects_constant_tenant_policy() -> None:
    unsafe = """
    def upgrade(connection):
        connection.execute('CREATE TABLE invoice (id uuid, org_id uuid NOT NULL)')
        connection.execute('ALTER TABLE invoice ENABLE ROW LEVEL SECURITY')
        connection.execute('ALTER TABLE invoice FORCE ROW LEVEL SECURITY')
        connection.execute('CREATE POLICY tenant_isolation ON invoice USING (true)')
    """

    assert unprotected_tenant_tables(unsafe) == ["invoice"]


def test_migration_guard_fails_closed_on_unbalanced_table_body() -> None:
    malformed = "CREATE TABLE invoice (id uuid, org_id uuid NOT NULL"

    assert unprotected_tenant_tables(malformed) == ["invoice"]


def test_repository_without_scope_fails_mypy_strict(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid_repo.py"
    invalid.write_text(
        "from flo.kernel.db.repo import ScopedRepo, Session\n"
        "class Item: pass\n"
        "def build(session: Session) -> None:\n"
        "    ScopedRepo[Item](session)\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-color-output", str(invalid)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "MYPYPATH": str(ROOT / "apps" / "api" / "src")},
    )

    assert result.returncode == 1
    assert 'Missing positional argument "scope"' in result.stdout
