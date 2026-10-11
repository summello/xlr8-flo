"""FIN-004/011 and AUD-007 primitives exercised against PostgreSQL."""

import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.budget.models import LedgerBucket, LedgerType
from flo.modules.budget.service import (
    InsufficientBudget,
    commit,
    post_entry,
    record_actual,
    release_commitment,
    reverse_entry,
)
from flo.modules.org.service import MasterCodeUnusable
from tests.authz.conftest import ROOT, load_migration
from tests.authz.conftest import authorization_database as authorization_database
from tests.budget.test_concurrency import connect
from tests.budget.test_post_entry import balance, close_periods, payload, post
from tests.budget.test_post_entry import posting_db as posting_db
from tests.budget.test_post_entry import project as project
from tests.budget.test_reservation_release import release, reservation
from tests.isolation.conftest import tenant_database as tenant_database
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import project_db as project_db

MIGRATION = load_migration(ROOT / "migrations/20260826_0023_ledger_lineage.py", "lineage")


def consume(db, project, function=commit, **changes):
    args = payload(db, project, **changes)
    args.pop("entry_type")
    return function(db.connection, Scope(db.org_a), **args)


def reverse(db, entry, **changes):
    return reverse_entry(
        db.connection,
        Scope(db.org_a),
        **(
            dict(
                entry_id=entry.id,
                actor_id=db.actor_id,
                effective_date=date(2026, 8, 26),
                reason="correction",
                idempotency_key=uuid4().hex,
            )
            | changes
        ),
    )


def release_held(db, entry, **changes):
    return release_commitment(
        db.connection,
        Scope(db.org_a),
        **(
            dict(
                commitment_entry_id=entry.id,
                amount=None,
                actor_id=db.actor_id,
                effective_date=date(2026, 8, 26),
                reason="cancelled",
                idempotency_key=uuid4().hex,
            )
            | changes
        ),
    )


@pytest.mark.parametrize(
    "allocated,amount,succeeds", [(100, 100, True), (120, 120, True), (110, 120, False)]
)
def test_conversion_availability_atomic_replay(posting_db, project, allocated, amount, succeeds):
    db = posting_db
    post(db, project, amount=Decimal(allocated))
    held = reservation(db, project)
    before = balance(db, project)
    args = dict(
        amount=Decimal(amount), from_reservation_entry_id=held.id, idempotency_key="convert"
    )
    if not succeeds:
        with pytest.raises(InsufficientBudget):
            consume(db, project, **args)
        assert balance(db, project) == before
        assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (2,)
        return
    entry = consume(db, project, **args)
    value = balance(db, project)
    assert (value.reserved, value.committed, value.available) == (0, amount, allocated - amount)
    assert entry.converts_entry_id == held.id
    assert consume(db, project, **args) == entry
    assert balance(db, project) == value
    with pytest.raises(ProblemError) as failure:
        consume(db, project, **(args | {"from_reservation_entry_id": None}))
    assert failure.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED


def test_actual_consumes_only_remaining_and_replays(posting_db, project):
    db = posting_db
    post(db, project)
    held = consume(db, project)
    args = dict(amount=Decimal(60), from_commitment_entry_id=held.id, idempotency_key="actual")
    actual = consume(db, project, record_actual, **args)
    before = balance(db, project)
    assert (before.committed, before.actual, before.available) == (40, 60, 0)
    assert consume(db, project, record_actual, **args) == actual
    with pytest.raises(ProblemError) as failure:
        consume(db, project, record_actual, amount=Decimal(50), from_commitment_entry_id=held.id)
    assert failure.value.checks["problem"] == "release_exceeds_commitment"
    assert balance(db, project) == before
    reversal = reverse(db, actual, idempotency_key="reverse")
    assert balance(db, project).actual == 0
    assert reverse(db, actual, idempotency_key="reverse") == reversal
    with pytest.raises(ProblemError) as failure:
        reverse(db, actual)
    assert failure.value.checks["problem"] == "entry_already_reversed"
    with pytest.raises(ProblemError) as failure:
        reverse(db, reversal)
    assert failure.value.checks["problem"] == "cannot_reverse_reversal"
    with pytest.raises(psycopg.errors.UniqueViolation):
        post(db, project, entry_type=LedgerType.REVERSAL, reverses_entry_id=actual.id)


@pytest.mark.parametrize("kind", ["reservation", "commitment"])
def test_partial_release_prevents_reversal(posting_db, project, kind):
    db = posting_db
    post(db, project)
    entry = reservation(db, project) if kind == "reservation" else consume(db, project)
    (release if kind == "reservation" else release_held)(db, entry, amount=Decimal(1))
    before = balance(db, project)
    with pytest.raises(ProblemError) as failure:
        reverse(db, entry)
    assert failure.value.checks["problem"] == "entry_partly_released"
    assert balance(db, project) == before


def test_commitment_release_remaining_netting_plant(posting_db, project):
    db = posting_db
    post(db, project)
    held = consume(db, project)
    released = release_held(db, held, amount=Decimal(40), idempotency_key="release")
    assert release_held(db, held, amount=Decimal(40), idempotency_key="release") == released
    reverse(db, released)
    query = "SELECT remaining FROM commitment_remaining WHERE commitment_entry_id=%s"
    assert db.connection.execute(query, (held.id,)).fetchone() == (Decimal(100),)
    # A planted view that forgets reversed releases must fail the same assertion.
    with db.connection.transaction(force_rollback=True):
        db.connection.execute(
            "CREATE OR REPLACE VIEW commitment_remaining AS SELECT r.id AS "
            "commitment_entry_id, r.org_id, r.amount AS committed_amount, "
            "COALESCE(SUM(l.amount),0) AS released_amount, r.amount + "
            "COALESCE(SUM(l.amount),0) AS remaining FROM ledger_entry r LEFT "
            "JOIN ledger_entry l ON l.releases_entry_id=r.id WHERE "
            "r.entry_type='commitment' GROUP BY r.id"
        )
        with pytest.raises(AssertionError):
            assert db.connection.execute(query, (held.id,)).fetchone() == (Decimal(100),)
    full = release_held(db, held, idempotency_key="full")
    assert full.amount == -100
    assert release_held(db, held, idempotency_key="full") == full
    assert release_held(db, held) is None


@pytest.mark.parametrize("field", ["department_code", "ledger_account_code"])
def test_actual_requires_usable_codes(posting_db, project, field):
    db = posting_db
    post(db, project)
    with pytest.raises(ProblemError) as failure:
        consume(db, project, record_actual, **{field: ""})
    assert failure.value.code == ErrorCode.VALIDATION_FAILED
    assert failure.value.errors[0].field == field
    db.connection.execute(
        "UPDATE master_record SET active=false WHERE code=%s",
        ("D" if field == "department_code" else "L",),
    )
    with pytest.raises(MasterCodeUnusable) as failure:
        consume(db, project, record_actual)
    assert failure.value.reason == "inactive"
    assert balance(db, project).actual == 0


def test_reversal_race(posting_db, project):
    db = posting_db
    post(db, project)
    entry = consume(db, project, record_actual)
    barrier = Barrier(2)

    def attempt(key):
        with connect(db) as conn, correlation_context("reversal-race"):
            barrier.wait(timeout=10)
            try:
                result = reverse_entry(
                    conn,
                    Scope(db.org_a),
                    entry_id=entry.id,
                    actor_id=db.actor_id,
                    effective_date=date(2026, 8, 26),
                    reason="correction",
                    idempotency_key=key,
                )
                conn.commit()
                return result.id
            except ProblemError as failure:
                conn.rollback()
                return failure.checks["problem"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["one", "two"]))
    assert results.count("entry_already_reversed") == 1
    assert balance(db, project).actual == 0


def test_lineage_and_migration_preserves_data(posting_db, project):
    db = posting_db
    post(db, project)
    reserved = reservation(db, project)
    committed = consume(db, project, from_reservation_entry_id=reserved.id)
    actual = consume(db, project, record_actual, from_commitment_entry_id=committed.id)
    reversed_entry = reverse(db, actual)
    rows = db.connection.execute(
        "SELECT chain_root_id, depth, approval_instance_id FROM ledger_lineage WHERE "
        "entry_id=%s ORDER BY depth",
        (reversed_entry.id,),
    ).fetchall()
    assert rows == [
        (reversed_entry.id, 0, None),
        (actual.id, 1, None),
        (committed.id, 2, None),
        (reserved.id, 3, None),
    ]
    released = db.connection.execute(
        "SELECT id FROM ledger_entry WHERE releases_entry_id=%s", (committed.id,)
    ).fetchone()[0]
    assert db.connection.execute(
        "SELECT chain_root_id FROM ledger_lineage WHERE entry_id=%s ORDER BY depth", (released,)
    ).fetchall() == [(released,), (committed.id,), (reserved.id,)]
    before = {
        t: db.connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
        for t in ["project", "project_balance", "audit_log"]
    }
    columns = [
        r[0]
        for r in db.connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE "
            "table_name='ledger_entry' AND column_name <> 'converts_entry_id' "
            "ORDER BY ordinal_position"
        ).fetchall()
    ]
    old = db.connection.execute(
        f"SELECT {', '.join(columns)} FROM ledger_entry ORDER BY id"
    ).fetchall()
    MIGRATION.downgrade(db.connection)
    assert db.connection.execute("SELECT * FROM ledger_entry ORDER BY id").fetchall() == old
    MIGRATION.upgrade(db.connection)
    assert (
        db.connection.execute(
            f"SELECT {', '.join(columns)} FROM ledger_entry ORDER BY id"
        ).fetchall()
        == old
    )
    for table, expected in before.items():
        assert db.connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == expected


def test_seeded_all_primitives_reconcile(posting_db, project):
    db = posting_db
    post(db, project, amount=Decimal(10000))
    rng = random.Random(705)
    for _ in range(40):
        held = reservation(db, project, Decimal(rng.randint(1, 100)))
        committed = consume(db, project, amount=held.amount, from_reservation_entry_id=held.id)
        amount = Decimal(rng.randint(1, int(committed.amount)))
        actual = consume(
            db, project, record_actual, amount=amount, from_commitment_entry_id=committed.id
        )
        if rng.randrange(2):
            reverse(db, actual)
        release_held(db, committed)
        value = balance(db, project)
        sums = dict(
            db.connection.execute(
                "SELECT bucket,SUM(amount) FROM ledger_entry GROUP BY bucket"
            ).fetchall()
        )
        for bucket in LedgerBucket:
            assert getattr(value, bucket) == sums.get(bucket, 0)
            if bucket != LedgerBucket.ALLOCATED:
                assert getattr(value, bucket) >= 0


def test_closed_reversal_and_projected_availability(posting_db, project):
    db = posting_db
    allocated = post(db, project)
    consume(db, project)
    with pytest.raises(InsufficientBudget):
        reverse(db, allocated)
    close_periods(db)
    with pytest.raises(ProblemError) as failure:
        reverse(db, allocated)
    assert failure.value.checks["problem"] == "period_closed"


@pytest.mark.parametrize("function", [commit, record_actual])
@pytest.mark.parametrize("amount", [Decimal(0), Decimal(-1), Decimal("0.001")])
def test_positive_amount_guard(posting_db, project, function, amount):
    with pytest.raises(ProblemError):
        consume(posting_db, project, function, amount=amount)
    assert balance(posting_db, project).version == 0


@pytest.mark.parametrize("amount", [Decimal(0), Decimal(-1), Decimal("0.001"), Decimal(101)])
def test_release_commitment_amount_guards(posting_db, project, amount):
    db = posting_db
    post(db, project)
    held = consume(db, project)
    before = balance(db, project)
    with pytest.raises(ProblemError):
        release_held(db, held, amount=amount)
    assert balance(db, project) == before


def test_source_guards_and_foreign_ids(posting_db, project):
    db = posting_db
    allocated = post(db, project)
    held = consume(db, project)
    for function in [
        lambda: release_held(db, allocated),
        lambda: consume(db, project, from_reservation_entry_id=held.id),
        lambda: consume(db, project, record_actual, from_commitment_entry_id=allocated.id),
    ]:
        with pytest.raises(ProblemError) as failure:
            function()
        assert failure.value.code == ErrorCode.VALIDATION_FAILED
    reverse(db, held)
    with pytest.raises(ProblemError) as failure:
        release_held(db, held)
    assert failure.value.checks["problem"] == "commitment_reversed"
    with pytest.raises(ProblemError) as failure:
        consume(db, project, record_actual, from_commitment_entry_id=held.id)
    assert failure.value.checks["problem"] == "conversion_source_reversed"
    assert db.connection.execute(
        "SELECT remaining FROM commitment_remaining WHERE commitment_entry_id=%s", (held.id,)
    ).fetchone() == (0,)
    for function in [
        lambda: reverse_entry(
            db.connection,
            Scope(db.org_b),
            entry_id=allocated.id,
            actor_id=db.actor_id,
            effective_date=date(2026, 8, 26),
            reason="foreign",
            idempotency_key="foreign-reverse",
        ),
        lambda: release_commitment(
            db.connection,
            Scope(db.org_b),
            commitment_entry_id=held.id,
            amount=None,
            actor_id=db.actor_id,
            reason="foreign",
            idempotency_key="foreign-release",
        ),
        lambda: commit(
            db.connection,
            Scope(db.org_b),
            **{k: v for k, v in payload(db, project).items() if k != "entry_type"},
        ),
    ]:
        with pytest.raises(ProblemError) as failure:
            function()
        assert failure.value.code == ErrorCode.NOT_FOUND


def test_cross_project_conversion_writes_nothing(posting_db, project):
    from tests.projects.conftest import body, service, unit

    db = posting_db
    other = service(db).create(body(unit(db, "OTHER")))
    post(db, project, amount=Decimal(200))
    post(db, other, amount=Decimal(200))
    reserved = reservation(db, project)
    committed = consume(db, project)
    before = (balance(db, project), balance(db, other))
    row_count = db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone()
    for function in [
        lambda: consume(db, other, from_reservation_entry_id=reserved.id),
        lambda: consume(db, other, record_actual, from_commitment_entry_id=committed.id),
    ]:
        with pytest.raises(ProblemError) as failure:
            function()
        assert failure.value.code == ErrorCode.NOT_FOUND
        assert (balance(db, project), balance(db, other)) == before
        assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == row_count


def test_reversal_of_release_projected_availability(posting_db, project):
    db = posting_db
    post(db, project)
    held = consume(db, project)
    released = release_held(db, held)
    consume(db, project)
    before = balance(db, project)
    with pytest.raises(InsufficientBudget):
        reverse(db, released)
    assert balance(db, project) == before


def test_actual_omitted_codes_and_replay_after_code_deactivation(posting_db, project):
    db = posting_db
    post(db, project)
    args = payload(db, project, idempotency_key="actual-code")
    args.pop("entry_type")
    args.pop("department_code")
    with pytest.raises(ProblemError) as failure:
        record_actual(db.connection, Scope(db.org_a), **args)
    assert failure.value.code == ErrorCode.VALIDATION_FAILED
    entry = consume(db, project, record_actual, idempotency_key="actual-code")
    before = balance(db, project)
    db.connection.execute("UPDATE master_record SET active=false")
    close_periods(db)
    assert consume(db, project, record_actual, idempotency_key="actual-code") == entry
    assert balance(db, project) == before


def test_conversion_link_foreign_key_guard(posting_db, project):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        post(posting_db, project, converts_entry_id=999999)
    assert balance(posting_db, project).version == 0


def test_allocation_reversal_can_allow_negative(posting_db, project):
    from flo.modules.org.schemas import SettingPut
    from flo.modules.org.service import OrgService

    db = posting_db
    allocated = post(db, project)
    consume(db, project)
    OrgService(db.connection, Scope(db.org_a), db.actor_id).set_setting(
        "allow_negative_budget", SettingPut(value=True)
    )
    reverse(db, allocated)
    assert balance(db, project).available == -100


def test_views_enforce_tenant_scope_and_plant_definer_violation(
    posting_db, project, tenant_database
):
    from psycopg import sql

    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.projects.conftest import body, service, unit

    db = posting_db
    post(db, project)
    consume(db, project)
    other = service(db, db.org_b).create(body(unit(db, "FOREIGN", db.org_b)))
    db.connection.execute("UPDATE project SET status = 'active' WHERE id = %s", (other.id,))
    foreign_args = payload(db, other, amount=Decimal(10))
    with tenant_transaction(db.connection, Scope(db.org_b)):
        post_entry(db.connection, Scope(db.org_b), **foreign_args)
        foreign_args.pop("entry_type")
        commit(db.connection, Scope(db.org_b), **(foreign_args | {"idempotency_key": "foreign"}))
    role = tenant_database.owner
    db.connection.execute(
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
    )
    db.connection.execute(
        sql.SQL("GRANT SELECT ON ledger_entry, commitment_remaining, ledger_lineage TO {}").format(
            sql.Identifier(role)
        )
    )

    try:
        for view in ["commitment_remaining", "ledger_lineage"]:
            # Count foreign orgs directly; then prove security_invoker is what conceals them.
            db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            try:
                with tenant_transaction(db.connection, Scope(db.org_a)):
                    assert db.connection.execute(
                        sql.SQL("SELECT count(*) FROM {} WHERE org_id=%s").format(
                            sql.Identifier(view)
                        ),
                        (db.org_b,),
                    ).fetchone() == (0,)
            finally:
                db.connection.execute("RESET ROLE")
            with db.connection.transaction(force_rollback=True):
                db.connection.execute(
                    sql.SQL("ALTER VIEW {} SET (security_invoker=false)").format(
                        sql.Identifier(view)
                    )
                )
                db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
                try:
                    with tenant_transaction(db.connection, Scope(db.org_a)):
                        with pytest.raises(AssertionError):
                            assert db.connection.execute(
                                sql.SQL("SELECT count(*) FROM {} WHERE org_id=%s").format(
                                    sql.Identifier(view)
                                ),
                                (db.org_b,),
                            ).fetchone() == (0,)
                finally:
                    db.connection.execute("RESET ROLE")
    finally:
        db.connection.execute(
            sql.SQL(
                "REVOKE ALL ON ledger_entry, commitment_remaining, ledger_lineage FROM {}"
            ).format(sql.Identifier(role))
        )
        db.connection.execute(
            sql.SQL("REVOKE USAGE ON SCHEMA public FROM {}").format(sql.Identifier(role))
        )
