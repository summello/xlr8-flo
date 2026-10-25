"""Bid capture: keyed by the buyer, in the vendor currency, never altered (SRC-006, SRC-010)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from flo.api.collaboration import get_collaboration_storage
from flo.api.health import app
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import AuthorizationTarget
from flo.modules.identity.service import IdentityAuthorizationService
from flo.modules.sourcing.bids import BidService
from flo.modules.sourcing.schemas import (
    BidCreate,
    BidLineIn,
    BidPatch,
    BidReplace,
    BidRequirementIn,
    BidSubmit,
    RequirementsPut,
    RfqAmend,
    RfqLineIn,
    SealedBidRead,
)
from tests.collaboration.test_attachments import upload
from tests.imports.test_upload import MemoryStorage
from tests.isolation.test_real_sessions import client, headers, login
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import connection_url, unit
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import refused, send, sql
from tests.sourcing.test_rfq_content import vendor
from tests.sourcing.test_rfq_lifecycle import life, ready
from tests.vendors.test_vendors import regrant


def bids(db, actor=None, connection=None):
    return BidService(connection or db[0], Scope(db[1]), actor or db[3], Settings())


def second_user(db):
    return db[0].execute("SELECT id FROM identity WHERE email='second@example.test'").fetchone()[0]


def live(db, qty="3", requirements=(), names=("V",)):
    """A published RFQ with one line, any requirements, and its invited vendors' ids."""
    rfq, row, line = ready(db, qty=qty, names=names)
    if requirements:
        edit = life(db).get(rfq.id).edit_revision
        life(db).put_requirements(
            rfq.id, RequirementsPut(requirements=list(requirements)), str(edit)
        )
    with correlation_context("publish"):
        rfq = life(db).publish(rfq.id)
    return rfq, row, line, [i.vendor_id for i in rfq.invitees]


def rfq_line(db, rfq):
    return sql(db, "SELECT id FROM rfq_line WHERE rfq_id=%s ORDER BY line_no", (rfq.id,))[0][0]


def key(db, rfq, vendor_id, currency="KWD", version=1, actor=None):
    body = BidCreate(
        vendor_id=vendor_id,
        rfq_version=version,
        currency=currency,
        sent_at=datetime.now(UTC) - timedelta(hours=1),
    )
    with correlation_context("bid"):
        return bids(db, actor).capture(rfq.id, body)


def evidence(db, bid_id, kind="bid", document=None):
    attachment = uuid4()
    sql(
        db,
        "INSERT INTO attachment(id,org_id,document_type,document_id,created_by) "
        "VALUES(%s,%s,%s,%s,%s) RETURNING 1",
        (attachment, db[1], kind, document or bid_id, db[3]),
    )
    return attachment


def edit(db, rfq, bid, **fields):
    with correlation_context("bid"):
        return bids(db).patch(rfq.id, bid.id, BidPatch(**fields), str(bid.row_version))


def complete(db, rfq, bid, price="1.2345"):
    """Lines, answers (if any) and evidence filled in, so the bid can be submitted."""
    line = rfq_line(db, rfq)
    answers = [
        BidRequirementIn(requirement_id=r[0], met=True)
        for r in sql(db, "SELECT id FROM rfq_requirement WHERE rfq_id=%s", (rfq.id,))
    ]
    return edit(
        db,
        rfq,
        bid,
        lines=[BidLineIn(rfq_line_id=line, quoted=True, unit_price=price, tax="0")],
        requirements=answers,
        evidence_attachment_id=evidence(db, bid.id),
    )


def submit(db, rfq, bid, actor=None):
    with correlation_context("bid"):
        return bids(db, actor).submit(rfq.id, bid.id, BidSubmit(), str(bid.row_version))


def submitted(db, qty="3", **kwargs):
    rfq, row, line, [vid, *_] = live(db, qty, **kwargs)
    bid = complete(db, rfq, key(db, rfq, vid))
    return rfq, line, vid, submit(db, rfq, bid)


def audit_after(db, rfq, action):
    query = "SELECT after FROM audit_log WHERE target_id=%s AND action=%s ORDER BY id"
    return [r[0] for r in sql(db, query, (rfq.id, action))]


def test_submit_lists_every_problem_at_once_then_accepts_a_complete_bid(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db, requirements=("Warranty 2y",))
    bid = key(db, rfq, vid)
    assert bid.status == "draft" and bid.keyed_by == db[3] and bid.current is False
    error = refused(lambda: submit(db, rfq, bid), ErrorCode.VALIDATION_FAILED)
    line = rfq_line(db, rfq)
    [requirement] = [r[0] for r in sql(db, "SELECT id FROM rfq_requirement")]
    assert {e.field for e in error.errors} == {
        f"lines/{line}",
        f"requirements/{requirement}",
        "evidence_attachment_id",
    }
    # a quoted line without a price cannot even be stored: the database says quoted = priced
    refused(
        lambda: edit(db, rfq, bid, lines=[BidLineIn(rfq_line_id=line, quoted=True)]),
        ErrorCode.VALIDATION_FAILED,
    )
    done = submit(db, rfq, complete(db, rfq, bid))
    assert done.status == "submitted" and done.submitted_at
    assert isinstance(done, SealedBidRead)  # sealed the moment it is submitted (SRC-007)
    assert audit_after(db, rfq, "bid.create") == [{"bid_id": str(bid.id), "vendor_id": str(vid)}]
    assert audit_after(db, rfq, "bid.submit") == [{"bid_id": str(bid.id), "vendor_id": str(vid)}]


def test_line_total_is_exact_decimal_in_the_vendors_currency(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db, qty="3")
    line = rfq_line(db, rfq)
    kwd = key(db, rfq, vid, "KWD")
    kwd = edit(
        db, rfq, kwd, lines=[BidLineIn(rfq_line_id=line, quoted=True, unit_price="1.2345", tax="0")]
    )
    assert Decimal(kwd.lines[0].line_total) == Decimal("3.704")  # 3.7035 at 3 decimals, HALF_UP
    assert Decimal(kwd.lines[0].quantity) == 3 and kwd.currency == "KWD"
    jpy = edit(db, rfq, kwd, currency="JPY")  # the lines are redone at 0 decimals
    assert Decimal(jpy.lines[0].line_total) == 4  # 3.7035 at 0 decimals
    assert Decimal(jpy.lines[0].unit_price) == Decimal(
        "1.2345"
    )  # the vendor's figure is never touched
    cols = {
        r[0]
        for r in sql(
            db,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name IN ('bid','bid_line')",
        )
    }
    assert not {c for c in cols if "convert" in c or "base" in c or "rate" in c}  # SRC-010


def test_a_bid_answers_the_quantity_of_its_version_even_after_amendment(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, line, [vid] = live(db, qty="4")
    life(db).amend(
        rfq.id,
        RfqAmend(
            reason="More",
            lines=[
                RfqLineIn(requisition_line_id=line, requisition_version=row.version, quantity="6")
            ],
        ),
    )
    rfq_line_id = rfq_line(db, rfq)
    lines = [BidLineIn(rfq_line_id=rfq_line_id, quoted=True, unit_price="10", tax="0")]
    bid = key(db, rfq, vid, "USD", version=1)
    bid = edit(db, rfq, bid, lines=lines)
    assert Decimal(bid.lines[0].quantity) == 4 and Decimal(bid.lines[0].line_total) == 40
    bid = edit(db, rfq, bid, rfq_version=2)  # a new version recomputes every line
    assert Decimal(bid.lines[0].quantity) == 6 and Decimal(bid.lines[0].line_total) == 60
    refused(lambda: edit(db, rfq, bid, rfq_version=9), ErrorCode.VALIDATION_FAILED)


def test_a_submitted_bid_and_its_children_are_immutable_in_the_database(lifecycle_db):
    db, _ = lifecycle_db
    rfq, line, vid, done = submitted(db, requirements=("Warranty",))
    line_id = rfq_line(db, rfq)
    for statement, args in [
        ("UPDATE bid SET freight=1 WHERE id=%s", (done.id,)),
        ("DELETE FROM bid WHERE id=%s", (done.id,)),
        ("UPDATE bid_line SET tax=1 WHERE bid_id=%s", (done.id,)),
        ("DELETE FROM bid_line WHERE bid_id=%s", (done.id,)),
        ("UPDATE bid_requirement SET met=false WHERE bid_id=%s", (done.id,)),
        ("DELETE FROM bid_requirement WHERE bid_id=%s", (done.id,)),
        (
            "INSERT INTO bid_line(org_id,bid_id,rfq_line_id,quantity,quoted,tax) "
            "SELECT org_id,id,%s,1,false,0 FROM bid WHERE id=%s",
            (uuid4(), done.id),
        ),
    ]:
        with pytest.raises(
            (psycopg.errors.ObjectNotInPrerequisiteState, psycopg.errors.ForeignKeyViolation)
        ) as error:
            sql(db, statement + " RETURNING 1", args)
        if isinstance(error.value, psycopg.errors.ForeignKeyViolation):
            raise AssertionError(statement)  # the guard must fire before any foreign key does
    assert line_id and line and vid


@pytest.mark.parametrize(
    "statement",
    ["TRUNCATE bid, bid_line, bid_requirement", "TRUNCATE bid_line", "TRUNCATE bid_requirement"],
)
def test_truncate_is_refused_on_every_bid_table(lifecycle_db, statement):
    db, _ = lifecycle_db
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        sql(db, statement)


def test_patch_guards_if_match_keyer_and_state(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    bid = key(db, rfq, vid)
    missing = refused(
        lambda: bids(db).patch(rfq.id, bid.id, BidPatch(freight="1"), None), ErrorCode.BAD_REQUEST
    )
    assert missing.checks["problem"] == "if_match_required"
    stale = refused(lambda: bids(db).patch(rfq.id, bid.id, BidPatch(freight="1"), "9"))
    assert stale.checks["problem"] == "stale_version"
    refused(
        lambda: bids(db, second_user(db)).patch(rfq.id, bid.id, BidPatch(freight="1"), "1"),
        ErrorCode.FORBIDDEN,
    )
    bid = edit(db, rfq, bid, freight="2.5")
    assert Decimal(bid.freight) == Decimal("2.5") and bid.row_version == 2
    done = submit(db, rfq, complete(db, rfq, bid))
    gone = refused(lambda: edit(db, rfq, done, freight="1"))
    assert gone.checks["problem"] == "not_draft"


@pytest.mark.parametrize("field", ["vendor_id", "replaces_bid_id", "keyed_by", "status"])
def test_fixed_fields_cannot_be_patched(lifecycle_db, field):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    bid = key(db, rfq, vid)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            return await send(
                browser,
                "PATCH",
                f"/rfqs/{rfq.id}/bids/{bid.id}",
                {field: str(uuid4())},
                {"If-Match": "1"},
            )

    assert asyncio.run(scenario()).status_code == 422


def test_bid_exists_blocks_a_second_original_and_replace_corrects_it(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db, requirements=("Warranty",))
    first = complete(db, rfq, key(db, rfq, vid), price="98765.4321")
    first = edit(db, rfq, first, freight="55.5", warranty="5 years")
    done = submit(db, rfq, first)
    error = refused(lambda: key(db, rfq, vid))
    assert error.checks["problem"] == "bid_exists" and str(done.id) in error.detail
    assert sql(db, "SELECT count(*) FROM bid")[0][0] == 1
    with correlation_context("replace"):
        body = BidReplace(reason="Typo in price", sent_at=datetime.now(UTC))
        new = bids(db).replace(rfq.id, done.id, body)
    assert (
        new.status == "draft"
        and new.replaces_bid_id == done.id
        and new.evidence_attachment_id is None
    )
    # a replacement copies structure only (Q1 = A): the keyer re-keys every figure and answer
    assert [x.rfq_line_id for x in new.lines] == [rfq_line(db, rfq)]
    assert all(
        x.quoted is False and x.unit_price is None and x.line_total is None for x in new.lines
    )
    assert Decimal(new.freight) == 0 and new.requirements == []
    assert new.lead_time_days is None and new.warranty is None and new.delivery_terms is None
    assert "98765.4321" not in new.model_dump_json() and "55.5" not in new.model_dump_json()
    assert audit_after(db, rfq, "bid.replace") == [
        {
            "bid_id": str(new.id),
            "replaces_bid_id": str(done.id),
            "vendor_id": str(vid),
            "reason": "Typo in price",
        }
    ]
    original = bids(db).read(rfq.id, done.id, all_bids=True)
    assert original.row_version == done.row_version and original.replaced_by_bid_id is None
    again = refused(lambda: bids(db).replace(rfq.id, done.id, body))
    assert again.checks["problem"] == "already_replaced"
    swapped = submit(db, rfq, complete(db, rfq, new, price="2"))
    assert swapped.status == "submitted"
    old = bids(db).read(rfq.id, done.id, all_bids=True)
    assert old.replaced_by_bid_id == new.id
    assert refused(lambda: key(db, rfq, vid)).checks["problem"] == "bid_exists"


def test_replace_needs_time_before_the_deadline_and_an_unawarded_rfq(lifecycle_db):
    db, _ = lifecycle_db
    rfq, line, vid, done = submitted(db)
    body = BidReplace(reason="r", sent_at=datetime.now(UTC))
    sql(db, "UPDATE rfq SET status='awarded' WHERE id=%s RETURNING 1", (rfq.id,))
    assert (
        refused(lambda: bids(db).replace(rfq.id, done.id, body)).checks["problem"] == "rfq_awarded"
    )
    # now() is fixed within a transaction, so deadline_at = now() is exactly "at the deadline"
    with pytest.raises(ProblemError) as late, db[0].transaction():
        sql(db, "UPDATE rfq SET deadline_at=now() WHERE id=%s RETURNING 1", (rfq.id,))
        bids(db).replace(rfq.id, done.id, body)
    assert late.value.checks["problem"] == "deadline_passed"
    sql(
        db,
        "UPDATE rfq SET status='published', deadline_at=now()+interval '1 day' "
        "WHERE id=%s RETURNING 1",
        (rfq.id,),
    )
    with pytest.raises(ValueError):
        BidReplace(reason="  ", sent_at=datetime.now(UTC))
    assert bids(db).replace(rfq.id, done.id, body).status == "draft"


def test_a_draft_cannot_be_replaced(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    bid = key(db, rfq, vid)
    body = BidReplace(reason="r", sent_at=datetime.now(UTC))
    assert (
        refused(lambda: bids(db).replace(rfq.id, bid.id, body)).checks["problem"] == "not_submitted"
    )


def test_two_concurrent_replaces_of_one_original_let_exactly_one_win(lifecycle_db):
    db, _ = lifecycle_db
    rfq, line, vid, done = submitted(db)
    barrier = Barrier(2)

    def attempt(_):
        with (
            psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
            correlation_context("replace-race"),
        ):
            barrier.wait(timeout=10)
            try:
                body = BidReplace(reason="r", sent_at=datetime.now(UTC))
                bids(db, connection=conn).replace(rfq.id, done.id, body)
                return "success"
            except ProblemError as exc:
                return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["already_replaced", "success"]


def test_bids_need_a_published_rfq_an_invitee_and_a_known_version(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _ = ready(db)
    vid = rfq.invitees[0].vendor_id
    assert refused(lambda: key(db, rfq, vid)).checks["problem"] == "not_open"
    with correlation_context("publish"):
        life(db).publish(rfq.id)
    stranger = vendor(db, "Stranger")
    refused(lambda: key(db, rfq, stranger.id), ErrorCode.VALIDATION_FAILED)
    refused(lambda: key(db, rfq, vid, version=7), ErrorCode.VALIDATION_FAILED)
    refused(lambda: key(db, rfq, vid, currency="ZZZ"), ErrorCode.VALIDATION_FAILED)
    first = key(db, rfq, vid)
    assert refused(lambda: key(db, rfq, vid)).checks["problem"] == "draft_exists"
    assert Decimal(first.freight) == 0


def test_reads_show_everything_to_approvers_and_only_own_bids_to_editors(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, b] = live(db, names=("A", "B"))
    mine = key(db, rfq, a)
    theirs = key(db, rfq, b, actor=second_user(db))

    async def scenario():
        async with client(app) as browser:
            await login(browser)

            async def reads(permissions):
                regrant(db, permissions)
                return (
                    await send(browser, "GET", f"/rfqs/{rfq.id}/bids"),
                    await send(browser, "GET", f"/rfqs/{rfq.id}/bids/{theirs.id}"),
                    await send(browser, "GET", f"/rfqs/{rfq.id}/bids/{mine.id}"),
                )

            both, other, own = await reads(["rfq.approve", "rfq.view"])
            assert {x["id"] for x in both.json()} == {str(mine.id), str(theirs.id)}
            assert other.status_code == 200 and own.headers["etag"] == '"1"'
            editor, other, own = await reads(["rfq.edit", "rfq.view"])
            assert [x["id"] for x in editor.json()] == [str(mine.id)]
            assert other.status_code == 403 and own.status_code == 200
            nobody, other, own = await reads(["rfq.view"])
            # neither permission: no collection and no one else's bid; the keyer reads their own
            assert (nobody.status_code, other.status_code, own.status_code) == (403, 403, 200)
            sql(
                db, "UPDATE bid SET keyed_by=%s WHERE id=%s RETURNING 1", (second_user(db), mine.id)
            )
            empty, _, _ = await reads(["rfq.edit", "rfq.view"])
            assert empty.status_code == 200 and empty.json() == []

    asyncio.run(scenario())


def test_evidence_is_a_bid_attachment_that_locks_at_submit(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    bid = key(db, rfq, vid)
    wrong_kind = evidence(db, bid.id, "rfq", rfq.id)
    elsewhere = evidence(db, uuid4(), "bid", rfq.id)  # another document's attachment
    for bad in (wrong_kind, elsewhere):
        error = refused(
            lambda bad=bad: edit(db, rfq, bid, evidence_attachment_id=bad),
            ErrorCode.VALIDATION_FAILED,
        )
        assert error.errors[0].field == "evidence_attachment_id"
    app.dependency_overrides[get_collaboration_storage] = lambda: MemoryStorage()
    url = f"/api/v1/documents/bid/{bid.id}/attachments"
    bid_url = f"/rfqs/{rfq.id}/bids/{bid.id}"

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            first = await upload(browser, url)
            assert first.status_code == 201, first.text
            attachment = first.json()["id"]
            gone = (await upload(browser, url)).json()["id"]
            sql(db, "UPDATE attachment SET deleted_at=now() WHERE id=%s RETURNING 1", (gone,))
            body = {"evidence_attachment_id": gone}
            deleted = await send(browser, "PATCH", bid_url, body, {"If-Match": "1"})
            assert deleted.status_code == 422, deleted.text
            body = {"evidence_attachment_id": attachment}
            linked = await send(browser, "PATCH", bid_url, body, {"If-Match": "1"})
            assert linked.status_code == 200, linked.text
            got = await browser.get(url)
            assert got.status_code == 200, got.text
            current = edit(
                db,
                rfq,
                bids(db).read(rfq.id, bid.id, all_bids=True),
                lines=[BidLineIn(rfq_line_id=rfq_line(db, rfq), quoted=False)],
            )
            submit(db, rfq, current)
            writes = [
                await upload(browser, url),
                await upload(browser, f"/api/v1/attachments/{attachment}/versions"),
                await browser.delete(
                    f"/api/v1/attachments/{attachment}", headers=headers(browser, uuid4().hex)
                ),
            ]
            for response in writes:
                assert response.status_code == 409, response.text
                assert response.json()["checks"]["problem"] == "not_draft"
            sealed = await browser.get(url)  # the vendor's quote is bid content: sealed (SRC-007)
            assert sealed.status_code == 409 and sealed.json()["checks"]["problem"] == "bid_sealed"
            sql(
                db,
                "UPDATE rfq SET deadline_at=now()-interval '1 minute' WHERE id=%s RETURNING 1",
                (rfq.id,),
            )
            assert (await browser.get(url)).status_code == 200  # after the deadline reading opens
            regrant(db, ["rfq.edit", "record.read"])
            assert (await browser.get(url)).status_code == 403  # the content needs rfq.approve
            regrant(db, ["rfq.edit", "rfq.approve"])
            assert (await browser.get(url)).status_code == 403  # and every attachment record.read
            regrant(db, ["rfq.approve", "record.read"])
            assert (await browser.get(url)).status_code == 200

    try:
        asyncio.run(scenario())
    finally:
        app.dependency_overrides.pop(get_collaboration_storage, None)


def test_bid_routes_conceal_foreign_tenants(lifecycle_db):
    db, _ = lifecycle_db
    foreign_unit = unit(db, "FBU", org=db[2])
    foreign_rfq = uuid4()
    owner = second_user(db)
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign_rfq, db[2], foreign_unit.id, owner),
        )
    rfq, _, _, [vid] = live(db)
    mine = key(db, rfq, vid)
    sent = datetime.now(UTC).isoformat()

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, suffix, data in [
                (
                    "POST",
                    f"/rfqs/{foreign_rfq}/bids",
                    {"vendor_id": str(vid), "rfq_version": 1, "currency": "USD", "sent_at": sent},
                ),
                ("GET", f"/rfqs/{foreign_rfq}/bids", None),
                ("GET", f"/rfqs/{foreign_rfq}/bids/{mine.id}", None),
                ("PATCH", f"/rfqs/{foreign_rfq}/bids/{mine.id}", {"freight": "1"}),
                ("POST", f"/rfqs/{foreign_rfq}/bids/{mine.id}/submit", {}),
                (
                    "POST",
                    f"/rfqs/{foreign_rfq}/bids/{mine.id}/replace",
                    {"reason": "r", "sent_at": sent},
                ),
                # a bid of another RFQ is an unknown id
                ("GET", f"/rfqs/{rfq.id}/bids/{uuid4()}", None),
                ("PATCH", f"/rfqs/{rfq.id}/bids/{uuid4()}", {"freight": "1"}),
            ]:
                response = await send(browser, method, suffix, data, {"If-Match": "1"})
                assert response.status_code == 404, (method, suffix, response.text)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("permissions", "status"),
    [(["rfq.view"], 403), (["rfq.edit", "rfq.view"], 201), (["rfq.approve", "rfq.view"], 403)],
)
def test_only_editors_key_bids_and_the_post_replays(lifecycle_db, permissions, status):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    regrant(db, permissions)
    body = {
        "vendor_id": str(vid),
        "rfq_version": 1,
        "currency": "KWD",
        "sent_at": datetime.now(UTC).isoformat(),
    }

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            key = headers(browser, "bid-key")
            url = f"/api/v1/sourcing/rfqs/{rfq.id}/bids"
            first = await browser.post(url, json=body, headers=key)
            second = await browser.post(url, json=body, headers=key)
            return first, second

    first, second = asyncio.run(scenario())
    assert first.status_code == status
    if status == 201:
        assert second.status_code == 201 and second.json()["id"] == first.json()["id"]
        assert first.headers["etag"] == '"1"'
        assert sql(db, "SELECT count(*) FROM bid")[0][0] == 1


def as_role(db, code):
    """Replace the admin's roles with one baseline role, granted organization-wide."""
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
        identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
        found = db[0].execute("SELECT id FROM role WHERE org_id=%s AND code=%s", (db[1], code))
        row = found.fetchone()
        role_id = row[0] if row else identity.seed_baseline_roles()[code].id
        identity.grant_role(db[3], role_id, AuthorizationTarget.organization(db[1]))


@pytest.mark.parametrize(
    ("role", "writes"),
    [
        ("requestor", False),
        ("purchasing-user", False),
        ("sourcing-specialist", True),
        ("organization-administrator", True),
    ],
)
def test_baseline_roles_over_the_bid_routes(lifecycle_db, role, writes):
    db, _ = lifecycle_db
    rfq, _, _, [a, b] = live(db, names=("A", "B"))
    theirs = key(db, rfq, b, actor=second_user(db))
    body = {
        "vendor_id": str(a),
        "rfq_version": 1,
        "currency": "KWD",
        "sent_at": datetime.now(UTC).isoformat(),
    }

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            as_role(db, role)
            base = f"/rfqs/{rfq.id}/bids"
            created = await send(browser, "POST", base, body)
            listed = await send(browser, "GET", base)
            read = await send(browser, "GET", f"{base}/{theirs.id}")
            patched = await send(
                browser, "PATCH", f"{base}/{theirs.id}", {"freight": "1"}, {"If-Match": "1"}
            )
            submitted = await send(
                browser, "POST", f"{base}/{theirs.id}/submit", {}, {"If-Match": "1"}
            )
            ok = 200 if writes else 403
            assert (created.status_code, listed.status_code, read.status_code) == (
                201 if writes else 403,
                ok,
                ok,
            ), (created.text, listed.text, read.text)
            # keyer only: nobody but the person who keyed the bid may change or submit it
            assert patched.status_code == 403 and submitted.status_code == 403
            if writes:
                own = created.json()["id"]
                mine = await send(
                    browser, "PATCH", f"{base}/{own}", {"freight": "1"}, {"If-Match": "1"}
                )
                assert mine.status_code == 200, mine.text

    asyncio.run(scenario())


def test_a_quoted_line_needs_its_tax_and_a_line_not_quoted_has_none(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    bid, line = key(db, rfq, vid), rfq_line(db, rfq)
    blank = refused(
        lambda: edit(
            db, rfq, bid, lines=[BidLineIn(rfq_line_id=line, quoted=True, unit_price="1")]
        ),
        ErrorCode.VALIDATION_FAILED,
    )
    assert [(e.field, e.message) for e in blank.errors] == [
        (f"lines/{line}", "Enter the tax on this line, or 0 if none.")
    ]
    taxed = refused(
        lambda: edit(db, rfq, bid, lines=[BidLineIn(rfq_line_id=line, quoted=False, tax="1")]),
        ErrorCode.VALIDATION_FAILED,
    )
    assert taxed.errors[0].message == "A line not quoted has no tax."
    saved = edit(db, rfq, bid, lines=[BidLineIn(rfq_line_id=line, quoted=False)])
    assert Decimal(saved.lines[0].tax) == 0  # stored as 0, the column is NOT NULL


def test_totals_are_exact_decimals_in_the_bid_currency_and_never_on_a_sealed_read(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db, qty="3")
    first = rfq_line(db, rfq)
    second, source = uuid4(), uuid4()
    clone = (
        "INSERT INTO {t} SELECT (jsonb_populate_record(null::{t}, to_jsonb(l) || "
        "%s::jsonb)).* FROM {t} l WHERE l.id = %s RETURNING 1"
    )
    [req_line] = sql(db, "SELECT requisition_line_id FROM rfq_line WHERE id=%s", (first,))[0]
    sql(
        db,
        clone.format(t="requisition_line"),
        (f'{{"id": "{source}", "line_no": 2}}', req_line),
    )
    sql(
        db,
        clone.format(t="rfq_line"),
        (f'{{"id": "{second}", "line_no": 2, "requisition_line_id": "{source}"}}', first),
    )
    with tenant_transaction(db[0], Scope(db[1])):  # the answered version's snapshot is
        db[0].execute(
            "SELECT set_config('session_replication_role', 'replica', true)"
        )  # append-only
        db[0].execute(
            "UPDATE rfq_version SET snapshot = jsonb_set(snapshot, ARRAY['lines', %s], "
            "snapshot->'lines'->%s) WHERE rfq_id=%s",
            (str(source), str(req_line), rfq.id),
        )
    bid = key(db, rfq, vid, "KWD")
    bid = edit(
        db,
        rfq,
        bid,
        freight="1.2345",
        lines=[
            BidLineIn(rfq_line_id=first, quoted=True, unit_price="1.2345", tax="0.5"),
            BidLineIn(rfq_line_id=second, quoted=True, unit_price="2", tax="0"),
        ],
    )
    # lines 3.7035 -> 3.704 and 6.000; freight 1.2345 -> 1.235; quantized at KWD's 3 decimals
    assert bid.totals is not None
    assert (bid.totals.lines, bid.totals.tax, bid.totals.freight, bid.totals.total) == (
        "9.704",
        "0.500",
        "1.235",
        "11.439",
    )
    assert "totals" not in SealedBidRead.model_fields


def test_a_lead_accepts_another_users_late_draft_only_when_late_and_with_a_reason(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [vid] = live(db)
    sql(
        db, "UPDATE rfq SET deadline_at=now()-interval '2 hours' WHERE id=%s RETURNING 1", (rfq.id,)
    )
    buyer = second_user(db)
    draft = key(db, rfq, vid, actor=buyer)
    line = rfq_line(db, rfq)
    with correlation_context("bid"):
        draft = bids(db, buyer).patch(
            rfq.id,
            draft.id,
            BidPatch(
                lines=[BidLineIn(rfq_line_id=line, quoted=True, unit_price="1", tax="0")],
                evidence_attachment_id=evidence(db, draft.id),
            ),
            str(draft.row_version),
        )

    def lead(reason, may=True):
        with correlation_context("late"):
            body = BidSubmit(accept_late_reason=reason)
            return bids(db).submit(rfq.id, draft.id, body, str(draft.row_version), may)

    refused(lambda: lead("Courier strike", may=False), ErrorCode.FORBIDDEN)
    assert refused(lambda: lead(None)).checks["problem"] == "late_bid"
    # on time (sent before the deadline): a non-keyer lead is refused
    sql(db, "UPDATE bid SET sent_at=now()-interval '3 hours' WHERE id=%s RETURNING 1", (draft.id,))
    refused(lambda: lead("Courier strike"), ErrorCode.FORBIDDEN)
    sql(db, "UPDATE bid SET sent_at=now()-interval '1 hour' WHERE id=%s RETURNING 1", (draft.id,))
    done = lead("Courier strike")
    assert done.status == "submitted"
    row = sql(db, "SELECT keyed_by, late_accepted_by FROM bid WHERE id=%s", (draft.id,))[0]
    assert row == (buyer, db[3])  # keyed_by stays the buyer; the lead accepted
    assert len(audit_after(db, rfq, "bid.submit")) == 1
    assert audit_after(db, rfq, "bid.late_override")[0]["reason"] == "Courier strike"
    refused(lambda: lead("again"), ErrorCode.CONFLICT)  # no longer a draft


def test_bids_received_counts_a_replaced_vendor_once_and_never_a_draft(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _, [a, b] = live(db, names=("A", "B"))
    assert life(db).get(rfq.id).bids_received == 0
    done = submit(db, rfq, complete(db, rfq, key(db, rfq, a)))
    key(db, rfq, b)  # a draft
    assert life(db).get(rfq.id).bids_received == 1
    with correlation_context("replace"):
        new = bids(db).replace(rfq.id, done.id, BidReplace(reason="r", sent_at=datetime.now(UTC)))
    assert life(db).get(rfq.id).bids_received == 1  # replacement still a draft
    submit(db, rfq, complete(db, rfq, new))
    assert life(db).get(rfq.id).bids_received == 1  # replaced once, counted once
