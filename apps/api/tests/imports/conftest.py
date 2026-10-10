from collections.abc import Iterator

# Production-stack probe fixtures keep framework tests independent of S04 templates.
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.api.imports import get_import_storage
from flo.modules.identity.service import AuthorizationTarget
from flo.modules.imports.handlers import HANDLERS, Issue, RowPlan, register_handler
from flo.modules.imports.schemas import Column, Template
from flo.modules.imports.templates import REGISTRY, register
from tests.authz.conftest import ROOT, AuthorizationDatabase, load_migration
from tests.authz.conftest import authorization_database as authorization_database
from tests.imports.test_upload import MemoryStorage
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.org.test_bootstrap import (
    admin_id,
    connection_url,
    create,
)
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db


@pytest.fixture(autouse=True)
def fixture_template() -> Iterator[None]:
    register(
        Template(
            name="test_fixture",
            version=1,
            columns=(
                Column(name="code", type="text", required=True, example="EXAMPLE"),
                Column(name="amount", type="decimal", example="1234.56"),
                Column(name="on_date", type="date", example="2026-10-08"),
            ),
            key_columns=("code",),
        )
    )
    try:
        yield
    finally:
        REGISTRY.pop("test_fixture")


@pytest.fixture
def imports_db(org_database: AuthorizationDatabase) -> Iterator[AuthorizationDatabase]:
    migration = load_migration(ROOT / "migrations/20261008_0027_import_batch.py", "imports")
    migration.upgrade(org_database.connection)
    rows = load_migration(ROOT / "migrations/20261009_0034_import_rows.py", "import_rows")
    rows.upgrade(org_database.connection)
    try:
        yield org_database
    finally:
        rows.downgrade(org_database.connection)
        migration.downgrade(org_database.connection)


@dataclass
class ProbeHandler:
    name: str = "probe"
    fail_key: str | None = None
    applied: list[str] = field(default_factory=list)

    def plan_row(self, context, row_no, values):
        key = values.get("key", "")
        value = values.get("value", "")
        kind = values.get("kind", "")
        existing = context.connection.execute(
            "SELECT id,value FROM import_probe WHERE org_id=%s AND key=%s",
            (context.scope.org_id, key),
        ).fetchone()
        action = "create" if existing is None else "skip" if existing[1] == value else "update"
        issues = []
        if not context.can(
            "project.create" if action == "create" else "project.update",
            AuthorizationTarget.organization(context.scope.org_id),
        ):
            issues.append(Issue("key", "permission_denied", f"No target permission for {key!r}."))
        codes = {
            "bad-code": "invalid_code",
            "bad-enum": "invalid_enum",
            "missing": "reference_missing",
            "business": "business_rule",
        }
        if kind in codes:
            issues.append(Issue("kind", codes[kind], f"Unsupported reference or value {kind!r}."))
        if value == "warn":
            issues.append(
                Issue("value", "owner_changed", f"Owner changes to {value!r}.", "warning")
            )
        if issues and any(issue.severity == "error" for issue in issues):
            action = "error"
        return RowPlan(
            action,
            tuple(issues),
            {"key": str(key), "value": str(value)},
            str(existing[1]) if existing and action != "error" else None,
        )

    def apply_row(self, context, plan, values):
        key = values["key"]
        row = context.connection.execute(
            "INSERT INTO import_probe(id,org_id,key,value) VALUES(%s,%s,%s,%s) "
            "ON CONFLICT(org_id,key) DO UPDATE SET value=excluded.value RETURNING id",
            (uuid4(), context.scope.org_id, key, values["value"]),
        ).fetchone()
        if key == self.fail_key:
            raise RuntimeError("injected private row failure")
        self.applied.append(str(key))
        return "probe", row[0]


@pytest.fixture
def probe(bootstrap_db, monkeypatch):
    conn = bootstrap_db
    a = create(conn)
    b = create(conn, "SECOND", "second@example.test")
    monkeypatch.setenv("DATABASE_URL", connection_url(conn))
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", "A" * 43)
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_MEMORY_COST_KIB", "8192")
    from flo.api.origin_auth import require_origin_secret

    store = MemoryStorage()
    app.dependency_overrides[require_origin_secret] = lambda: None
    app.dependency_overrides[get_import_storage] = lambda: store
    app.middleware_stack = None
    conn.execute(
        "CREATE TABLE import_probe(id uuid PRIMARY KEY, org_id uuid NOT NULL, "
        "key text NOT NULL,value text NOT NULL,UNIQUE(org_id,key))"
    )
    register(
        Template(
            name="probe",
            version=1,
            columns=(
                Column(name="key", type="text", required=True),
                Column(name="value", type="text", required=True),
                Column(name="kind", type="code"),
            ),
            key_columns=("key",),
        )
    )
    handler = ProbeHandler()
    register_handler(handler)
    try:
        yield conn, a.org_id, b.org_id, admin_id(conn), store, handler
    finally:
        HANDLERS.pop("probe")
        REGISTRY.pop("probe")
        conn.execute("DROP TABLE import_probe")
        app.dependency_overrides.clear()
        app.middleware_stack = None
