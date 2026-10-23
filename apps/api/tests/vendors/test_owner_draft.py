"""A requester writes their own draft vendor and nothing else (E11-S14, DECISIONS 237)."""

import asyncio
from uuid import UUID, uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.vendors.documents import draft_write
from flo.modules.vendors.schemas import (
    ContactCreate,
    DocumentCreate,
    SensitivePut,
    SubmitCreate,
    VendorCreate,
    VendorPatch,
)
from flo.modules.vendors.service import VendorService
from tests.collaboration.conftest import collaboration_db as collaboration_db  # noqa: F401
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import admin_id
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_header import unit
from tests.vendors.test_changes import make_actor, problem, set_status, sql, status_of
from tests.vendors.test_changes import vdb as vdb  # noqa: F401
from tests.vendors.test_documents import attach, types
from tests.vendors.test_vendors import body, regrant, send

TAX = "12-3456789"
SENSITIVE = SensitivePut(tax_id=TAX, tax_id_country="US", bank_account="000123454821")


def svc(db, actor):
    return VendorService(db[0], Scope(db[1]), actor)


def contact(type):
    return ContactCreate(type=type, name="C", email="c@example.test")


def draft_of(db, actor):
    return svc(db, actor).create(
        VendorCreate(trading_name="V", legal_name="V", availability="global")
    )


def test_owner_saves_every_part_of_their_draft_and_submits(vdb):
    db, _ = vdb
    types(db, TAX=True)
    owner = make_actor(db, ["vendor.request", "vendor.view"])
    s, vendor = svc(db, owner), draft_of(db, owner)
    for type in ("billing", "service_delivery"):
        s.create_contact(vendor.id, contact(type))
    s.update(vendor.id, VendorPatch(trading_name="Renamed"), f'"{vendor.version}"')
    attachment, _ = attach(db, vendor.id)
    s.add_document(vendor.id, DocumentCreate(type_code="TAX", attachment_id=attachment))
    assert s.put_sensitive(vendor.id, SENSITIVE, '"0"').changed  # no reveal permission needed
    masked = s.get(vendor.id).sensitive
    assert masked.tax_id and TAX not in masked.tax_id and "3456789" not in masked.tax_id
    assert masked.bank_account and "000123454821" not in masked.bank_account
    s.submit(vendor.id, SubmitCreate())
    assert status_of(db, vendor.id) == "pending_approval"


def test_owner_is_locked_out_after_submit_with_409_not_draft(header_db):
    db = header_db
    owner = make_actor(db, ["vendor.request", "vendor.view"])
    s, vendor = svc(db, owner), draft_of(db, owner)
    made = s.create_contact(vendor.id, contact("billing"))
    set_status(db, vendor.id, "pending_approval")
    attachment, _ = attach(db, vendor.id)
    for write in (
        lambda: s.create_contact(vendor.id, contact("service_delivery")),
        lambda: s.delete_contact(vendor.id, made.id),
        lambda: s.put_sensitive(vendor.id, SENSITIVE, '"0"'),
        lambda: s.update(vendor.id, VendorPatch(trading_name="X"), f'"{vendor.version}"'),
        lambda: s.add_document(
            vendor.id, DocumentCreate(type_code="TAX", attachment_id=attachment)
        ),
    ):
        with pytest.raises(ProblemError) as error:
            write()
        assert problem(error) == (ErrorCode.CONFLICT, "not_draft")


def test_another_requesters_draft_is_404_and_a_vendor_manager_still_edits_any(header_db):
    db = header_db
    owner = make_actor(db, ["vendor.request", "vendor.view"])
    other = make_actor(db, ["vendor.request", "vendor.view"])
    vendor = draft_of(db, owner)
    for write in (
        lambda: svc(db, other).create_contact(vendor.id, contact("billing")),
        lambda: svc(db, other).put_sensitive(vendor.id, SENSITIVE, '"0"'),
        lambda: svc(db, other).update(vendor.id, VendorPatch(trading_name="X"), '"1"'),
    ):
        with pytest.raises(ProblemError) as error:
            write()
        assert error.value.code == ErrorCode.NOT_FOUND
    manager = make_actor(db, ["vendor.edit", "vendor.request", "vendor.sensitive.reveal"])
    svc(db, manager).create_contact(vendor.id, contact("billing"))
    set_status(db, vendor.id, "active")  # edit holders are not bound to draft
    svc(db, manager).create_contact(vendor.id, contact("service_delivery"))
    editor = make_actor(db, ["vendor.edit", "vendor.request"])  # edit without reveal: still no PUT
    with pytest.raises(ProblemError) as error:
        svc(db, editor).put_sensitive(vendor.id, SENSITIVE, '"0"')
    assert error.value.code == ErrorCode.FORBIDDEN


def test_owner_draft_routes_admit_vendor_request_and_conceal_foreign_tenants(header_db):
    """TEN-010: a request-only caller writes own draft over HTTP; a foreign vendor is 404."""
    db = header_db
    foreign = VendorService(db[0], Scope(db[2]), db[3])
    theirs = foreign.create(VendorCreate(trading_name="F", legal_name="F", availability="global"))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            mine = (await send(browser, "POST", data=body())).json()["id"]
            regrant(db, ["vendor.request", "vendor.view"])  # the login is now a plain requester
            made = await send(
                browser,
                "POST",
                f"/{mine}/contacts",
                {"type": "billing", "name": "B", "email": "b@example.test"},
            )
            assert made.status_code == 201, made.text
            put = await browser.request(
                "PUT",
                f"/api/v1/vendors/{mine}/sensitive",
                json={"tax_id": TAX, "tax_id_country": "US"},
                headers=headers(browser, uuid4().hex) | {"If-Match": '"0"'},
            )
            assert put.status_code == 200 and TAX not in put.text
            seen = await send(browser, "GET", f"/{mine}")
            assert TAX not in seen.text and seen.json()["sensitive"]["tax_id"]
            for method, suffix, data in [
                (
                    "POST",
                    f"/{theirs.id}/contacts",
                    {"type": "billing", "name": "B", "email": "b@example.test"},
                ),
                (
                    "POST",
                    f"/{theirs.id}/documents",
                    {"type_code": "TAX", "attachment_id": str(uuid4())},
                ),
            ]:
                assert (await send(browser, method, suffix, data)).status_code == 404, suffix
            set_status(db, mine, "pending_approval")
            late = await send(
                browser,
                "POST",
                f"/{mine}/contacts",
                {"type": "service_delivery", "name": "S", "email": "s@example.test"},
            )
            assert late.status_code == 409 and late.json()["checks"]["problem"] == "not_draft", (
                late.text
            )

    asyncio.run(scenario())


async def put_file(browser, path):
    return await browser.post(
        path,
        files={"file": ("a.pdf", b"%PDF-1.7", "application/octet-stream")},
        headers=headers(browser, uuid4().hex)
        | {"Content-Type": "multipart/form-data; boundary=owner-draft"},
    )


def test_owner_uploads_and_attaches_a_document_and_a_second_requester_gets_404(collaboration_db):
    db = collaboration_db[0]
    types(db, TAX=True)
    other = make_actor(db, ["vendor.request", "vendor.view"])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            mine = (await send(browser, "POST", data=body())).json()["id"]
            regrant(db, ["vendor.request", "record.read"])  # the requestor role's shape
            up = await put_file(browser, f"/api/v1/documents/vendor/{mine}/attachments")
            assert up.status_code == 201, up.text
            doc = await send(
                browser,
                "POST",
                f"/{mine}/documents",
                {"type_code": "TAX", "attachment_id": up.json()["id"]},
            )
            assert doc.status_code == 201, doc.text
            with pytest.raises(ProblemError) as refused:  # another requester's view of this draft
                draft_write(db[0], Scope(db[1]), UUID(mine), other)
            assert refused.value.code == ErrorCode.NOT_FOUND
            set_status(db, mine, "pending_approval")
            late = await put_file(browser, f"/api/v1/documents/vendor/{mine}/attachments")
            assert late.status_code == 409 and late.json()["checks"]["problem"] == "not_draft"

    asyncio.run(scenario())


def test_vendor_edit_holder_without_vendor_request_reaches_every_amended_route(collaboration_db):
    db = collaboration_db[0]
    types(db, TAX=True)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            mine = (await send(browser, "POST", data=body())).json()["id"]
            regrant(db, ["vendor.edit", "vendor.view", "vendor.sensitive.reveal", "record.read"])
            c = await send(
                browser,
                "POST",
                f"/{mine}/contacts",
                {"type": "billing", "name": "B", "email": "b@example.test"},
            )
            assert c.status_code == 201, c.text
            assert (
                await send(browser, "PATCH", f"/{mine}/contacts/{c.json()['id']}", {"name": "X"})
            ).status_code == 200
            assert (
                await send(browser, "PATCH", f"/{mine}", {"trading_name": "Renamed"})
            ).status_code == 200
            put = await browser.request(
                "PUT",
                f"/api/v1/vendors/{mine}/sensitive",
                json={"tax_id": TAX, "tax_id_country": "US"},
                headers=headers(browser, uuid4().hex) | {"If-Match": '"0"'},
            )
            assert put.status_code == 200, put.text
            up = await put_file(browser, f"/api/v1/documents/vendor/{mine}/attachments")
            assert up.status_code == 201, up.text
            doc = await send(
                browser,
                "POST",
                f"/{mine}/documents",
                {"type_code": "TAX", "attachment_id": up.json()["id"]},
            )
            assert doc.status_code == 201, doc.text
            # billing is required: a 409 (not 403) shows the route was reached
            gone = await send(browser, "DELETE", f"/{mine}/contacts/{c.json()['id']}")
            assert (
                gone.status_code == 409 and gone.json()["checks"]["problem"] == "required_contact"
            )

    asyncio.run(scenario())


def test_owner_reads_their_request_in_any_status_masked_and_others_get_404(vdb):
    """DECISIONS 239: the requestor role (no vendor.view) reopens its own request."""
    db, _ = vdb
    types(db, TAX=True)
    other = make_actor(db, ["vendor.request"])
    nobody = make_actor(db, ["record.read"])
    foreign = VendorService(db[0], Scope(db[2]), admin_id(db[0], "second@example.test")).create(
        VendorCreate(trading_name="F", legal_name="F", availability="global")
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            mine = (await send(browser, "POST", data=body())).json()["id"]
            for type in ("billing", "service_delivery"):
                await send(
                    browser,
                    "POST",
                    f"/{mine}/contacts",
                    {"type": type, "name": "C", "email": "c@example.test"},
                )
            await browser.request(
                "PUT",
                f"/api/v1/vendors/{mine}/sensitive",
                json={"tax_id": TAX, "tax_id_country": "US"},
                headers=headers(browser, uuid4().hex) | {"If-Match": '"0"'},
            )
            regrant(db, ["vendor.request"])  # the requestor role: no vendor.view
            for status in ("draft", "pending_approval"):
                set_status(db, mine, status)
                got = await send(browser, "GET", f"/{mine}")
                assert got.status_code == 200 and TAX not in got.text, got.text
                assert got.json()["sensitive"]["tax_id"].endswith("6789")
                assert len((await send(browser, "GET", f"/{mine}/contacts")).json()) == 2
                assert (await send(browser, "GET", f"/{mine}/documents")).status_code == 200
            for suffix in ("", "/contacts", "/documents"):  # TEN-010: a foreign tenant's vendor
                assert (await send(browser, "GET", f"/{foreign.id}{suffix}")).status_code == 404
            return mine

    mine = UUID(asyncio.run(scenario()))
    set_status(db, mine, "draft")
    with pytest.raises(ProblemError) as refused:  # someone else's request
        svc(db, other).can_read_request(mine)
    assert refused.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(ProblemError) as neither:  # a member holding neither permission
        svc(db, nobody).can_read_request(mine)
    assert neither.value.code == ErrorCode.FORBIDDEN


def test_duplicate_check_says_whether_the_caller_can_override(vdb):
    db, _ = vdb
    manager = make_actor(db, ["vendor.request", "vendor.approve"])
    requestor = make_actor(db, ["vendor.request"])
    assert svc(db, manager).duplicate_check("12-3456789", "US", {}).can_override is True
    assert svc(db, requestor).duplicate_check("12-3456789", "US", {}).can_override is False


def test_a_requestor_lists_active_units_only_and_neither_permission_is_403(header_db):
    """DECISIONS 241: vendor.request lists active units; org.unit.read still sees all."""
    db = header_db
    live, retired = unit(db, "LIVE"), unit(db, "OLD")
    foreign = unit(db, "THEIRS", org=db[2])
    sql(db, "UPDATE org_unit SET active=false WHERE id=%s", (retired.id,))

    async def units(browser, query="?active=false&page_size=100"):
        return await browser.request(
            "GET", "/api/v1/org/units" + query, headers=headers(browser, uuid4().hex)
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["org.unit.read"])
            plain = (await units(browser, "?page_size=100")).json()["rows"]
            both = {u["id"] for u in plain}
            assert {str(live.id), str(retired.id)} <= both and str(foreign.id) not in both
            regrant(db, ["vendor.request"])
            got = await units(browser)
            ids = {u["id"] for u in got.json()["rows"]}
            assert got.status_code == 200
            assert str(live.id) in ids and str(retired.id) not in ids  # active=false is ignored
            assert str(foreign.id) not in ids  # TEN-010
            regrant(db, ["record.read"])
            assert (await units(browser)).status_code == 403

    asyncio.run(scenario())


def test_the_three_owner_reads_refuse_other_requesters_and_members_over_http(vdb):
    """DECISIONS 239 through the routes: a second requester is 404, a member with neither
    permission is 403, on the vendor, its contacts and its documents."""
    db, _ = vdb
    owner = make_actor(db, ["vendor.request"])
    theirs = draft_of(db, owner)  # created by someone other than the logged-in caller

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["vendor.request"])  # a second requester
            for suffix in ("", "/contacts", "/documents"):
                got = await send(browser, "GET", f"/{theirs.id}{suffix}")
                assert got.status_code == 404, (suffix, got.text)
            regrant(db, ["record.read"])  # a member holding neither permission
            for suffix in ("", "/contacts", "/documents"):
                got = await send(browser, "GET", f"/{theirs.id}{suffix}")
                assert got.status_code == 403, (suffix, got.text)
            regrant(db, ["vendor.view"])  # vendor.view still reads any vendor
            for suffix in ("", "/contacts", "/documents"):
                got = await send(browser, "GET", f"/{theirs.id}{suffix}")
                assert got.status_code == 200, (suffix, got.text)

    asyncio.run(scenario())
