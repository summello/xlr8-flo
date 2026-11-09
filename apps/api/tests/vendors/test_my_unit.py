"""The unit the vendor pages judge eligibility for: the user's one BU assignment (E11-S21)."""

import asyncio

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.identity.service import assigned_unit
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import admin_id
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db
from tests.vendors.test_vendors import regrant, send, unit

VIEW = ["vendor.view"]


def bu_target(unit_id):
    return AuthorizationTarget(ScopeType.BU, unit_id)


def mine(db):
    return assigned_unit(db[0], Scope(db[1]), db[3])


def test_assigned_unit_rule(header_db):
    db = header_db
    plant4, plant7 = unit(db, "P4"), unit(db, "P7")
    assert mine(db) is None  # org-scoped admin only
    regrant(db, VIEW)
    assert mine(db) is None
    regrant(db, VIEW, bu_target(plant7.id), keep=True)
    assert mine(db) == plant7.id  # an org-scoped assignment beside one BU is ignored
    regrant(db, ["vendor.edit"], bu_target(plant7.id), keep=True)
    assert mine(db) == plant7.id  # two roles on the same BU
    regrant(db, VIEW, bu_target(plant4.id), keep=True)
    assert mine(db) is None  # two different BUs: no guess


def test_assigned_unit_ignores_project_future_and_other_tenant(header_db):
    db = header_db
    plant = unit(db, "P7")
    regrant(db, VIEW, AuthorizationTarget(ScopeType.PROJECT, uuid_project(db, plant)))
    assert mine(db) is None  # project-scoped only
    regrant(db, VIEW, bu_target(plant.id))
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE user_role SET effective_from = CURRENT_TIMESTAMP + interval '1 day' "
            "WHERE org_id=%s AND user_id=%s",
            (db[1], db[3]),
        )
    assert mine(db) is None  # not yet effective
    # another tenant's assignment never counts, whichever user it names
    theirs = unit(db, "T1", org=db[2])
    other = admin_id(db[0], "second@example.test")
    regrant((db[0], db[2], None, other), VIEW, bu_target(theirs.id))
    assert assigned_unit(db[0], Scope(db[1]), other) is None
    assert assigned_unit(db[0], Scope(db[2]), other) == theirs.id


def uuid_project(db, plant):
    from tests.requisitions.test_header import project

    return project(db, plant).id


def test_my_unit_route_and_permission(header_db):
    db = header_db
    plant = unit(db, "P7")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            empty = await send(browser, "GET", "/my-unit")  # not parsed as a vendor id
            assert empty.status_code == 200, empty.text
            assert empty.json() == {"unit_id": None, "name": None}
            regrant(db, VIEW, bu_target(plant.id), keep=True)
            got = (await send(browser, "GET", "/my-unit")).json()
            assert got == {"unit_id": str(plant.id), "name": "P7"}
            regrant(db, ["vendor.edit"])
            assert (await send(browser, "GET", "/my-unit")).status_code == 403

    asyncio.run(scenario())


def test_my_unit_route_conceals_foreign_tenants(header_db):
    db = header_db
    theirs = unit(db, "T1", org=db[2])
    other = admin_id(db[0], "second@example.test")
    regrant((db[0], db[2], None, other), VIEW, bu_target(theirs.id))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            got = await send(browser, "GET", "/my-unit")
            assert got.json() == {"unit_id": None, "name": None}  # org A user: no org B unit

    asyncio.run(scenario())
