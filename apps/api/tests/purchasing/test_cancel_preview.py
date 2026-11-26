"""The read behind the cancel dialog (E13-S19, PO-013): the figure is the one `cancel` releases."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.purchasing.cancel import cancel_preview
from tests.isolation.test_real_sessions import client, login
from tests.purchasing.test_cancel import cancel_po, releases
from tests.purchasing.test_changes import (
    decide_change,
    entries,
    issued,
    line,
    open_change,
    publish_change,
    submit,
)
from tests.purchasing.test_issue import approve
from tests.purchasing.test_purchase_orders import create, pos, ready, send
from tests.purchasing.test_transmission import foreign_po
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import sql
from tests.vendors.test_vendors import regrant


def preview(db, po):
    return cancel_preview(db[0], Scope(db[1]), po.id, db[3])


def released_by_cancel(db, po):
    before = entries(db)
    cancel_po(db, po)
    return -sum((e[1] for e in releases(db, before)), Decimal(0))


def test_the_figure_is_what_the_cancel_then_releases_and_the_reservation_is_named(lifecycle_db):
    db, _ = lifecycle_db
    po, row = issued(db)
    [(main,)] = sql(db, "SELECT project_id FROM requisition")
    before = entries(db)
    seen = preview(db, po)
    assert entries(db) == before  # a read posts nothing
    assert seen.status == "issued" and seen.rereserved and seen.released_currency == "USD"
    assert seen.requisition_number == row.number
    assert seen.reservation_project is not None and seen.main_project.id == main
    assert Decimal(seen.released) == released_by_cancel(db, po) > 0


def test_without_a_live_reservation_nothing_is_re_reserved(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    sql(db, "UPDATE requisition SET reservation_entry_id=NULL RETURNING 1")
    seen = preview(db, po)
    assert not seen.rereserved and seen.reservation_project is None
    assert Decimal(seen.released) == released_by_cancel(db, po) > 0


def test_after_a_partial_release_only_the_remainder(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    publish_change(db)
    decide_change(db, submit(db, open_change(db, po, lines=[line(quantity="3")])))
    [(current,)] = sql(db, "SELECT id FROM purchase_order WHERE status='issued'")
    now = pos(db).get(current)
    assert Decimal(preview(db, now).released) == released_by_cancel(db, now)


def test_after_a_change_order_increase_both_commitment_entries_count(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    publish_change(db)
    decide_change(db, submit(db, open_change(db, po, lines=[line(quantity="5")])))
    [(current,)] = sql(db, "SELECT id FROM purchase_order WHERE status='issued'")
    assert len(sql(db, "SELECT 1 FROM ledger_entry WHERE entry_type='commitment'")) == 2
    now = pos(db).get(current)
    assert Decimal(preview(db, now).released) == released_by_cancel(db, now)


def test_before_issue_nothing_is_released_and_a_cancelled_po_is_refused(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "2")
    draft = preview(db, po)
    assert (draft.status, draft.released, draft.released_currency) == ("draft", "0.0000", None)
    assert not draft.rereserved and draft.reservation_project is None
    approve(db, po)
    assert preview(db, po).status == "approved"
    cancel_po(db, po)
    with pytest.raises(ProblemError) as error:
        preview(db, po)
    assert error.value.code == ErrorCode.CONFLICT


def test_cancel_preview_conceals_foreign_tenants_and_needs_amend(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "2")
    other = foreign_po(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for missing in (other, uuid4()):
                got = await send(browser, "GET", f"/{missing}/cancel-preview")
                assert got.status_code == 404
            regrant(db, ["purchase_order.view"])
            denied = await send(browser, "GET", f"/{po.id}/cancel-preview")
            assert denied.status_code == 403 and "released" not in denied.text
            regrant(db, ["purchase_order.view", "purchase_order.amend"])
            got = await send(browser, "GET", f"/{po.id}/cancel-preview")
            assert got.status_code == 200 and got.json()["released"] == "0.0000"

    asyncio.run(scenario())
