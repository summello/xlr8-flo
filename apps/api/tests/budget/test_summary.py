"""Currency-separated, unit-scoped dashboard summary through the real stack."""

import asyncio

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget.service import budget_summary
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db
from tests.projects.test_schedule import schedule_db as schedule_db


def test_foreign_summary_unit(schedule_db):
    _, _, _, (_, foreign) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            response = await browser.get(
                "/api/v1/budget/summary", params={"unit_id": str(foreign.bu_id)}
            )
            assert response.status_code == 404, response.text

    asyncio.run(scenario())


def test_summary_scoped_currency_totals(schedule_db):
    conn, org, foreign_org, (root, foreign) = schedule_db
    with tenant_transaction(conn, Scope(foreign_org)):
        conn.execute("UPDATE project_balance SET allocated=999 WHERE project_id=%s", (foreign.id,))
    with tenant_transaction(conn, Scope(org)):
        conn.execute(
            "UPDATE project_balance SET allocated=100,reserved=10,committed=20,actual=30 "
            "WHERE project_id=%s",
            (root.id,),
        )
    result = budget_summary(conn, Scope(org), root.bu_id)
    assert len(result.totals) == 1
    assert result.totals[0].model_dump() == {
        "currency": "USD",
        "allocated": "100.0000",
        "reserved": "10.0000",
        "committed": "20.0000",
        "actual": "30.0000",
        "available": "40.0000",
    }

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for params in ({}, {"unit_id": str(root.bu_id)}):
                response = await browser.get("/api/v1/budget/summary", params=params)
                assert response.status_code == 200, response.text
                assert response.json() == result.model_dump()

    asyncio.run(scenario())


def test_summary_groups_currencies_and_excludes_other_units(schedule_db):
    from flo.kernel.logging import correlation_context
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService
    from flo.modules.projects.schemas import ProjectCreate
    from flo.modules.projects.service import ProjectService
    from tests.org.test_bootstrap import settings

    conn, org, _, (root, _) = schedule_db
    with correlation_context("summary-currencies"):
        svc = ProjectService(conn, Scope(org), root.owner_id, settings(conn))
        other_unit = OrgService(conn, Scope(org), root.owner_id).create_unit(
            OrgUnitCreate(code="OTHER", name="Other", kind="ou", parent_id=root.bu_id)
        )
        rows = []
        for bu_id, currency in ((root.bu_id, "EUR"), (other_unit.id, "USD")):
            rows.append(
                svc.create(
                    ProjectCreate(
                        bu_id=bu_id,
                        name=currency,
                        department_code="D",
                        ledger_account_code="L",
                        currency=currency,
                    )
                )
            )
        with tenant_transaction(conn, Scope(org)):
            for row, amount in ((root, "10.1"), (rows[0], "20.2"), (rows[1], "30.3")):
                conn.execute(
                    "UPDATE project_balance SET allocated=%s WHERE project_id=%s", (amount, row.id)
                )
    selected = budget_summary(conn, Scope(org), root.bu_id)
    assert [(r.currency, r.allocated) for r in selected.totals] == [
        ("EUR", "20.2000"),
        ("USD", "10.1000"),
    ]
    all_units = budget_summary(conn, Scope(org))
    assert [(r.currency, r.allocated) for r in all_units.totals] == [
        ("EUR", "20.2000"),
        ("USD", "40.4000"),
    ]


def test_summary_permission_and_unit_scope_guards(schedule_db):
    from flo.kernel.logging import correlation_context
    from flo.modules.identity.models import AuthorizationTarget, ScopeType
    from flo.modules.identity.service import IdentityAuthorizationService

    conn, org, _, (root, _) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            with correlation_context("summary-permissions"), tenant_transaction(conn, Scope(org)):
                identity = IdentityAuthorizationService(conn, Scope(org), root.owner_id)
                role = identity.create_role("unit-ledger", "Unit ledger reader")
                identity.grant_permission(role.id, "ledger.read")
                conn.execute("DELETE FROM user_role WHERE user_id=%s", (root.owner_id,))
                identity.grant_role(
                    root.owner_id, role.id, AuthorizationTarget(ScopeType.BU, root.bu_id)
                )
            assert (await browser.get("/api/v1/budget/summary")).status_code == 403
            response = await browser.get(
                "/api/v1/budget/summary", params={"unit_id": str(root.bu_id)}
            )
            assert response.status_code == 200, response.text
            with tenant_transaction(conn, Scope(org)):
                conn.execute("DELETE FROM role_permission WHERE role_id=%s", (role.id,))
            assert (
                await browser.get("/api/v1/budget/summary", params={"unit_id": str(root.bu_id)})
            ).status_code == 403
            with tenant_transaction(conn, Scope(org)):
                conn.execute("DELETE FROM user_role WHERE user_id=%s", (root.owner_id,))
            assert (
                await browser.get("/api/v1/budget/summary", params={"unit_id": str(root.bu_id)})
            ).status_code == 404

    asyncio.run(scenario())
