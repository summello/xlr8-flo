"""The vendor-copy PDF is frozen at issue and reproduced from the stored snapshot (E13-S08)."""

import asyncio
import hashlib
import io
import json
import logging
from datetime import date
from uuid import uuid4

import psycopg
import pypdf
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import Outcome, OutcomeContext
from flo.modules.org.schemas import OrgAddressClose, SettingPut
from flo.modules.org.service import OrgService
from flo.modules.purchasing import pdf
from flo.modules.purchasing.pdf import freeze, frozen, pdf_for, render, vendor_copy_read
from flo.modules.purchasing.vendor_copy import (
    VendorCopy,
    VendorCopyAddress,
    VendorCopyLine,
    VendorCopyNote,
)
from tests.isolation.test_real_sessions import client, login
from tests.purchasing.test_changes import (
    changes,
    decide_change,
    line,
    open_change,
    publish_change,
    submit,
)
from tests.purchasing.test_issue import approve, run
from tests.purchasing.test_purchase_orders import create, pos, ready, send
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import sql
from tests.vendors.test_vendors import regrant

LETTERHEAD = "Acme Procurement\n1 Harbour Road\nRotterdam"
STANDARD = "Goods remain the property of the seller until paid in full."

FIELDS = {
    "number", "revision", "issued_on", "needed_by", "currency", "vendor_trading_name",
    "vendor_legal_name", "bill_to", "ship_to", "terms", "letterhead_address", "standard_terms",
    "lines", "price_total", "tax_total", "freight_total", "total", "notes",
    "line1", "line2", "city", "region", "postal_code", "country",
    "line_no", "description", "quantity", "uom_code", "unit_price", "price", "tax", "freight",
    "line_total", "body", "created_on",
}  # fmt: skip


def names(model, seen=None):
    """Every field name of `model` and of the models nested in its annotations, recursively."""
    seen = set() if seen is None else seen
    for name, info in model.model_fields.items():
        seen.add(name)
        stack = [info.annotation]
        while stack:
            arg = stack.pop()
            if hasattr(arg, "model_fields"):
                names(arg, seen)
            stack.extend(getattr(arg, "__args__", ()))
    return seen


def text(data: bytes) -> str:
    return "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(data)).pages)


def issued(db, qty="4", awarded="10"):
    rfq, _, a, _, award_id, unit_id = ready(db, awarded, awarded)
    po = create(db, rfq, a, award_id, qty)
    approve(db, po)
    run(db, po)
    return po.id, unit_id


def setting(db, key, value):
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(key, SettingPut(value=value))


def read(db, po_id):
    return pdf_for(db[0], Scope(db[1]), po_id)


def stored(db):
    return sql(db, "SELECT po_id, version, pdf_sha256 FROM po_version ORDER BY version")


def vendor_json(db, po_id):
    return vendor_copy_read(db[0], Scope(db[1]), po_id).model_dump(mode="json")


# -- the whitelist ---------------------------------------------------------------------------


def test_the_vendor_copy_allows_exactly_these_field_names():
    assert names(VendorCopy) == FIELDS  # plant 2: a chartfield on a line adds a name


def test_chartfields_budget_figures_and_internal_notes_never_reach_the_vendor(
    lifecycle_db, monkeypatch
):
    from tests.purchasing.test_purchase_orders import address
    from tests.sourcing import test_award
    from tests.sourcing.test_cancel_supersede import approved_award
    from tests.sourcing.test_comparison import fx, rate_date_of

    db, _ = lifecycle_db
    real = test_award.quoted
    monkeypatch.setattr(
        test_award, "quoted", lambda db, rfq, vid, currency, **kw: real(db, rfq, vid, "EUR", **kw)
    )
    rfq, _, _, [a, _b] = test_award.scene(db, prices=("7.5", "9"), tax="13.37", freight="2.01")
    fx(db, rate_date_of(db, rfq), USD="1.7")  # 1 EUR = 1.7 USD
    award_id = approved_award(db, rfq, a, "5").id
    address(db, sql(db, "SELECT unit_id FROM rfq WHERE id=%s", (rfq.id,))[0][0])
    setting(db, "tax.consumes_budget", False)  # budget_total leaves the tax out
    po = create(db, rfq, a, award_id, "5")
    # distinctive chartfields on the PO and its reservation; triggers are off for this write
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("SET LOCAL session_replication_role = replica")
        for table in ("po_line", "ledger_entry"):
            db[0].execute(
                f"UPDATE {table} SET department_code = 'DEPTLEAK7', "  # noqa: S608
                "ledger_account_code = 'LEDGERLEAK9'"
            )
    approve(db, po)
    run(db, po)
    po = pos(db).get(po.id)
    sql(
        db,
        "INSERT INTO note (id, org_id, document_type, document_id, author_id, body, visibility) "
        "VALUES (%s, %s, 'purchase_order', %s, %s, 'NOTELEAK1', 'internal') RETURNING 1",
        (uuid4(), db[1], po.id, db[3]),
    )
    assert po.currency == "EUR" and po.lines[0].department_code == "DEPTLEAK7"
    internal = {po.budget_total, po.budget_total_project, po.total_converted}
    visible = {po.total, po.price_total, po.tax_total, po.freight_total}
    assert not internal & visible and po.rate["rate"] != "1"
    shown = vendor_json(db, po.id)
    blob, pdf_text = json.dumps(shown), text(read(db, po.id))
    for needle in (
        "DEPTLEAK7", "LEDGERLEAK9", "NOTELEAK1", str(po.lines[0].project_id), *internal
    ):  # fmt: skip
        assert needle not in blob and needle not in pdf_text, needle
    assert po.total in blob and po.total in pdf_text.replace(" ", "")


# -- settings --------------------------------------------------------------------------------


def test_letterhead_and_standard_terms_print_verbatim_and_are_validated(lifecycle_db):
    db, _ = lifecycle_db
    setting(db, "po.letterhead_address", LETTERHEAD)
    setting(db, "po.standard_terms", "<script>alert(1)</script> " + STANDARD)
    po_id, unit_id = issued(db)
    body = text(read(db, po_id))
    assert "Acme Procurement" in body and "Rotterdam" in body and STANDARD in body
    assert "<script>alert(1)</script>" in body

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            from tests.isolation.test_real_sessions import headers

            async def put(key, value, unit=None):
                payload = {"value": value} | ({"unit_id": str(unit)} if unit else {})
                return await browser.request(
                    "PUT",
                    f"/api/v1/org/settings/{key}",
                    json=payload,
                    headers=headers(browser, uuid4().hex),
                )

            for key, bad in (
                ("po.letterhead_address", "a\n" * 9),
                ("po.letterhead_address", "x" * 601),
                ("po.standard_terms", "   "),
                ("po.standard_terms", "x" * 4001),
            ):
                bad_value = await put(key, bad)
                assert bad_value.status_code == 422, (key, bad_value.text)
                assert bad_value.json()["checks"]["problem"] == "invalid_setting_value"
            override = await put("po.standard_terms", "x", unit_id)
            assert override.status_code == 422
            assert override.json()["checks"]["problem"] == "org_only_setting"
            assert (await put("po.letterhead_address", "x" * 600)).status_code == 200

    asyncio.run(scenario())


def test_unset_settings_leave_their_blocks_out(lifecycle_db):
    db, _ = lifecycle_db
    po_id, _ = issued(db)
    copy = frozen(db[0], Scope(db[1]), po_id).copy
    assert copy.letterhead_address is None and copy.standard_terms is None
    assert "Standard terms" not in text(read(db, po_id))


# -- snapshot, not live ----------------------------------------------------------------------


def test_the_pdf_comes_from_the_snapshot_not_from_live_data(lifecycle_db):
    db, _ = lifecycle_db
    setting(db, "po.letterhead_address", LETTERHEAD)
    setting(db, "po.standard_terms", STANDARD)
    po_id, unit_id = issued(db)
    first = read(db, po_id)
    ship_to = pos(db).get(po_id).ship_to_address_id
    setting(db, "po.letterhead_address", "Somebody Else Ltd")
    setting(db, "po.standard_terms", "Different terms")
    OrgService(db[0], Scope(db[1]), db[3]).close_address(
        unit_id, ship_to, OrgAddressClose(effective_to=date.today())
    )
    again = read(db, po_id)  # plant 1: rendering a live vendor_copy() fails this
    [(_, _, sha)] = stored(db)
    assert hashlib.sha256(again).hexdigest() == sha == hashlib.sha256(first).hexdigest()
    body = text(again)
    assert "Acme Procurement" in body and STANDARD in body and "Somebody Else" not in body
    shown = vendor_json(db, po_id)
    assert shown["issued"] is True and shown["template_version"] == "po-v1"
    assert shown["vendor_copy"]["letterhead_address"] == LETTERHEAD
    assert shown["vendor_copy"]["ship_to"] is not None
    assert shown["email_summary"].endswith("The purchase order is attached as a PDF.")


def test_each_revision_has_its_own_snapshot_and_the_old_one_never_moves(lifecycle_db):
    db, _ = lifecycle_db
    po_id, _ = issued(db)
    first = read(db, po_id)
    po = pos(db).get(po_id)
    change = open_change(db, po, terms="Net 45 days")
    applied = changes(db).apply(po_id, 1, f'"{change.version}"')
    new_id = applied.result_po_id
    assert read(db, po_id) == first and "Net 45" not in text(first)
    assert read(db, po_id) == read(db, po_id)  # rendering one snapshot twice: same bytes
    assert "Net 45 days" in text(read(db, new_id))
    assert sql(db, "SELECT po_id, version FROM po_version ORDER BY version") == [
        (po_id, 2),
        (new_id, 2),
    ]
    assert sql(db, "SELECT count(*) FROM purchase_order") == [(2,)]
    assert frozen(db[0], Scope(db[1]), new_id).copy.revision == 1
    assert sql(db, "SELECT version FROM po_version WHERE po_id=%s", (po_id,)) == [(2,)]


# -- the renderer ----------------------------------------------------------------------------


def golden_line(no, description, quantity, unit_price, price, tax, total):
    return VendorCopyLine(
        line_no=no, description=description, quantity=quantity, uom_code="EA",
        unit_price=unit_price, price=price, tax=tax, freight="1.00", line_total=total,
    )  # fmt: skip


GOLDEN = VendorCopy(
    number="PO-00001", revision=0, issued_on=date(2026, 10, 30), needed_by=date(2026, 12, 1),
    currency="EUR", vendor_trading_name="Nordlicht GmbH",
    vendor_legal_name="Nordlicht Handels GmbH",
    bill_to=VendorCopyAddress(line1="1 Main St", city="Springfield", country="US"),
    ship_to=VendorCopyAddress(
        line1="9 Dock Rd", line2="Gate 4", city="Rotterdam", postal_code="3011", country="NL"
    ),
    terms="Net 30", letterhead_address=LETTERHEAD, standard_terms=STANDARD,
    lines=[
        golden_line(1, "Steel bolts M8", "100.0000", "0.25", "25.00", "4.75", "30.75"),
        golden_line(2, "Washers", "200.0000", "0.10", "20.00", "3.80", "24.80"),
    ],
    price_total="45.00", tax_total="8.55", freight_total="2.00", total="55.55",
    notes=[VendorCopyNote(body="Deliver to gate 4.", created_on=date(2026, 10, 29))],
)  # fmt: skip
GOLDEN_SHA256 = "4ec771510bb11bc43488d23a19e40b2268927882ecbbca5a9bc156adff8b21d1"


def test_po_v1_renders_the_golden_snapshot_to_the_same_bytes_everywhere():
    snapshot = GOLDEN.model_dump(mode="json")
    data = render(VendorCopy.model_validate(snapshot), "po-v1")
    # plant 4: changing one layout constant in po-v1 changes this hash
    assert hashlib.sha256(data).hexdigest() == GOLDEN_SHA256
    assert render(GOLDEN, "po-v1") == data
    body = text(data)
    for expected in (
        "PO-00001",
        "Nordlicht GmbH",
        "Gate 4",
        "Deliver to gate 4.",
        "Net 30",
        "55.55",
    ):
        assert expected in body


def test_render_refuses_an_unknown_template_and_an_unissued_copy():
    with pytest.raises(ValueError):
        render(GOLDEN, "po-v9")
    with pytest.raises(ValueError):
        render(GOLDEN.model_copy(update={"issued_on": None}), "po-v1")


def test_a_long_po_keeps_every_line_and_every_term_token():
    tokens = " ".join(f"T{n:04d}" for n in range(1, 701))
    assert len(tokens) > 4000 - 100
    lines = [
        GOLDEN.lines[0].model_copy(update={"line_no": n, "description": f"LINE-{n:02d}"})
        for n in range(1, 61)
    ]
    copy = GOLDEN.model_copy(
        update={"lines": lines, "standard_terms": tokens[:4000], "vendor_trading_name": "Café ☃ 鹿"}
    )
    body = text(render(copy, "po-v1"))  # a glyph the font lacks never raises
    assert all(f"LINE-{n:02d}" in body for n in range(1, 61))
    assert all(token in body for token in tokens[:4000].split()[:-1])


# -- integrity -------------------------------------------------------------------------------


def test_a_render_that_differs_from_the_issued_bytes_is_never_served(
    lifecycle_db, monkeypatch, caplog
):
    db, _ = lifecycle_db
    po_id, _ = issued(db)
    monkeypatch.setattr(pdf, "render", lambda copy, version: b"%PDF-other")
    with caplog.at_level(logging.ERROR), pytest.raises(ProblemError) as raised:
        read(db, po_id)  # plant 3: without the hash comparison these bytes are served
    assert raised.value.code == ErrorCode.INTERNAL_ERROR
    assert "po_pdf_hash_mismatch" in caplog.text


def test_po_version_is_append_only(lifecycle_db):
    db, _ = lifecycle_db
    po_id, _ = issued(db)
    for statement in (
        "UPDATE po_version SET template_version = 'po-v9'",
        "DELETE FROM po_version",
        "TRUNCATE po_version",
    ):  # plant 5: without the row trigger the UPDATE and DELETE succeed
        with (
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
            tenant_transaction(db[0], Scope(db[1])),
        ):
            db[0].execute(statement)
    assert len(stored(db)) == 1 and po_id


def test_a_row_is_frozen_once_by_issue_replays_settles_and_applies(lifecycle_db):
    db, _ = lifecycle_db
    po_id, _ = issued(db)
    run(db, type("P", (), {"id": po_id}), "1")  # an issue replay with the pre-issue If-Match
    with pytest.raises(psycopg.errors.UniqueViolation):
        with tenant_transaction(db[0], Scope(db[1])):
            freeze(db[0], Scope(db[1]), po_id)
    publish_change(db)
    po = pos(db).get(po_id)
    change = submit(db, open_change(db, po, lines=[line(unit_price="77.5")]))
    decide_change(db, change)
    context = OutcomeContext(db[3], None, None, lambda permission, target: True)
    changes(db).settle(change.id, Outcome.APPROVED, context)  # a retried decision
    done = changes(db).get(po_id, 1)
    changes(db).apply(po_id, 1, f'"{done.version - 1}"')  # a retried apply
    assert (
        sql(db, "SELECT count(*) FROM po_version")
        == sql(db, "SELECT count(*) FROM purchase_order WHERE issued_at IS NOT NULL")
        == [(2,)]
    )


def test_a_render_failure_rolls_the_whole_issue_back(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    ledger = sql(db, "SELECT count(*) FROM ledger_entry")
    qty = sql(db, "SELECT issued_qty FROM requisition_line")
    version = sql(db, "SELECT version FROM purchase_order")
    monkeypatch.setattr(pdf, "render", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        run(db, po, str(version[0][0]))
    db[0].rollback()
    assert sql(db, "SELECT status, version FROM purchase_order") == [("approved", version[0][0])]
    assert sql(db, "SELECT count(*) FROM po_version") == [(0,)]
    assert sql(db, "SELECT count(*) FROM ledger_entry") == ledger
    assert sql(db, "SELECT issued_qty FROM requisition_line") == qty


# -- routes ----------------------------------------------------------------------------------


BASE = "/api/v1/purchase-orders"


def test_the_pdf_route_serves_the_issued_bytes_with_safe_headers(lifecycle_db):
    db, _ = lifecycle_db
    po_id, _ = issued(db)
    number = pos(db).get(po_id).number

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            got = await browser.get(f"{BASE}/{po_id}/pdf")
            assert got.status_code == 200, got.text
            assert got.headers["content-type"] == "application/pdf"
            assert got.headers["content-disposition"] == f"inline; filename*=UTF-8''{number}.pdf"
            assert got.headers["x-content-type-options"] == "nosniff"
            assert got.headers["cache-control"] == "private, no-store"
            assert hashlib.sha256(got.content).hexdigest() == stored(db)[0][2]

    asyncio.run(scenario())


def test_a_draft_has_a_live_vendor_copy_and_no_pdf_and_a_cancelled_one_neither(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert (await browser.get(f"{BASE}/{po.id}/pdf")).status_code == 404
            live = await browser.get(f"{BASE}/{po.id}/vendor-copy")
            assert live.status_code == 200 and live.json()["issued"] is False
            assert live.json()["vendor_copy"]["number"] == po.number
            sql(db, "UPDATE purchase_order SET status='cancelled' RETURNING 1")
            assert (await browser.get(f"{BASE}/{po.id}/vendor-copy")).status_code == 404

    asyncio.run(scenario())


def test_vendor_copy_routes_conceal_foreign_tenants(lifecycle_db):
    from tests.requisitions.test_header import unit

    db, _ = lifecycle_db
    po_id, _ = issued(db)
    vendor, requisition = sql(db, "SELECT vendor_id, requisition_id FROM purchase_order")[0]
    foreign_unit, foreign_rfq, foreign_award, foreign_po = (
        unit(db, "FBU", org=db[2]),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    with tenant_transaction(db[0], Scope(db[2])):  # real rows, owned by the other tenant
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign_rfq, db[2], foreign_unit.id, db[3]),
        )
        db[0].execute(
            "INSERT INTO award (id,org_id,number,rfq_id,basis,status,rates,created_by) "
            "VALUES (%s,%s,'AWD-X',%s,'manual','approved','{}',%s)",
            (foreign_award, db[2], foreign_rfq, db[3]),
        )
        db[0].execute(
            "INSERT INTO purchase_order (id,org_id,number,unit_id,vendor_id,requisition_id,"
            "vendor_snapshot,award_id,currency,rate,bill_to,recipients,requester_id,created_by,"
            "requisition_version) "
            "VALUES (%s,%s,'PO-X',%s,%s,%s,'{}',%s,'USD','{}','{}','[]',%s,%s,1)",
            (foreign_po, db[2], foreign_unit.id, vendor, requisition, foreign_award, db[3], db[3]),
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for id in (foreign_po, uuid4()):
                for suffix in ("pdf", "vendor-copy"):
                    assert (await send(browser, "GET", f"/{id}/{suffix}")).status_code == 404
            regrant(db, ["rfq.view"])  # a grant in scope, but not the permission
            for suffix in ("pdf", "vendor-copy"):
                assert (await send(browser, "GET", f"/{po_id}/{suffix}")).status_code == 403
            regrant(db, ["purchase_order.view"])
            for suffix in ("pdf", "vendor-copy"):
                assert (await send(browser, "GET", f"/{po_id}/{suffix}")).status_code == 200

    asyncio.run(scenario())
