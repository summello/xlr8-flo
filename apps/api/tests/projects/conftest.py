from collections.abc import Iterator
from datetime import date

import pytest
from psycopg.conninfo import make_conninfo

from flo.api.auth import get_auth_settings
from flo.api.projects import router
from flo.kernel.config import Settings
from flo.kernel.tenancy.context import Scope
from flo.modules.identity.models import AuthorizationTarget
from flo.modules.org.schemas import MasterCreate, OrgUnitCreate
from flo.modules.org.service import OrgService
from flo.modules.projects.schemas import ProjectCreate
from flo.modules.projects.service import ProjectService
from tests.authz.conftest import ROOT, load_migration
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_effective_access import build_app, grant_permissions
from tests.authz.test_resolver import service_for
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database


@pytest.fixture
def project_db(org_database) -> Iterator:
    db = org_database
    migrations = [
        load_migration(ROOT / ("migrations/" + name), name)
        for name in [
            "20260826_0015_master_records.py",
            "20260826_0017_projects.py",
            "20260826_0018_project_hierarchy.py",
            "20260826_0019_ledger.py",
            "20260826_0020_project_balance.py",
            "20260826_0022_reservation_release.py",
        ]
    ]
    for migration in migrations:
        migration.upgrade(db.connection)
    try:
        for org in (db.org_a, db.org_b):
            svc = OrgService(db.connection, Scope(org), db.actor_id)
            svc.create_master(
                "department",
                MasterCreate(code="D", name="Department", effective_from=date(2000, 1, 1)),
            )
            svc.create_master(
                "ledger_account",
                MasterCreate(
                    code="L",
                    name="Ledger",
                    effective_from=date(2000, 1, 1),
                    attributes={"account_type": "expense"},
                ),
            )
        grant_permissions(db, db.actor_id, "project.create", "project.read", "project.update")
        with service_for(db, Scope(db.org_b), "foreign-creator") as identity:
            role = identity.create_role("foreign-creator", "Foreign creator")
            identity.grant_role(db.actor_id, role.id, AuthorizationTarget.organization(db.org_b))
        yield db
    finally:
        for migration in reversed(migrations):
            migration.downgrade(db.connection)


def settings(db):
    return Settings(
        database_url=make_conninfo(db.connection.info.dsn, password=db.connection.info.password)
    )


def service(db, org=None, connection=None):
    return ProjectService(
        connection or db.connection, Scope(org or db.org_a), db.actor_id, settings(db)
    )


def unit(db, code="BU", org=None):
    return OrgService(db.connection, Scope(org or db.org_a), db.actor_id).create_unit(
        OrgUnitCreate(code=code, name=code, kind="bu")
    )


def body(bu, **changes):
    return ProjectCreate(
        bu_id=bu.id,
        name="Project",
        department_code="D",
        ledger_account_code="L",
        currency="USD",
        **changes,
    )


def app_for(db, viewer=None):
    app = build_app(db, viewer or db.actor_id)
    app.include_router(router)
    app.dependency_overrides[get_auth_settings] = lambda: settings(db)
    return app
