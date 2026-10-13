"""Change sets: words for every row, deltas on money, funding that matches the next submit."""

import asyncio
from uuid import uuid4

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import Outcome
from flo.modules.requisitions import revisions
from flo.modules.requisitions.schemas import LineCreate, LinesReplace, RequisitionPatch
from flo.modules.requisitions.service import RequisitionService
from tests.requisitions.test_approval_effects import linked
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import client, login, org_svc, project, send, unit
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lifecycle import command, edit_justification, outcome, sql
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lines import BODY, service
from tests.requisitions.test_lines import lines_db as lines_db
from tests.requisitions.test_submission_check import change, complete


def changes(db, id, against=None):
    return revisions.change_set(db[0], Scope(db[1]), id, against, lambda project_id: True)


def reprice(db, row, price, quantity="1"):
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    line = service(db).list_lines(row.id).rows[0]
    service(db).write(
        row.id,
        str(current.version),
        line_id=line.id,
        body=LineCreate(**(BODY | {"quantity": quantity, "unit_price": price})),
    )


def test_money_rows_carry_a_delta_and_every_row_a_word_label(lifecycle_db):
    db, _ = lifecycle_db
    row, _ = complete(db, price="175000")
    command(db, row)
    outcome(db, row, Outcome.RETURNED)
    reprice(db, row, "171500")
    edit_justification_text = RequisitionPatch(justification="Cheaper supplier")
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    RequisitionService(db[0], Scope(db[1]), db[3]).update(
        row.id, edit_justification_text, str(current.version), lambda p: True
    )
    found = changes(db, row.id)
    assert found.from_revision == 1 and found.to == "draft" and not found.nothing_changed
    assert found.funding is None  # a standalone request has nothing reserved yet
    by_field = {r.field: r for r in found.rows}
    estimate = by_field["estimate"]
    assert (estimate.label, estimate.was, estimate.now) == ("Estimate", "175000.00", "171500.00")
    assert (estimate.delta.amount, estimate.delta.percent, estimate.delta.direction) == (
        "3500.00",
        "2.0",
        "down",
    )
    assert (
        by_field["justification"].label == "Justification"
        and by_field["justification"].delta is None
    )
    labels = [r.label for r in found.rows]
    assert "Line 1 unit price" in labels and "Line 1 amount" in labels
    price = next(r for r in found.rows if r.label == "Line 1 unit price")
    assert price.delta.direction == "down" and price.delta.amount == "3500.00"
    assert all(r.label and r.label[0].isupper() and "_" not in r.label for r in found.rows)
    assert all(r.delta is None for r in found.rows if r.field in {"justification", "title"})


def test_added_and_removed_lines_are_named_by_number(lifecycle_db):
    db, _ = lifecycle_db
    row, _ = complete(db, price="60")
    command(db, row)
    outcome(db, row, Outcome.RETURNED)
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    first = service(db).list_lines(row.id).rows[0]
    service(db).write(
        row.id,
        str(current.version),
        replacement=LinesReplace(lines=[BODY | {"description": "Valve"}]),
    )
    found = changes(db, row.id)
    assert {(r.label, r.change) for r in found.rows if r.field.startswith("lines/")} == {
        ("Line 1", "removed"),
        ("Line 2", "added"),
    }
    assert first.id  # the original line is the removed one
    assert found.material and found.routing == "restarts"


def test_funding_outcomes_match_what_the_next_submit_does(lifecycle_db):
    db, _ = lifecycle_db
    p = project(db, unit(db, uuid4().hex))
    row = linked(db, p, price="60", available="200")
    outcome(db, row, Outcome.RETURNED)
    edit = lambda text: edit_justification(db, row, text)  # noqa: E731
    edit("Text only")
    found = changes(db, row.id)
    assert (found.funding.reserved_now, found.funding.reserved_after, found.funding.outcome) == (
        "60.00",
        "60.00",
        "unchanged",
    )
    assert found.funding.project_available_after == "140.00" and found.funding.currency == "USD"
    reprice(db, row, "100")
    found = changes(db, row.id)
    assert found.funding.outcome == "reserved_more" and found.funding.reserved_after == "100.00"
    assert found.funding.project_available_after == "100.00"
    reprice(db, row, "40")
    found = changes(db, row.id)
    assert found.funding.outcome == "released_once" and found.funding.reserved_after == "40.00"
    assert found.funding.project_available_after == "160.00"
    hidden = revisions.change_set(db[0], Scope(db[1]), row.id, None, lambda project_id: False)
    assert hidden.funding.project_available_after is None
    # A pre-approval project change is a move (E09-S05 amendment 4).
    target = project(db, org_svc(db).get_unit(row.bu_id))
    change(db, "project", target.id, "status='active'")
    sql(
        db, "UPDATE project_balance SET allocated=500 WHERE project_id=%s RETURNING 1", (target.id,)
    )
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    RequisitionService(db[0], Scope(db[1]), db[3]).update(
        row.id, RequisitionPatch(project_id=target.id), str(current.version), lambda p: True
    )
    found = changes(db, row.id)
    assert found.funding.outcome == "moved" and found.funding.reserved_after == "40.00"
    assert found.funding.project_available_after == "460.00"
    assert any(r.field == "project_id" and r.label == "Project" for r in found.rows)


def test_http_changes_revisions_and_unknown_revision(lifecycle_db):
    db, _ = lifecycle_db
    row, _ = complete(db, price="60")
    command(db, row, note="First go")
    outcome(db, row, Outcome.RETURNED)
    reprice(db, row, "80")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            default = await send(browser, "GET", f"/{row.id}/changes")
            assert default.status_code == 200
            explicit = await send(browser, "GET", f"/{row.id}/changes?against=1")
            assert explicit.json() == default.json()
            body = default.json()
            assert body["from_revision"] == 1 and body["routing"] == "restarts"
            assert body["material"] is True
            assert (await send(browser, "GET", f"/{row.id}/changes?against=9")).status_code == 404
            listed = await send(browser, "GET", f"/{row.id}/revisions")
            assert [
                (r["revision_no"], r["kind"], r["status"], r["note"]) for r in listed.json()
            ] == [(1, "submission", "applied", "First go")]

    asyncio.run(scenario())


def test_every_row_names_its_value_type(lifecycle_db):
    db, _ = lifecycle_db
    row, _ = complete(db, price="60")
    command(db, row)
    outcome(db, row, Outcome.RETURNED)
    reprice(db, row, "80", quantity="3")
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    RequisitionService(db[0], Scope(db[1]), db[3]).update(
        row.id,
        RequisitionPatch(justification="Other", needed_by="2031-02-03"),
        str(current.version),
        lambda p: True,
    )
    kinds = {r.field.rsplit("/", 1)[-1]: r.value_type for r in changes(db, row.id).rows}
    assert kinds["quantity"] == "quantity"  # a quantity never reads as money
    assert kinds["unit_price"] == kinds["amount"] == kinds["estimate"] == "money"
    assert kinds["justification"] == "text" and kinds["needed_by"] == "date"
