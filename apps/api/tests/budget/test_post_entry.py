"""FIN-005/006: posting invariants on real PostgreSQL."""

import ast
import random
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from flo.kernel.errors import ERROR_TAXONOMY, ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget.ledger import InsufficientBudget, LedgerRepository
from flo.modules.budget.models import LedgerBucket, LedgerType
from flo.modules.budget.service import get_balance, post_entry
from flo.modules.org.service import OrgService
from tests.authz.conftest import ROOT, load_migration
from tests.authz.conftest import authorization_database as authorization_database
from tests.isolation.conftest import tenant_database as tenant_database
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import body, service, unit
from tests.projects.conftest import project_db as project_db

BALANCE = load_migration(ROOT / "migrations/20260826_0020_project_balance.py", "balance")
FISCAL = load_migration(ROOT / "migrations/20260826_0016_fiscal.py", "posting_fiscal")


@pytest.fixture
def posting_db(project_db):
    FISCAL.upgrade(project_db.connection)
    try:
        yield project_db
    finally:
        FISCAL.downgrade(project_db.connection)


@pytest.fixture
def project(posting_db):
    return service(posting_db).create(body(unit(posting_db)))


def payload(db, project, **changes):
    values = dict(
        project_id=project.id,
        entry_type=LedgerType.ALLOCATION,
        amount=Decimal("100"),
        currency="USD",
        source_type="system",
        source_id=None,
        effective_date=date(2026, 8, 26),
        actor_id=db.actor_id,
        department_code="D",
        ledger_account_code="L",
        idempotency_key=uuid4().hex,
    )
    return values | changes


def post(db, project, **changes):
    return post_entry(db.connection, Scope(db.org_a), **payload(db, project, **changes))


def balance(db, project):
    return get_balance(db.connection, Scope(db.org_a), project.id)


def close_periods(db):
    with tenant_transaction(db.connection, Scope(db.org_a)):
        db.connection.execute(
            "SELECT set_config('app.fiscal_actor', %s, true)", (str(db.actor_id),)
        )
        db.connection.execute("UPDATE fiscal_period SET status = 'closed'")


def test_creation_initializes_zero_balance(posting_db, project):
    value = balance(posting_db, project)
    assert (value.allocated, value.reserved, value.committed, value.actual, value.available) == (
        Decimal(0),
    ) * 5
    assert value.version == 0


def test_post_and_audit_once(posting_db, project):
    db = posting_db
    args = payload(db, project)
    entry = post_entry(db.connection, Scope(db.org_a), **args)
    assert post_entry(db.connection, Scope(db.org_a), **args) == entry
    value = balance(db, project)
    assert value.allocated == value.available == Decimal("100")
    assert value.version == 1
    rows = db.connection.execute(
        "SELECT after FROM audit_log WHERE action = 'ledger.post'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0]["entry_id"] == entry.id
    assert rows[0][0]["amount"] == "100.00"


@pytest.mark.parametrize(
    "change",
    [
        {"entry_type": LedgerType.ADJUSTMENT, "reason": "change"},
        {"amount": Decimal("101")},
        {"currency": "EUR"},
        {"bucket": LedgerBucket.ACTUAL},
        {"source_type": "manual"},
        {"source_id": uuid4()},
        {"effective_date": date(2026, 8, 27)},
        {"transfer_group_id": uuid4()},
        {"reason": "another"},
        {"department_code": "other"},
        {"ledger_account_code": "other"},
        {"reverses_entry_id": 1000},
        {"entry_type": LedgerType.REVERSAL, "reverses_entry_id": 1000},
    ],
)
def test_replay_compares_immutable_payload(posting_db, project, change):
    db = posting_db
    args = payload(db, project)
    post_entry(db.connection, Scope(db.org_a), **args)
    with pytest.raises(ProblemError) as failure:
        post_entry(db.connection, Scope(db.org_a), **(args | change))
    assert failure.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED
    assert ERROR_TAXONOMY[failure.value.code].status == 422
    assert failure.value.checks["problem"] == "idempotency_conflict"
    assert balance(db, project).version == 1


def test_replay_other_project_conflicts(posting_db, project):
    args = payload(posting_db, project)
    post_entry(posting_db.connection, Scope(posting_db.org_a), **args)
    other = service(posting_db).create(body(unit(posting_db, "OTHER")))
    with pytest.raises(ProblemError) as failure:
        post_entry(
            posting_db.connection, Scope(posting_db.org_a), **(args | {"project_id": other.id})
        )
    assert failure.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED
    assert balance(posting_db, other).version == 0


def test_replay_after_close_and_closed_period_rolls_back(posting_db, project):
    db = posting_db
    args = payload(db, project)
    entry = post_entry(db.connection, Scope(db.org_a), **args)
    before = balance(db, project)
    close_periods(db)
    assert post_entry(db.connection, Scope(db.org_a), **args) == entry
    with pytest.raises(ProblemError) as failure:
        post(db, project)
    assert failure.value.code == ErrorCode.CONFLICT
    assert failure.value.checks["problem"] == "period_closed"
    assert balance(db, project) == before
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)


def test_currency_float_and_rounding(posting_db, project):
    db = posting_db
    with pytest.raises(ProblemError) as failure:
        post(db, project, currency="EUR")
    assert failure.value.code == ErrorCode.VALIDATION_FAILED
    assert failure.value.checks["problem"] == "currency_mismatch"
    assert failure.value.errors[0].field == "currency"
    with pytest.raises(TypeError, match="Decimal"):
        post(db, project, amount=1.5)
    entry = post(db, project, amount=Decimal("10.125"))
    assert entry.amount == Decimal("10.13")
    assert balance(db, project).allocated == Decimal("10.13")


@pytest.mark.parametrize("allowed", [False, True])
def test_negative_budget_setting(posting_db, project, allowed):
    db = posting_db
    from flo.modules.org.schemas import SettingPut

    OrgService(db.connection, Scope(db.org_a), db.actor_id).set_setting(
        "allow_negative_budget",
        SettingPut(value=allowed),
    )
    if allowed:
        post(db, project, entry_type=LedgerType.RESERVATION, amount=Decimal("60"))
        assert balance(db, project).available == Decimal("-60")
    else:
        with pytest.raises(InsufficientBudget) as failure:
            post(db, project, entry_type=LedgerType.RESERVATION, amount=Decimal("60"))
        assert failure.value.available == Decimal(0)
        assert failure.value.requested == Decimal(60)
        assert balance(db, project).version == 0


def test_failure_between_insert_and_rollup_rolls_back(posting_db, project, monkeypatch):
    db = posting_db
    before = balance(db, project)

    def fail(*args):
        raise RuntimeError("planted failure")

    monkeypatch.setattr(LedgerRepository, "update_balance", fail)
    with pytest.raises(RuntimeError, match="planted failure"):
        post(db, project)
    assert balance(db, project) == before
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action = 'ledger.post'"
    ).fetchone() == (0,)


def test_caller_business_change_rolls_back_with_post(posting_db, project):
    db = posting_db
    with pytest.raises(RuntimeError):
        with tenant_transaction(db.connection, Scope(db.org_a)):
            db.connection.execute(
                "UPDATE project SET name = 'changed' WHERE id = %s", (project.id,)
            )
            post(db, project)
            raise RuntimeError("business failure")
    assert service(db).get(project.id).name == project.name
    assert balance(db, project).version == 0
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)


def test_mixed_200_posts_reconcile(posting_db, project):
    db = posting_db
    rng = random.Random(702)
    reversible_allocations = []
    for _ in range(200):
        kind = rng.choice(
            [
                LedgerType.ALLOCATION,
                LedgerType.RESERVATION,
                LedgerType.COMMITMENT,
                LedgerType.ACTUAL,
                LedgerType.ADJUSTMENT,
                LedgerType.TRANSFER,
                LedgerType.RELEASE,
                LedgerType.REVERSAL,
            ]
        )
        args = dict(entry_type=kind, amount=Decimal(rng.randint(1, 20)), allow_negative=True)
        if kind in (LedgerType.ADJUSTMENT, LedgerType.TRANSFER):
            args["amount"] *= rng.choice([-1, 1])
            args["reason"] = "reconciliation test"
            args["transfer_group_id"] = uuid4()
        elif kind == LedgerType.RELEASE:
            value = balance(db, project)
            bucket = rng.choice([LedgerBucket.RESERVED, LedgerBucket.COMMITTED])
            held = getattr(value, bucket)
            if held == 0:
                args["entry_type"] = LedgerType.ALLOCATION
            else:
                args.update(bucket=bucket, amount=-min(args["amount"], held))
        elif kind == LedgerType.REVERSAL:
            if reversible_allocations:
                args["reverses_entry_id"] = reversible_allocations.pop().id
            else:
                args["entry_type"] = LedgerType.ALLOCATION
        entry = post(db, project, **args)
        if entry.entry_type == LedgerType.ALLOCATION:
            reversible_allocations.append(entry)
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (200,)
    value = balance(db, project)
    sums = dict(
        db.connection.execute(
            "SELECT bucket, SUM(amount) FROM ledger_entry WHERE project_id = %s GROUP BY bucket",
            (project.id,),
        ).fetchall()
    )
    for bucket in LedgerBucket:
        assert getattr(value, bucket) == sums.get(bucket, Decimal(0))
    assert value.available == value.allocated - value.reserved - value.committed - value.actual


def test_reversals_derive_bucket_and_enforce_availability(posting_db, project):
    db = posting_db
    allocated = post(db, project)
    with pytest.raises(TypeError, match="Decimal"):
        post(
            db, project, entry_type=LedgerType.REVERSAL, reverses_entry_id=allocated.id, amount=1.5
        )
    post(db, project, entry_type=LedgerType.RESERVATION, amount=Decimal(60))
    with pytest.raises(InsufficientBudget):
        post(db, project, entry_type=LedgerType.REVERSAL, reverses_entry_id=allocated.id)
    post(
        db,
        project,
        entry_type=LedgerType.REVERSAL,
        reverses_entry_id=allocated.id,
        allow_negative=True,
    )
    assert balance(db, project).available == Decimal(-60)
    with pytest.raises(psycopg.errors.UniqueViolation):
        post(
            db,
            project,
            entry_type=LedgerType.REVERSAL,
            reverses_entry_id=allocated.id,
            allow_negative=True,
        )


@pytest.mark.parametrize("kind", [LedgerType.ADJUSTMENT, LedgerType.TRANSFER])
def test_negative_allocated_posts_check_projected_balance(posting_db, project, kind):
    with pytest.raises(InsufficientBudget):
        post(
            posting_db,
            project,
            entry_type=kind,
            amount=Decimal(-1),
            transfer_group_id=uuid4(),
            reason="decrease",
        )
    assert balance(posting_db, project).version == 0


def test_foreign_project_and_reversal_hidden(posting_db, project):
    db = posting_db
    foreign = service(db, db.org_b).create(body(unit(db, "FOREIGN", db.org_b)))
    with pytest.raises(ProblemError) as failure:
        post(db, foreign)
    assert failure.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(ProblemError) as failure:
        get_balance(db.connection, Scope(db.org_a), foreign.id)
    assert failure.value.code == ErrorCode.NOT_FOUND
    with tenant_transaction(db.connection, Scope(db.org_b)):
        original = post_entry(db.connection, Scope(db.org_b), **payload(db, foreign))
    with pytest.raises(ProblemError) as failure:
        post(db, project, entry_type=LedgerType.REVERSAL, reverses_entry_id=original.id)
    assert failure.value.code == ErrorCode.NOT_FOUND


def test_missing_balance_created_under_lock(posting_db, project):
    db = posting_db
    db.connection.execute("DELETE FROM project_balance WHERE project_id = %s", (project.id,))
    post(db, project)
    assert balance(db, project).allocated == Decimal(100)


def test_future_release_linkage_rejects_before_io():
    with pytest.raises(NotImplementedError, match="release linkage arrives in E07-S04"):
        post_entry(
            None,
            Scope(uuid4()),
            **dict(
                project_id=uuid4(),
                entry_type=LedgerType.RELEASE,
                amount=Decimal(-1),
                currency="USD",
                source_type="system",
                source_id=None,
                effective_date=date.today(),
                actor_id=uuid4(),
                department_code="D",
                ledger_account_code="L",
                idempotency_key="future",
                releases_entry_id=1,
            ),
        )


def test_balance_migration_preserves_data_and_rebuilds(posting_db, project):
    db = posting_db
    post(db, project)
    before = {
        t: db.connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
        for t in ["project", "ledger_entry", "audit_log", "organization", "org_unit"]
    }
    BALANCE.downgrade(db.connection)
    assert db.connection.execute("SELECT to_regclass('project_balance')").fetchone() == (None,)
    BALANCE.upgrade(db.connection)
    assert balance(db, project).allocated == Decimal(100)
    for table, rows in before.items():
        assert db.connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows


@pytest.mark.parametrize("bucket", ["reserved", "committed", "actual"])
def test_balance_nonnegative_guard_rejects_violation(posting_db, project, bucket):
    with pytest.raises(psycopg.errors.CheckViolation) as failure:
        posting_db.connection.execute(
            sql.SQL("UPDATE project_balance SET {} = -1 WHERE project_id = %s").format(
                sql.Identifier(bucket)
            ),
            (project.id,),
        )
    assert failure.value.diag.constraint_name == "project_balance_nonnegative_buckets"


def test_money_source_guard():
    directory = Path(__file__).parents[2] / "src/flo/modules/budget"

    def violations(source):
        return [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.BinOp)
            and isinstance(node.op, (ast.FloorDiv, ast.Mod))
            or isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("round", "float")
            or isinstance(node, ast.Constant)
            and isinstance(node.value, float)
        ]

    for path in directory.glob("*.py"):
        assert not violations(path.read_text()), path
    for planted in ["round(amount)", "amount // 2", "amount % 2", "float(amount)", "amount = 1.5"]:
        assert violations(planted)


@pytest.mark.parametrize(
    "change",
    [
        {"entry_type": LedgerType.RELEASE},
        {"bucket": LedgerBucket.ALLOCATED},
        {"entry_type": LedgerType.REVERSAL, "bucket": LedgerBucket.RESERVED},
        {"entry_type": LedgerType.RELEASE, "bucket": LedgerBucket.ACTUAL},
        {"amount": Decimal("-1")},
        {"amount": Decimal("0.001")},
        {"entry_type": LedgerType.RELEASE, "bucket": LedgerBucket.RESERVED, "amount": Decimal("1")},
        {"skip_period_check": True},
    ],
)
def test_posting_guards_reject_real_violations(posting_db, project, change):
    with pytest.raises(ProblemError) as failure:
        post(posting_db, project, **change)
    assert failure.value.code == ErrorCode.VALIDATION_FAILED
    assert balance(posting_db, project).version == 0
    assert posting_db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)


def test_release_and_reversal_of_release_check_availability(posting_db, project):
    db = posting_db
    post(db, project)
    post(db, project, entry_type=LedgerType.RESERVATION, amount=Decimal("100"))
    released = post(
        db,
        project,
        entry_type=LedgerType.RELEASE,
        bucket=LedgerBucket.RESERVED,
        amount=Decimal("-100"),
    )
    post(db, project, entry_type=LedgerType.RESERVATION, amount=Decimal("100"))
    with pytest.raises(InsufficientBudget):
        post(db, project, entry_type=LedgerType.REVERSAL, reverses_entry_id=released.id)
    assert balance(db, project).reserved == Decimal("100")
    close_periods(db)
    post(
        db,
        project,
        entry_type=LedgerType.RELEASE,
        bucket=LedgerBucket.RESERVED,
        amount=Decimal("-100"),
        skip_period_check=True,
    )
    assert balance(db, project).reserved == Decimal(0)


def test_balance_tenant_policy_blocks_foreign_mutation(posting_db, project, tenant_database):
    from flo.kernel.tenancy.guards import unprotected_tenant_tables

    db = posting_db
    foreign = service(db, db.org_b).create(body(unit(db, "POLICY", db.org_b)))
    role = tenant_database.owner
    assert unprotected_tenant_tables(BALANCE.UPGRADE_SQL) == []
    assert unprotected_tenant_tables(
        BALANCE.UPGRADE_SQL.replace("ALTER TABLE project_balance FORCE ROW LEVEL SECURITY;", "")
    ) == ["project_balance"]
    for statement in [
        "GRANT USAGE ON SCHEMA public TO {}",
        "GRANT SELECT, INSERT, UPDATE ON project_balance TO {}",
    ]:
        db.connection.execute(sql.SQL(statement).format(sql.Identifier(role)))
    try:
        db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(db.connection, Scope(db.org_a)):
            assert db.connection.execute("SELECT project_id FROM project_balance").fetchall() == [
                (project.id,)
            ]
            assert (
                db.connection.execute(
                    "UPDATE project_balance SET allocated = 1 WHERE project_id = %s",
                    (foreign.id,),
                ).rowcount
                == 0
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_transaction(db.connection, Scope(db.org_a)):
                db.connection.execute(
                    "UPDATE project_balance SET org_id = %s WHERE project_id = %s",
                    (db.org_b, project.id),
                )
    finally:
        db.connection.execute("RESET ROLE")
        db.connection.execute(
            sql.SQL("REVOKE ALL ON project_balance FROM {}").format(sql.Identifier(role))
        )
        db.connection.execute(
            sql.SQL("REVOKE USAGE ON SCHEMA public FROM {}").format(sql.Identifier(role))
        )
