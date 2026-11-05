"""The reads behind the PO list and detail screens (E13-S12)."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget.service import get_balance
from flo.modules.identity.models import BASELINE_ROLE_PERMISSIONS, permission_code, role_code
from flo.modules.purchasing.cancel import cancel
from flo.modules.purchasing.models import PurchaseOrderRepository
from flo.modules.purchasing.schemas import PoCancelCreate
from flo.modules.purchasing.service import _po_links
from tests.isolation.test_real_sessions import client, login
from tests.purchasing.test_changes import decide_change, line, open_change, publish_change, submit
from tests.purchasing.test_issue import run
from tests.purchasing.test_purchase_orders import _all, create, pos, ready, send
from tests.purchasing.test_transmission import (
    Sender,
    contact,
    dispatch,
    foreign_po,
    issued_po,
    kill,
    new_po,
    resend,
)
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import sql
from tests.vendors.test_vendors import regrant

TABS = {"all", "draft", "awaiting", "ready", "issued", "attention"}


def listing(db, tab="all", size=50):
    return pos(db).list_orders(_all(db), tab, None, size)


def revise(db, po):
    """Apply a change order: the PO becomes `superseded`, revision 1 is issued. Returns its id."""
    publish_change(db)
    change = submit(db, open_change(db, pos(db).get(po.id), lines=[line(unit_price="77.5")]))
    decide_change(db, change)
    return sql(db, "SELECT id FROM purchase_order WHERE revision = 1")[0][0]


# -- B1, B2: tabs, counts, columns -----------------------------------------------------------


def test_the_six_tabs_and_counts_follow_status_and_the_latest_send(lifecycle_db):
    db, _ = lifecycle_db
    po = new_po(db)  # approved
    page = listing(db)
    assert set(page.counts) == TABS  # plant: return only `all` and `draft` and this fails
    assert (page.counts["all"], page.counts["ready"], page.counts["issued"]) == (1, 1, 0)
    assert [r.id for r in listing(db, "ready").rows] == [po.id]
    assert listing(db, "draft").rows == [] and listing(db, "awaiting").rows == []

    run(db, po)
    assert listing(db).counts["issued"] == 1 and listing(db).counts["ready"] == 0
    [clean] = listing(db, "issued").rows
    assert clean.sent.result == "queued" and listing(db, "attention").rows == []

    kill(db)  # the outbox row is dead: derived `failed`, not the null `result` column
    page = listing(db)
    assert (page.counts["issued"], page.counts["attention"], page.counts["all"]) == (0, 1, 1)
    assert listing(db, "issued").rows == []
    [row] = listing(db, "attention").rows
    assert row.id == po.id and row.sent.result == "failed"
    assert (row.revision, row.project_count, len(row.projects)) == (0, 1, 1)
    assert row.projects[0].code and row.projects[0].name


def test_a_multi_recipient_attempt_reports_its_worst_row(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    dispatch(db, Sender())
    assert listing(db).rows[0].sent.result == "sent"
    first, second = contact(db, "A", "a@example.com"), contact(db, "B", "b@example.com")
    from flo.kernel.ports.email import PermanentEmailError

    resend(db, po, [first.id, second.id])
    dispatch(db, Sender(None, PermanentEmailError("no")))  # one delivered, one dead
    [row] = listing(db, "attention").rows
    assert row.sent.result == "failed"  # plant: report the best row and the tab disagrees
    assert listing(db).counts["attention"] == 1


def test_the_list_runs_a_fixed_number_of_queries(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    create(db, rfq, a, award_id, "4")
    create(db, rfq, a, award_id, "3")
    calls = []
    real = PurchaseOrderRepository.execute

    def counting(self, query, params=None):
        calls.append(query)
        return real(self, query, params)

    monkeypatch.setattr(PurchaseOrderRepository, "execute", counting)
    assert len(listing(db, "all", 1).rows) == 1
    one = len(calls)
    calls.clear()
    assert len(listing(db, "all", 2).rows) == 2
    assert len(calls) == one  # plant: a query per row makes this fail


def test_the_search_runs_on_the_server_across_pages(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    first = create(db, rfq, a, award_id, "4")
    second = create(db, rfq, a, award_id, "3")
    page = pos(db).list_orders(_all(db), "all", None, 1)  # the older PO is on page 2
    assert page.next_cursor and page.rows[0].id == second.id
    found = pos(db).list_orders(_all(db), "all", None, 1, q=first.number.lower())
    assert [r.id for r in found.rows] == [
        first.id
    ]  # plant: filter the page client-side and this fails
    code = found.rows[0].projects[0].code
    assert [r.id for r in pos(db).list_orders(_all(db), "all", None, 5, q=code).rows] == [
        second.id,
        first.id,
    ]
    assert pos(db).list_orders(_all(db), "all", None, 5, q="no-such-%").rows == []


# -- B3, B4: versions and history --------------------------------------------------------------


def test_versions_walk_the_chain_both_ways_and_history_lists_the_life(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    new_id = revise(db, po)
    for start in (po.id, new_id):
        versions = pos(db).versions(start)
        assert [(v.revision, v.status, v.is_current) for v in versions] == [
            (0, "superseded", False),
            (1, "issued", True),
        ]
        assert versions[0].issued_at is not None
    assert pos(db).versions(new_id)[1].total == pos(db).get(new_id).total
    history = pos(db).history(new_id)
    assert not history.truncated
    assert "purchase_order.issue" in [e.action for e in history.entries]
    assert [e.occurred_at for e in history.entries] == sorted(
        e.occurred_at for e in history.entries
    )


# -- B5: budget summary ------------------------------------------------------------------------


def test_budget_summary_counts_this_po_once_in_every_state(lifecycle_db):
    db, _ = lifecycle_db
    po = new_po(db)  # approved
    [project] = {line.project_id for line in pos(db).get(po.id).lines}
    scope = Scope(db[1])
    [card] = pos(db).budget_summary(po.id).projects
    balance = get_balance(db[0], scope, project)
    assert card.this_po_state == "reserved" and card.project_code
    assert Decimal(card.available_after_issue) == balance.available  # no second subtraction

    run(db, po)
    [card] = pos(db).budget_summary(po.id).projects
    balance = get_balance(db[0], scope, project)
    assert card.this_po_state == "committed" and Decimal(card.this_po) > 0
    assert Decimal(card.available_after_issue) == balance.available
    assert Decimal(card.committed) + Decimal(card.this_po) == balance.committed  # excludes itself
    assert Decimal(card.actual) == balance.actual

    with correlation_context("cancel"):
        cancel(
            db[0], scope, po.id, db[3], f'"{pos(db).get(po.id).version}"',
            PoCancelCreate(reason="not needed", notify_vendor=False),
        )  # fmt: skip
    assert pos(db).budget_summary(po.id).projects[0].this_po_state == "released"


def test_budget_summary_of_a_superseded_revision_is_409_with_the_current_one(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    new_id = revise(db, po)
    with pytest.raises(ProblemError) as caught:  # plant: return figures here and this fails
        pos(db).budget_summary(po.id)
    assert caught.value.checks == {"problem": "superseded", "current_id": str(new_id)}
    assert pos(db).budget_summary(new_id).projects


# -- B6: live vendor status and permitted actions ----------------------------------------------


def test_actions_follow_status_permissions_and_who_drafted(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "2")  # a draft
    read = pos(db).get(po.id)
    buyer = {"purchase_order.create", "purchase_order.amend"}
    assert pos(db).with_access(read, buyer, True).actions == [
        "submit", "edit", "delete", "cancel",
    ]  # fmt: skip
    assert pos(db).with_access(read, buyer, True).vendor_status is not None
    assert pos(db).with_access(read, buyer, False).vendor_status is None
    # submit is the drafting buyer's alone
    other = pos(db, actor=uuid4()).with_access(read, {"purchase_order.create"}, False)
    assert other.actions == ["edit", "delete"]
    assert pos(db).with_access(read, set(), False).actions == []


def test_a_po_link_opens_the_purchase_order_route(lifecycle_db):
    db, _ = lifecycle_db
    po = new_po(db)
    requisition = pos(db).get(po.id).requisition_id
    with tenant_transaction(db[0], Scope(db[1])):
        [node] = _po_links(db[0], Scope(db[1]), requisition, None)
    assert node.href == f"/purchase-orders/{po.id}"


def test_purchasing_user_reads_vendors_and_the_ledger_but_writes_neither():
    held = BASELINE_ROLE_PERMISSIONS[role_code("purchasing-user")]
    assert {permission_code("vendor.view"), permission_code("ledger.read")} <= held
    assert not held & {permission_code("vendor.edit"), permission_code("budget.allocate")}


# -- routes ------------------------------------------------------------------------------------


def test_po_read_routes_conceal_foreign_tenants_and_check_permissions(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    other = foreign_po(db)
    tails = ("", "/versions", "/history", "/budget-summary")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for missing in (other, uuid4()):
                for tail in tails:
                    assert (await send(browser, "GET", f"/{missing}{tail}")).status_code == 404
            regrant(db, ["rfq.view"])
            for tail in tails:
                assert (await send(browser, "GET", f"/{po.id}{tail}")).status_code == 403
            regrant(db, ["purchase_order.view"])  # no ledger.read: all or nothing
            assert (await send(browser, "GET", f"/{po.id}/versions")).status_code == 200
            assert (await send(browser, "GET", f"/{po.id}/history")).status_code == 200
            denied = await send(browser, "GET", f"/{po.id}/budget-summary")
            assert denied.status_code == 403 and "committed" not in denied.text
            regrant(db, ["purchase_order.view", "ledger.read"])
            got = await send(browser, "GET", f"/{po.id}/budget-summary")
            assert got.status_code == 200 and got.json()["projects"]

    asyncio.run(scenario())


def test_the_detail_route_offers_only_what_the_caller_may_run(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(
                db,
                ["purchase_order.view", "purchase_order.amend", "purchase_order.close",
                 "vendor.view"],
            )  # fmt: skip
            got = (await send(browser, "GET", f"/{po.id}")).json()
            assert got["actions"] == ["change_order", "reduce", "cancel", "close"]
            assert got["vendor_status"] is not None
            regrant(db, ["purchase_order.view"])  # plant: ignore permissions and this fails
            got = (await send(browser, "GET", f"/{po.id}")).json()
            assert got["actions"] == [] and got["vendor_status"] is None

    asyncio.run(scenario())
