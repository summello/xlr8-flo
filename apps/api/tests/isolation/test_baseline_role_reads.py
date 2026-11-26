"""E22-S06 TEN-010: the newly readable project and master lists stop at the tenant."""

from datetime import date

from flo.kernel.tenancy.context import Scope
from flo.modules.identity.models import AuthorizationTarget
from flo.modules.org.schemas import MasterCreate
from flo.modules.org.service import OrgService
from tests.authz.conftest import authorization_database as authorization_database
from tests.identity.test_baseline_role_reads import call, holder
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import body, service, unit
from tests.projects.conftest import project_db as project_db


def test_requestor_of_org_a_cannot_read_org_b_project(project_db):
    db = project_db
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    viewer = holder(db, "requestor", AuthorizationTarget.organization(db.org_a))
    assert call(db, viewer, "GET", f"/api/v1/projects/{foreign.id}").status_code == 404


def test_requestor_of_org_a_master_list_excludes_org_b_rows(project_db):
    db = project_db
    OrgService(db.connection, Scope(db.org_b), db.actor_id).create_master(
        "department", MasterCreate(code="FOREIGN", name="Foreign", effective_from=date(2000, 1, 1))
    )
    viewer = holder(db, "requestor", AuthorizationTarget.organization(db.org_a))
    response = call(db, viewer, "GET", "/api/v1/master/department")
    assert response.status_code == 200
    assert [row["code"] for row in response.json()["rows"]] == ["D"]
