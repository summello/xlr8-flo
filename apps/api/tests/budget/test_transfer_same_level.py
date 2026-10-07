"""BUD-002/004: sibling eligibility, atomicity and real-Postgres serialization."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from time import monotonic, sleep
from uuid import uuid4

import httpx
import psycopg
import pytest
from pydantic import ValidationError

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.budget import transfers
from flo.modules.budget.schemas import TransferCreate
from flo.modules.budget.service import get_balance, reverse_entry, transfer
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity, service_for
from tests.budget.test_allocation import allocation_db as allocation_db
from tests.budget.test_allocation import app, payload, request
from tests.budget.test_post_entry import posting_db as posting_db
from tests.isolation.conftest import tenant_database as tenant_database
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import body, service, settings, unit
from tests.projects.conftest import project_db as project_db


@pytest.fixture
def transfer_db(allocation_db):
    grant_permissions(allocation_db, allocation_db.actor_id, "budget.transfer")
    return allocation_db


def pair(db):
    bu = unit(db)
    a, b = service(db).create(body(bu)), service(db).create(body(bu))
    assert request(db, a).status_code == 201
    return a, b


def data(a, b, **changes):
    return payload(amount="25") | dict(from_project_id=str(a.id), to_project_id=str(b.id)) | changes


def send(db, a, b, viewer=None, key=None, **changes):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app(db, viewer)), base_url="https://testserver"
        ) as client:
            return await client.post(
                "/api/v1/budget/transfers",
                json=data(a, b, **changes),
                headers={"Idempotency-Key": key or uuid4().hex},
            )

    return asyncio.run(run())


def call(db, a, b, key="transfer", conn=None, **changes):
    return transfer(
        conn or db.connection,
        Scope(db.org_a),
        TransferCreate.model_validate(data(a, b, **changes)),
        db.actor_id,
        key,
    )


def reconciles(db, *projects):
    for p in projects:
        balance = get_balance(db.connection, Scope(db.org_a), p.id)
        sums = dict(
            db.connection.execute(
                "SELECT bucket, sum(amount) FROM ledger_entry WHERE project_id=%s GROUP BY bucket",
                (p.id,),
            ).fetchall()
        )
        for bucket in ("allocated", "reserved", "committed", "actual"):
            assert getattr(balance, bucket) == sums.get(bucket, Decimal(0))


def test_transfer_and_replay(transfer_db):
    db = transfer_db
    a, b = pair(db)
    first = send(db, a, b, key="once")
    assert first.status_code == 201, first.text
    result = first.json()
    assert [Decimal(e["amount"]) for e in result["entries"]] == [Decimal(-25), Decimal(25)]
    assert all(e["transfer_group_id"] == result["transfer_group_id"] for e in result["entries"])
    assert get_balance(db.connection, Scope(db.org_a), a.id).allocated == Decimal(75)
    assert get_balance(db.connection, Scope(db.org_a), b.id).allocated == Decimal(25)
    assert send(db, a, b, key="once").json() == result
    assert send(db, a, b, key="once", amount="26").status_code == 422
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (3,)
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action='budget.transfer'"
    ).fetchone() == (1,)
    assert db.connection.execute(
        "SELECT sum(amount) FROM ledger_entry WHERE transfer_group_id=%s",
        (result["transfer_group_id"],),
    ).fetchone() == (Decimal(0),)
    reconciles(db, a, b)


@pytest.mark.parametrize("amount", ["0", "-1", 25, "NaN", "0.00001"])
def test_positive_decimal_string_guard(transfer_db, amount):
    a, b = pair(transfer_db)
    assert send(transfer_db, a, b, amount=amount).status_code == 422
    assert transfer_db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)


def test_same_project_guard(transfer_db):
    a, _ = pair(transfer_db)
    result = send(transfer_db, a, a)
    assert result.status_code == 422
    assert result.json()["checks"]["problem"] == "same_project"


def test_different_level_guard(transfer_db):
    db = transfer_db
    a, b = pair(db)
    child = service(db).create(body(unit(db, "child-bu"), parent_id=b.id))
    result = send(db, a, child)
    assert result.status_code == 409, result.text
    assert result.json()["checks"]["problem"] == "transfer_not_eligible"
    assert "levels" in result.json()["detail"]
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)


def test_cousin_guard(transfer_db):
    db = transfer_db
    a, b = pair(db)
    left = service(db).create(body(unit(db, "left"), parent_id=a.id))
    right = service(db).create(body(unit(db, "right"), parent_id=b.id))
    assert request(db, left).status_code == 201
    result = send(db, left, right)
    assert result.status_code == 409, result.text
    assert result.json()["checks"]["problem"] == "transfer_not_eligible"
    assert "different parents" in result.json()["detail"]
    assert "E07-S08" in result.json()["detail"]


def test_currency_guard(transfer_db):
    db = transfer_db
    a, b = pair(db)
    db.connection.execute("UPDATE project SET currency='EUR' WHERE id=%s", (b.id,))
    result = send(db, a, b)
    assert result.status_code == 409
    assert result.json()["checks"]["problem"] == "transfer_not_eligible"
    assert "currencies" in result.json()["detail"]


@pytest.mark.parametrize(
    "status", ["draft", "active", "completed", "abandoned", "deferred", "approval_pending"]
)
@pytest.mark.parametrize("side", ["giving", "receiving"])
def test_status_guard(transfer_db, status, side):
    db = transfer_db
    a, b = pair(db)
    db.connection.execute(
        "UPDATE project SET status=%s WHERE id=%s", (status, a.id if side == "giving" else b.id)
    )
    result = send(db, a, b)
    assert result.status_code == (201 if side == "giving" or status in {"draft", "active"} else 409)


@pytest.mark.parametrize("side", ["giving", "receiving"])
def test_foreign_transfer(transfer_db, side):
    db = transfer_db
    a, b = pair(db)
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    assert (
        send(
            db, foreign if side == "giving" else a, foreign if side == "receiving" else b
        ).status_code
        == 404
    )


@pytest.mark.parametrize("allowed_side", ["giving", "receiving"])
@pytest.mark.parametrize("in_scope", [False, True])
def test_both_project_authorization_guards(transfer_db, allowed_side, in_scope):
    db = transfer_db
    a, b = pair(db)
    viewer = insert_identity(db, "transfer-viewer")
    allowed, denied = (a, b) if allowed_side == "giving" else (b, a)
    with service_for(db, Scope(db.org_a), "transfer-grants") as identity:
        role = identity.create_role("transfer", "Transfer")
        identity.grant_permission(role.id, "budget.transfer")
        identity.grant_role(viewer, role.id, AuthorizationTarget(ScopeType.PROJECT, allowed.id))
        if in_scope:
            read_role = identity.create_role("reader", "Reader")
            identity.grant_permission(read_role.id, "project.read")
            identity.grant_role(
                viewer, read_role.id, AuthorizationTarget(ScopeType.PROJECT, denied.id)
            )
    assert send(db, a, b, viewer=viewer).status_code == (403 if in_scope else 404)
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)


def test_failure_between_legs_is_atomic(transfer_db, monkeypatch):
    db = transfer_db
    a, b = pair(db)
    before = [get_balance(db.connection, Scope(db.org_a), p.id) for p in (a, b)]
    original = transfers.post_entry

    def fail(*args, **kwargs):
        if kwargs["idempotency_key"].endswith(":in"):
            raise RuntimeError("planted second leg failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(transfers, "post_entry", fail)
    with pytest.raises(RuntimeError, match="planted second leg failure"):
        call(db, a, b)
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action='ledger.post'"
    ).fetchone() == (1,)
    assert before == [get_balance(db.connection, Scope(db.org_a), p.id) for p in (a, b)]
    reconciles(db, a, b)


def test_single_leg_reversal_guard(transfer_db):
    db = transfer_db
    a, b = pair(db)
    result = call(db, a, b)
    for entry in result.entries:
        with pytest.raises(ProblemError) as error:
            reverse_entry(
                db.connection,
                Scope(db.org_a),
                entry_id=entry.id,
                actor_id=db.actor_id,
                effective_date=entry.effective_date,
                reason="Reverse transfer leg",
                idempotency_key=uuid4().hex,
            )
        assert error.value.code == ErrorCode.CONFLICT
        assert error.value.checks["problem"] == "cannot_reverse_transfer"
    call(db, b, a, key="reverse-transfer")
    assert get_balance(db.connection, Scope(db.org_a), a.id).allocated == Decimal(100)
    reconciles(db, a, b)


@pytest.mark.parametrize("allow", [False, True])
def test_available_guard(transfer_db, allow):
    db = transfer_db
    a, b = pair(db)
    OrgService(db.connection, Scope(db.org_a), db.actor_id).set_setting(
        "allow_negative_budget", SettingPut(value=allow)
    )
    result = send(db, a, b, amount="101")
    assert result.status_code == (201 if allow else 409)
    if not allow:
        assert result.json()["checks"]["problem"] == "insufficient_budget"
    reconciles(db, a, b)


def test_evidence_guard_and_service_replay(transfer_db):
    db = transfer_db
    a, b = pair(db)
    OrgService(db.connection, Scope(db.org_a), db.actor_id).set_setting(
        "budget.evidence_required", SettingPut(value=True)
    )
    assert send(db, a, b).status_code == 422
    first = call(db, a, b, evidence_ref="support")
    assert call(db, a, b, evidence_ref="support") == first
    with pytest.raises(ProblemError) as error:
        call(db, a, b, evidence_ref="changed")
    assert error.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED
    assert db.connection.execute("SELECT ref FROM ledger_evidence").fetchall() == [
        ("support",),
        ("support",),
    ]


def test_closed_period_guard(transfer_db):
    from tests.budget.test_post_entry import close_periods

    db = transfer_db
    a, b = pair(db)
    close_periods(db)
    result = send(db, a, b)
    assert result.status_code == 409
    assert result.json()["checks"]["problem"] == "period_closed"
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)


def concurrent(db, jobs):
    barrier = Barrier(2)

    def run(job):
        a, b, key, amount = job
        with psycopg.connect(settings(db).database_url.get_secret_value(), autocommit=True) as conn:
            conn.execute("SET lock_timeout = '5s'")
            with correlation_context(key):
                barrier.wait(timeout=5)
                try:
                    return call(db, a, b, key=key, conn=conn, amount=amount)
                except ProblemError as error:
                    return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, jobs))


def test_opposite_transfers_never_deadlock_50_iterations(transfer_db):
    db = transfer_db
    a, b = pair(db)
    assert request(db, b).status_code == 201
    for i in range(50):
        results = concurrent(db, [(a, b, f"out-{i}", "25"), (b, a, f"in-{i}", "25")])
        assert all(not isinstance(r, ProblemError) for r in results)
    assert all(
        get_balance(db.connection, Scope(db.org_a), p.id).allocated == Decimal(100) for p in (a, b)
    )
    reconciles(db, a, b)


def test_concurrent_overspend_exactly_one_succeeds(transfer_db):
    db = transfer_db
    a, b = pair(db)
    c = service(db).create(body(unit(db, "third")))
    results = concurrent(db, [(a, b, "one", "60"), (a, c, "two", "60")])
    errors = [r for r in results if isinstance(r, ProblemError)]
    assert len(errors) == 1
    assert errors[0].code == ErrorCode.INSUFFICIENT_BUDGET
    assert get_balance(db.connection, Scope(db.org_a), a.id).allocated == Decimal(40)
    reconciles(db, a, b, c)


@pytest.mark.parametrize("amount", ["0", "-1", 25, "NaN", "0.00001"])
def test_transfer_amount_contract_without_database(amount):
    values = payload(amount=amount) | {
        "from_project_id": str(uuid4()),
        "to_project_id": str(uuid4()),
    }
    with pytest.raises(ValidationError):
        TransferCreate.model_validate(values)


def test_transfer_decimal_contract_without_database():
    values = payload(amount="25.1234") | {
        "from_project_id": str(uuid4()),
        "to_project_id": str(uuid4()),
    }
    parsed = TransferCreate.model_validate(values)
    assert parsed.amount == Decimal("25.1234")
    assert -parsed.amount + parsed.amount == Decimal(0)


def test_existing_nontransfer_leg_key_is_a_conflict(transfer_db):
    db = transfer_db
    a, b = pair(db)
    assert request(db, a, key="collision:out").status_code == 201
    result = send(db, a, b, key="collision")
    assert result.status_code == 422, result.text
    assert result.json()["checks"]["problem"] == "idempotency_conflict"
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (2,)
    reconciles(db, a, b)


def test_receiving_status_is_read_after_balance_lock(transfer_db):
    db = transfer_db
    a, b = pair(db)
    before = [get_balance(db.connection, Scope(db.org_a), p.id) for p in (a, b)]
    ledger_count = db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone()
    dsn = settings(db).database_url.get_secret_value()
    with (
        psycopg.connect(dsn, autocommit=True) as blocker,
        psycopg.connect(dsn, autocommit=True) as worker,
    ):
        worker.execute("SET lock_timeout = '10s'")

        def run():
            with correlation_context("status-after-lock"):
                try:
                    return call(db, a, b, conn=worker, key="status-after-lock")
                except ProblemError as error:
                    return error

        with ThreadPoolExecutor(max_workers=1) as pool:
            with blocker.transaction():
                blocker.execute(
                    "SELECT project_id FROM project_balance WHERE project_id=%s FOR UPDATE",
                    (b.id,),
                )
                blocker.execute("UPDATE project SET status='completed' WHERE id=%s", (b.id,))
                future = pool.submit(run)
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    blocked = db.connection.execute(
                        "SELECT %s = ANY(pg_blocking_pids(%s))",
                        (blocker.info.backend_pid, worker.info.backend_pid),
                    ).fetchone()
                    if blocked == (True,):
                        break
                    sleep(0.01)
                else:
                    pytest.fail("Transfer did not block on the receiving balance lock")
                assert not future.done()
            result = future.result(timeout=5)
    assert isinstance(result, ProblemError)
    assert result.code == ErrorCode.CONFLICT
    assert result.checks["problem"] == "transfer_not_eligible"
    assert before == [get_balance(db.connection, Scope(db.org_a), p.id) for p in (a, b)]
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == ledger_count
