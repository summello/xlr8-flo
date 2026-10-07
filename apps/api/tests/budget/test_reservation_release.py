"""FIN-011/012 and ACC-001/006: release guards and actual PostgreSQL concurrency."""

import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from flo.kernel.errors import ERROR_TAXONOMY, ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.budget.models import LedgerType
from flo.modules.budget.service import release_reservation, reserve
from tests.authz.conftest import ROOT, load_migration
from tests.authz.conftest import authorization_database as authorization_database
from tests.budget.test_concurrency import connect
from tests.budget.test_post_entry import balance, close_periods, payload, post
from tests.budget.test_post_entry import posting_db as posting_db
from tests.budget.test_post_entry import project as project
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import project_db as project_db

MIGRATION = load_migration(ROOT / "migrations/20260826_0022_reservation_release.py", "release")


def reservation(db, project, amount=Decimal(100)):
    args = payload(db, project, amount=amount)
    args.pop("entry_type")
    return reserve(db.connection, Scope(db.org_a), **args)


def release(db, entry, **changes):
    return release_reservation(
        db.connection,
        Scope(db.org_a),
        **(
            dict(
                reservation_entry_id=entry.id,
                amount=None,
                actor_id=db.actor_id,
                effective_date=date(2026, 8, 26),
                reason="cancelled",
                idempotency_key=uuid4().hex,
            )
            | changes
        ),
    )


def test_partial_full_replay_and_excess(posting_db, project):
    db = posting_db
    post(db, project)
    entry = reservation(db, project)
    assert release(db, entry, amount=Decimal(40)).amount == Decimal(-40)
    before = balance(db, project)
    with pytest.raises(ProblemError) as failure:
        release(db, entry, amount=Decimal(61))
    assert failure.value.code == ErrorCode.CONFLICT
    assert ERROR_TAXONOMY[failure.value.code].status == 409
    assert failure.value.checks["problem"] == "release_exceeds_reservation"
    assert balance(db, project) == before
    full = release(db, entry, idempotency_key="full")
    assert full.amount == Decimal(-60)
    assert release(db, entry, idempotency_key="full") == full
    assert release(db, entry) is None
    assert balance(db, project).reserved == 0
    with pytest.raises(ProblemError) as failure:
        release(db, entry, idempotency_key="full", reason="changed")
    assert failure.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED


@pytest.mark.parametrize("amount", [Decimal(-1), Decimal(0), Decimal("0.001")])
def test_invalid_release_amount_writes_nothing(posting_db, project, amount):
    post(posting_db, project)
    entry = reservation(posting_db, project)
    before = balance(posting_db, project)
    with pytest.raises(ProblemError) as failure:
        release(posting_db, entry, amount=amount)
    assert failure.value.code == ErrorCode.VALIDATION_FAILED
    assert balance(posting_db, project) == before


def test_foreign_wrong_type_reversed_and_closed(posting_db, project):
    db = posting_db
    allocation = post(db, project)
    with pytest.raises(ProblemError) as failure:
        release(db, allocation)
    assert failure.value.code == ErrorCode.VALIDATION_FAILED
    entry = reservation(db, project)
    with pytest.raises(ProblemError) as failure:
        release_reservation(
            db.connection,
            Scope(db.org_b),
            reservation_entry_id=entry.id,
            amount=None,
            actor_id=db.actor_id,
            reason="foreign",
            idempotency_key="foreign",
        )
    assert failure.value.code == ErrorCode.NOT_FOUND
    post(db, project, entry_type=LedgerType.REVERSAL, reverses_entry_id=entry.id)
    with pytest.raises(ProblemError) as failure:
        release(db, entry)
    assert failure.value.checks["problem"] == "reservation_reversed"
    assert db.connection.execute("SELECT remaining FROM reservation_remaining").fetchone() == (0,)
    entry = reservation(db, project)
    close_periods(db)
    assert release(db, entry).amount == Decimal(-100)
    assert balance(db, project).reserved == 0


def test_concurrent_full_releases(posting_db, project):
    db = posting_db
    post(db, project)
    entry = reservation(db, project)
    barrier = Barrier(2)

    def attempt(key):
        with connect(db) as conn, correlation_context("release-race"):
            barrier.wait(timeout=10)
            result = release_reservation(
                conn,
                Scope(db.org_a),
                reservation_entry_id=entry.id,
                amount=None,
                actor_id=db.actor_id,
                reason="cancel",
                idempotency_key=key,
            )
            conn.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["one", "two"]))
    assert results.count(None) == 1
    assert db.connection.execute(
        "SELECT count(*) FROM ledger_entry WHERE entry_type='release'"
    ).fetchone() == (1,)
    assert balance(db, project).reserved == 0


def test_seeded_remaining_reconciles(posting_db, project):
    db = posting_db
    post(db, project, amount=Decimal(10000))
    rng = random.Random(704)
    entries = []
    for _ in range(100):
        if not entries or rng.randrange(3) == 0:
            entries.append(reservation(db, project, Decimal(rng.randint(1, 100))))
        else:
            entry = rng.choice(entries)
            remaining = db.connection.execute(
                "SELECT remaining FROM reservation_remaining WHERE reservation_entry_id=%s",
                (entry.id,),
            ).fetchone()[0]
            release(
                db, entry, amount=Decimal(rng.randint(1, int(remaining))) if remaining else None
            )
        assert (
            balance(db, project).reserved
            == db.connection.execute(
                "SELECT COALESCE(SUM(remaining),0) FROM reservation_remaining"
            ).fetchone()[0]
        )


def test_migration_preserves_rows_and_append_only(posting_db, project):
    db = posting_db
    post(db, project)
    reservation(db, project)
    before = db.connection.execute("SELECT * FROM ledger_entry ORDER BY id").fetchall()
    related = {
        table: db.connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in ["project", "project_balance", "audit_log"]
    }
    columns = [
        row[0]
        for row in db.connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='ledger_entry' AND column_name <> 'releases_entry_id' "
            "ORDER BY ordinal_position"
        ).fetchall()
    ]
    old_rows = db.connection.execute(
        f"SELECT {', '.join(columns)} FROM ledger_entry ORDER BY id"
    ).fetchall()
    MIGRATION.downgrade(db.connection)
    assert db.connection.execute("SELECT * FROM ledger_entry ORDER BY id").fetchall() == old_rows
    MIGRATION.upgrade(db.connection)
    assert db.connection.execute("SELECT * FROM ledger_entry ORDER BY id").fetchall() == before
    for table, rows in related.items():
        assert db.connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows
    for statement in [
        "UPDATE ledger_entry SET amount=1",
        "DELETE FROM ledger_entry",
        "TRUNCATE ledger_entry",
    ]:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            db.connection.execute(statement)


def test_link_constraint_and_migration_preflight_reject_violation(posting_db, project):
    db = posting_db
    post(db, project)
    entry = reservation(db, project)
    with pytest.raises(psycopg.errors.CheckViolation):
        post(db, project, entry_type=LedgerType.RELEASE, amount=Decimal(-1), bucket="reserved")
    with pytest.raises(psycopg.errors.CheckViolation):
        post(db, project, releases_entry_id=entry.id)
    release(db, entry)
    with pytest.raises(psycopg.errors.RaiseException, match="unlinked releases"):
        with db.connection.transaction():
            MIGRATION.downgrade(db.connection)
            MIGRATION.upgrade(db.connection)


def test_release_lock_precedes_remaining_read(posting_db, project):
    from flo.kernel.tenancy.rls import tenant_transaction

    db = posting_db
    post(db, project)
    entry = reservation(db, project)
    release(db, entry)
    # Even an exhausted reservation must wait for the balance lock before reading remaining.
    with connect(db) as holder:
        with tenant_transaction(holder, Scope(db.org_a)):
            holder.execute(
                "SELECT * FROM project_balance WHERE project_id=%s FOR UPDATE", (project.id,)
            )
            with connect(db) as waiter, correlation_context("release-lock"):
                waiter.execute("SET statement_timeout = '250ms'")
                with pytest.raises(psycopg.errors.QueryCanceled):
                    release_reservation(
                        waiter,
                        Scope(db.org_a),
                        reservation_entry_id=entry.id,
                        amount=None,
                        actor_id=db.actor_id,
                        reason="cancel",
                        idempotency_key="lock",
                    )
                waiter.rollback()


def test_reserve_overspend_guard(posting_db, project):
    from flo.modules.budget.service import InsufficientBudget

    post(posting_db, project)
    with pytest.raises(InsufficientBudget):
        reservation(posting_db, project, Decimal(101))
    assert balance(posting_db, project).reserved == 0


def test_concurrent_reserves_use_atomic_overspend_guard(posting_db, project):
    from flo.modules.budget.service import InsufficientBudget

    db = posting_db
    post(db, project)
    barrier = Barrier(2)

    def attempt(key):
        args = payload(db, project, amount=Decimal(60), idempotency_key=key)
        args.pop("entry_type")
        with connect(db) as conn, correlation_context("reserve-race"):
            barrier.wait(timeout=10)
            try:
                result = reserve(conn, Scope(db.org_a), **args)
                conn.commit()
                return result.id
            except InsufficientBudget:
                conn.rollback()
                return "insufficient"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["one", "two"]))
    assert results.count("insufficient") == 1
    assert balance(db, project).reserved == Decimal(60)
    assert balance(db, project).available == Decimal(40)
