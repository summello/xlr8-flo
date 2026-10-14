"""Submission validation against real Postgres and the production HTTP stack."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget.service import get_balance, lock_balance
from flo.modules.org.schemas import MasterPatch, OrgAddressCreate, SettingPut
from flo.modules.org.service import today
from flo.modules.requisitions.schemas import LineCreate, SubmissionInvalid
from flo.modules.requisitions.service import check_submission
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import (
    client,
    connection_url,
    draft,
    login,
    org_svc,
    project,
    send,
    unit,
)
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lines import BODY, service
from tests.requisitions.test_lines import lines_db as lines_db


def check(db, row, **kwargs):
    return check_submission(db[0], Scope(db[1]), row.id, lock=kwargs.pop("lock", False), **kwargs)


def change(db, table, id, assignments, args=()):
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            f"UPDATE {table} SET {assignments} WHERE org_id=%s AND id=%s", (*args, db[1], id)
        )


def complete(db, p=None, price="100"):
    row = draft(
        db,
        p=p,
        bu=None if p else unit(db, uuid4().hex),
        justification="Needed",
        needed_by=today(),
        department_code="D",
        ledger_account_code="L",
    )
    address = org_svc(db).create_address(
        row.bu_id,
        OrgAddressCreate(
            kind="ship_to",
            line1="Road",
            city="City",
            country="US",
            effective_from=today() - timedelta(days=30),
        ),
    )
    change(db, "requisition", row.id, "delivery_address_id=%s", (address.id,))
    service(db).write(
        row.id, "1", body=LineCreate(**(BODY | {"quantity": "1", "unit_price": price}))
    )
    if p:
        change(db, "project", p.id, "status='active'")
        with tenant_transaction(db[0], Scope(db[1])):
            db[0].execute(
                "UPDATE project_balance SET allocated=100 WHERE org_id=%s AND project_id=%s",
                (db[1], p.id),
            )
    return row, address


def test_empty_draft_collects_all_categories_and_submission_invalid(header_db):
    db = header_db
    row = draft(db)
    change(db, "requisition", row.id, "title=' '")
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("DELETE FROM requisition_sponsor WHERE requisition_id=%s", (row.id,))
    result = check(db, row)
    assert not result.ready and result.funding is None
    assert [(p.field, p.message) for p in result.problems] == [
        ("required/title", "Enter a title"),
        ("required/justification", "Explain why it is needed"),
        ("required/needed_by", "Enter the date it is needed by"),
        ("required/department_code", "Choose a department"),
        ("required/ledger_account_code", "Choose a ledger account"),
        ("required/delivery_address_id", "Choose a delivery location"),
        ("required/sponsors", "Name at least one sponsor"),
        ("lines/lines", "Add at least one line"),
    ]
    error = SubmissionInvalid(result.problems)
    assert error.checks == {"problem": "submission_invalid"}
    assert len(error.errors) == 8 and "Fix 8 things" in error.detail


def test_needed_by_today_passes_yesterday_fails_and_non_draft_not_ready(lines_db):
    db = lines_db
    row, _ = complete(db)
    assert check(db, row).ready
    change(db, "requisition", row.id, "needed_by=%s", (today() - timedelta(days=1),))
    assert check(db, row).problems[0].message == "The needed-by date is in the past"
    change(db, "requisition", row.id, "needed_by=%s,status='approval_pending'", (today(),))
    result = check(db, row)
    assert not result.ready and not result.problems


def test_master_data_inactive_expired_future_unknown_and_closed_delivery(lines_db):
    db = lines_db
    row, address = complete(db)
    org = org_svc(db)
    ledger = org.assert_usable("ledger_account", "L", today())
    uom = org.assert_usable("uom", "EA", today())
    org.update_master("ledger_account", ledger.id, MasterPatch(active=False))
    org.update_master("uom", uom.id, MasterPatch(effective_to=today() - timedelta(days=1)))
    change(db, "org_address", address.id, "effective_to=%s", (today() - timedelta(days=1),))
    result = check(db, row)
    assert [(p.field, p.message) for p in result.problems] == [
        (
            "master_data/ledger_account_code",
            "Ledger account L is inactive. Choose an active account",
        ),
        (
            "master_data/delivery_address_id",
            "Delivery location is no longer available. Choose another",
        ),
        ("lines/1/uom_code", "Unit EA is no longer effective. Choose an active unit"),
    ]
    org.update_master(
        "uom", uom.id, MasterPatch(effective_from=today() + timedelta(days=1), effective_to=None)
    )
    assert "not effective yet" in check(db, row).problems[-1].message
    change(
        db, "requisition_line", service(db).list_lines(row.id).rows[0].id, "category_code='MISSING'"
    )
    assert check(db, row).problems[-1].field == "lines/1/category_code"
    assert "unknown" in check(db, row).problems[-1].message


def test_generated_project_guard(lines_db):
    db = lines_db
    p = project(db)
    row, _ = complete(db, p)
    change(db, "project", p.id, "generated_by_requisition_id=%s", (row.id,))
    assert [(v.field, v.message) for v in check(db, row).problems] == [
        ("project/project_id", "The chosen project cannot take a requisition")
    ]


def test_project_status_depth_currency_and_inactive_standalone(lines_db):
    db = lines_db
    p = project(db)
    row, _ = complete(db, p)
    change(db, "project", p.id, "status='deferred',currency='EUR'")
    # Build a real depth-five hierarchy through the existing project service.
    parent = p.id
    for i in range(4):
        from flo.modules.projects.schemas import ProjectCreate
        from flo.modules.projects.service import ProjectService
        from tests.org.test_bootstrap import settings

        child = ProjectService(db[0], Scope(db[1]), db[3], settings(db[0])).create(
            ProjectCreate(
                bu_id=p.bu_id,
                parent_id=parent,
                name=f"Depth {i}",
                currency="USD",
                department_code="D",
                ledger_account_code="L",
            )
        )
        parent = child.id
    change(db, "requisition", row.id, "project_id=%s", (parent,))
    change(db, "project", parent, "status='deferred',currency='EUR'")
    result = check(db, row)
    assert [p.field for p in result.problems] == ["project/project_id"] * 3 + ["funds/estimate"]
    assert result.problems[0].message.endswith("it is deferred")
    assert "five-level limit" in result.problems[1].message
    assert result.problems[2].message == "The project's currency changed. Reopen the requisition"
    standalone, _ = complete(db)
    change(db, "org_unit", standalone.bu_id, "active=false")
    assert check(db, standalone).problems[0].message == "Choose an active business unit"


def test_shortfall_negative_policy_standalone_and_need_override(lines_db):
    db = lines_db
    p = project(db)
    row, _ = complete(db, p, "120000")
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE project_balance SET allocated=100000 WHERE org_id=%s AND project_id=%s",
            (db[1], p.id),
        )
    result = check(db, row)
    assert result.problems[0].field == "funds/estimate"
    assert (
        result.problems[0].message == "Over the available funds by 20,000.00 USD. "
        "Reduce the estimate or ask for a budget transfer"
    )
    assert result.funding.available_after == "-20000.0000"
    org_svc(db).set_setting("allow_negative_budget", SettingPut(value=True, unit_id=p.bu_id))
    assert not check(db, row).problems
    assert check(db, row, need=Decimal("50000")).ready
    standalone, _ = complete(db, price="120000")
    assert check(db, standalone).ready and check(db, standalone).funding is None


def test_nothing_left_to_reserve_is_never_a_funds_problem(lines_db):
    """Opus review major: the ledger refuses only a posting that lowers available."""
    db = lines_db
    p = project(db)
    row, _ = complete(db, p, "120000")
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE project_balance SET allocated=-20000 WHERE org_id=%s AND project_id=%s",
            (db[1], p.id),
        )
    assert check(db, row).problems[0].field == "funds/estimate"
    # need 0 (everything already held): available is negative but nothing more is reserved.
    nothing = check(db, row, need=Decimal("0"))
    assert not nothing.problems and nothing.funding.available_after == "-20000.0000"


def test_funding_figures_and_no_writes_any_table(lines_db):
    db = lines_db
    p = project(db)
    row, _ = complete(db, p)

    def snapshot():
        tables = (
            db[0]
            .execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            .fetchall()
        )
        from psycopg import sql

        return {
            t: db[0]
            .execute(
                sql.SQL(
                    "SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text"
                ).format(sql.Identifier(t))
            )
            .fetchall()
            for (t,) in tables
        }

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            before = snapshot()
            check(db, row, lock=True)
            assert snapshot() == before
            response = await send(browser, "GET", f"/{row.id}/submission-check")
            assert response.status_code == 200, response.text
            funding = response.json()["funding"]
            assert funding == dict(
                available="100.0000",
                this_requisition="100.0000",
                already_reserved="0.0000",
                available_after="0.0000",
                currency="USD",
                advisory=True,
            )
            assert Decimal(funding["available"]) == get_balance(db[0], Scope(db[1]), p.id).available
            after = snapshot()
            # Session middleware touches last_seen_at on every authenticated read.
            import json

            for data in (before, after):
                data["auth_session"] = [
                    {
                        k: v
                        for k, v in json.loads(r[0]).items()
                        if k not in ("last_seen_at", "idle_expires_at")
                    }
                    for r in data["auth_session"]
                ]
            assert after == before

    asyncio.run(scenario())


def test_submission_check_foreign_tenant_and_permission_outcomes(header_db):
    db = header_db
    own = draft(db)
    foreign = draft(db, org=db[2])

    async def scenario():
        async with client(app) as browser:
            await browser.get("/api/v1/auth/sessions")
            assert (await send(browser, "GET", f"/{own.id}/submission-check")).status_code == 401
            await login(browser)
            assert (
                await send(browser, "GET", f"/{foreign.id}/submission-check")
            ).status_code == 404
            assert (await send(browser, "GET", f"/{own.id}/submission-check")).status_code == 200
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "DELETE FROM role_permission WHERE org_id=%s "
                    "AND permission_code='requisition.view'",
                    (db[1],),
                )
            assert (await send(browser, "GET", f"/{own.id}/submission-check")).status_code == 403
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                )
            assert (await send(browser, "GET", f"/{own.id}/submission-check")).status_code == 404

    asyncio.run(scenario())


def test_lock_balance_missing_and_scoped(header_db):
    db = header_db
    p = project(db)
    assert lock_balance(db[0], Scope(db[1]), p.id) == get_balance(db[0], Scope(db[1]), p.id)
    for id, scope in ((uuid4(), Scope(db[1])), (p.id, Scope(db[2]))):
        with pytest.raises(ProblemError) as error:
            lock_balance(db[0], scope, id)
        assert error.value.code.value == "not-found"


def test_balance_lock_before_read_and_requisition_lock(lines_db):
    db = lines_db
    p = project(db)
    row, _ = complete(db, p)
    read = Event()
    from flo.modules.requisitions import validation

    original = validation.get_balance

    def observe(*args):
        read.set()
        return original(*args)

    with psycopg.connect(connection_url(db[0]), autocommit=True) as other:
        with (
            patch.object(validation, "get_balance", side_effect=observe),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            with tenant_transaction(db[0], Scope(db[1])):
                lock_balance(db[0], Scope(db[1]), p.id)
                future = pool.submit(check_submission, other, Scope(db[1]), row.id, lock=True)
                assert not read.wait(0.3), "Balance was read before acquiring the funding lock"
                assert not future.done()
                # A waiting validator must not own the requisition lock yet.
                db[0].execute("SELECT id FROM requisition WHERE id=%s FOR UPDATE NOWAIT", (row.id,))
                db[0].execute(
                    "UPDATE project_balance SET allocated=200 WHERE project_id=%s", (p.id,)
                )
            result = future.result(timeout=5)
            assert read.is_set() and result.funding.available == "200.0000"
            assert result.funding.advisory is False


def test_returned_draft_reserved_remaining_and_need_override(lines_db):
    from flo.modules.budget.service import release_reservation, reserve

    db = lines_db
    p = project(db)
    row, _ = complete(db, p)
    args = dict(
        project_id=p.id,
        currency="USD",
        source_type="requisition",
        source_id=row.id,
        effective_date=today(),
        actor_id=db[3],
        department_code="D",
        ledger_account_code="L",
    )
    entry = reserve(db[0], Scope(db[1]), amount=Decimal(80), idempotency_key=uuid4().hex, **args)
    change(db, "requisition", row.id, "reservation_entry_id=%s,funding_round=1", (entry.id,))
    release_reservation(
        db[0],
        Scope(db[1]),
        reservation_entry_id=entry.id,
        amount=Decimal(20),
        actor_id=db[3],
        reason="reduce",
        idempotency_key=uuid4().hex,
    )
    result = check(db, row)
    assert result.ready and result.funding.already_reserved == "60.0000"
    assert result.funding.available == "40.0000" and result.funding.available_after == "0.0000"
    change(db, "requisition", row.id, "estimate=150")
    assert check(db, row, need=Decimal(40)).ready
    assert check(db, row).problems[0].message.startswith("Over the available funds by 50.00 USD")
    other = project(db, unit(db, "OTHER"))
    change(db, "requisition", row.id, "project_id=%s", (other.id,))
    assert check(db, row).funding.already_reserved == "0.0000"


def test_two_project_locks_sorted_deduplicated_and_held_by_caller(lines_db):
    from flo.modules.requisitions import validation

    db = lines_db
    p = project(db)
    other = project(db, unit(db, "OTHER"))
    row, _ = complete(db, p)
    order = []
    original = validation.lock_balance

    def observe(conn, scope, id):
        order.append(id)
        return original(conn, scope, id)

    with patch.object(validation, "lock_balance", side_effect=observe):
        with tenant_transaction(db[0], Scope(db[1])):
            check(db, row, lock=True, also_lock=[other.id, p.id, other.id])
            with psycopg.connect(connection_url(db[0]), autocommit=True) as contender:
                for table, column, id in (
                    ("project_balance", "project_id", p.id),
                    ("project_balance", "project_id", other.id),
                    ("requisition", "id", row.id),
                ):
                    with pytest.raises(psycopg.errors.LockNotAvailable), contender.transaction():
                        contender.execute(
                            f"SELECT 1 FROM {table} WHERE {column}=%s FOR UPDATE NOWAIT", (id,)
                        )
    assert order == sorted([p.id, other.id])


def test_legacy_line_missing_fields_collect_all(lines_db):
    db = lines_db
    row, _ = complete(db)
    line = service(db).list_lines(row.id).rows[0]
    # Database constraints keep invalid numeric rows out; legacy descriptions can
    # still contain whitespace and must be rejected at submission.
    change(db, "requisition_line", line.id, "description=' '")
    assert check(db, row).problems[0].message == "Line 1 needs a description"


def test_funding_target_change_while_locking_requires_retry(lines_db):
    from flo.modules.requisitions import validation

    db = lines_db
    p = project(db)
    other = project(db, unit(db, "OTHER"))
    row, _ = complete(db, p)
    original = validation.lock_balance

    def move(conn, scope, id):
        balance = original(conn, scope, id)
        with psycopg.connect(connection_url(db[0]), autocommit=True) as writer:
            with tenant_transaction(writer, Scope(db[1])):
                writer.execute(
                    "UPDATE requisition SET project_id=%s WHERE id=%s", (other.id, row.id)
                )
        return balance

    with patch.object(validation, "lock_balance", side_effect=move):
        with pytest.raises(ProblemError) as error:
            check(db, row, lock=True)
    assert error.value.checks == {"problem": "stale_version"}


def test_legacy_numeric_line_checks(lines_db):
    db = lines_db
    row, _ = complete(db)
    line = service(db).list_lines(row.id).rows[0]
    with tenant_transaction(db[0], Scope(db[1])):
        # Simulate imported legacy rows predating write constraints; DDL and data
        # remain inside this test's disposable database transaction.
        db[0].execute(
            "ALTER TABLE requisition_line DROP CONSTRAINT requisition_line_quantity_check"
        )
        db[0].execute(
            "ALTER TABLE requisition_line DROP CONSTRAINT requisition_line_unit_price_check"
        )
        change(db, "requisition_line", line.id, "quantity=0,unit_price=0")
        result = check(db, row)
        assert [(p.field, p.message) for p in result.problems] == [
            ("lines/1/quantity", "Line 1 needs a quantity"),
            ("lines/1/unit_price", "Line 1 needs a unit price"),
        ]


def test_resubmit_need_fifty_thousand_against_sixty_thousand(lines_db):
    from flo.modules.budget.service import reserve

    db = lines_db
    p = project(db)
    row, _ = complete(db, p, "150000")
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE project_balance SET allocated=160000 WHERE project_id=%s", (p.id,))
    entry = reserve(
        db[0],
        Scope(db[1]),
        project_id=p.id,
        amount=Decimal(100000),
        currency="USD",
        source_type="requisition",
        source_id=row.id,
        effective_date=today(),
        actor_id=db[3],
        department_code="D",
        ledger_account_code="L",
        idempotency_key=uuid4().hex,
    )
    change(db, "requisition", row.id, "reservation_entry_id=%s,funding_round=1", (entry.id,))
    result = check(db, row, lock=True, need=Decimal(50000))
    assert result.ready and result.funding.available == "60000.0000"
    assert result.funding.already_reserved == "100000.0000"
    assert result.funding.available_after == "10000.0000"
