"""Internal versus vendor-facing notes and attachments on a PO, enforced by test (E13-S11)."""

import asyncio
import hashlib
from uuid import uuid4

import pytest

from flo.api.collaboration import get_collaboration_storage
from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from tests.collaboration.test_attachments import upload
from tests.collaboration.test_notes import set_role
from tests.imports.test_upload import MemoryStorage
from tests.isolation.test_real_sessions import client, headers, login
from tests.purchasing.test_changes import changes, open_change
from tests.purchasing.test_issue import approve, run
from tests.purchasing.test_purchase_orders import create, pos, ready
from tests.purchasing.test_transmission import Sender, dispatch, foreign_po
from tests.purchasing.test_vendor_copy import read, text
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import sql
from tests.vendors.test_vendors import regrant

INT_NOTE, EXT_NOTE, INT_FILE, INT_BYTES = "INTNOTE-7Q", "EXTNOTE-3K", "INTFILE-9Z", "INTBYTES-5W"
V = ["record.read", "purchase_order.view"]


@pytest.fixture(autouse=True)
def storage():
    store = MemoryStorage()
    app.dependency_overrides[get_collaboration_storage] = lambda: store
    yield store
    app.dependency_overrides.pop(get_collaboration_storage, None)


def docs(po_id, kind="notes", type="purchase_order"):
    return f"/api/v1/documents/{type}/{po_id}/{kind}"


def two(db):
    """A draft PO and an issued one on the same award."""
    rfq, _, a, _, award_id, _ = ready(db)
    draft, issued = create(db, rfq, a, award_id, "4"), create(db, rfq, a, award_id, "4")
    approve(db, issued)
    run(db, issued)
    return draft.id, issued.id


def mk(db, po_id, visibility="internal", body="n", type="purchase_order"):
    """A note by the test user, written directly so the acting role is not tested twice."""
    id = uuid4()
    sql(
        db,
        "INSERT INTO note (id, org_id, document_type, document_id, author_id, body, visibility) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING 1",
        (id, db[1], type, po_id, db[3], body, visibility),
    )
    return id


def stored_note(db, id):
    return sql(db, "SELECT body, visibility, version FROM note WHERE id=%s", (id,))[0]


async def post_note(browser, po_id, visibility, type="purchase_order"):
    return await browser.post(
        docs(po_id, type=type),
        json={"body": "x", "visibility": visibility},
        headers=headers(browser, uuid4().hex),
    )


async def patch_note(browser, id, **patch):
    return await browser.patch(
        f"/api/v1/notes/{id}",
        json=patch,
        headers=headers(browser, uuid4().hex) | {"If-Match": '"1"'},
    )


def test_nothing_internal_reaches_the_supplier_email_or_pdf(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    dispatch(db, Sender())  # the RFQ invitations are not under test

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for body, visibility in ((INT_NOTE, "internal"), (EXT_NOTE, "shareable")):
                made = await browser.post(
                    docs(po.id),
                    json={"body": body, "visibility": visibility},
                    headers=headers(browser, uuid4().hex),
                )
                assert made.status_code == 201, made.text
            up = await upload(
                browser, docs(po.id, "attachments"), f"{INT_FILE}.txt", INT_BYTES.encode()
            )
            assert up.status_code == 201, up.text

    asyncio.run(scenario())
    approve(db, po)
    run(db, po)
    [(payload,)] = sql(
        db, "SELECT payload FROM outbox WHERE left(idempotency_key, 16)='po_transmission:'"
    )
    shown = str({k: v for k, v in payload.items() if k != "attachments"})
    for needle in (INT_NOTE, INT_FILE, INT_BYTES):
        assert needle not in shown, needle

    seen = []

    class Recorder(Sender):
        def send(self, to, template, context, idempotency_key, attachments=()):
            seen.append((template, context, tuple(attachments)))
            return "id"

    dispatch(db, Recorder())
    [(_, context, [attachment])] = seen
    [(sha,)] = sql(db, "SELECT pdf_sha256 FROM po_version")
    assert attachment.content_type == "application/pdf"
    assert hashlib.sha256(attachment.content).hexdigest() == sha
    assert INT_BYTES.encode() not in attachment.content
    body = text(attachment.content)
    assert EXT_NOTE in body
    for needle in (INT_NOTE, INT_FILE, INT_BYTES):
        assert needle not in body and needle not in str(context), needle


def test_a_note_flipped_to_shareable_reaches_the_next_revision_only(lifecycle_db):
    db, _ = lifecycle_db
    _, issued = two(db)
    note = mk(db, issued, "internal", "FLIPNOTE-2M")
    first = read(db, issued)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            flipped = await patch_note(browser, note, visibility="shareable")
            assert flipped.status_code == 200, flipped.text
            got = await browser.get(f"/api/v1/purchase-orders/{issued}/pdf")
            assert got.status_code == 200 and got.content == first
            assert "FLIPNOTE-2M" not in text(got.content)

    asyncio.run(scenario())
    change = open_change(db, pos(db).get(issued), terms="Net 45 days")
    new_id = changes(db).apply(issued, 1, f'"{change.version}"').result_po_id
    assert "FLIPNOTE-2M" in text(read(db, new_id))
    assert "FLIPNOTE-2M" not in text(read(db, issued))


def test_notes_name_their_author_and_visibility(lifecycle_db):
    db, _ = lifecycle_db
    draft, _ = two(db)
    mk(db, draft, "internal")
    mk(db, draft, "shareable")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            got = await browser.get(docs(draft))
            assert got.status_code == 200
            rows = got.json()
            assert {r["visibility"] for r in rows} == {"internal", "shareable"}
            assert all(r["author_email"] == "admin@example.test" for r in rows)

    asyncio.run(scenario())


def test_attachments_and_reads_per_role(lifecycle_db):
    db, _ = lifecycle_db
    draft, issued = two(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            set_role(db, "requestor")
            for kind in ("notes", "attachments"):
                assert (await browser.get(docs(draft, kind))).status_code == 403
            for perms, wanted in (
                (V + ["purchase_order.create"], {draft: 201, issued: 403}),
                (V + ["purchase_order.amend"], {draft: 403, issued: 201}),
            ):
                regrant(db, perms)
                for po_id, status in wanted.items():
                    up = await upload(
                        browser, docs(po_id, "attachments"), f"{INT_FILE}.txt", INT_BYTES.encode()
                    )
                    assert up.status_code == status, (perms, po_id, up.text)

    asyncio.run(scenario())


def test_sharing_a_po_note_needs_the_po_write_rule(lifecycle_db):
    db, _ = lifecycle_db
    draft, issued = two(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)

            async def share(po_id):
                made = (await post_note(browser, po_id, "shareable")).status_code
                flip = (
                    await patch_note(browser, mk(db, po_id), visibility="shareable")
                ).status_code
                return made, flip

            own_shareable = mk(db, draft, "shareable", "keep")
            other_shareable = mk(db, issued, "shareable", "keep")
            regrant(db, V)
            assert (await post_note(browser, draft, "internal")).status_code == 201
            assert await share(draft) == (403, 403) and await share(issued) == (403, 403)
            flip = mk(db, draft)
            assert (await patch_note(browser, flip, visibility="shareable")).status_code == 403
            assert stored_note(db, flip) == ("n", "internal", 1)
            assert sql(db, "SELECT count(*) FROM note_revision WHERE note_id=%s", (flip,)) == [(0,)]
            edit = await patch_note(browser, other_shareable, body="changed")
            assert edit.status_code == 403
            assert stored_note(db, other_shareable) == ("keep", "shareable", 1)
            assert sql(
                db, "SELECT count(*) FROM note_revision WHERE note_id=%s", (other_shareable,)
            ) == [(0,)]
            down = await patch_note(browser, own_shareable, visibility="internal")
            assert down.status_code == 200

            regrant(db, V + ["purchase_order.create"])
            assert await share(draft) == (201, 200) and await share(issued) == (403, 403)
            regrant(db, V + ["purchase_order.amend"])
            assert await share(draft) == (403, 403) and await share(issued) == (201, 200)
            set_role(db, "purchasing-user")
            assert await share(draft) == (201, 200) and await share(issued) == (201, 200)

    asyncio.run(scenario())


def test_other_document_types_keep_the_read_rule(lifecycle_db):
    db, _ = lifecycle_db
    draft, _ = two(db)
    [(req,)] = sql(db, "SELECT requisition_id FROM purchase_order WHERE id=%s", (draft,))
    own = mk(db, req, type="requisition")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["record.read", "requisition.view"])
            assert (await post_note(browser, req, "shareable", "requisition")).status_code == 201
            flip = await patch_note(browser, own, visibility="shareable")
            assert flip.status_code == 200, flip.text

    asyncio.run(scenario())


def test_foreign_tenant_po_documents_are_404(lifecycle_db):
    db, _ = lifecycle_db
    two(db)
    other = foreign_po(db)
    foreign_note = uuid4()
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO note (id, org_id, document_type, document_id, author_id, body, "
            "visibility) VALUES (%s, %s, 'purchase_order', %s, %s, 'n', 'internal')",
            (foreign_note, db[2], other, db[3]),
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for kind in ("notes", "attachments"):
                assert (await browser.get(docs(other, kind))).status_code == 404
            assert (await post_note(browser, other, "internal")).status_code == 404
            up = await upload(browser, docs(other, "attachments"), "a.txt", b"x")
            assert up.status_code == 404
            assert (
                await patch_note(browser, foreign_note, visibility="shareable")
            ).status_code == 404

    asyncio.run(scenario())
    with tenant_transaction(db[0], Scope(db[2])):
        row = db[0].execute("SELECT visibility, version FROM note WHERE id=%s", (foreign_note,))
        assert row.fetchone() == ("internal", 1)
