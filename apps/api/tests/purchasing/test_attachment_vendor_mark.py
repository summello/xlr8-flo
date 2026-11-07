"""Per-file vendor-facing mark on PO attachments: intent only (E13-S25, PO-017)."""

import asyncio
from uuid import uuid4

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from tests.collaboration.test_notes import set_role
from tests.isolation.test_real_sessions import client, headers, login
from tests.purchasing.test_changes import changes, open_change
from tests.purchasing.test_purchase_orders import pos
from tests.purchasing.test_transmission import foreign_po
from tests.purchasing.test_vendor_visibility import (  # noqa: F401
    INT_BYTES,
    INT_FILE,
    V,
    docs,
    storage,
    two,
)
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import sql
from tests.vendors.test_vendors import regrant

ACTION = "purchase_order.attachment.vendor_facing"


def base(po_id):
    return f"/api/v1/purchase-orders/{po_id}/attachments"


async def add(browser, po_id, name=f"{INT_FILE}.txt"):
    from tests.collaboration.test_attachments import upload

    up = await upload(browser, docs(po_id, "attachments"), name, INT_BYTES.encode())
    assert up.status_code == 201, up.text
    return up.json()["id"]


async def put(browser, po_id, aid, value=True):
    return await browser.put(
        f"{base(po_id)}/{aid}/vendor-facing",
        json={"vendor_facing": value},
        headers=headers(browser, uuid4().hex),
    )


async def flag(browser, po_id, aid):
    got = await browser.get(base(po_id))
    assert got.status_code == 200, got.text
    return [f["vendor_facing"] for f in got.json() if f["id"] == aid]


def setstatus(db, po_id, status):
    with tenant_transaction(db[0], Scope(db[1])):
        # force any status: the immutability trigger would refuse a way out of issued
        db[0].execute("SET LOCAL session_replication_role = replica")
        db[0].execute("UPDATE purchase_order SET status=%s WHERE id=%s", (status, po_id))


def audits(db):
    return sql(db, "SELECT count(*) FROM audit_log WHERE action=%s", (ACTION,))[0][0]


def version_of(db, po_id):
    return sql(db, "SELECT version FROM purchase_order WHERE id=%s", (po_id,))[0][0]


def test_default_internal_set_repeat_flip_and_audit(lifecycle_db):
    db, _ = lifecycle_db
    draft, _ = two(db)
    before = version_of(db, draft)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            aid = await add(browser, draft)
            assert await flag(browser, draft, aid) == [False]  # default internal
            got = await put(browser, draft, aid, True)
            assert got.status_code == 200 and got.json()["vendor_facing"] is True, got.text
            assert audits(db) == 1
            assert (await put(browser, draft, aid, True)).status_code == 200
            assert audits(db) == 1  # no-op repeat writes nothing
            assert await flag(browser, draft, aid) == [True]
            flipped = await put(browser, draft, aid, False)
            assert flipped.status_code == 200 and flipped.json()["vendor_facing"] is False
            assert audits(db) == 2
            assert (await put(browser, draft, aid, False)).status_code == 200
            assert audits(db) == 2

    asyncio.run(scenario())
    assert version_of(db, draft) == before  # the mark is not PO content


def test_a_new_file_version_resets_the_mark(lifecycle_db):
    db, _ = lifecycle_db
    draft, _ = two(db)

    async def scenario():
        from tests.collaboration.test_attachments import upload

        async with client(app) as browser:
            await login(browser)
            aid = await add(browser, draft)
            assert (await put(browser, draft, aid)).status_code == 200
            assert await flag(browser, draft, aid) == [True]
            new = await upload(browser, f"/api/v1/attachments/{aid}/versions", "v2.txt", b"v2")
            assert new.status_code == 201, new.text
            assert await flag(browser, draft, aid) == [False]
            assert (await put(browser, draft, aid)).status_code == 200
            assert await flag(browser, draft, aid) == [True]

    asyncio.run(scenario())


def test_the_mark_follows_the_file_across_a_change_order(lifecycle_db):
    db, _ = lifecycle_db
    _, issued = two(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            aid = await add(browser, issued)
            assert (await put(browser, issued, aid)).status_code == 200
            change = open_change(db, pos(db).get(issued), terms="Net 45 days")
            new_id = changes(db).apply(issued, 1, f'"{change.version}"').result_po_id
            listed = (await browser.get(base(new_id))).json()
            assert [(f["id"], f["document_id"], f["vendor_facing"]) for f in listed] == [
                (aid, str(issued), True)
            ]
            assert (await put(browser, new_id, aid, False)).status_code == 200
            assert await flag(browser, new_id, aid) == [False]

    asyncio.run(scenario())


def test_the_mark_needs_the_po_write_rule_and_a_live_status(lifecycle_db):
    db, _ = lifecycle_db
    draft, issued = two(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            files = {draft: await add(browser, draft), issued: await add(browser, issued)}
            set_role(db, "requestor")
            assert (await put(browser, draft, files[draft])).status_code == 403
            for perms, wanted in (
                (V, {draft: 403, issued: 403}),
                (V + ["purchase_order.create"], {draft: 200, issued: 403}),
                (V + ["purchase_order.amend"], {draft: 403, issued: 200}),
            ):
                regrant(db, perms)
                for po_id, status in wanted.items():
                    got = await put(browser, po_id, files[po_id], po_id == draft)
                    assert got.status_code == status, (perms, po_id, got.text)
            regrant(db, V + ["purchase_order.amend"])
            setstatus(db, issued, "cancelled")
            assert (await put(browser, issued, files[issued])).status_code == 409
            setstatus(db, issued, "closed")
            assert (await put(browser, issued, files[issued])).status_code == 200
            setstatus(db, issued, "deleted")
            assert (await put(browser, issued, files[issued])).status_code == 404

    asyncio.run(scenario())


def test_mark_routes_conceal_foreign_tenants(lifecycle_db):
    db, _ = lifecycle_db
    draft, issued = two(db)
    other = foreign_po(db)
    foreign_file = uuid4()
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO attachment (id, org_id, document_type, document_id, created_by) "
            "VALUES (%s, %s, 'purchase_order', %s, %s)",
            (foreign_file, db[2], other, db[3]),
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            mine = await add(browser, draft)
            elsewhere = await add(browser, issued)  # a file on another PO, outside the chain
            assert (await browser.get(base(other))).status_code == 404
            assert (await browser.get(base(uuid4()))).status_code == 404
            for po_id, aid in (
                (other, foreign_file),
                (other, mine),
                (uuid4(), mine),
                (draft, foreign_file),
                (draft, uuid4()),
                (draft, elsewhere),
            ):
                assert (await put(browser, po_id, aid)).status_code == 404, (po_id, aid)
            assert audits(db) == 0
            assert (await put(browser, draft, mine)).status_code == 200

    asyncio.run(scenario())
