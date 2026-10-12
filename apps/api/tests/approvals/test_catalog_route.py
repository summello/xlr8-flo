"""Catalogue uses the registered server fields and session authorization."""

import asyncio

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals import conditions
from flo.modules.approvals.service import register_document_type
from flo.modules.identity.service import IdentityAuthorizationService
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db


class Adapter:
    document_type = "requisition"
    approve_permission = "requisition.approve"

    def fields(self):
        return {"amount": "money"}


def test_catalog_registered_fields_and_foreign_tenant(workflow_db, monkeypatch):
    db = workflow_db
    monkeypatch.setattr(conditions, "_adapters", {})
    monkeypatch.setattr(conditions, "_providers", {})
    register_document_type(Adapter())

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            result = await browser.get(
                "/api/v1/workflows/catalog?document_type=requisition", headers=headers(browser)
            )
            assert result.status_code == 200, result.text
            assert result.json() == {"amount": "money"}
            # Collection endpoint has no record id: a foreign tenant cannot be selected
            # by injecting its org_id. The catalogue is global, not tenant business data.
            foreign = await browser.get(
                f"/api/v1/workflows/catalog?document_type=requisition&org_id={db[2]}",
                headers=headers(browser),
            )
            assert foreign.status_code == 200, foreign.text
            assert foreign.json() == {"amount": "money"}
            # Change the registered catalogue: proves the response is not hardcoded.
            monkeypatch.setattr(Adapter, "fields", lambda self: {"server_field": "decimal"})
            result = await browser.get(
                "/api/v1/workflows/catalog?document_type=requisition", headers=headers(browser)
            )
            assert result.json() == {"server_field": "decimal"}
            role = (
                db[0]
                .execute(
                    "SELECT id FROM role WHERE org_id=%s AND code='organization-administrator'",
                    (db[1],),
                )
                .fetchone()[0]
            )
            IdentityAuthorizationService(db[0], Scope(db[1]), db[3]).revoke_permission(
                role, "workflow.read"
            )
            denied = await browser.get(
                "/api/v1/workflows/catalog?document_type=requisition", headers=headers(browser)
            )
            assert denied.status_code == 403, denied.text
            foreign = await browser.get(
                f"/api/v1/workflows/catalog?document_type=requisition&org_id={db[2]}",
                headers=headers(browser),
            )
            assert foreign.status_code == 403, foreign.text

    asyncio.run(scenario())
