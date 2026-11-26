"""Vendor eligibility: one server rule (E11-S11, VEN-012)."""

import asyncio
from datetime import timedelta
from uuid import uuid4

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.modules.vendors.schemas import DisableCreate, VendorCreate
from flo.modules.vendors.service import VendorService
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db
from tests.vendors.test_documents import ON, add, new_vendor, service, sql, types
from tests.vendors.test_vendors import allow_local_disable, send, unit


def codes(result):
    return [r.code for r in result.reasons]


def one(db, vendor, unit_id):
    return service(db).eligibility([vendor.id], unit_id, ON)[vendor.id]


def set_status(db, vendor, status):
    sql(db, "UPDATE vendor SET status=%s WHERE id=%s", (status, vendor.id))


def test_each_reason_and_their_order(header_db):
    db = header_db
    allow_local_disable(db)
    plant = unit(db, "P4")
    types(db, INS=True, TAX=True, ISO=True)
    vendor = new_vendor(db)
    set_status(db, vendor, "suspended")
    service(db).disable_locally(vendor.id, plant.id, DisableCreate(reason="x"))
    add(db, vendor.id, "ISO", ON - timedelta(days=2), "verified")  # expired
    add(db, vendor.id, "TAX", ON + timedelta(days=90), "rejected")  # rejected; INS missing
    result = one(db, vendor, plant.id)
    assert not result.eligible
    assert codes(result) == [
        "status",
        "disabled_here",
        "document_missing",
        "document_rejected",
        "document_expired",
    ]
    assert [r.text for r in result.reasons] == [
        "Vendor is Suspended",
        "Disabled in P4 by admin@example.test",
        "INS missing",
        "TAX rejected",
        "ISO expired 14 Oct",
    ]
    assert result.reasons[-1].date == ON - timedelta(days=2)
    assert result.reasons[-1].document_type == "ISO"


def test_not_available_and_status_labels(header_db):
    db = header_db
    here, there = unit(db, "HERE"), unit(db, "THERE")
    vendor = service(db).create(
        VendorCreate(
            trading_name="R", legal_name="R", availability="restricted", unit_ids=[there.id]
        )
    )
    set_status(db, vendor, "pending_approval")
    result = one(db, vendor, here.id)
    assert [r.text for r in result.reasons] == [
        "Vendor is Pending Approval",
        "Not available in HERE",
    ]
    set_status(db, vendor, "active")
    assert one(db, vendor, there.id).eligible


def test_expired_document_leaves_status_and_warnings_do_not_block(header_db):
    db = header_db
    top = unit(db, "TOP")
    types(db, INS=True, TAX=True, OPT=False)
    vendor = new_vendor(db)
    set_status(db, vendor, "active")
    add(db, vendor.id, "INS", ON - timedelta(days=1), "verified")
    result = one(db, vendor, top.id)
    assert codes(result) == ["document_missing", "document_expired"]
    assert sql(db, "SELECT status FROM vendor WHERE id=%s", (vendor.id,))[0][0] == "active"
    # expiring and unverified are warnings only; an optional type is never a reason
    other = new_vendor(db)
    set_status(db, other, "active")
    add(db, other.id, "INS", ON + timedelta(days=12), "verified")
    add(db, other.id, "TAX")
    add(db, other.id, "OPT", ON - timedelta(days=5), "rejected")
    result = one(db, other, top.id)
    assert result.eligible and result.reasons == []
    assert [w.text for w in result.warnings] == ["INS expiring in 12 Days", "TAX unverified"]


def test_restricted_vendor_in_child_ou_of_allowed_bu_is_eligible(header_db):
    db = header_db
    bu = unit(db, "BU")
    ou = unit(db, "OU", bu.id, kind="ou")
    vendor = service(db).create(
        VendorCreate(trading_name="R", legal_name="R", availability="restricted", unit_ids=[bu.id])
    )
    set_status(db, vendor, "active")
    assert one(db, vendor, ou.id).eligible


def test_batch_is_bounded_and_foreign_vendors_are_absent(header_db):
    db = header_db
    top = unit(db, "TOP")
    types(db, INS=True)
    mine = [new_vendor(db) for _ in range(50)]
    foreign = new_vendor(db, db[2])
    seen = []
    original = db[0].execute

    class Counting:
        def __getattr__(self, name):
            return getattr(db[0], name)

        def execute(self, *args, **kwargs):
            seen.append(args[0])
            return original(*args, **kwargs)

    svc = VendorService(Counting(), Scope(db[1]), db[3])
    result = svc.eligibility([v.id for v in mine] + [foreign.id], top.id, ON)
    assert set(result) == {v.id for v in mine}
    assert len(seen) <= 6, len(seen)


def test_eligibility_routes_and_list_row(header_db):
    db = header_db
    top = unit(db, "TOP")
    foreign = new_vendor(db, db[2])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            made = (
                await send(
                    browser,
                    "POST",
                    data={"trading_name": "V", "legal_name": "V", "availability": "global"},
                )
            ).json()
            got = await send(browser, "GET", f"/{made['id']}/eligibility?unit_id={top.id}")
            assert got.status_code == 200, got.text
            assert got.json()["eligible"] is False  # a new vendor is a draft
            assert got.json()["reasons"][0]["code"] == "status"
            for path in (
                f"/{foreign.id}/eligibility?unit_id={top.id}",
                f"/{made['id']}/eligibility?unit_id={uuid4()}",
            ):
                assert (await send(browser, "GET", path)).status_code == 404
            rows = (await send(browser, "GET", f"?available_in={top.id}")).json()["rows"]
            assert rows[0]["eligibility"]["reasons"][0]["text"] == "Vendor is Draft"
            plain = (await send(browser, "GET")).json()["rows"]
            assert plain[0]["eligibility"] is None
            assert foreign.id not in {r["id"] for r in rows}

    asyncio.run(scenario())


def test_vendor_eligibility_route_conceals_foreign_tenants(header_db):
    test_eligibility_routes_and_list_row(header_db)
