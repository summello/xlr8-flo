"""USR-007: a tenant seeded with the baseline roles can allocate budget and read the ledger."""

import asyncio

import httpx

from flo.kernel.tenancy.context import Scope
from flo.modules.identity.models import AuthorizationTarget
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_resolver import insert_identity, service_for
from tests.budget.test_allocation import allocation_db as allocation_db
from tests.budget.test_allocation import app, payload, project, request
from tests.budget.test_post_entry import posting_db as posting_db
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import project_db as project_db


def holder(db, role_code: str):
    user = insert_identity(db, role_code)
    with service_for(db, Scope(db.org_a), "seed-baseline") as service:
        roles = service.seed_baseline_roles()
        role = next(r for code, r in roles.items() if str(code) == role_code)
        service.grant_role(user, role.id, AuthorizationTarget.organization(db.org_a))
    return user


def read_indicators(db, row, viewer):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app(db, viewer)), base_url="https://testserver"
        ) as client:
            return await client.get(f"/api/v1/projects/{row.id}/tree?include=balances")

    return asyncio.run(send())


def test_seeded_organization_administrator_allocates_and_reads_the_ledger(allocation_db):
    db = allocation_db
    row = project(db)
    admin = holder(db, "organization-administrator")
    assert request(db, row, data=payload(), viewer=admin).status_code == 201
    assert read_indicators(db, row, admin).status_code == 200


def test_seeded_requestor_can_neither_allocate_nor_read_the_ledger(allocation_db):
    db = allocation_db
    row = project(db)
    requestor = holder(db, "requestor")
    assert request(db, row, data=payload(), viewer=requestor).status_code == 403
    assert read_indicators(db, row, requestor).status_code == 403
