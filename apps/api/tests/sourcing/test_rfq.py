"""RFQ drafts and the open-quantity guard (E12-S01: SRC-001, SRC-003, SRC-015, REQ-014)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.requisitions.links import links
from flo.modules.sourcing.schemas import RfqCreate, RfqLineIn, RfqLinesPatch
from flo.modules.sourcing.service import RfqService
from tests.isolation.test_real_sessions import client, headers, login
from tests.requisitions.test_amendments import amend
from tests.requisitions.test_approval_effects import approve, linked, state
from tests.requisitions.test_expired_codes import add_code, expire
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import connection_url, draft, project, unit
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lifecycle import sql
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.vendors.test_vendors import regrant


def approved(db, qty="10", bu=None):
    """An approved requisition whose one line is for `qty` units; returns (row, line id)."""
    row = linked(db, project(db, unit(db, uuid4().hex)))
    approve(db, row)
    line = sql(db, "SELECT id FROM requisition_line WHERE requisition_id=%s", (row.id,))[0][0]
    sql(db, "UPDATE requisition_line SET quantity=%s WHERE id=%s RETURNING 1", (qty, line))
    if bu is not None:  # one unit for several requisitions (a ship-to address is per unit)
        sql(
            db,
            "UPDATE requisition SET bu_id=%s, number=%s WHERE id=%s RETURNING 1",
            (bu.id, f"REQ-{uuid4().hex[:8]}", row.id),
        )
    return state(db, row.id), line


def entry(row, line, qty):
    return RfqLineIn(requisition_line_id=line, requisition_version=row.version, quantity=qty)


def service(db, connection=None):
    return RfqService(connection or db[0], Scope(db[1]), db[3], Settings())


def patch(db, id, body):
    """An edit made with the current edit revision, as a client that just read the RFQ would."""
    revision = sql(db, "SELECT edit_revision FROM rfq WHERE id=%s", (id,))[0][0]
    return service(db).update_lines(id, body, str(revision))


def draft_rfq(db, *entries, title="Pumps"):
    return service(db).create(RfqCreate(title=title, lines=list(entries)))


def sourced(db, line):
    return sql(db, "SELECT sourced_qty FROM requisition_line WHERE id=%s", (line,))[0][0]


def open_qty(db, line):
    query = "SELECT quantity-cancelled_qty-sourced_qty FROM requisition_line WHERE id=%s"
    return sql(db, query, (line,))[0][0]


def rfq_count(db):
    return sql(db, "SELECT count(*) FROM rfq")[0][0]


def refused(call, code=ErrorCode.CONFLICT):
    with pytest.raises(ProblemError) as error:
        call()
    assert error.value.code == code, error.value.checks
    return error.value


def test_guard_accepts_under_and_equal_and_refuses_above_with_the_figures(lifecycle_db):
    db, _ = lifecycle_db
    row, line = approved(db)
    first = draft_rfq(db, entry(row, line, "4"))
    assert first.status == "draft" and first.number.startswith("RFQ")
    assert first.lines[0].open_qty == "6.0000" and open_qty(db, line) == Decimal("6")
    draft_rfq(db, entry(row, line, "6"))  # equal to open
    assert open_qty(db, line) == 0
    count = rfq_count(db)
    error = refused(lambda: draft_rfq(db, entry(row, line, "0.0001")))
    assert error.checks["problem"] == "over_open_quantity"
    assert (
        error.checks[f"open.{line}"] == "0.0000" and error.checks[f"requested.{line}"] == "0.0001"
    )
    assert sourced(db, line) == Decimal("10") and rfq_count(db) == count


def test_several_requisitions_in_one_rfq_are_all_or_nothing(lifecycle_db):
    db, _ = lifecycle_db
    bu = unit(db, uuid4().hex)
    (a, line_a), (b, line_b) = approved(db, bu=bu), approved(db, "3", bu=bu)
    rfq = draft_rfq(db, entry(a, line_a, "5"), entry(b, line_b, "3"))
    assert {line.requisition_id for line in rfq.lines} == {a.id, b.id}
    assert sourced(db, line_a) == 5 and sourced(db, line_b) == 3
    count = rfq_count(db)
    refused(lambda: draft_rfq(db, entry(a, line_a, "5"), entry(b, line_b, "1")))
    assert sourced(db, line_a) == 5 and sourced(db, line_b) == 3 and rfq_count(db) == count


def test_other_unit_and_other_currency_are_422_per_line_and_write_nothing(lifecycle_db):
    db, _ = lifecycle_db
    bu = unit(db, uuid4().hex)
    (a, line_a), (b, line_b), (c, line_c) = approved(db, bu=bu), approved(db), approved(db, bu=bu)
    sql(db, "UPDATE requisition SET currency='EUR' WHERE id=%s RETURNING 1", (c.id,))
    error = refused(
        lambda: draft_rfq(db, entry(a, line_a, "1"), entry(b, line_b, "1"), entry(c, line_c, "1")),
        ErrorCode.VALIDATION_FAILED,
    )
    assert error.checks["problem"] == "another_unit"
    assert [e.field for e in error.errors] == [
        "lines/1/requisition_line_id",
        "lines/2/requisition_line_id",
    ]
    assert all(sourced(db, line) == 0 for line in (line_a, line_b, line_c))
    assert rfq_count(db) == 0


def test_removing_or_lowering_a_line_returns_exactly_the_quantity(lifecycle_db):
    db, _ = lifecycle_db
    bu = unit(db, uuid4().hex)
    (a, line_a), (b, line_b) = approved(db, bu=bu), approved(db, "4", bu=bu)
    rfq = draft_rfq(db, entry(a, line_a, "7.5"), entry(b, line_b, "4"))
    lowered = patch(
        db, rfq.id, RfqLinesPatch(lines=[entry(a, line_a, "2.25"), entry(b, line_b, "4")])
    )
    assert [line.quantity for line in lowered.lines] == ["2.2500", "4.0000"]
    assert open_qty(db, line_a) == Decimal("7.75")
    removed = patch(db, rfq.id, RfqLinesPatch(lines=[entry(a, line_a, "2.25")]))
    assert len(removed.lines) == 1 and open_qty(db, line_b) == Decimal("4")
    grown = patch(db, rfq.id, RfqLinesPatch(lines=[entry(a, line_a, "10"), entry(b, line_b, "1")]))
    assert [line.line_no for line in grown.lines] == [1, 2]
    assert open_qty(db, line_a) == 0
    refused(lambda: patch(db, rfq.id, RfqLinesPatch(lines=[entry(a, line_a, "11")])))
    patch(db, rfq.id, RfqLinesPatch(lines=[]))
    assert open_qty(db, line_a) == 10 and open_qty(db, line_b) == 4
    actions = {r[0] for r in sql(db, "SELECT action FROM audit_log WHERE target_id=%s", (rfq.id,))}
    assert actions == {"rfq.create", "rfq.lines.update"}


def test_lines_of_an_rfq_that_is_not_a_draft_do_not_change(lifecycle_db):
    db, _ = lifecycle_db
    row, line = approved(db)
    rfq = draft_rfq(db, entry(row, line, "3"))
    sql(db, "UPDATE rfq SET status='published' WHERE id=%s RETURNING 1", (rfq.id,))
    error = refused(lambda: patch(db, rfq.id, RfqLinesPatch(lines=[])))
    assert error.checks["problem"] == "not_draft" and sourced(db, line) == 3


def test_two_rfqs_drafting_the_last_ten_units_at_once_let_exactly_one_win(lifecycle_db):
    db, _ = lifecycle_db
    row, line = approved(db)
    barrier = Barrier(2)

    def attempt(title):
        with (
            psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
            correlation_context("rfq-race"),
        ):
            barrier.wait(timeout=10)
            try:
                draft_rfq_on(conn, title)
                return "success"
            except ProblemError as exc:
                return exc.checks["problem"]

    def draft_rfq_on(conn, title):
        service(db, conn).create(RfqCreate(title=title, lines=[entry(row, line, "10")]))

    with ThreadPoolExecutor(2) as pool:
        outcomes = sorted(pool.map(attempt, ["A", "B"]))
    assert outcomes == ["over_open_quantity", "success"]
    assert sourced(db, line) == 10 and rfq_count(db) == 1


def test_stale_version_pending_amendment_and_expired_code_write_nothing(lifecycle_db):
    db, _ = lifecycle_db
    row, line = approved(db)
    stale = refused(
        lambda: draft_rfq(
            db, entry(row, line, "1").model_copy(update={"requisition_version": row.version + 1})
        )
    )
    assert stale.checks["problem"] == "stale_version"
    amend(db, row, "100")
    assert refused(lambda: draft_rfq(db, entry(row, line, "1"))).checks["problem"] == (
        "stale_version"
    )
    other, other_line = approved(db)
    expire(db, "department", "D")
    add_code(db, "department", "D2")
    code = refused(lambda: draft_rfq(db, entry(other, other_line, "1")))
    assert code.checks["problem"] == "code_expired"
    assert sourced(db, line) == 0 and sourced(db, other_line) == 0 and rfq_count(db) == 0


def test_returning_quantity_is_allowed_while_an_amendment_is_pending(lifecycle_db):
    db, _ = lifecycle_db
    row, line = approved(db)
    rfq = draft_rfq(db, entry(row, line, "6"))
    amend(db, row, "100")
    patch(db, rfq.id, RfqLinesPatch(lines=[]))
    assert sourced(db, line) == 0


def test_the_requisition_links_list_its_rfq_only_for_those_who_may_view_it(lifecycle_db):
    db, _ = lifecycle_db
    row, line = approved(db)
    rfq = draft_rfq(db, entry(row, line, "2"))

    def nodes(check):
        return [n for n in links(db[0], Scope(db[1]), row.id, check).nodes if n.kind == "rfq"]

    visible = nodes(lambda permission, scope_type, target: True)
    assert [(n.id, n.state, n.relation) for n in visible] == [(str(rfq.id), "Draft", "sourced_by")]
    assert nodes(lambda permission, scope_type, target: permission != "rfq.view") == []


def line_body(row, line, qty):
    return {"requisition_line_id": str(line), "requisition_version": row.version, "quantity": qty}


async def send(browser, method, suffix="", data=None, extra=None):
    return await browser.request(
        method,
        "/api/v1/sourcing" + suffix,
        json=data,
        headers=headers(browser, uuid4().hex) | (extra or {}),
    )


def test_routes_create_read_list_and_replay_through_the_real_middleware(lifecycle_db):
    db, _ = lifecycle_db
    row, line = approved(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            listed = await send(browser, "GET", "/requisition-lines")
            assert listed.status_code == 200, listed.text
            only = [r for r in listed.json()["rows"] if r["line_id"] == str(line)][0]
            assert only["open_qty"] == "10.0000" and only["selectable"] is True
            body = {"title": "Pumps", "lines": [line_body(row, line, "4")]}
            key = headers(browser, "same-key")
            first = await browser.post("/api/v1/sourcing/rfqs", json=body, headers=key)
            second = await browser.post("/api/v1/sourcing/rfqs", json=body, headers=key)
            assert first.status_code == second.status_code == 201, first.text
            assert first.json()["id"] == second.json()["id"]
            number = await send(browser, "GET", f"/rfqs/{first.json()['id']}")
            assert number.json()["lines"][0]["open_qty"] == "6.0000"
            with_rfq = await send(
                browser, "GET", f"/requisition-lines?with_rfq={first.json()['id']}"
            )
            assert [r["reason"] for r in with_rfq.json()["rows"] if r["line_id"] == str(line)] == [
                "on_rfq"
            ]
            number_quantity = line_body(row, line, "4") | {"quantity": 4}  # not a decimal string
            refused_number = await send(
                browser, "POST", "/rfqs", {"title": "x", "lines": [number_quantity]}
            )
            assert refused_number.status_code == 422
            over = await send(
                browser, "POST", "/rfqs", {"title": "x", "lines": [line_body(row, line, "7")]}
            )
            assert over.status_code == 409 and over.json()["checks"]["problem"] == (
                "over_open_quantity"
            )
            patched = await send(
                browser,
                "PATCH",
                f"/rfqs/{first.json()['id']}/lines",
                {"lines": [line_body(row, line, "1")]},
                {"If-Match": first.headers["ETag"]},
            )
            assert first.headers["ETag"] == '"1"' and patched.headers["ETag"] == '"2"'
            assert patched.status_code == 200 and patched.json()["lines"][0]["quantity"] == "1.0000"

    asyncio.run(scenario())
    assert rfq_count(db) == 1 and sourced(db, line) == 1


def test_rfq_routes_conceal_foreign_tenants(header_db):
    db = header_db
    foreign_unit = unit(db, "FBU", org=db[2])
    foreign = draft(db, bu=foreign_unit, org=db[2])
    foreign_line = uuid4()
    foreign_rfq = uuid4()
    owner = db[0].execute("SELECT id FROM identity WHERE email='second@example.test'").fetchone()[0]
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO requisition_line (id,org_id,requisition_id,line_no,line_type,description,"
            "quantity,uom_code,unit_price,amount,currency) VALUES (%s,%s,%s,1,'item','Pump',"
            "10,'EA',1,10,'USD')",
            (foreign_line, db[2], foreign.id),
        )
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign_rfq, db[2], foreign_unit.id, owner),
        )
    mine = unit(db, "MINE")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            line = {
                "requisition_line_id": str(foreign_line),
                "requisition_version": 1,
                "quantity": "1",
            }
            for method, suffix, data in [
                ("GET", f"/rfqs/{foreign_rfq}", None),
                ("PATCH", f"/rfqs/{foreign_rfq}/lines", {"lines": []}),
                ("POST", "/rfqs", {"title": "x", "lines": [line]}),
                ("GET", f"/requisition-lines?unit_id={foreign_unit.id}", None),
                ("GET", f"/requisition-lines?unit_id={mine.id}&with_rfq={foreign_rfq}", None),
            ]:
                response = await send(browser, method, suffix, data)
                assert response.status_code == 404, (method, suffix, response.text)

    asyncio.run(scenario())
    assert sql_org(db, db[2], "SELECT sourced_qty FROM requisition_line") == [(0,)]


def sql_org(db, org, query):
    with tenant_transaction(db[0], Scope(org)):
        return db[0].execute(query).fetchall()


@pytest.mark.parametrize(
    ("permissions", "status"),
    [
        (["requisition.view"], 403),  # requestor: no rfq.create
        (["rfq.create", "rfq.view"], 201),  # sourcing specialist
    ],
)
def test_only_sourcing_roles_create_rfqs(lifecycle_db, permissions, status):
    db, _ = lifecycle_db
    row, line = approved(db)
    regrant(db, permissions)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            return await send(
                browser, "POST", "/rfqs", {"title": "Pumps", "lines": [line_body(row, line, "1")]}
            )

    assert asyncio.run(scenario()).status_code == status
    assert sourced(db, line) == (1 if status == 201 else 0)


def test_a_replayed_patch_cannot_restore_older_quantities(lifecycle_db):
    db, _ = lifecycle_db
    row, line = approved(db)
    rfq = draft_rfq(db, entry(row, line, "4"))
    assert rfq.edit_revision == 1

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            path = f"/rfqs/{rfq.id}/lines"

            def edit(qty, match):
                return send(browser, "PATCH", path, {"lines": [line_body(row, line, qty)]}, match)

            assert (await send(browser, "GET", f"/rfqs/{rfq.id}")).headers["ETag"] == '"1"'
            first = await edit("6", {"If-Match": '"1"'})
            assert first.status_code == 200 and first.headers["ETag"] == '"2"', first.text
            second = await edit("2", {"If-Match": '"2"'})
            assert second.status_code == 200 and second.headers["ETag"] == '"3"'
            held, audits = sourced(db, line), audit_count(db, rfq.id)
            replay = await edit("6", {"If-Match": '"1"'})
            assert replay.status_code == 409 and replay.json()["checks"]["problem"] == (
                "stale_version"
            )
            assert (await edit("6", {})).json()["checks"]["problem"] == "if_match_required"
            assert (await edit("6", {"If-Match": "x"})).status_code == 400
            return held, audits

    held, audits = asyncio.run(scenario())
    assert held == sourced(db, line) == 2 and audit_count(db, rfq.id) == audits


def audit_count(db, id):
    return sql(db, "SELECT count(*) FROM audit_log WHERE target_id=%s", (id,))[0][0]
