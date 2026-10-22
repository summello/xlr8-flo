"""Vendor record, list, availability and local disable through production middleware."""

import asyncio
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.service import (
    AuthorizationTarget,
    IdentityAuthorizationService,
)
from flo.modules.org.schemas import OrgUnitCreate, SettingPut
from flo.modules.vendors.service import VendorService
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_header import org_svc


def unit(db, code, parent=None, org=None, kind="bu"):
    return org_svc(db, org).create_unit(
        OrgUnitCreate(code=code, name=code, kind=kind, parent_id=parent)
    )


def allow_local_disable(db, value=True):
    org_svc(db).set_setting("vendor.allow_local_disable", SettingPut(value=value))


async def send(browser, method, suffix="", data=None, match="auto"):
    """`match`: "auto" sends the live version on a PATCH, None omits If-Match, else the value."""
    extra = {}
    if method == "PATCH" and match is not None:
        extra["If-Match"] = str(
            await current_version(browser, suffix) if match == "auto" else match
        )
    return await browser.request(
        method,
        "/api/v1/vendors" + suffix,
        json=data,
        headers=headers(browser, uuid4().hex) | extra,
    )


async def current_version(browser, suffix):
    """The live version of the vendor or contact a PATCH targets (1 when it is not visible)."""
    parts = suffix.strip("/").split("/")
    if len(parts) == 3:  # <vendor>/contacts/<contact>
        got = await browser.request("GET", f"/api/v1/vendors/{parts[0]}/contacts")
        rows = [c for c in got.json() if c["id"] == parts[2]] if got.status_code == 200 else []
    else:
        got = await browser.request("GET", "/api/v1/vendors/" + parts[0])
        rows = [got.json()] if got.status_code == 200 else []
    return rows[0]["version"] if rows else 1


def body(name="Acme", legal="Acme Ltd", availability="global", unit_ids=()):
    return {
        "trading_name": name,
        "legal_name": legal,
        "availability": availability,
        "unit_ids": [str(u) for u in unit_ids],
    }


def regrant(db, permissions, target=None, keep=False):
    """Replace the admin's roles with one holding only `permissions` (keep=True adds one)."""
    with tenant_transaction(db[0], Scope(db[1])):
        if not keep:
            db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
        identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
        role = identity.create_role(f"r{uuid4().hex[:8]}", "Test role")
        for permission in permissions:
            identity.grant_permission(role.id, permission)
        identity.grant_role(db[3], role.id, target or AuthorizationTarget.organization(db[1]))


def test_create_validates_availability_shape_and_numbers_vendors(header_db):
    db = header_db
    bu = unit(db, "BU")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for payload, field in [
                (body(availability="restricted"), "unit_ids"),
                (body(unit_ids=[bu.id]), "unit_ids"),
                (body(availability="restricted", unit_ids=[uuid4()]), "unit_ids"),
            ]:
                response = await send(browser, "POST", data=payload)
                assert response.status_code == 422, response.text
                assert response.json()["errors"][0]["field"] == field
            blank = await send(browser, "POST", data=body(name="   "))
            assert blank.status_code == 422
            first = await send(browser, "POST", data=body())
            second = await send(browser, "POST", data=body("Zed", "Zed", "restricted", [bu.id]))
            assert first.status_code == second.status_code == 201, second.text
            assert first.json()["number"] == "V-000001"
            assert second.json()["number"] == "V-000002"
            assert first.json()["status"] == "draft"
            assert first.json()["created_by_email"] == "admin@example.test"
            assert [u["unit_code"] for u in second.json()["units"]] == ["BU"]
            audit = (
                db[0]
                .execute("SELECT action FROM audit_log WHERE target_id=%s", (first.json()["id"],))
                .fetchall()
            )
            assert audit == [("vendor.create",)]

    asyncio.run(scenario())


def test_list_searches_names_and_number_pages_stably_and_never_tax_ids(header_db):

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for name, legal in [("Same", "Alpha Holdings"), ("Same", "Beta"), ("Same", "Gamma")]:
                assert (await send(browser, "POST", data=body(name, legal))).status_code == 201
            await send(browser, "POST", data=body("Other", "Delta 100%"))
            by_trading = (await send(browser, "GET", "?q=SAM")).json()["rows"]
            assert len(by_trading) == 3
            by_legal = (await send(browser, "GET", "?q=holdings")).json()["rows"]
            assert [r["legal_name"] for r in by_legal] == ["Alpha Holdings"]
            by_number = (await send(browser, "GET", "?q=v-000004")).json()["rows"]
            assert [r["trading_name"] for r in by_number] == ["Other"]
            # LIKE wildcards are literal.
            assert (await send(browser, "GET", "?q=%25")).json()["rows"][0]["legal_name"] == (
                "Delta 100%"
            )
            assert (await send(browser, "GET", "?q=_")).json()["rows"] == []
            first = (await send(browser, "GET", "?limit=2")).json()
            second = (await send(browser, "GET", "?limit=2&cursor=" + first["next_cursor"])).json()
            ids = [r["id"] for r in first["rows"] + second["rows"]]
            assert len(ids) == len(set(ids)) == 4 and second["next_cursor"] is None
            assert [r["trading_name"] for r in first["rows"]] == ["Other", "Same"]
            assert (await send(browser, "GET", "?limit=101")).status_code == 422
            assert (await send(browser, "GET", "?cursor=junk")).status_code == 422
            assert (await send(browser, "GET", "?status=active")).json()["rows"] == []
            assert (
                len((await send(browser, "GET", "?status=active&status=draft")).json()["rows"]) == 4
            )

    asyncio.run(scenario())


def test_availability_walks_ancestors_both_ways(header_db):
    db = header_db
    allow_local_disable(db)
    bu = unit(db, "BU")
    ou = unit(db, "OU", bu.id, kind="ou")
    other = unit(db, "OTHER")
    service = VendorService(db[0], Scope(db[1]), db[3])
    from flo.modules.vendors.schemas import DisableCreate, VendorCreate

    def make(name, availability, units=()):
        return service.create(
            VendorCreate(
                trading_name=name, legal_name=name, availability=availability, unit_ids=list(units)
            )
        )

    restricted_bu = make("RestrictedBU", "restricted", [bu.id])
    restricted_ou = make("RestrictedOU", "restricted", [ou.id])
    global_vendor = make("Global", "global")
    service.disable_locally(global_vendor.id, bu.id, DisableCreate(reason="policy"))

    assert service.available_in(restricted_bu.id, ou.id)  # restricting to a BU covers its OUs
    assert service.available_in(restricted_bu.id, bu.id)
    assert not service.available_in(restricted_bu.id, other.id)
    assert service.available_in(restricted_ou.id, ou.id)
    assert not service.available_in(restricted_ou.id, bu.id)  # never downwards
    assert not service.available_in(global_vendor.id, ou.id)  # disabled in BU hides it in the OU
    assert not service.available_in(global_vendor.id, bu.id)
    assert service.available_in(global_vendor.id, other.id)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            names = lambda r: sorted(v["trading_name"] for v in r.json()["rows"])  # noqa: E731
            assert names(await send(browser, "GET", f"?available_in={ou.id}")) == [
                "RestrictedBU",
                "RestrictedOU",
            ]
            assert names(await send(browser, "GET", f"?available_in={bu.id}")) == ["RestrictedBU"]
            assert names(await send(browser, "GET", f"?available_in={other.id}")) == ["Global"]
            assert (await send(browser, "GET", f"?available_in={uuid4()}")).status_code == 404

    asyncio.run(scenario())


def test_local_disable_guards_audit_and_leaves_the_vendor_row_alone(header_db):
    db = header_db
    bu = unit(db, "BU")
    other = unit(db, "OTHER")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            made = (await send(browser, "POST", data=body())).json()
            restricted = (
                await send(browser, "POST", data=body("R", "R", "restricted", [bu.id]))
            ).json()
            path = f"/{made['id']}/units/{bu.id}"
            reason = {"reason": "Policy"}
            refused = await send(browser, "POST", path + "/disable", reason)
            assert refused.status_code == 409
            assert refused.json()["checks"]["problem"] == "local_disable_not_allowed"
            allow_local_disable(db)
            not_global = await send(
                browser, "POST", f"/{restricted['id']}/units/{bu.id}/disable", reason
            )
            assert not_global.status_code == 409
            assert not_global.json()["checks"]["problem"] == "not_global"
            assert (
                await send(browser, "POST", path + "/disable", {"reason": ""})
            ).status_code == 422
            before = (
                db[0].execute("SELECT updated_at FROM vendor WHERE id=%s", (made["id"],)).fetchone()
            )
            done = await send(browser, "POST", path + "/disable", reason)
            assert done.status_code == 200, done.text
            assert done.json()["disabled_in"][0]["unit_code"] == "BU"
            assert done.json()["disabled_in"][0]["by_email"] == "admin@example.test"
            again = await send(browser, "POST", path + "/disable", reason)
            assert again.json()["checks"]["problem"] == "already_disabled"
            enabled = await send(browser, "POST", path + "/enable")
            assert enabled.status_code == 200 and enabled.json()["disabled_in"] == []
            assert (await send(browser, "POST", path + "/enable")).json()["checks"][
                "problem"
            ] == "not_disabled"
            after = (
                db[0].execute("SELECT updated_at FROM vendor WHERE id=%s", (made["id"],)).fetchone()
            )
            assert before == after
            actions = (
                db[0]
                .execute(
                    "SELECT action, reason FROM audit_log WHERE target_id=%s ORDER BY id",
                    (made["id"],),
                )
                .fetchall()
            )
            assert actions == [
                ("vendor.create", None),
                ("vendor.local_disable", "Policy"),
                ("vendor.local_enable", "Policy"),
            ]
            # vendor.edit granted at one unit only: allowed there, 403 at another.
            regrant(db, ["vendor.edit", "vendor.view"], AuthorizationTarget(*scope_bu(bu.id)))
            ok = await send(browser, "POST", path + "/disable", reason)
            assert ok.status_code == 200, ok.text
            # A grant at the other unit without vendor.edit is a 403; no grant there is a 404.
            regrant(db, ["vendor.view"], AuthorizationTarget(*scope_bu(other.id)), keep=True)
            denied = await send(browser, "POST", f"/{made['id']}/units/{other.id}/disable", reason)
            assert denied.status_code == 403
            assert (await send(browser, "POST", f"{path}/enable")).status_code == 200

    asyncio.run(scenario())


def scope_bu(unit_id):
    from flo.modules.identity.service import ScopeType

    return ScopeType.BU, unit_id


def test_patch_names_and_availability_only_while_draft(header_db):
    db = header_db
    bu = unit(db, "BU")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            made = (await send(browser, "POST", data=body())).json()
            path = "/" + made["id"]
            renamed = await send(browser, "PATCH", path, {"trading_name": " New "})
            assert renamed.status_code == 200 and renamed.json()["trading_name"] == "New"
            bad = await send(browser, "PATCH", path, {"availability": "restricted"})
            assert bad.status_code == 422
            ok = await send(
                browser, "PATCH", path, {"availability": "restricted", "unit_ids": [str(bu.id)]}
            )
            assert ok.status_code == 200 and ok.json()["units"][0]["unit_code"] == "BU"
            back = await send(browser, "PATCH", path, {"availability": "global", "unit_ids": []})
            assert back.json()["units"] == [] and back.json()["availability"] == "global"
            db[0].execute("UPDATE vendor SET status='active' WHERE id=%s", (made["id"],))
            blocked = await send(
                browser, "PATCH", path, {"availability": "restricted", "unit_ids": [str(bu.id)]}
            )
            assert blocked.status_code == 409
            assert blocked.json()["checks"]["problem"] == "availability_needs_approval"
            assert (
                await send(browser, "PATCH", path, {"legal_name": "Renamed"})
            ).status_code == 200
            actions = (
                db[0]
                .execute(
                    "SELECT action FROM audit_log WHERE target_id=%s ORDER BY id", (made["id"],)
                )
                .fetchall()
            )
            assert actions.count(("vendor.update",)) == 4

    asyncio.run(scenario())


def test_vendor_routes_conceal_foreign_tenants(header_db):
    db = header_db
    foreign_bu = unit(db, "FBU", org=db[2])
    own_bu = unit(db, "OWN")
    from flo.modules.vendors.schemas import VendorCreate

    foreign_vendor = VendorService(db[0], Scope(db[2]), db[3]).create(
        VendorCreate(trading_name="Foreign", legal_name="Foreign", availability="global")
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            mine = (await send(browser, "POST", data=body("Mine", "Mine"))).json()
            fid = str(foreign_vendor.id)
            for method, suffix, data in [
                ("GET", f"/{fid}", None),
                ("PATCH", f"/{fid}", {"legal_name": "X"}),
                ("POST", f"/{fid}/units/{own_bu.id}/disable", {"reason": "x"}),
                ("POST", f"/{fid}/units/{own_bu.id}/enable", None),
                ("POST", f"/{mine['id']}/units/{foreign_bu.id}/disable", {"reason": "x"}),
                ("POST", f"/{mine['id']}/units/{foreign_bu.id}/enable", None),
                ("GET", f"?available_in={foreign_bu.id}", None),
            ]:
                response = await send(browser, method, suffix, data)
                assert response.status_code == 404, (method, suffix, response.text)
            listed = await send(browser, "GET", "?q=foreign")
            assert listed.json()["rows"] == []
            assert [r["id"] for r in (await send(browser, "GET")).json()["rows"]] == [mine["id"]]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("permissions", "list_status", "create_status"),
    [
        ([], 403, 403),  # requestor-like: no vendor.view
        (["vendor.view"], 200, 403),  # sourcing specialist
        (["vendor.view", "vendor.create", "vendor.edit", "vendor.approve"], 200, 201),
    ],
)
def test_vendor_permissions_per_role(header_db, permissions, list_status, create_status):
    db = header_db
    regrant(db, permissions or ["requisition.view"])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert (await send(browser, "GET")).status_code == list_status
            assert (await send(browser, "POST", data=body())).status_code == create_status

    asyncio.run(scenario())


def test_patch_vendor_needs_if_match_and_a_stale_replay_conflicts(header_db):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            made = (await send(browser, "POST", data=body())).json()
            path = "/" + made["id"]
            assert made["version"] == 1
            missing = await send(browser, "PATCH", path, {"legal_name": "A"}, match=None)
            assert missing.status_code == 400
            assert missing.json()["checks"]["problem"] == "if_match_required"
            assert (
                await send(browser, "PATCH", path, {"legal_name": "A"}, match="x")
            ).status_code == 400
            first = await send(browser, "PATCH", path, {"legal_name": "A"}, match=1)
            assert first.status_code == 200 and first.json()["version"] == 2
            assert first.headers["ETag"] == '"2"'
            got = await send(browser, "GET", path)
            assert got.json()["version"] == 2 and got.headers["ETag"] == '"2"'
            again = await send(browser, "PATCH", path, {"legal_name": "C"}, match=2)  # GET's ETag
            assert again.status_code == 200 and again.json()["version"] == 3
            assert (await send(browser, "GET", path)).headers["ETag"] == '"3"'
            replay = await send(browser, "PATCH", path, {"legal_name": "B"}, match=1)
            assert replay.status_code == 409
            assert replay.json()["checks"]["problem"] == "stale_version"
            assert (await send(browser, "GET", path)).json()["legal_name"] == "C"

    asyncio.run(scenario())
