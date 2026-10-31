"""Issue converts the reservation into a commitment, once (E13-S05)."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.budget import reconcile
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from flo.modules.purchasing.issue import issue
from flo.modules.requisitions.schemas import SubmissionInvalid
from tests.isolation.test_real_sessions import client, login
from tests.purchasing.test_po_approval import decide_po, one, submit
from tests.purchasing.test_pricing import taxed
from tests.purchasing.test_purchase_orders import create, pos, send
from tests.purchasing.test_purchase_orders import ready as ready
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import refused, sql
from tests.vendors.test_vendors import regrant


def approve(db, po):
    sql(db, "UPDATE purchase_order SET status='approved' WHERE id=%s RETURNING 1", (po.id,))


def run(db, po, version="1", connection=None):
    with correlation_context("issue"):
        return issue(connection or db[0], Scope(db[1]), po.id, db[3], f'"{version}"')


def ledger(db):
    return sql(
        db,
        "SELECT entry_type, bucket, amount, project_id, ledger_account_code, source_type "
        "FROM ledger_entry ORDER BY id",
    )


def totals(db):
    return sql(db, "SELECT sum(reserved), sum(committed) FROM project_balance")[0]


def split(db, po, qty_each="2"):
    """Two lines of one requisition line on one sub-project, on different ledger accounts."""
    sql(
        db,
        "INSERT INTO po_line SELECT org_id, po_id, 2, award_id, rfq_line_id, vendor_id, "
        "requisition_id, requisition_line_id, description, uom_code, quantity, unit_price, "
        "project_id, department_code, ledger_account_code, price, tax, freight, line_total, "
        "budget_amount, budget_amount_project FROM po_line RETURNING 1",
    )
    sql(
        db,
        "UPDATE po_line SET quantity=%s, price=price/2, line_total=line_total/2, "
        "budget_amount=budget_amount/2, budget_amount_project=budget_amount_project/2 "
        "RETURNING 1",
        (qty_each,),
    )
    sql(db, "UPDATE po_line SET ledger_account_code='OTHER' WHERE line_no=2 RETURNING 1")


def test_issue_converts_the_reservation_into_commitments_and_drift_is_empty(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    split(db, po)
    approve(db, po)
    reserved, committed = totals(db)
    entries = len(ledger(db))
    issued = run(db, po)
    assert issued.status == "issued" and issued.version == 2
    new = ledger(db)[entries:]
    commitments = [e for e in new if e[0] == "commitment"]
    assert sorted(e[2] for e in commitments) == [Decimal("180.0000"), Decimal("180.0000")]
    assert {e[4] for e in commitments} == {issued.lines[0].ledger_account_code, "OTHER"}
    # D-M2-26: both commit on the sub-project that holds the reservation, never the main project
    sub = sql(db, "SELECT project_id FROM po_line LIMIT 1")[0][0]
    assert {e[3] for e in commitments} == {sub} and all(
        e[5] == "purchase_order" for e in commitments
    )
    after_reserved, after_committed = totals(db)
    assert reserved - after_reserved == after_committed - committed == Decimal("360.0000")
    assert sql(db, "SELECT issued_qty FROM requisition_line") == [(Decimal("4.0000"),)]
    assert sql(db, "SELECT issued_at IS NOT NULL, issued_by FROM purchase_order") == [(True, db[3])]
    assert sql(db, "SELECT count(*) FROM audit_log WHERE action='purchase_order.issue'") == [(1,)]
    reconcile.reconcile_org(db[0], Scope(db[1]))
    status = reconcile.reconciliation_status(db[0], Scope(db[1]))
    assert status.status == "ok" and status.drift_rows == 0


def test_a_retry_with_the_same_or_a_new_key_never_commits_twice(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    first = run(db, po)
    rows = ledger(db)
    again = run(db, po)  # the same If-Match after the issue: the same result
    assert again.model_dump() == first.model_dump()
    assert ledger(db) == rows  # plant 2: dropping the commit's key posts a second commitment
    assert sql(db, "SELECT issued_qty FROM requisition_line") == [(Decimal("4.0000"),)]
    refused(lambda: run(db, po, "7"))  # a stale version beyond the issue is a 409


def test_approval_alone_posts_nothing_and_issue_needs_an_approved_order(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db, "4")
    rows = ledger(db)
    error = refused(lambda: run(db, po))
    assert error.checks["problem"] == "invalid_transition"
    submit(db, po)
    decide_po(db, po)
    assert ledger(db) == rows  # approval posts nothing (E13-S04 regression)
    assert run(db, po, "3").status == "issued"
    assert len(ledger(db)) > len(rows)


def test_the_budget_amounts_freeze_at_issue(lifecycle_db):
    db, _ = lifecycle_db
    rfq, a, award, unit_id = taxed(db)
    po = create(db, rfq, a, award.id, "10")
    approve(db, po)
    org = OrgService(db[0], Scope(db[1]), db[3])
    org.set_setting("tax.consumes_budget", SettingPut(value=False, unit_id=unit_id))  # after draft
    issued = run(db, po)
    assert issued.lines[0].budget_amount_project == "820.00"
    assert [e[2] for e in ledger(db) if e[0] == "commitment"] == [Decimal("820.0000")]
    org.set_setting("tax.consumes_budget", SettingPut(value=True, unit_id=unit_id))
    assert pos(db).get(po.id).lines[0].budget_amount_project == "820.00"


def test_over_the_reservation_needs_availability_and_nothing_is_written(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    sub = sql(db, "SELECT project_id FROM po_line")[0][0]
    held = sql(db, "SELECT remaining FROM reservation_remaining WHERE remaining > 0")[0][0]
    # one cent more than the reservation holds, with the project's availability at zero
    sql(
        db,
        "UPDATE po_line SET budget_amount_project = %s, price = %s, line_total = %s RETURNING 1",
        (held + Decimal("0.01"),) * 3,
    )
    sql(db, "UPDATE project_balance SET allocated = allocated - available WHERE project_id=%s "
        "RETURNING 1", (sub,))  # fmt: skip
    before = (ledger(db), totals(db))
    error = refused(lambda: run(db, po))
    assert error.checks["problem"] == "over_reservation"
    assert error.checks["excess"] == "0.0100" or error.checks["excess"] == "0.01"
    assert (ledger(db), totals(db)) == before
    assert sql(db, "SELECT status FROM purchase_order") == [("approved",)]
    sql(db, "UPDATE project_balance SET allocated = allocated + 1 WHERE project_id=%s RETURNING 1",
        (sub,))  # fmt: skip
    assert run(db, po).status == "issued"


def test_a_group_with_no_value_or_a_null_code_refuses_and_writes_nothing(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    before = (ledger(db), totals(db))
    sql(db, "UPDATE po_line SET ledger_account_code=NULL RETURNING 1")
    error = refused(lambda: run(db, po))
    assert error.checks == {"problem": "missing_accounting_code", "line_no": "1"}
    sql(db, "UPDATE po_line SET ledger_account_code='L', price=0, tax=0, freight=0 RETURNING 1")
    assert refused(lambda: run(db, po)).checks["problem"] == "zero_value_line"
    assert (ledger(db), totals(db)) == before
    assert sql(db, "SELECT status, version FROM purchase_order") == [("approved", 1)]


@pytest.mark.parametrize("code", ["department_code", "ledger_account_code"])
def test_submit_refuses_a_requisition_with_a_null_header_code(lifecycle_db, code):
    from tests.requisitions.test_header import unit
    from tests.requisitions.test_lifecycle import command
    from tests.requisitions.test_lifecycle import project as make_project
    from tests.requisitions.test_submission_check import change, complete

    db, _ = lifecycle_db
    row, _ = complete(db, make_project(db, unit(db, uuid4().hex)))
    change(db, "requisition", row.id, f"{code}=NULL")
    rows = ledger(db)
    with pytest.raises(SubmissionInvalid):
        command(db, row)
    assert sql(
        db, "SELECT status, reservation_entry_id FROM requisition WHERE id=%s", (row.id,)
    ) == [("draft", None)]
    assert ledger(db) == rows  # nothing reserved, so the reserve path never sees a null code


def test_requisition_completes_after_its_last_line_is_issued_and_not_before(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, a, _, award_id, _ = ready(db, "10", "10")
    first = create(db, rfq, a, award_id, "4")
    second = create(db, rfq, a, award_id, "6")
    for po in (first, second):
        approve(db, po)
    run(db, first)
    status = "SELECT status FROM requisition WHERE id=%s"
    assert sql(db, status, (row.id,)) != [("completed",)]
    run(db, second)
    assert sql(db, status, (row.id,)) == [("completed",)]
    assert sql(db, "SELECT count(*) FROM requisition_status_event WHERE to_status='completed'") == [
        (1,)
    ]


def test_an_expired_code_or_stale_requisition_is_a_409_and_writes_nothing(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    before = (ledger(db), totals(db))
    sql(db, "UPDATE requisition SET version = version + 1 WHERE id=%s RETURNING 1", (row.id,))
    assert refused(lambda: run(db, po)).checks["problem"] == "stale_version"
    assert (ledger(db), totals(db)) == before
    assert sql(db, "SELECT status FROM purchase_order") == [("approved",)]


def test_the_issue_route_authorizes_and_replays(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    key = {"Idempotency-Key": "issue-once"}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["purchase_order.view"])
            assert (
                await send(browser, "POST", f"/{po.id}/issue", None, {"If-Match": '"1"'})
            ).status_code == 403
            regrant(db, ["purchase_order.view", "purchase_order.issue"])
            assert (await send(browser, "POST", f"/{po.id}/issue")).status_code == 400
            headers = key | {"If-Match": '"1"'}
            first = await send(browser, "POST", f"/{po.id}/issue", None, headers)
            assert first.status_code == 200 and first.json()["status"] == "issued"
            assert first.headers["ETag"] == '"2"'
            replay = await send(browser, "POST", f"/{po.id}/issue", None, headers)
            assert replay.status_code == 200 and replay.json() == first.json()

    asyncio.run(scenario())
    assert len([e for e in ledger(db) if e[0] == "commitment"]) == 1


def test_each_group_commits_under_its_own_stable_ledger_key(lifecycle_db, monkeypatch):
    from flo.modules.purchasing import issue as issue_module

    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    split(db, po)
    approve(db, po)
    seen = []
    real = issue_module.commit

    def spy(*args, **kwargs):
        seen.append(kwargs["idempotency_key"])
        return real(*args, **kwargs)

    monkeypatch.setattr(issue_module, "commit", spy)
    run(db, po)
    assert len(seen) == len(set(seen)) == 2 and all(seen)  # plant: an empty key fails here
    assert all(k.startswith(f"po:{po.id}:v1:commit:") for k in seen)
    assert sorted(k.rsplit("|", 1)[1] for k in seen) == sorted(
        r[0] for r in sql(db, "SELECT ledger_account_code FROM po_line")
    )  # the key names the group
    keys = [
        r[0]
        for r in sql(db, "SELECT idempotency_key FROM ledger_entry WHERE entry_type='commitment'")
    ]
    assert sorted(keys) == sorted(seen)


def test_route_retries_with_the_same_and_a_new_key_commit_once(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    first_key, other_key = ({"Idempotency-Key": k, "If-Match": '"1"'} for k in ("k-1", "k-2"))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            first = await send(browser, "POST", f"/{po.id}/issue", None, first_key)
            same = await send(browser, "POST", f"/{po.id}/issue", None, first_key)
            new = await send(browser, "POST", f"/{po.id}/issue", None, other_key)
            assert (first.status_code, same.status_code, new.status_code) == (200, 200, 200)
            assert same.json() == first.json() == new.json()

    asyncio.run(scenario())
    assert len([e for e in ledger(db) if e[0] == "commitment"]) == 1
    assert sql(db, "SELECT issued_qty FROM requisition_line") == [(Decimal("4.0000"),)]


def test_issue_lines_refuses_more_than_was_ordered_and_writes_nothing(lifecycle_db):
    from flo.modules.requisitions.service import issue_lines

    db, _ = lifecycle_db
    rfq, _, a, _, award_id, _ = ready(db)
    create(db, rfq, a, award_id, "4")  # ordered_qty = 4
    [(line_id,)] = sql(db, "SELECT id FROM requisition_line")
    audits = sql(db, "SELECT count(*) FROM audit_log WHERE action='requisition.issue.update'")
    rows = ledger(db)
    issue_lines(db[0], Scope(db[1]), [(line_id, Decimal(3))], db[3])
    error = refused(lambda: issue_lines(db[0], Scope(db[1]), [(line_id, Decimal(2))], db[3]))
    assert error.checks["problem"] == "over_issue"
    assert sql(db, "SELECT issued_qty FROM requisition_line") == [(Decimal("3.0000"),)]
    assert sql(db, "SELECT count(*) FROM audit_log WHERE action='requisition.issue.update'") == [
        (audits[0][0] + 1,)
    ]  # only the first call audited
    assert ledger(db) == rows
