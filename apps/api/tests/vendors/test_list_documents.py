"""Vendor list: document attention, the `documents` filter and the tab counts (E11-S12)."""

import asyncio
from datetime import timedelta

from flo.api.health import app
from flo.modules.org.service import today
from flo.modules.vendors.schemas import VendorCreate
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db
from tests.vendors.test_documents import add, new_vendor, service, sql, types
from tests.vendors.test_vendors import send


def made(db, name, status="active"):
    vendor = service(db).create(
        VendorCreate(trading_name=name, legal_name=name, availability="global")
    )
    sql(db, "UPDATE vendor SET status=%s WHERE id=%s", (status, vendor.id))
    return vendor


def test_filter_pages_by_cursor_and_lists_attention_in_order(header_db):
    db = header_db
    types(db, INS=True)
    day = today()
    a, b, c, d = (made(db, n) for n in "ABCD")
    add(db, a.id, "INS", day + timedelta(days=12), "verified")  # expiring
    add(db, c.id, "INS", day - timedelta(days=1), "verified")  # expired
    add(db, d.id, "INS", day + timedelta(days=90), "verified")  # fine
    # b has no document: INS is missing
    s = service(db)
    rows = {v.trading_name: v for v in s.search().rows}
    assert [(x.state, x.count, x.days_left) for x in rows["A"].documents] == [("expiring", 1, 12)]
    assert [x.state for x in rows["B"].documents] == ["missing"]
    assert rows["D"].documents == []
    both = s.search(documents=["expiring", "expired"])
    assert [v.trading_name for v in both.rows] == ["A", "C"]
    first = s.search(documents=["expiring", "expired", "missing"], limit=2)
    assert [v.trading_name for v in first.rows] == ["A", "B"] and first.next_cursor
    rest = s.search(documents=["expiring", "expired", "missing"], limit=2, cursor=first.next_cursor)
    assert [v.trading_name for v in rest.rows] == ["C"] and rest.next_cursor is None


def test_counts_come_from_the_server(header_db):
    db = header_db
    types(db, INS=True)
    for name, status in [
        ("A", "active"),
        ("B", "active"),
        ("C", "draft"),
        ("D", "pending_approval"),
    ]:
        made(db, name, status)
    made(db, "E", "suspended")
    made(db, "F", "rejected")
    add(db, made(db, "G", "inactive").id, "INS", today() + timedelta(days=90), "verified")
    counts = service(db).counts()
    assert (counts.active, counts.pending, counts.inactive, counts.all) == (2, 2, 3, 7)
    assert counts.attention == 6  # every vendor but G lacks the required INS


def test_counts_and_documents_filter_routes_conceal_foreign_tenants(header_db):
    db = header_db
    types(db, INS=True)
    new_vendor(db, db[2])  # theirs: must not be counted or listed
    mine = made(db, "Mine")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            counts = (await send(browser, "GET", "/counts")).json()
            assert (counts["all"], counts["attention"]) == (1, 1)
            listed = (await send(browser, "GET", "?documents=missing")).json()["rows"]
            assert [r["id"] for r in listed] == [str(mine.id)]
            assert (await send(browser, "GET", "?documents=bogus")).status_code == 422

    asyncio.run(scenario())
