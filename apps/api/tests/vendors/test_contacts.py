"""Typed vendor contacts through production middleware (E11-S02)."""

import asyncio
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.org.schemas import MasterCreate
from flo.modules.vendors.schemas import VendorCreate
from flo.modules.vendors.service import VendorService
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_header import org_svc
from tests.vendors.test_vendors import body, current_version, regrant


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


def contact(type="billing", name="Bea", email="bea@example.test", **extra):
    return {"type": type, "name": name, "email": email} | extra


def service(db):
    return VendorService(db[0], Scope(db[1]), db[3])


async def vendor(browser):
    return (await send(browser, "POST", data=body())).json()["id"]


def test_required_types_are_unique_immutable_and_undeletable(header_db):
    db = header_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            vid = await vendor(browser)
            path = f"/{vid}/contacts"
            made = await send(browser, "POST", path, contact())
            assert made.status_code == 201, made.text
            assert made.json()["receives_documents"] is False
            second = await send(browser, "POST", path, contact(name="Other"))
            assert second.status_code == 409
            assert second.json()["checks"]["problem"] == "contact_type_taken"
            sd = (await send(browser, "POST", path, contact("service_delivery"))).json()
            assert sd["receives_documents"] is True
            for field, value in [("type", "x"), ("bogus", 1), ("email", "nope"), ("name", "")]:
                bad = await send(browser, "PATCH", f"{path}/{sd['id']}", {field: value})
                assert bad.status_code == 422, (field, bad.text)
            for cid in (made.json()["id"], sd["id"]):
                gone = await send(browser, "DELETE", f"{path}/{cid}")
                assert gone.status_code == 409
                assert gone.json()["checks"]["problem"] == "required_contact"
            edited = await send(
                browser, "PATCH", f"{path}/{sd['id']}", {"phone": "+1 555", "name": "Sam"}
            )
            assert edited.status_code == 200 and edited.json()["phone"] == "+1 555"
            listed = (await send(browser, "GET", path)).json()
            assert [c["type"] for c in listed] == ["billing", "service_delivery"]
            assert (await send(browser, "POST", path, contact("nope"))).status_code == 422
            blank = await send(browser, "POST", path, contact("LEGAL", name="   "))
            assert blank.status_code == 422
            assert blank.json()["errors"][0]["field"] == "name"
            assert (
                await send(browser, "PATCH", f"{path}/{sd['id']}", {"name": " "})
            ).status_code == 422
            cleared = await send(browser, "PATCH", f"{path}/{sd['id']}", {"phone": "  "})
            assert cleared.status_code == 200 and cleared.json()["phone"] is None
            # the database refuses a second required contact even without the service
            with (
                pytest.raises(psycopg.errors.UniqueViolation),
                tenant_transaction(db[0], Scope(db[1])),
            ):
                db[0].execute(
                    "INSERT INTO vendor_contact(id,org_id,vendor_id,type,name,email,"
                    "receives_documents) VALUES(%s,%s,%s,'billing','D','d@example.test',false)",
                    (uuid4(), db[1], vid),
                )

    asyncio.run(scenario())


def test_additional_types_come_from_master_data_and_billing_is_reserved(header_db):
    db = header_db
    org = org_svc(db)
    for reserved in ("billing", "SERVICE_DELIVERY"):
        with pytest.raises(ProblemError, match="validation-failed"):
            org.create_master("vendor_contact_type", MasterCreate(code=reserved, name="x"))
    org.create_master("vendor_contact_type", MasterCreate(code="legal", name="Legal"))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            vid = await vendor(browser)
            path = f"/{vid}/contacts"
            ok = await send(browser, "POST", path, contact("legal"))
            assert ok.status_code == 201 and ok.json()["type"] == "LEGAL"
            assert ok.json()["receives_documents"] is False
            twice = await send(browser, "POST", path, contact("LEGAL", "Lee", "lee@example.test"))
            assert twice.status_code == 201  # additional types may repeat
            gone = await send(browser, "DELETE", f"{path}/{ok.json()['id']}")
            assert gone.status_code == 204
            assert (await send(browser, "POST", path, contact("other"))).status_code == 422

    asyncio.run(scenario())


def test_document_recipients_and_contacts_complete(header_db):
    db = header_db
    svc = service(db)
    made = svc.create(VendorCreate(trading_name="V", legal_name="V", availability="global"))
    from flo.modules.vendors.schemas import ContactCreate, ContactPatch

    assert svc.contacts_complete(made.id) == ["billing", "service_delivery"]
    assert svc.document_recipients(made.id) == []
    billing = svc.create_contact(
        made.id, ContactCreate(type="billing", name="B", email="b@example.test")
    )
    assert svc.contacts_complete(made.id) == ["service_delivery"]
    assert svc.document_recipients(made.id) == []
    svc.create_contact(
        made.id, ContactCreate(type="service_delivery", name="S", email="s@example.test")
    )
    assert svc.contacts_complete(made.id) == []
    assert [r.email for r in svc.document_recipients(made.id)] == ["s@example.test"]
    svc.update_contact(made.id, billing.id, ContactPatch(receives_documents=True), "1")
    assert [r.email for r in svc.document_recipients(made.id)] == [
        "s@example.test",
        "b@example.test",
    ]
    sd = [c for c in svc.list_contacts(made.id) if c.type == "service_delivery"][0]
    svc.update_contact(made.id, sd.id, ContactPatch(receives_documents=False), "1")
    svc.update_contact(made.id, billing.id, ContactPatch(receives_documents=False), "2")
    assert svc.document_recipients(made.id) == []


def test_contact_audit_records_email_but_not_phone(header_db):
    db = header_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            vid = await vendor(browser)
            path = f"/{vid}/contacts"
            made = (await send(browser, "POST", path, contact(phone="+1 555 0100"))).json()
            await send(browser, "PATCH", f"{path}/{made['id']}", {"email": "new@example.test"})
            dump = repr(
                db[0]
                .execute(
                    "SELECT * FROM audit_log WHERE target_id=%s "
                    "AND action LIKE 'vendor.contact.%%' ORDER BY id",
                    (vid,),
                )
                .fetchall()
            )
            assert dump.count("vendor.contact.") == 2
            assert "new@example.test" in dump and "bea@example.test" in dump
            assert "555" not in dump

    asyncio.run(scenario())


def test_vendor_contact_routes_conceal_foreign_tenants(header_db):
    db = header_db
    foreign = VendorService(db[0], Scope(db[2]), db[3])
    other = foreign.create(VendorCreate(trading_name="F", legal_name="F", availability="global"))
    from flo.modules.vendors.schemas import ContactCreate

    theirs = foreign.create_contact(
        other.id, ContactCreate(type="billing", name="F", email="f@example.test")
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            mine = await vendor(browser)
            for method, suffix, data in [
                ("GET", f"/{other.id}/contacts", None),
                ("POST", f"/{other.id}/contacts", contact()),
                ("PATCH", f"/{other.id}/contacts/{theirs.id}", {"name": "X"}),
                ("DELETE", f"/{other.id}/contacts/{theirs.id}", None),
                # a foreign contact id under my own vendor
                ("PATCH", f"/{mine}/contacts/{theirs.id}", {"name": "X"}),
                ("DELETE", f"/{mine}/contacts/{theirs.id}", None),
            ]:
                response = await send(browser, method, suffix, data)
                assert response.status_code == 404, (method, suffix, response.text)

    asyncio.run(scenario())


def test_sourcing_specialist_reads_but_cannot_write_contacts(header_db):
    db = header_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            vid = await vendor(browser)
            path = f"/{vid}/contacts"
            made = (await send(browser, "POST", path, contact("service_delivery"))).json()
            regrant(db, ["vendor.view"])
            assert (await send(browser, "GET", path)).status_code == 200
            assert (await send(browser, "POST", path, contact())).status_code == 403
            assert (
                await send(browser, "PATCH", f"{path}/{made['id']}", {"name": "X"})
            ).status_code == 403
            assert (await send(browser, "DELETE", f"{path}/{made['id']}")).status_code == 403

    asyncio.run(scenario())


def test_patch_contact_needs_if_match_and_a_stale_replay_conflicts(header_db):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            vid = await vendor(browser)
            path = f"/{vid}/contacts"
            made = (await send(browser, "POST", path, contact())).json()
            one = f"{path}/{made['id']}"
            assert made["version"] == 1
            missing = await send(browser, "PATCH", one, {"name": "A"}, match=None)
            assert missing.status_code == 400
            assert missing.json()["checks"]["problem"] == "if_match_required"
            first = await send(browser, "PATCH", one, {"name": "A"}, match=1)
            assert first.status_code == 200 and first.json()["version"] == 2
            assert first.headers["ETag"] == '"2"'
            replay = await send(browser, "PATCH", one, {"name": "B"}, match=1)
            assert replay.status_code == 409
            assert replay.json()["checks"]["problem"] == "stale_version"
            assert (await send(browser, "GET", path)).json()[0]["name"] == "A"

    asyncio.run(scenario())
