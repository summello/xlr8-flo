"""Real Postgres violations for every ledger invariant (FIN-004/007, BUD-005/006)."""

import re
from decimal import Decimal
from itertools import product
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.guards import unprotected_tenant_tables
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget.ledger_rules import BUCKET_FOR_TYPE, expected_sign
from flo.modules.budget.models import LedgerBucket, LedgerType
from tests.authz.conftest import ROOT, load_migration
from tests.authz.conftest import authorization_database as authorization_database
from tests.isolation.conftest import tenant_database as tenant_database
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import body, service, unit
from tests.projects.conftest import project_db as project_db

MIGRATION = load_migration(ROOT / "migrations/20260826_0019_ledger.py", "ledger_schema")


@pytest.fixture
def ledger_db(project_db):
    yield project_db


def entry(db, org=None, **changes):
    org = org or db.org_a
    bu = unit(db, uuid4().hex, org)
    project = service(db, org).create(body(bu))
    values = dict(
        org_id=org,
        bu_id=bu.id,
        project_id=project.id,
        entry_type="allocation",
        bucket="allocated",
        amount=Decimal("10"),
        currency="USD",
        source_type="system",
        department_code="D",
        ledger_account_code="L",
        effective_date="2026-08-26",
        actor_id=db.actor_id,
    )
    values.update(changes)
    return values


def insert(db, values):
    columns = list(values)
    row = db.connection.execute(
        sql.SQL("INSERT INTO ledger_entry ({}) VALUES ({}) RETURNING id").format(
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(sql.Placeholder() for _ in columns),
        ),
        tuple(values.values()),
    ).fetchone()
    assert row is not None
    return row[0]


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
@pytest.mark.parametrize("owner", [False, True], ids=["administrator", "owner"])
def test_append_only_rejects_mutation(ledger_db, tenant_database, operation, owner):
    db = ledger_db
    insert(db, entry(db))
    role = tenant_database.owner
    if owner:
        db.connection.execute(
            sql.SQL("ALTER TABLE ledger_entry OWNER TO {}").format(sql.Identifier(role))
        )
        db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
    try:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState) as failure:
            with tenant_transaction(db.connection, Scope(db.org_a)):
                db.connection.execute(
                    {
                        "UPDATE": "UPDATE ledger_entry SET reason = 'changed'",
                        "DELETE": "DELETE FROM ledger_entry",
                        "TRUNCATE": "TRUNCATE ledger_entry",
                    }[operation]
                )
        assert failure.value.sqlstate == "55000"
    finally:
        db.connection.execute("RESET ROLE")
        db.connection.execute("ALTER TABLE ledger_entry OWNER TO CURRENT_USER")


@pytest.mark.parametrize(
    ("constraint", "valid", "invalid"),
    [
        ("ledger_type_bucket_ok", {}, {"bucket": "actual"}),
        ("ledger_sign_ok", {}, {"amount": Decimal("-1")}),
        (
            "ledger_source_link",
            {"source_type": "manual", "reason": "authorized"},
            {"source_type": "manual", "reason": None},
        ),
        (
            "ledger_transfer_group",
            {"entry_type": "transfer", "transfer_group_id": uuid4()},
            {"transfer_group_id": None},
        ),
        ("ledger_reversal_link", {}, {"reverses_entry_id": 1}),
        (
            "ledger_adjustment_reason",
            {"entry_type": "adjustment", "reason": "authorized"},
            {"reason": None},
        ),
    ],
)
def test_named_check_rejects_violation(ledger_db, constraint, valid, invalid):
    values = entry(ledger_db, **valid)
    existing = insert(ledger_db, values)
    if constraint == "ledger_reversal_link":
        invalid = {"reverses_entry_id": existing}
    with pytest.raises(psycopg.errors.CheckViolation) as failure:
        insert(ledger_db, {**values, **invalid})
    assert failure.value.diag.constraint_name == constraint


@pytest.mark.parametrize("source", ["requisition", "purchase_order", "transfer", "import"])
def test_document_sources_require_link(ledger_db, source):
    values = entry(ledger_db, source_type=source, source_id=uuid4())
    insert(ledger_db, values)
    with pytest.raises(psycopg.errors.CheckViolation) as failure:
        insert(ledger_db, {**values, "source_id": None})
    assert failure.value.diag.constraint_name == "ledger_source_link"


@pytest.mark.parametrize(
    "field", ["bucket", "currency", "amount", "org_id", "project_id", "bu_id", "missing"]
)
def test_reversal_integrity_rejects_mismatch(ledger_db, field):
    db = ledger_db
    original = entry(db)
    original_id = insert(db, original)
    reversal = {
        **original,
        "entry_type": "reversal",
        "amount": -original["amount"],
        "reverses_entry_id": original_id,
    }
    changed = {
        "bucket": "reserved",
        "currency": "EUR",
        "amount": Decimal("-9"),
        "org_id": db.org_b,
        "project_id": uuid4(),
        "bu_id": uuid4(),
        "missing": original_id + 1000,
    }
    if field == "org_id":
        reversal = entry(
            db,
            db.org_b,
            entry_type="reversal",
            amount=Decimal("-10"),
            reverses_entry_id=original_id,
        )
    else:
        reversal["reverses_entry_id" if field == "missing" else field] = changed[field]
    with pytest.raises(psycopg.errors.CheckViolation) as failure:
        insert(db, reversal)
    assert failure.value.sqlstate == "23514"
    insert(
        db,
        {
            **original,
            "entry_type": "reversal",
            "amount": Decimal("-10"),
            "reverses_entry_id": original_id,
        },
    )


def test_duplicate_reversal_rejected(ledger_db):
    values = entry(ledger_db)
    original = insert(ledger_db, values)
    reversal = {
        **values,
        "entry_type": "reversal",
        "amount": Decimal("-10"),
        "reverses_entry_id": original,
    }
    insert(ledger_db, reversal)
    with pytest.raises(psycopg.errors.UniqueViolation) as failure:
        insert(ledger_db, reversal)
    assert failure.value.sqlstate == "23505"


def test_idempotency_is_per_organization(ledger_db):
    values = entry(ledger_db, idempotency_key="same-key")
    insert(ledger_db, values)
    with pytest.raises(psycopg.errors.UniqueViolation) as failure:
        insert(ledger_db, values)
    assert failure.value.sqlstate == "23505"
    insert(ledger_db, entry(ledger_db, ledger_db.org_b, idempotency_key="same-key"))


def test_tenant_rls_rejects_foreign_insert_and_hides_select(ledger_db, tenant_database):
    db = ledger_db
    own = insert(db, entry(db))
    foreign = entry(db, db.org_b)
    insert(db, foreign)
    role = tenant_database.owner
    for statement in [
        "GRANT USAGE ON SCHEMA public TO {}",
        "GRANT SELECT, INSERT ON ledger_entry TO {}",
        "GRANT USAGE ON SEQUENCE ledger_entry_id_seq TO {}",
    ]:
        db.connection.execute(sql.SQL(statement).format(sql.Identifier(role)))
    try:
        db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(db.connection, Scope(db.org_a)):
            assert db.connection.execute("SELECT id FROM ledger_entry").fetchall() == [(own,)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_transaction(db.connection, Scope(db.org_a)):
                insert(db, foreign)
    finally:
        db.connection.execute("RESET ROLE")
        db.connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))


@pytest.mark.parametrize("field", ["department_code", "ledger_account_code"])
def test_financial_dimensions_required(ledger_db, field):
    with pytest.raises(psycopg.errors.NotNullViolation):
        insert(ledger_db, entry(ledger_db, **{field: None}))


@pytest.mark.parametrize("field", ["project_id", "bu_id"])
def test_tenant_consistent_foreign_keys(ledger_db, field):
    own, foreign = entry(ledger_db), entry(ledger_db, ledger_db.org_b)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert(ledger_db, {**own, field: foreign[field]})


def test_project_bu_must_match(ledger_db):
    own, other = entry(ledger_db), entry(ledger_db)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        insert(ledger_db, {**own, "bu_id": other["bu_id"]})


def test_up_down_preserves_preexisting_rows(project_db):
    db = project_db
    service(db).create(body(unit(db)))
    tables = [
        "project",
        "org_unit",
        "organization",
        "identity",
        "permission",
        "role",
        "role_permission",
        "authorization_scope",
        "numbering_counter",
    ]
    # Keep the fixture schema intact even when a preservation assertion fails.
    with db.connection.transaction():
        balance_migration = load_migration(
            ROOT / "migrations/20260826_0020_project_balance.py", "balance_preserve"
        )
        release_migration = load_migration(
            ROOT / "migrations/20260826_0022_reservation_release.py", "release_preserve"
        )
        lineage_migration = load_migration(
            ROOT / "migrations/20260826_0023_ledger_lineage.py", "lineage_preserve"
        )
        lineage_migration.downgrade(db.connection)
        release_migration.downgrade(db.connection)
        balance_migration.downgrade(db.connection)
        MIGRATION.downgrade(db.connection)
        before = {
            t: db.connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables
        }
        MIGRATION.upgrade(db.connection)
        try:
            insert(db, {**entry_without_create(db), "idempotency_key": "preservation"})
            assert set(
                db.connection.execute(
                    "SELECT code FROM permission WHERE code IN "
                    "('ledger.read','budget.allocate','budget.adjust','budget.transfer')"
                ).fetchall()
            ) == {("ledger.read",), ("budget.allocate",), ("budget.adjust",), ("budget.transfer",)}
            assert (
                db.connection.execute("SELECT * FROM role_permission ORDER BY 1").fetchall()
                == before["role_permission"]
            )
        finally:
            MIGRATION.downgrade(db.connection)
        for table in tables:
            assert (
                db.connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                == before[table]
            )
        assert db.connection.execute("SELECT to_regclass('ledger_entry')").fetchone() == (None,)
        MIGRATION.upgrade(db.connection)
        MIGRATION.downgrade(db.connection)
        MIGRATION.upgrade(db.connection)
        balance_migration.upgrade(db.connection)
        release_migration.upgrade(db.connection)
        lineage_migration.upgrade(db.connection)


def entry_without_create(db):
    project_id, bu_id = db.connection.execute("SELECT id, bu_id FROM project").fetchone()
    return dict(
        org_id=db.org_a,
        project_id=project_id,
        bu_id=bu_id,
        actor_id=db.actor_id,
        entry_type="allocation",
        bucket="allocated",
        amount=Decimal("1"),
        currency="USD",
        source_type="system",
        department_code="D",
        ledger_account_code="L",
        effective_date="2026-08-26",
    )


def test_python_rules_equal_parsed_sql_and_database(ledger_db):
    text = MIGRATION.UPGRADE_SQL

    def values(fragment):
        return re.findall(r"'([^']+)'", fragment)

    for enum, name in [(LedgerType, "ledger_type"), (LedgerBucket, "ledger_bucket")]:
        definition = re.search(rf"CREATE TYPE {name} AS ENUM \((.*?)\);", text, re.S)
        assert definition
        assert values(definition[1]) == [item.value for item in enum]
    bucket_check = text.split("CONSTRAINT ledger_type_bucket_ok CHECK (")[1].split(
        ",\n CONSTRAINT"
    )[0]
    parsed = {}
    for types, buckets in re.findall(
        r"\(entry_type (IN \([^)]*\)|= '[^']*') AND bucket (IN \([^)]*\)|= '[^']*')\)",
        bucket_check,
    ):
        for kind in values(types):
            parsed[LedgerType(kind)] = frozenset(LedgerBucket(b) for b in values(buckets))
    assert "OR\n  entry_type = 'reversal'" in bucket_check
    parsed[LedgerType.REVERSAL] = frozenset(LedgerBucket)
    assert parsed == BUCKET_FOR_TYPE
    sign_check = text.split("CONSTRAINT ledger_sign_ok CHECK (")[1].split(",\n CONSTRAINT")[0]
    signs = {}
    for types, operator in re.findall(
        r"entry_type (IN \([^)]*\)|= '[^']*') AND amount ([<>]) 0", sign_check
    ):
        signs.update({LedgerType(t): 1 if operator == ">" else -1 for t in values(types)})
    unconstrained = re.search(r"OR\n  entry_type IN \((.*?)\)", sign_check, re.S)
    assert unconstrained
    signs.update({LedgerType(t): None for t in values(unconstrained[1])})
    assert signs == {kind: expected_sign(kind) for kind in LedgerType}
    base = entry(ledger_db, reason="authorized", transfer_group_id=uuid4())
    for kind, bucket, sign in product(LedgerType, LedgerBucket, [1, -1]):
        if kind == LedgerType.REVERSAL:
            original_kind = {
                "allocated": "allocation",
                "reserved": "reservation",
                "committed": "commitment",
                "actual": "actual",
            }[bucket]
            original = insert(ledger_db, {**base, "entry_type": original_kind, "bucket": bucket})
            reversal = {
                **base,
                "entry_type": kind,
                "bucket": bucket,
                "amount": Decimal("-10"),
                "reverses_entry_id": original,
            }
            reversed_id = insert(ledger_db, reversal)
            if sign == 1:
                insert(
                    ledger_db,
                    {**reversal, "amount": Decimal("10"), "reverses_entry_id": reversed_id},
                )
            continue
        allowed = bucket in BUCKET_FOR_TYPE[kind] and expected_sign(kind) in (None, sign)
        values_to_insert = {**base, "entry_type": kind, "bucket": bucket, "amount": Decimal(sign)}
        if kind == LedgerType.RELEASE:
            original = insert(
                ledger_db, {**base, "entry_type": "reservation", "bucket": "reserved"}
            )
            values_to_insert["releases_entry_id"] = original
        if allowed:
            insert(ledger_db, values_to_insert)
        else:
            with pytest.raises(psycopg.errors.CheckViolation):
                insert(ledger_db, values_to_insert)
    assert unprotected_tenant_tables(text) == []
    assert unprotected_tenant_tables(
        text.replace("ALTER TABLE ledger_entry FORCE ROW LEVEL SECURITY;", "")
    ) == ["ledger_entry"]


def test_concurrent_reversals_only_one_commits(ledger_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    from psycopg.conninfo import make_conninfo

    db = ledger_db
    values = entry(db)
    original = insert(db, values)
    reversal = {
        **values,
        "entry_type": "reversal",
        "amount": Decimal("-10"),
        "reverses_entry_id": original,
    }
    barrier = Barrier(2)
    connection_info = make_conninfo(db.connection.info.dsn, password=db.connection.info.password)

    def attempt():
        with psycopg.connect(connection_info) as connection:
            barrier.wait(timeout=10)
            try:
                insert(SimpleNamespace(connection=connection), reversal)
                connection.commit()
                return "committed"
            except psycopg.errors.UniqueViolation:
                connection.rollback()
                return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["committed", "duplicate"]
    assert db.connection.execute(
        "SELECT count(*) FROM ledger_entry WHERE reverses_entry_id = %s", (original,)
    ).fetchone() == (1,)


@pytest.mark.parametrize(
    "field",
    [
        "org_id",
        "bu_id",
        "project_id",
        "entry_type",
        "bucket",
        "amount",
        "currency",
        "source_type",
        "effective_date",
        "actor_id",
        "posted_at",
    ],
)
def test_required_attribution_rejects_null(ledger_db, field):
    with pytest.raises(psycopg.errors.NotNullViolation):
        insert(ledger_db, entry(ledger_db, **{field: None}))


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        (
            {"entry_type": "adjustment", "reason": "authorized", "amount": Decimal("0")},
            "ledger_entry_amount_check",
        ),
        ({"source_type": "unknown"}, "ledger_entry_source_type_check"),
        ({"entry_type": "reversal"}, "ledger_reversal_link"),
    ],
)
def test_remaining_checks_reject_violation(ledger_db, change, constraint):
    with pytest.raises(psycopg.errors.CheckViolation) as failure:
        insert(ledger_db, entry(ledger_db, **change))
    assert failure.value.diag.constraint_name == constraint


@pytest.mark.parametrize("field", ["entry_type", "bucket"])
def test_database_enum_rejects_unknown_value(ledger_db, field):
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        insert(ledger_db, entry(ledger_db, **{field: "unknown"}))


def test_unique_index_rejects_duplicate_without_trigger(ledger_db):
    db = ledger_db
    values = entry(db)
    original = insert(db, values)
    reversal = {
        **values,
        "entry_type": "reversal",
        "amount": Decimal("-10"),
        "reverses_entry_id": original,
    }
    insert(db, reversal)
    with pytest.raises(psycopg.errors.UniqueViolation) as failure:
        with db.connection.transaction():
            db.connection.execute(
                "ALTER TABLE ledger_entry DISABLE TRIGGER ledger_reversal_integrity"
            )
            insert(db, reversal)
    assert failure.value.diag.constraint_name == "ledger_one_reversal"
