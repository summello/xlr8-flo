"""Line contracts through the production app and real Postgres."""

import asyncio
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.org.schemas import MasterCreate, MasterPatch
from flo.modules.requisitions.lines import LineService
from flo.modules.requisitions.schemas import LineCreate, RequisitionPatch
from flo.modules.requisitions.service import RequisitionService
from tests.requisitions.test_header import (
    AuthorizationTarget,
    IdentityAuthorizationService,
    ScopeType,
    client,
    connection_url,
    draft,
    login,
    org_svc,
    project,
    send,
    svc,
)
from tests.requisitions.test_header import (
    bootstrap_db as bootstrap_db,
)
from tests.requisitions.test_header import (
    empty_database as empty_database,
)
from tests.requisitions.test_header import (
    header_db as header_db,
)

BODY = dict(line_type="item", description="Pump", quantity="3", uom_code="EA", unit_price="0.10")


@pytest.fixture
def lines_db(header_db):
    for org in (header_db[1], header_db[2]):
        for kind, code in [
            ("uom", "EA"),
            ("item_category", "ITEM"),
            ("service_category", "SERVICE"),
        ]:
            org_svc(header_db, org).create_master(
                kind,
                MasterCreate(
                    code=code,
                    name=code,
                    effective_from=date(2000, 1, 1),
                    attributes={"dimension": "count"} if kind == "uom" else {},
                ),
            )
    return header_db


def service(db, connection=None):
    return LineService(connection or db[0], Scope(db[1]), db[3])


async def write(browser, row, data=BODY, version=1, method="POST", line=None):
    return await send(
        browser,
        method,
        f"/{row.id}/lines" + (f"/{line}" if line else ""),
        data,
        extra={"If-Match": str(version)},
    )


def test_rounding_tracking_audit_idempotency_and_stable_numbers(lines_db):
    db = lines_db
    row = draft(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            key = uuid4().hex
            a = await send(
                browser, "POST", f"/{row.id}/lines", BODY, key=key, extra={"If-Match": "1"}
            )
            assert a.status_code == 201, a.text
            assert a.json()["estimate"] == "0.3000"
            assert a.headers["etag"] == '"2"'
            replay = await send(
                browser, "POST", f"/{row.id}/lines", BODY, key=key, extra={"If-Match": "1"}
            )
            assert replay.json() == a.json()
            line = a.json()["line"]
            assert line["requested_qty"] == "3.0000" and line["remaining_qty"] == "3.0000"
            b = await write(
                browser,
                row,
                BODY | {"quantity": "2.5", "unit_price": "19.99"},
                2,
                "PUT",
                line["id"],
            )
            assert b.status_code == 200, b.text
            assert b.json()["estimate"] == "49.9800"
            deleted = await write(browser, row, None, 3, "DELETE", line["id"])
            assert deleted.json()["line"] is None and deleted.json()["estimate"] == "0.0000"
            c = await write(browser, row, version=4)
            assert c.json()["line"]["line_no"] == 2
            got = await send(browser, "GET", f"/{row.id}/lines")
            assert got.json()["rows"] == [c.json()["line"]]
            assert [
                r[0]
                for r in db[0]
                .execute("SELECT action FROM audit_log WHERE target_id=%s ORDER BY id", (row.id,))
                .fetchall()
            ] == [
                "requisition.create",
                "requisition.line.create",
                "requisition.line.update",
                "requisition.line.delete",
                "requisition.line.create",
            ]

    asyncio.run(scenario())


def test_zero_exponent_currency(lines_db):
    db = lines_db
    p = project(db)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE project SET currency='JPY' WHERE id=%s", (p.id,))
    row = draft(db, p=p)
    result = service(db).write(
        row.id, "1", body=LineCreate(**(BODY | {"quantity": "2.5", "unit_price": "19.5"}))
    )
    assert result.estimate == "49.0000"


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": 3},
        {"unit_price": 0.1},
        {"quantity": "0"},
        {"quantity": "-1"},
        {"quantity": "1.00001"},
        {"unit_price": "1.00001"},
        {"line_type": "other"},
        {"amount": "1"},
        {"currency": "JPY"},
        {"line_no": 3},
        {"quantity": "99999999999999", "unit_price": "99999999999999"},
        {"line_type": "service", "category_code": "ITEM"},
        {"uom_code": "UNKNOWN"},
    ],
)
def test_invalid_values_preserve_everything(lines_db, changes):
    db = lines_db
    row = draft(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            result = await write(browser, row, BODY | changes)
            assert result.status_code == 422, result.text
            if changes.get("quantity") == "99999999999999":
                assert result.json()["checks"]["problem"] == "amount_too_large"
            assert (await send(browser, "GET", f"/{row.id}/lines")).json() == dict(
                rows=[], estimate="0.0000", currency="USD", version=1
            )

    asyncio.run(scenario())


def test_master_effectivity_guards(lines_db):
    db = lines_db
    row = draft(db)
    org = org_svc(db)
    master = org.assert_usable("uom", "EA", date.today())
    org.update_master("uom", master.id, MasterPatch(active=False))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            response = await write(browser, row)
            assert (
                response.status_code == 422 and response.json()["checks"]["problem"] == "inactive"
            )

    asyncio.run(scenario())


def test_version_draft_and_absent_line_guards(lines_db):
    db = lines_db
    row = draft(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, data, line in [
                ("POST", BODY, None),
                ("PUT", {"lines": []}, None),
                ("PUT", BODY, uuid4()),
                ("DELETE", None, uuid4()),
            ]:
                response = await send(
                    browser, method, f"/{row.id}/lines" + (f"/{line}" if line else ""), data
                )
                assert response.status_code == 400, response.text
                result = await write(browser, row, data, 2, method, line)
                assert (
                    result.status_code == 409
                    and result.json()["checks"]["problem"] == "stale_version"
                )
            assert (await write(browser, row, BODY, 1, "PUT", uuid4())).status_code == 404
            assert (await write(browser, row, None, 1, "DELETE", uuid4())).status_code == 404
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "UPDATE requisition SET status='approval_pending' WHERE id=%s", (row.id,)
                )
            for method, data, line in [
                ("POST", BODY, None),
                ("PUT", {"lines": []}, None),
                ("PUT", BODY, uuid4()),
                ("DELETE", None, uuid4()),
            ]:
                result = await write(browser, row, data, 1, method, line)
                assert (
                    result.status_code == 409
                    and result.json()["checks"]["problem"] == "not_editable"
                )

    asyncio.run(scenario())


def test_replace_atomic_unknown_duplicate_and_limits(lines_db):
    db = lines_db
    row = draft(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            first = await write(browser, row)
            second = await write(browser, row, version=2)
            updated = BODY | {"line_id": first.json()["line"]["id"], "quantity": "4"}
            result = await write(browser, row, {"lines": [updated, BODY]}, 3, "PUT")
            assert result.status_code == 200, result.text
            snapshot = result.json()
            assert snapshot["version"] == 4 and snapshot["estimate"] == "0.7000"
            assert second.json()["line"]["id"] not in [r["id"] for r in snapshot["rows"]]
            for lines in [
                [updated, BODY, BODY, BODY | {"uom_code": "NO"}],
                [BODY | {"line_id": str(uuid4())}],
                [updated, updated],
                [BODY] * 501,
                [BODY | {"quantity": "99999999999999", "unit_price": "1"}] * 2,
            ]:
                rejected = await write(browser, row, {"lines": lines}, 4, "PUT")
                assert rejected.status_code == 422, rejected.text
                assert (await send(browser, "GET", f"/{row.id}/lines")).json() == snapshot
            empty = await write(browser, row, {"lines": []}, 4, "PUT")
            assert empty.json()["estimate"] == "0.0000"

    asyncio.run(scenario())


def test_estimate_property_200_random_operations(lines_db):
    db = lines_db
    row = draft(db)
    rng = random.Random(902)
    version = 1
    for _ in range(200):
        rows = service(db).list_lines(row.id).rows
        op = rng.choice(["add", "update", "delete"]) if rows else "add"
        selected = rng.choice(rows).id if rows else None
        body = LineCreate(
            **(
                BODY
                | {
                    "quantity": str(rng.randint(1, 30)),
                    "unit_price": str(Decimal(rng.randint(1, 2000)) / 100),
                }
            )
        )
        result = service(db).write(
            row.id,
            str(version),
            body=body if op != "delete" else None,
            line_id=selected if op != "add" else None,
            delete=op == "delete",
        )
        version = result.version
        amounts = (
            db[0]
            .execute(
                "SELECT COALESCE(SUM(amount),0) FROM requisition_line WHERE requisition_id=%s",
                (row.id,),
            )
            .fetchone()[0]
        )
        assert Decimal(result.estimate) == amounts


@pytest.mark.parametrize(
    "assignment",
    [
        "awarded_qty=4",
        "ordered_qty=3,cancelled_qty=1",
        "sourced_qty=-1",
        "awarded_qty=-1",
        "ordered_qty=-1",
        "cancelled_qty=-1",
        "amount=-1",
        "quantity=0",
        "unit_price=0",
        "line_type='bad'",
    ],
)
def test_database_checks_reject_violations(lines_db, assignment):
    db = lines_db
    row = draft(db)
    result = service(db).write(row.id, "1", body=LineCreate(**BODY))
    with pytest.raises(psycopg.errors.CheckViolation), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE requisition_line SET " + assignment + " WHERE id=%s", (result.line.id,)
        )


def test_remaining_and_currency_mismatch_guard(lines_db):
    db = lines_db
    row = draft(db)
    result = service(db).write(row.id, "1", body=LineCreate(**BODY))
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE requisition_line SET ordered_qty=1,cancelled_qty=0.5,currency='JPY' "
            "WHERE id=%s",
            (result.line.id,),
        )
    assert service(db).list_lines(row.id).rows[0].remaining_qty == "1.5000"
    with pytest.raises(ProblemError) as error:
        service(db).write(row.id, "2", body=LineCreate(**BODY))
    assert error.value.checks["problem"] == "currency_mismatch"


def test_foreign_tenant_line_routes_and_permissions(lines_db):
    db = lines_db
    own = draft(db)
    foreign = draft(db, org=db[2])
    foreign_line = (
        LineService(db[0], Scope(db[2]), db[3]).write(foreign.id, "1", body=LineCreate(**BODY)).line
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for target in [foreign, own]:
                for method, data, line in [
                    ("GET", None, None),
                    ("POST", BODY, None),
                    ("PUT", {"lines": []}, None),
                    ("PUT", BODY, foreign_line.id),
                    ("DELETE", None, foreign_line.id),
                ]:
                    if target == own and line is None:
                        continue
                    response = await write(browser, target, data, 1, method, line)
                    assert response.status_code == 404, response.text
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                )
            for method, data, line in [
                ("GET", None, None),
                ("POST", BODY, None),
                ("PUT", {"lines": []}, None),
                ("PUT", BODY, uuid4()),
                ("DELETE", None, uuid4()),
            ]:
                assert (await write(browser, own, data, 1, method, line)).status_code == 404
            auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
            role = auth.create_role("reader", "Reader")
            auth.grant_permission(role.id, "requisition.view")
            auth.grant_role(db[3], role.id, AuthorizationTarget(ScopeType.ORG, db[1]))
            assert (await write(browser, own, None, 1, "GET")).status_code == 200
            for method, data, line in [
                ("POST", BODY, None),
                ("PUT", {"lines": []}, None),
                ("PUT", BODY, uuid4()),
                ("DELETE", None, uuid4()),
            ]:
                assert (await write(browser, own, data, 1, method, line)).status_code == 403

    asyncio.run(scenario())


def test_currency_project_moves(lines_db):
    db = lines_db
    p = project(db)
    same = project(db, bu=org_svc(db).get_unit(p.bu_id))
    jpy = project(db, bu=org_svc(db).get_unit(p.bu_id))
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE project SET currency='JPY' WHERE id=%s", (jpy.id,))
    row = draft(db, p=p)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await write(browser, row)
            moved = await send(
                browser,
                "PATCH",
                f"/{row.id}",
                {"project_id": str(same.id)},
                extra={"If-Match": "2"},
            )
            assert moved.status_code == 200 and moved.json()["estimate"] == "0.3000"
            before = (await send(browser, "GET", f"/{row.id}/lines")).json()
            rejected = await send(
                browser, "PATCH", f"/{row.id}", {"project_id": str(jpy.id)}, extra={"If-Match": "3"}
            )
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["checks"]["problem"] == "currency_change_blocked"
            assert rejected.json()["errors"][0]["field"] == "project_id"
            # can_edit is set by the single-record GET only (E22-S02)
            assert (await send(browser, "GET", f"/{row.id}")).json() == moved.json() | {"can_edit": True}
            assert (await send(browser, "GET", f"/{row.id}/lines")).json() == before
            await write(browser, row, None, 3, "DELETE", before["rows"][0]["id"])
            moved = await send(
                browser, "PATCH", f"/{row.id}", {"project_id": str(jpy.id)}, extra={"If-Match": "4"}
            )
            assert (
                moved.status_code == 200
                and moved.json()["currency"] == "JPY"
                and moved.json()["estimate"] == "0.0000"
            )

    asyncio.run(scenario())


def concurrent(db, row, operations):
    barrier = Barrier(2)
    # Both readers reach the lock together. Hold the winner after its SELECT to make
    # the missing-lock plant deterministic without a barrier inside the held lock.
    original = LineService._get
    entered = set()

    def get(self, id, *, lock=False):
        first = self.connection not in entered
        if first:
            entered.add(self.connection)
            barrier.wait(timeout=10)
        result = original(self, id, lock=lock)
        if first:
            self.connection.execute("SELECT pg_sleep(0.05)")
        return result

    def run(operation):
        with (
            psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
            correlation_context("line-race"),
        ):
            try:
                return operation(conn)
            except ProblemError as exc:
                return exc

    with patch.object(RequisitionService, "_get", get), ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, operations))
    return results


def test_concurrent_inserts_same_version(lines_db):
    db = lines_db
    row = draft(db)
    operations = [lambda conn: service(db, conn).write(row.id, "1", body=LineCreate(**BODY))] * 2
    results = concurrent(db, row, operations)
    assert sum(not isinstance(r, ProblemError) for r in results) == 1
    assert [r.checks["problem"] for r in results if isinstance(r, ProblemError)] == [
        "stale_version"
    ]
    state = service(db).list_lines(row.id)
    assert len(state.rows) == 1 and state.estimate == "0.3000" and state.version == 2


def test_concurrent_currency_patch_and_insert(lines_db):
    db = lines_db
    p = project(db)
    jpy = project(db, bu=org_svc(db).get_unit(p.bu_id))
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE project SET currency='JPY' WHERE id=%s", (jpy.id,))
    row = draft(db, p=p)
    operations = [
        lambda conn: service(db, conn).write(row.id, "1", body=LineCreate(**BODY)),
        lambda conn: RequisitionService(conn, Scope(db[1]), db[3]).update(
            row.id, RequisitionPatch(project_id=jpy.id), "1", lambda _: True
        ),
    ]
    results = concurrent(db, row, operations)
    assert sum(not isinstance(r, ProblemError) for r in results) == 1
    header = svc(db).get(row.id)
    state = service(db).list_lines(row.id)
    assert all(line.currency == header.currency for line in state.rows)
    assert (header.project_id == p.id and len(state.rows) == 1) or (
        header.project_id == jpy.id and not state.rows
    )


def test_max_lines_and_estimate_overflow_preserve_snapshot(lines_db):
    from flo.modules.requisitions.schemas import LinesReplace

    db = lines_db
    row = draft(db)
    result = service(db).write(row.id, "1", replacement=LinesReplace(lines=[BODY] * 500))
    assert len(result.rows) == 500
    with pytest.raises(ProblemError) as error:
        service(db).write(row.id, "2", body=LineCreate(**BODY))
    assert error.value.code.value == "validation-failed"
    assert service(db).list_lines(row.id) == result
    with pytest.raises(ProblemError):
        service(db).write(row.id, "invalid", body=LineCreate(**BODY))


def test_not_effective_master_and_future_category(lines_db):
    from datetime import timedelta

    db = lines_db
    row = draft(db)
    org_svc(db).create_master(
        "uom",
        MasterCreate(
            code="FUTURE",
            name="Future",
            effective_from=date.today() + timedelta(days=1),
            attributes={"dimension": "count"},
        ),
    )
    with pytest.raises(ProblemError) as error:
        service(db).write(row.id, "1", body=LineCreate(**(BODY | {"uom_code": "FUTURE"})))
    assert error.value.checks["problem"] == "not_effective"
    assert svc(db).get(row.id).version == 1


@pytest.mark.parametrize(
    "quantity,price,currency,expected",
    [
        ("3", "0.10", "USD", Decimal("0.30")),
        ("2.5", "19.99", "USD", Decimal("49.98")),
        ("2.5", "19.5", "JPY", Decimal("49")),
    ],
)
def test_amount_calculation_unit(quantity, price, currency, expected):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    line_service = LineService(MagicMock(), Scope(uuid4()))
    with patch(
        "flo.modules.requisitions.lines.assert_usable", return_value=SimpleNamespace(code="EA")
    ):
        values = line_service._values(
            LineCreate(**(BODY | {"quantity": quantity, "unit_price": price})), currency, 0
        )
    assert values["amount"] == expected and values["currency"] == currency


def test_line_foreign_key_unique_number_and_rls_guards(lines_db):
    from psycopg import sql

    db = lines_db
    conn = db[0]
    row = draft(db)
    result = service(db).write(row.id, "1", body=LineCreate(**BODY))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), tenant_transaction(conn, Scope(db[1])):
        conn.execute("UPDATE requisition_line SET org_id=%s WHERE id=%s", (db[2], result.line.id))
    with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(conn, Scope(db[1])):
        conn.execute(
            "INSERT INTO requisition_line SELECT (jsonb_populate_record("
            "NULL::requisition_line,to_jsonb(r) || "
            "jsonb_build_object('id',gen_random_uuid()))).* "
            "FROM requisition_line r WHERE id=%s",
            (result.line.id,),
        )
    role = "line_probe_" + uuid4().hex
    conn.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        conn.execute(
            sql.SQL("GRANT SELECT,INSERT,UPDATE ON requisition_line TO {}").format(
                sql.Identifier(role)
            )
        )
        conn.execute(
            sql.SQL("ALTER TABLE requisition_line OWNER TO {}").format(sql.Identifier(role))
        )
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(conn, Scope(db[1])):
            assert conn.execute("SELECT id FROM requisition_line").fetchall() == [(result.line.id,)]
        with tenant_transaction(conn, Scope(db[2])):
            assert conn.execute("SELECT id FROM requisition_line").fetchall() == []
            assert (
                conn.execute(
                    "UPDATE requisition_line SET description='foreign edit' "
                    "WHERE id=%s RETURNING id",
                    (result.line.id,),
                ).fetchall()
                == []
            )
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            tenant_transaction(conn, Scope(db[1])),
        ):
            conn.execute(
                "INSERT INTO requisition_line SELECT (jsonb_populate_record("
                "NULL::requisition_line,to_jsonb(r) || "
                "jsonb_build_object('id',gen_random_uuid(),'org_id',%s::uuid))).* "
                "FROM requisition_line r WHERE id=%s",
                (db[2], result.line.id),
            )
    finally:
        conn.execute("RESET ROLE")
        conn.execute(
            sql.SQL("ALTER TABLE requisition_line OWNER TO {}").format(
                sql.Identifier(conn.info.user)
            )
        )
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_single_put_audits_even_if_values_equal_and_replace_only_audits_changes(lines_db):
    from flo.modules.requisitions.schemas import LinesReplace

    db = lines_db
    row = draft(db)
    first = service(db).write(row.id, "1", body=LineCreate(**BODY))
    second = service(db).write(row.id, "2", body=LineCreate(**BODY))
    edited = service(db).write(row.id, "3", body=LineCreate(**BODY), line_id=first.line.id)
    assert edited.version == 4
    audit = (
        db[0]
        .execute(
            "SELECT action,before,after FROM audit_log WHERE target_id=%s ORDER BY id", (row.id,)
        )
        .fetchall()
    )
    assert [r[0] for r in audit] == [
        "requisition.create",
        "requisition.line.create",
        "requisition.line.create",
        "requisition.line.update",
    ]
    assert audit[-1][1]["line"] == audit[-1][2]["line"]
    assert audit[-1][2]["line"]["id"] == str(first.line.id)
    replacement = service(db).write(
        row.id,
        "4",
        replacement=LinesReplace(
            lines=[BODY | {"line_id": line.id} for line in [first.line, second.line]]
        ),
    )
    assert replacement.version == 5 and replacement.estimate == "0.6000"
    assert (
        db[0].execute("SELECT count(*) FROM audit_log WHERE target_id=%s", (row.id,)).fetchone()[0]
        == 4
    )
