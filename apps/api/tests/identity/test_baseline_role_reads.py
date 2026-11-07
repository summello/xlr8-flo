"""E22-S06 (USR-005, USR-006, ACC-004): baseline roles read, not write."""

import asyncio

import httpx
import pytest

from flo.api.master import router as master_router
from flo.kernel.tenancy.context import Scope
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_resolver import insert_identity, service_for
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import app_for, body, service, unit
from tests.projects.conftest import project_db as project_db

ROLES = ("requestor", "approver", "sourcing-specialist", "purchasing-user", "vendor-manager")


def holder(db, role_code, target):
    user = insert_identity(db, role_code)
    with service_for(db, Scope(db.org_a), "seed-baseline") as identity:
        role = identity.seed_baseline_roles()[role_code]
        identity.grant_role(user, role.id, target)
    return user


def call(db, viewer, method, path, json=None, headers=None):
    app = app_for(db, viewer)
    app.include_router(master_router)

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            return await client.request(method, path, json=json, headers=headers)

    return asyncio.run(send())


@pytest.mark.parametrize("role", ROLES)
def test_baseline_role_reads_both_and_still_cannot_write(project_db, role):
    db = project_db
    bu = unit(db)
    row = service(db).create(body(bu))
    viewer = holder(db, role, AuthorizationTarget.organization(db.org_a))
    assert call(db, viewer, "GET", "/api/v1/projects").status_code == 200
    assert call(db, viewer, "GET", f"/api/v1/projects/{row.id}").status_code == 200
    assert call(db, viewer, "GET", "/api/v1/master/department").status_code == 200
    create = body(bu).model_dump(mode="json")
    assert call(db, viewer, "POST", "/api/v1/projects", json=create).status_code == 403
    patch = {"name": "X"}
    assert (
        call(
            db, viewer, "PATCH", f"/api/v1/projects/{row.id}", patch, {"If-Match": "1"}
        ).status_code
        == 403
    )
    assert (
        call(
            db, viewer, "POST", "/api/v1/master/department", {"code": "Z", "name": "Z"}
        ).status_code
        == 403
    )


def test_bu_scoped_requestor_reads_own_bu_project_and_other_bu_is_404(project_db):
    db = project_db
    a, b = unit(db, "A"), unit(db, "B")
    mine, other = service(db).create(body(a)), service(db).create(body(b))
    viewer = holder(db, "requestor", AuthorizationTarget(ScopeType.BU, a.id))
    assert call(db, viewer, "GET", f"/api/v1/projects/{mine.id}").status_code == 200
    assert call(db, viewer, "GET", f"/api/v1/projects/{other.id}").status_code == 404
    listed = call(db, viewer, "GET", "/api/v1/projects")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["rows"]] == [str(mine.id)]
    masters = call(db, viewer, "GET", "/api/v1/master/department")
    assert masters.status_code == 200
    assert [row["code"] for row in masters.json()["rows"]] == ["D"]  # org-level rows


def test_project_scoped_requestor_lists_only_that_project(project_db):
    db = project_db
    a = unit(db, "A")
    mine, other = service(db).create(body(a)), service(db).create(body(a))
    viewer = holder(db, "requestor", AuthorizationTarget(ScopeType.PROJECT, mine.id))
    listed = call(db, viewer, "GET", "/api/v1/projects")
    assert [row["id"] for row in listed.json()["rows"]] == [str(mine.id)]
    assert call(db, viewer, "GET", f"/api/v1/projects/{other.id}").status_code == 404


def test_reader_with_no_grant_anywhere_still_gets_403_on_the_lists(project_db):
    db = project_db
    viewer = insert_identity(db, "nobody")
    assert call(db, viewer, "GET", "/api/v1/projects").status_code == 403
    assert call(db, viewer, "GET", "/api/v1/master/department").status_code == 403
