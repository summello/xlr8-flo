from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi import FastAPI

from flo.api.admin_users import get_admin_connection
from flo.api.org import router
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.org.service import OrgService
from tests.authz.conftest import (
    ROOT,
    AuthorizationDatabase,
    load_migration,
)
from tests.authz.conftest import (
    authorization_database as authorization_database,
)
from tests.authz.test_effective_access import build_app as access_app
from tests.authz.test_resolver import service_for


@pytest.fixture
def org_database(authorization_database: AuthorizationDatabase) -> Iterator[AuthorizationDatabase]:
    db = authorization_database
    # Seed both organizations in the pre-migration tables to exercise the backfill.
    for org in (db.org_a, db.org_b):
        with service_for(db, Scope(org), "org-seed") as service:
            service.create_role("seed", "Seed")
    migration = load_migration(ROOT / "migrations/20260826_0013_org_units.py", "org_migration")
    migration.upgrade(db.connection)
    try:
        yield db
    finally:
        migration.downgrade(db.connection)


def service(db: AuthorizationDatabase, org: UUID | None = None) -> OrgService:
    return OrgService(db.connection, Scope(org or db.org_a), db.actor_id)


def app_for(db: AuthorizationDatabase, viewer: object) -> FastAPI:
    app = access_app(db, viewer)
    app.include_router(router)
    app.dependency_overrides[get_admin_connection] = lambda: db.connection
    return app


@pytest.fixture(autouse=True)
def correlation() -> Iterator[None]:
    with correlation_context("org-story-test"):
        yield
