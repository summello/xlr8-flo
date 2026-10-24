"""Sealed bids, the draft rule, the late-bid override and the submit states (SRC-007, SRC-008)."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from flo.api.collaboration import get_collaboration_storage
from flo.api.health import app
from flo.kernel.errors import ErrorCode
from flo.kernel.logging import correlation_context
from flo.modules.sourcing.schemas import BidRead, BidSubmit, SealedBidRead
from tests.collaboration.test_attachments import upload
from tests.imports.test_upload import MemoryStorage
from tests.isolation.test_real_sessions import client, headers, login
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_bids import (
    as_role,
    audit_after,
    bids,
    complete,
    edit,
    key,
    live,
    submit,
)
from tests.sourcing.test_rfq import refused, send, sql
from tests.vendors.test_vendors import regrant

PRICE = "98765.4321"
FIGURES = (PRICE, "98,765.4321", "296296.296", "77777.7777")  # price, grouped, 3 x price, freight


async def seeded(db, browser):
    """Two vendors' submitted bids whose price, total, freight and evidence file hold FIGURES."""
    rfq, _, _, [a, b] = live(db, names=("A", "B"))
    done = []
    for vendor_id in (a, b):
        bid = key(db, rfq, vendor_id)
        url = f"/api/v1/documents/bid/{bid.id}/attachments"
        uploaded = await upload(browser, url, data=f"%PDF-1.7 quote {PRICE}".encode())
        assert uploaded.status_code == 201, uploaded.text
        bid = complete(db, rfq, bid, price=PRICE)
        evidence_id = uploaded.json()["id"]
        bid = edit(db, rfq, bid, freight="77777.7777", evidence_attachment_id=evidence_id)
        done.append((submit(db, rfq, bid), evidence_id))
    return rfq, done


def back_date(db, rfq):
    query = "UPDATE rfq SET deadline_at=now()-interval '2 hours' WHERE id=%s RETURNING 1"
    sql(db, query, (rfq.id,))


def open_deadline(db, rfq):
    sql(
        db,
        "UPDATE rfq SET deadline_at=now()-interval '1 minute' WHERE id=%s RETURNING 1",
        (rfq.id,),
    )


@pytest.fixture(autouse=True)
def _storage():
    storage = MemoryStorage()  # one store for the whole test, or the content read finds nothing
    app.dependency_overrides[get_collaboration_storage] = lambda: storage
    yield
    app.dependency_overrides.pop(get_collaboration_storage, None)


def routes(rfq, bid_id, evidence_id):
    return [
        f"/api/v1/sourcing/rfqs/{rfq.id}/bids",
        f"/api/v1/sourcing/rfqs/{rfq.id}/bids/{bid_id}",
        f"/api/v1/documents/bid/{bid_id}/attachments",
        f"/api/v1/documents/bid/{bid_id}/notes",
        f"/api/v1/attachments/{evidence_id}/content",
    ]


ROLES = [
    ("role", "requestor"),
    ("role", "purchasing-user"),
    ("role", "sourcing-specialist"),
    ("role", "organization-administrator"),
    ("permissions", ["rfq.approve", "rfq.view", "rfq.edit", "record.read"]),  # the RFQ owner
    ("permissions", ["rfq.view", "record.read"]),  # an evaluator
    ("permissions", ["rfq.edit", "rfq.view"]),  # the keyer
]


def test_no_bid_figure_leaves_the_server_before_the_deadline_for_any_role(lifecycle_db):
    db, _ = lifecycle_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rfq, done = await seeded(db, browser)
            [(first, first_evidence), (second, _)] = done
            assert isinstance(first, SealedBidRead) and isinstance(second, SealedBidRead)
            assert not any(f in first.model_dump_json() for f in FIGURES)  # the submit response
            for kind, grant in ROLES:
                as_role(db, grant) if kind == "role" else regrant(db, grant)
                for bid, evidence_id in done:
                    for url in routes(rfq, bid.id, evidence_id):
                        response = await browser.get(url)
                        body = response.text + response.content.decode("latin-1")
                        assert not any(f in body for f in FIGURES), (grant, url, body)
                regrant_listing = await browser.get(f"/api/v1/sourcing/rfqs/{rfq.id}/bids")
                if regrant_listing.status_code == 200:  # status rows only: who sent, and when
                    rows = regrant_listing.json()
                    assert {r["status"] for r in rows} == {"submitted"}
                    assert all("sent_at" in r and "lines" not in r for r in rows), grant
            # the evidence of a sealed bid is 409 for anyone who could otherwise read it
            regrant(db, ["rfq.approve", "record.read", "rfq.view"])
            for url in routes(rfq, first.id, first_evidence)[2:]:
                sealed = await browser.get(url)
                assert sealed.status_code == 409, (url, sealed.text)
                assert sealed.json()["checks"]["problem"] == "bid_sealed"
            for url in routes(rfq, first.id, first_evidence)[2:]:
                regrant(db, ["rfq.approve"])  # without record.read the caller learns nothing
                assert (await browser.get(url)).status_code == 403, url
                regrant(db, ["rfq.view", "record.read"])
                assert (await browser.get(url)).status_code == 403, url

    asyncio.run(scenario())


def test_after_the_deadline_approvers_and_keyers_read_the_figures(lifecycle_db):
    db, _ = lifecycle_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rfq, done = await seeded(db, browser)
            [(first, first_evidence), _] = done
            open_deadline(db, rfq)
            regrant(db, ["rfq.approve", "rfq.view", "record.read"])
            listed = await browser.get(f"/api/v1/sourcing/rfqs/{rfq.id}/bids")
            assert listed.status_code == 200 and PRICE in listed.text and len(listed.json()) == 2
            one = await browser.get(f"/api/v1/sourcing/rfqs/{rfq.id}/bids/{first.id}")
            assert one.json()["lines"][0]["unit_price"] == PRICE
            content = await browser.get(f"/api/v1/attachments/{first_evidence}/content")
            assert content.status_code == 200 and PRICE.encode() in content.content
            assert (await browser.get(f"/api/v1/documents/bid/{first.id}/notes")).status_code == 200
            regrant(db, ["rfq.edit", "rfq.view"])  # the keyer reads their own bid
            own = await browser.get(f"/api/v1/sourcing/rfqs/{rfq.id}/bids/{first.id}")
            assert own.status_code == 200 and PRICE in own.text

    asyncio.run(scenario())


def test_a_draft_is_its_keyers_alone_before_and_after_the_deadline(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    draft = edit(db, rfq, complete(db, rfq, key(db, rfq, vid), price=PRICE), warranty="9 years")
    approver = sql(db, "SELECT id FROM identity WHERE email='second@example.test'")[0][0]
    for passed in (False, True):
        if passed:
            open_deadline(db, rfq)
        as_approver = bids(db, approver)
        listed = as_approver.list_bids(rfq.id, all_bids=True)
        read = as_approver.read(rfq.id, draft.id, all_bids=True)
        assert isinstance(read, SealedBidRead) and read.status == "draft"
        assert [type(x) for x in listed] == [SealedBidRead]
        assert not any(
            f in read.model_dump_json() + listed[0].model_dump_json() for f in (PRICE, "9 years")
        )
        mine = bids(db).read(rfq.id, draft.id, all_bids=True)
        assert isinstance(mine, BidRead) and mine.warranty == "9 years"
    done = submit(db, rfq, draft)  # the deadline passed: the approver now sees the figures
    assert isinstance(done, BidRead)
    full = bids(db, approver).read(rfq.id, done.id, all_bids=True)
    assert isinstance(full, BidRead) and full.lines[0].unit_price == PRICE


def test_a_drafts_evidence_is_its_keyers_alone(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    bid = key(db, rfq, vid)
    url = f"/api/v1/documents/bid/{bid.id}/attachments"

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert (await upload(browser, url)).status_code == 201
            assert (await browser.get(url)).status_code == 200  # the keyer
            sql(
                db,
                "UPDATE bid SET keyed_by=%s WHERE id=%s RETURNING 1",
                (
                    sql(db, "SELECT id FROM identity WHERE email='second@example.test'")[0][0],
                    bid.id,
                ),
            )
            for passed in (False, True):
                if passed:
                    open_deadline(db, rfq)
                refused_read = await browser.get(url)
                assert refused_read.status_code == 409
                assert refused_read.json()["checks"]["problem"] == "bid_sealed"

    asyncio.run(scenario())


def test_the_vendors_sent_time_decides_lateness(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    sql(
        db, "UPDATE rfq SET deadline_at=now()-interval '2 hours' WHERE id=%s RETURNING 1", (rfq.id,)
    )
    on_time = complete(db, rfq, key(db, rfq, vid))  # keyed now, after the deadline ...
    on_time = edit(
        db, rfq, on_time, sent_at=datetime.now(UTC) - timedelta(hours=3)
    )  # ... sent before
    done = submit(db, rfq, on_time)
    assert done.late is None and done.status == "submitted"
    assert audit_after(db, rfq, "bid.late_override") == []


def test_a_late_bid_is_blocked_unless_the_lead_accepts_it_with_a_reason(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    sql(
        db, "UPDATE rfq SET deadline_at=now()-interval '2 hours' WHERE id=%s RETURNING 1", (rfq.id,)
    )
    bid = complete(db, rfq, key(db, rfq, vid))  # sent one hour ago: an hour late
    error = refused(lambda: submit(db, rfq, bid))
    assert error.checks["problem"] == "late_bid"
    assert sql(db, "SELECT status FROM bid WHERE id=%s", (bid.id,))[0][0] == "draft"

    def accept(may):
        with correlation_context("late"):
            body = BidSubmit(accept_late_reason="  Courier strike  ")
            return bids(db).submit(rfq.id, bid.id, body, str(bid.row_version), may)

    refused(lambda: accept(False), ErrorCode.FORBIDDEN)
    assert sql(db, "SELECT status FROM bid WHERE id=%s", (bid.id,))[0][0] == "draft"

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            url = f"/rfqs/{rfq.id}/bids/{bid.id}/submit"
            body = {"accept_late_reason": "Courier strike"}
            regrant(db, ["rfq.edit", "rfq.view"])  # not the lead: the router passes may=False
            assert (
                await send(browser, "POST", url, body, {"If-Match": str(bid.row_version)})
            ).status_code == 403
            regrant(db, ["rfq.edit", "rfq.approve", "rfq.view"])
            accepted = await send(browser, "POST", url, body, {"If-Match": str(bid.row_version)})
            assert accepted.status_code == 200, accepted.text
            return accepted.json()

    marked = asyncio.run(scenario())
    assert marked["late"]["reason"] == "Courier strike" and marked["late"]["accepted_by_email"]
    columns = sql(db, "SELECT late_accepted_by, late_accepted_at, late_reason FROM bid")[0]
    assert columns[0] == db[3] and columns[1] and columns[2] == "Courier strike"
    assert audit_after(db, rfq, "bid.late_override")[0]["reason"] == "Courier strike"
    assert bids(db).read(rfq.id, bid.id, all_bids=True).late.reason == "Courier strike"


@pytest.mark.parametrize(
    ("status", "problem"),
    [
        ("draft", "rfq_not_published"),
        ("cancelled", "rfq_cancelled"),
        ("awarded", "rfq_awarded"),
        ("published", None),
        ("closed", None),
        ("under_evaluation", None),
    ],
)
def test_a_bid_submits_only_while_bidding_is_live(lifecycle_db, status, problem):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    bid = complete(db, rfq, key(db, rfq, vid))
    sql(db, "UPDATE rfq SET status=%s WHERE id=%s RETURNING 1", (status, rfq.id))
    if problem:
        assert refused(lambda: submit(db, rfq, bid)).checks["problem"] == problem
        assert sql(db, "SELECT status FROM bid WHERE id=%s", (bid.id,))[0][0] == "draft"
    else:
        assert submit(db, rfq, bid).status == "submitted"


def test_note_writes_on_a_bid_are_sealed_like_note_reads(lifecycle_db):
    db, _ = lifecycle_db
    other = sql(db, "SELECT id FROM identity WHERE email='second@example.test'")[0][0]

    async def post(browser, bid_id):
        return await browser.post(
            f"/api/v1/documents/bid/{bid_id}/notes",
            json={"body": "Ask about freight", "visibility": "internal"},
            headers=headers(browser, uuid4().hex),
        )

    def notes(bid_id):
        return sql(db, "SELECT count(*) FROM note WHERE document_id=%s", (bid_id,))[0][0]

    rfq2, _, _, [vid] = live(db)
    draft = key(db, rfq2, vid)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            rfq, [(done, _), _] = await seeded(db, browser)
            regrant(db, ["rfq.approve", "rfq.edit", "rfq.view", "record.read"])
            # a submitted bid, before the deadline: the approver may neither add nor edit a note
            sealed = await post(browser, done.id)
            assert sealed.status_code == 409 and sealed.json()["checks"]["problem"] == "bid_sealed"
            assert notes(done.id) == 0
            note = uuid4()
            sql(
                db,
                "INSERT INTO note(id,org_id,document_type,document_id,author_id,body,visibility) "
                "VALUES(%s,%s,'bid',%s,%s,'Original','internal') RETURNING 1",
                (note, db[1], done.id, db[3]),
            )
            edited = await browser.patch(
                f"/api/v1/notes/{note}",
                json={"body": "Changed"},
                headers=headers(browser, uuid4().hex) | {"If-Match": "1"},
            )
            assert edited.status_code == 409 and edited.json()["checks"]["problem"] == "bid_sealed"
            assert sql(db, "SELECT body, version FROM note WHERE id=%s", (note,))[0] == (
                "Original",
                1,
            )
            # after the deadline the approver writes as before
            open_deadline(db, rfq)
            assert (await post(browser, done.id)).status_code == 201
            # a draft: its keyer writes, any other approver is sealed out, before and after
            assert (await post(browser, draft.id)).status_code == 201
            sql(db, "UPDATE bid SET keyed_by=%s WHERE id=%s RETURNING 1", (other, draft.id))
            for passed in (False, True):
                if passed:
                    open_deadline(db, rfq2)
                refused_write = await post(browser, draft.id)
                assert refused_write.status_code == 409
                assert refused_write.json()["checks"]["problem"] == "bid_sealed"
            assert notes(draft.id) == 1

    asyncio.run(scenario())
