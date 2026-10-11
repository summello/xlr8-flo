"""FIN-006: independent PostgreSQL sessions exercise the actual row lock."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget.ledger import InsufficientBudget
from flo.modules.budget.models import LedgerType
from flo.modules.budget.service import post_entry
from tests.authz.conftest import authorization_database as authorization_database
from tests.budget.test_post_entry import balance, payload, post
from tests.budget.test_post_entry import posting_db as posting_db
from tests.budget.test_post_entry import project as project
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import project_db as project_db


def connect(db):
    return psycopg.connect(
        make_conninfo(db.connection.info.dsn, password=db.connection.info.password)
    )


def attempts(db, project, keys):
    barrier = Barrier(2)

    def attempt(key):
        with connect(db) as conn, correlation_context("concurrent-post"):
            barrier.wait(timeout=10)
            try:
                entry = post_entry(
                    conn,
                    Scope(db.org_a),
                    **payload(
                        db,
                        project,
                        entry_type=LedgerType.RESERVATION,
                        amount=Decimal(60),
                        idempotency_key=key,
                    ),
                )
                conn.commit()
                return entry.id
            except InsufficientBudget:
                conn.rollback()
                return "insufficient"

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(attempt, keys))


def test_concurrent_reservation_cannot_overspend(posting_db, project):
    db = posting_db
    post(db, project)
    results = attempts(db, project, ["one", "two"])
    assert results.count("insufficient") == 1
    assert balance(db, project).reserved == Decimal(60)
    assert balance(db, project).available == Decimal(40)


def test_concurrent_identical_key_changes_balance_once(posting_db, project):
    db = posting_db
    post(db, project)
    results = attempts(db, project, ["same", "same"])
    assert results[0] == results[1] != "insufficient"
    assert balance(db, project).reserved == Decimal(60)
    assert balance(db, project).version == 2
    assert db.connection.execute(
        "SELECT count(*) FROM ledger_entry WHERE idempotency_key = 'same'"
    ).fetchone() == (1,)


def test_lock_precedes_availability_validation(posting_db, project):
    db = posting_db
    # No funds: reading before locking would raise InsufficientBudget immediately.
    with connect(db) as holder:
        with tenant_transaction(holder, Scope(db.org_a)):
            holder.execute(
                "SELECT * FROM project_balance WHERE project_id = %s FOR UPDATE", (project.id,)
            )
            with connect(db) as waiter, correlation_context("lock-probe"):
                waiter.execute("SET statement_timeout = '250ms'")
                with pytest.raises(psycopg.errors.QueryCanceled):
                    post_entry(
                        waiter,
                        Scope(db.org_a),
                        **payload(
                            db,
                            project,
                            entry_type=LedgerType.RESERVATION,
                            amount=Decimal(60),
                        ),
                    )
                waiter.rollback()
    assert balance(db, project).version == 0
