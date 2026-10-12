"""Catalogue events, scope and add-from-catalogue through the production app."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.requisitions.catalogue import CatalogueService, record_event, record_requested
from flo.modules.requisitions.lines import LineService
from flo.modules.requisitions.schemas import LineCreate
from tests.requisitions.test_header import (  # noqa: F401  (fixtures)
    bootstrap_db,
    client,
    draft,
    empty_database,
    header_db,
    headers,
    login,
    send,
    unit,
)
from tests.requisitions.test_lines import BODY  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db


def record(db, bu, description="Proximity sensor", event="requested", source=None, **extra):
    values = dict(
        bu_id=bu.id,
        line_type="item",
        description=description,
        uom_code="EA",
        category_code=None,
        event=event,
        source_type="requisition_line",
        source_id=source or uuid4(),
        quantity="2",
        unit_price="19.9900",
        currency="USD",
    )
    return record_event(db[0], Scope(db[1]), **(values | extra))


def tree(db):
    a = unit(db, "A")
    a1 = unit(db, "A1", parent=a.id)
    b = unit(db, "B")
    return a, a1, b


def count(db, table):
    with tenant_transaction(db[0], Scope(db[1])):
        return db[0].execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_recording_the_same_source_twice_records_one_event(lines_db):
    db = lines_db
    a, _, _ = tree(db)
    source = uuid4()
    first = record(db, a, source=source)
    second = record(db, a, source=source)
    assert first == second
    assert count(db, "catalogue_event") == 1
    with tenant_transaction(db[0], Scope(db[1])):
        assert db[0].execute("SELECT times_requested FROM catalogue_item").fetchone() == (1,)
    # A new source for the same item is a second event and increments once.
    record(db, a, description="  PROXIMITY   sensor ", source=uuid4())
    assert count(db, "catalogue_item") == 1 and count(db, "catalogue_event") == 2
    with tenant_transaction(db[0], Scope(db[1])):
        assert db[0].execute("SELECT times_requested FROM catalogue_item").fetchone() == (2,)


def test_scope_is_the_unit_and_its_ancestors_only(lines_db):
    db = lines_db
    a, a1, b = tree(db)
    in_a, in_a1, in_b = (
        record(db, a, "Item in A"),
        record(db, a1, "Item in A1"),
        record(db, b, "Item in B"),
    )
    service = CatalogueService(db[0], Scope(db[1]))
    from_a1 = {row.id for row in service.search(a1.id, "item", None, 20).rows}
    assert from_a1 == {in_a, in_a1}  # own and ancestor, never a sibling
    from_a = {row.id for row in service.search(a.id, "item", None, 20).rows}
    assert from_a == {in_a}  # never a descendant's record
    assert {row.id for row in service.search(b.id, "item", None, 20).rows} == {in_b}


def test_search_is_case_and_whitespace_insensitive_and_bounded(lines_db):
    db = lines_db
    a, _, _ = tree(db)
    record(db, a)
    service = CatalogueService(db[0], Scope(db[1]))
    rows = service.search(a.id, "  Proximity   SENSOR", None, 4).rows
    assert [row.description for row in rows] == ["Proximity sensor"]
    assert rows[0].last_unit_price == "19.9900" and rows[0].last_vendor_name is None
    assert service.search(a.id, "sensor", "service", 4).rows == []

    for bad in ("p", " p ", "x" * 101):
        with pytest.raises(ProblemError) as error:
            service.search(a.id, bad, None, 4)
        assert error.value.code == ErrorCode.VALIDATION_FAILED
    with pytest.raises(ProblemError):
        service.search(a.id, "sensor", None, 21)


def test_http_search_and_foreign_unit_is_404(lines_db):
    db = lines_db
    a, _, _ = tree(db)
    record(db, a)
    foreign = unit(db, "F", org=db[2])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            ok = await browser.get("/api/v1/catalogue", params={"bu_id": str(a.id), "q": "sens"})
            assert ok.status_code == 200 and len(ok.json()["rows"]) == 1
            one = await browser.get("/api/v1/catalogue", params={"bu_id": str(a.id), "q": "s"})
            assert one.status_code == 422
            other = await browser.get(
                "/api/v1/catalogue", params={"bu_id": str(foreign.id), "q": "sens"}
            )
            assert other.status_code == 404

    asyncio.run(scenario())


def test_add_from_catalogue_defaults_and_guards(lines_db):
    db = lines_db
    a, a1, b = tree(db)
    visible = record(db, a)
    sibling = record(db, b, "Pump in B")
    req = draft(db, bu=a1)
    other_unit_req = draft(db, bu=b)

    async def scenario():
        async with client(app) as browser:
            await login(browser)

            async def post(row, data, version=1):
                return await send(
                    browser, "POST", f"/{row.id}/lines", data, extra={"If-Match": str(version)}
                )

            created = await post(req, {"catalogue_item_id": str(visible)})
            assert created.status_code == 201, created.text
            line = created.json()["line"]
            assert Decimal(line["quantity"]) == 1 and Decimal(line["unit_price"]) == Decimal(
                "19.99"
            )
            assert Decimal(line["amount"]) == Decimal("19.99")
            assert line["description"] == "Proximity sensor" and line["uom_code"] == "EA"
            assert line["catalogue_item_id"] == str(visible)
            hidden = await post(req, {"catalogue_item_id": str(sibling)}, 2)
            assert hidden.status_code == 422
            assert str(sibling) not in hidden.text and "Pump" not in hidden.text
            unknown = await post(req, {"catalogue_item_id": str(uuid4())}, 2)
            # A foreign id and a nonexistent id are indistinguishable.
            assert unknown.status_code == 422
            assert unknown.json()["detail"] == hidden.json()["detail"]
            mixed = await post(req, {"catalogue_item_id": str(visible), "description": "x"}, 2)
            assert mixed.status_code == 422
            neither = await post(req, {"quantity": "1"}, 2)
            assert neither.status_code == 422
            override = await post(
                req, {"catalogue_item_id": str(visible), "quantity": "3", "unit_price": "1"}, 2
            )
            assert override.status_code == 201
            assert Decimal(override.json()["line"]["amount"]) == 3
            sideways = await post(other_unit_req, {"catalogue_item_id": str(visible)})
            assert sideways.status_code == 422  # A is neither B nor an ancestor of B

    asyncio.run(scenario())


def test_currency_mismatch_between_item_and_request_is_refused(lines_db):
    db = lines_db
    a, _, _ = tree(db)
    item = record(db, a, currency="EUR")
    req = draft(db, bu=a)
    service = LineService(db[0], Scope(db[1]), db[3])

    with pytest.raises(ProblemError) as error:
        service.write(req.id, "1", body=LineCreate(catalogue_item_id=item))
    assert error.value.checks["problem"] == "currency_mismatch"


def test_record_requested_records_every_line_once(lines_db):
    db = lines_db
    a, _, _ = tree(db)
    req = draft(db, bu=a)
    service = LineService(db[0], Scope(db[1]), db[3])
    service.write(req.id, "1", body=LineCreate(**BODY))
    assert record_requested(db[0], Scope(db[1]), req.id) == 1
    assert record_requested(db[0], Scope(db[1]), req.id) == 1
    assert count(db, "catalogue_event") == 1
    with tenant_transaction(db[0], Scope(db[1])):
        assert db[0].execute("SELECT times_requested FROM catalogue_item").fetchone() == (1,)


def test_events_are_append_only(lines_db):
    db = lines_db
    a, _, _ = tree(db)
    record(db, a)
    for statement in (
        "UPDATE catalogue_event SET quantity = 9",
        "DELETE FROM catalogue_event",
        "TRUNCATE catalogue_event",
    ):
        with tenant_transaction(db[0], Scope(db[1])), pytest.raises(psycopg.Error):
            with db[0].transaction():
                db[0].execute(statement)
    assert count(db, "catalogue_event") == 1


def test_foreign_tenant_cannot_see_items_through_the_service(lines_db):
    db = lines_db
    a, _, _ = tree(db)
    record(db, a)
    foreign = unit(db, "FA", org=db[2])
    assert CatalogueService(db[0], Scope(db[2])).search(foreign.id, "sensor", None, 4).rows == []
