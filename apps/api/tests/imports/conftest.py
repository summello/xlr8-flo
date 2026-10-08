from collections.abc import Iterator

import pytest

from flo.modules.imports.schemas import Column, Template
from flo.modules.imports.templates import REGISTRY, register
from tests.authz.conftest import ROOT, AuthorizationDatabase, load_migration
from tests.authz.conftest import authorization_database as authorization_database
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database


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
    try:
        yield org_database
    finally:
        migration.downgrade(org_database.connection)
