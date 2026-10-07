from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.errors import CheckViolation, InsufficientPrivilege, ObjectNotInPrerequisiteState

from flo.kernel.audit import (
    AUDIT_READ_PERMISSION,
    ActorKind,
    AuditActor,
    AuditFieldMissing,
    AuditReader,
    AuditWriter,
    CorrelationMissing,
    Outcome,
    allowlisted_snapshot,
    detach_monthly_partition,
    ensure_monthly_partition,
    monthly_partition,
)
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.logging import correlation_context, current_correlation_id
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction

ROOT = Path(__file__).resolve().parents[4]
MIGRATION_PATH = ROOT / "migrations" / "20260825_0005_audit_log.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audit_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@dataclass(frozen=True, slots=True)
class AuditDatabase:
    connection: psycopg.Connection[tuple[object, ...]]
    migration: ModuleType
    business_table: str


def _drop_audit_objects(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    connection.execute("RESET ROLE")
    rows = connection.execute(
        "SELECT tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname = 'public' AND tablename LIKE 'audit_log_%'"
    ).fetchall()
    for row in rows:
        connection.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(cast(str, row[0])))
        )
    connection.execute("DROP TABLE IF EXISTS audit_log")
    connection.execute("DROP FUNCTION IF EXISTS raise_append_only()")


@pytest.fixture
def audit_database() -> Iterator[AuditDatabase]:
    configured_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    database_url = configured_url or "postgresql://flo:flo-local@127.0.0.1:5432/flo_test"
    database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        connection = psycopg.connect(database_url, autocommit=True)
    except psycopg.Error as exc:
        if configured_url:
            pytest.fail(f"configured Postgres is unavailable: {type(exc).__name__}")
        pytest.skip("local Postgres is unavailable; run the repository stack")
        raise

    migration = _load_migration()
    business_table = f"audit_business_{uuid4().hex[:12]}"
    _drop_audit_objects(connection)
    connection.execute(
        sql.SQL("CREATE TABLE {} (id uuid PRIMARY KEY, value text NOT NULL)").format(
            sql.Identifier(business_table)
        )
    )
    migration.upgrade(connection)
    database = AuditDatabase(connection, migration, business_table)
    try:
        yield database
    finally:
        connection.execute("RESET ROLE")
        _drop_audit_objects(connection)
        connection.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(business_table))
        )
        connection.close()


@dataclass(frozen=True, slots=True)
class SensitiveModel:
    id: UUID
    name: str
    password_hash: str
    token_hash: str
    bank_account: str
    tax_id: str
    newly_added_secret: str


def _sensitive_model() -> SensitiveModel:
    return SensitiveModel(
        id=uuid4(),
        name="Pump replacement",
        password_hash="argon2-secret",
        token_hash="token-secret",
        bank_account="bank-secret",
        tax_id="tax-secret",
        newly_added_secret="future-secret",
    )


def test_snapshots_include_only_named_fields_and_new_columns_stay_absent() -> None:
    model = _sensitive_model()

    snapshot = allowlisted_snapshot(model, ("id", "name"))

    assert snapshot == {"id": str(model.id), "name": "Pump replacement"}
    assert {
        "password_hash",
        "token_hash",
        "bank_account",
        "tax_id",
        "newly_added_secret",
    }.isdisjoint(snapshot)
    assert all(
        secret not in repr(snapshot)
        for secret in (
            "argon2-secret",
            "token-secret",
            "bank-secret",
            "tax-secret",
            "future-secret",
        )
    )


def test_declared_snapshot_field_missing_fails_instead_of_passing_by_absence() -> None:
    with pytest.raises(AuditFieldMissing, match="name"):
        allowlisted_snapshot({"id": str(uuid4())}, ("id", "name"))


def test_user_actor_without_an_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="require an actor id"):
        AuditActor(ActorKind.USER)


def test_naive_partition_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        monthly_partition(datetime(2026, 8, 25))


def _writer(database: AuditDatabase, org_id: UUID) -> AuditWriter:
    return AuditWriter(cast(AuditConnection, database.connection), Scope(org_id))


def _audit_count(database: AuditDatabase, org_id: UUID) -> int:
    connection = cast(AuditConnection, database.connection)
    with tenant_transaction(connection, Scope(org_id)):
        row = database.connection.execute(
            "SELECT count(*) FROM audit_log WHERE org_id = %s", (org_id,)
        ).fetchone()
    assert row is not None
    return cast(int, row[0])


def test_business_change_and_audit_row_commit_or_roll_back_together(
    audit_database: AuditDatabase,
) -> None:
    org_id, actor_id = uuid4(), uuid4()
    connection = cast(AuditConnection, audit_database.connection)
    committed_id, rolled_back_id = uuid4(), uuid4()

    with correlation_context("request-commit"), tenant_transaction(
        connection, Scope(org_id)
    ):
        audit_database.connection.execute(
            sql.SQL("INSERT INTO {} (id, value) VALUES (%s, 'committed')").format(
                sql.Identifier(audit_database.business_table)
            ),
            (committed_id,),
        )
        _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.USER, actor_id),
            action="project.update",
            target_type="project",
            target_id=committed_id,
            outcome=Outcome.SUCCESS,
            after_source={"id": committed_id, "name": "Committed", "token_hash": "hidden"},
            after_fields=("id", "name"),
        )

    with pytest.raises(RuntimeError, match="forced failure"), correlation_context(
        "request-rollback"
    ), tenant_transaction(connection, Scope(org_id)):
        audit_database.connection.execute(
            sql.SQL("INSERT INTO {} (id, value) VALUES (%s, 'rolled back')").format(
                sql.Identifier(audit_database.business_table)
            ),
            (rolled_back_id,),
        )
        _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.USER, actor_id),
            action="project.update",
            target_type="project",
            target_id=rolled_back_id,
            outcome=Outcome.SUCCESS,
        )
        raise RuntimeError("forced failure after business and audit writes")

    business_rows = audit_database.connection.execute(
        sql.SQL("SELECT id FROM {} ORDER BY id").format(
            sql.Identifier(audit_database.business_table)
        )
    ).fetchall()
    assert business_rows == [(committed_id,)]
    assert _audit_count(audit_database, org_id) == 1


def test_writer_stores_only_allowlisted_before_and_after_fields(
    audit_database: AuditDatabase,
) -> None:
    org_id = uuid4()
    model = _sensitive_model()
    connection = cast(AuditConnection, audit_database.connection)
    with correlation_context("snapshot-redaction"), tenant_transaction(
        connection, Scope(org_id)
    ):
        record = _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.SYSTEM),
            action="project.update",
            target_type="project",
            target_id=model.id,
            outcome=Outcome.SUCCESS,
            before_source=model,
            before_fields=("id", "name"),
            after_source={**vars_for_model(model), "name": "Pump replacement approved"},
            after_fields=("id", "name"),
        )

    assert record.before == {"id": str(model.id), "name": "Pump replacement"}
    assert record.after == {
        "id": str(model.id),
        "name": "Pump replacement approved",
    }
    serialized = f"{record.before!r}{record.after!r}"
    assert all(
        secret not in serialized
        for secret in (
            model.password_hash,
            model.token_hash,
            model.bank_account,
            model.tax_id,
            model.newly_added_secret,
        )
    )


def vars_for_model(model: SensitiveModel) -> dict[str, object]:
    """Expose the test model as a mapping without relying on a ``__dict__``."""

    return {
        "id": model.id,
        "name": model.name,
        "password_hash": model.password_hash,
        "token_hash": model.token_hash,
        "bank_account": model.bank_account,
        "tax_id": model.tax_id,
        "newly_added_secret": model.newly_added_secret,
    }


@pytest.mark.parametrize(
    ("action", "target_type"),
    [("", "project"), ("project.update", "")],
)
def test_writer_rejects_missing_action_identity(
    audit_database: AuditDatabase, action: str, target_type: str
) -> None:
    org_id = uuid4()
    with pytest.raises(ValueError, match="must be non-empty"), correlation_context(
        "invalid-event"
    ), tenant_transaction(cast(AuditConnection, audit_database.connection), Scope(org_id)):
        _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.SYSTEM),
            action=action,
            target_type=target_type,
            outcome=Outcome.ERROR,
        )
    assert _audit_count(audit_database, org_id) == 0


def test_writer_creates_month_partition_and_uses_utc_microsecond_timestamp(
    audit_database: AuditDatabase,
) -> None:
    org_id = uuid4()
    before = audit_database.connection.execute(
        "SELECT clock_timestamp()"
    ).fetchone()
    assert before is not None
    expected_partition = monthly_partition(cast(datetime, before[0]))
    assert audit_database.connection.execute(
        "SELECT to_regclass(%s)", (f"public.{expected_partition.name}",)
    ).fetchone() == (None,)

    with correlation_context("partition-create"), tenant_transaction(
        cast(AuditConnection, audit_database.connection), Scope(org_id)
    ):
        record = _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.SYSTEM),
            action="configuration.create",
            target_type="configuration",
            outcome=Outcome.SUCCESS,
        )

    assert record.occurred_at.tzinfo is not None
    assert record.occurred_at.utcoffset() == timedelta(0)
    assert audit_database.connection.execute(
        "SELECT to_regclass(%s)", (f"public.{expected_partition.name}",)
    ).fetchone() == (expected_partition.name,)


def test_background_job_restores_request_correlation_and_lost_id_fails(
    audit_database: AuditDatabase,
) -> None:
    org_id = uuid4()
    with correlation_context("api-request-correlation"):
        job_payload = {"correlation_id": current_correlation_id()}

    correlation_id = job_payload["correlation_id"]
    assert correlation_id is not None
    with correlation_context(correlation_id), tenant_transaction(
        cast(AuditConnection, audit_database.connection), Scope(org_id)
    ):
        _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.JOB),
            action="purchase_order.email",
            target_type="purchase_order",
            outcome=Outcome.SUCCESS,
        )

    with pytest.raises(CorrelationMissing), tenant_transaction(
        cast(AuditConnection, audit_database.connection), Scope(org_id)
    ):
        _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.JOB),
            action="purchase_order.email",
            target_type="purchase_order",
            outcome=Outcome.ERROR,
        )

    with tenant_transaction(cast(AuditConnection, audit_database.connection), Scope(org_id)):
        rows = audit_database.connection.execute(
            "SELECT correlation_id FROM audit_log WHERE org_id = %s", (org_id,)
        ).fetchall()
    assert rows == [("api-request-correlation",)]


def test_blank_correlation_id_fails_before_an_audit_row_is_written(
    audit_database: AuditDatabase,
) -> None:
    org_id = uuid4()
    with pytest.raises(CorrelationMissing), correlation_context(""), tenant_transaction(
        cast(AuditConnection, audit_database.connection), Scope(org_id)
    ):
        _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.JOB),
            action="purchase_order.email",
            target_type="purchase_order",
            outcome=Outcome.ERROR,
        )
    assert _audit_count(audit_database, org_id) == 0


@pytest.mark.parametrize(
    ("full_address", "stored_prefix"),
    [
        ("203.0.113.79", "203.0.113.0/24"),
        ("2001:db8:abcd:1234::99", "2001:db8:abcd::/48"),
    ],
)
def test_only_truncated_ip_prefix_is_stored(
    audit_database: AuditDatabase,
    full_address: str,
    stored_prefix: str,
) -> None:
    org_id = uuid4()
    with correlation_context("ip-redaction"), tenant_transaction(
        cast(AuditConnection, audit_database.connection), Scope(org_id)
    ):
        _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.SYSTEM),
            action="session.inspect",
            target_type="session",
            outcome=Outcome.SUCCESS,
            ip_address_value=full_address,
        )

    with tenant_transaction(cast(AuditConnection, audit_database.connection), Scope(org_id)):
        row = audit_database.connection.execute(
            "SELECT row_to_json(audit_log)::text FROM audit_log WHERE org_id = %s", (org_id,)
        ).fetchone()
    assert row is not None
    stored_row = cast(str, row[0])
    assert stored_prefix in stored_row
    assert full_address not in stored_row


def test_invalid_ip_error_does_not_echo_the_supplied_value(
    audit_database: AuditDatabase,
) -> None:
    org_id = uuid4()
    supplied = "203.0.113.79-not-an-address"
    with pytest.raises(ValueError) as error, correlation_context(
        "invalid-ip"
    ), tenant_transaction(cast(AuditConnection, audit_database.connection), Scope(org_id)):
        _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.SYSTEM),
            action="session.inspect",
            target_type="session",
            outcome=Outcome.ERROR,
            ip_address_value=supplied,
        )
    assert supplied not in str(error.value)
    assert _audit_count(audit_database, org_id) == 0


@dataclass(slots=True)
class PermissionProbe:
    allowed: bool
    checked: list[str]

    def require(self, permission: str) -> None:
        self.checked.append(permission)
        if not self.allowed:
            raise PermissionError(permission)


def test_audit_read_requires_permission_and_writes_reader_event(
    audit_database: AuditDatabase,
) -> None:
    org_id, source_actor, reader_id = uuid4(), uuid4(), uuid4()
    connection = cast(AuditConnection, audit_database.connection)
    with correlation_context("seed-event"), tenant_transaction(connection, Scope(org_id)):
        _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.USER, source_actor),
            action="project.create",
            target_type="project",
            target_id=uuid4(),
            outcome=Outcome.SUCCESS,
        )

    denied = PermissionProbe(False, [])
    with pytest.raises(PermissionError, match=AUDIT_READ_PERMISSION), correlation_context(
        "denied-read"
    ), tenant_transaction(connection, Scope(org_id)):
        AuditReader(connection, Scope(org_id), denied).read(
            reader=AuditActor(ActorKind.USER, reader_id)
        )
    assert denied.checked == [AUDIT_READ_PERMISSION]
    assert _audit_count(audit_database, org_id) == 1

    allowed = PermissionProbe(True, [])
    with correlation_context("authorized-read"), tenant_transaction(connection, Scope(org_id)):
        records = AuditReader(connection, Scope(org_id), allowed).read(
            reader=AuditActor(ActorKind.USER, reader_id)
        )

    assert allowed.checked == [AUDIT_READ_PERMISSION]
    assert [record.action for record in records] == ["project.create"]
    with tenant_transaction(connection, Scope(org_id)):
        read_row = audit_database.connection.execute(
            "SELECT actor_id, action, outcome, correlation_id FROM audit_log "
            "WHERE org_id = %s AND action = 'audit.read'",
            (org_id,),
        ).fetchone()
    assert read_row == (reader_id, "audit.read", "success", "authorized-read")


def test_audit_read_limit_fails_before_permission_or_database_access(
    audit_database: AuditDatabase,
) -> None:
    permissions = PermissionProbe(True, [])
    reader = AuditReader(
        cast(AuditConnection, audit_database.connection), Scope(uuid4()), permissions
    )

    with pytest.raises(ValueError, match="between 1 and 1000"):
        reader.read(reader=AuditActor(ActorKind.USER, uuid4()), limit=0)

    assert permissions.checked == []


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_log SET reason = 'changed'", "DELETE FROM audit_log"]
)
def test_database_trigger_rejects_update_and_delete_for_table_owner(
    audit_database: AuditDatabase,
    statement: str,
) -> None:
    org_id = uuid4()
    connection = cast(AuditConnection, audit_database.connection)
    with correlation_context("immutable-seed"), tenant_transaction(connection, Scope(org_id)):
        _writer(audit_database, org_id).write(
            actor=AuditActor(ActorKind.SYSTEM),
            action="configuration.create",
            target_type="configuration",
            outcome=Outcome.SUCCESS,
        )

    verb = statement.partition(" ")[0]
    with pytest.raises(
        ObjectNotInPrerequisiteState, match=f"{verb} is forbidden"
    ), tenant_transaction(connection, Scope(org_id)):
        audit_database.connection.execute(statement)
    assert _audit_count(audit_database, org_id) == 1


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_log SET reason = 'changed'", "DELETE FROM audit_log"]
)
def test_application_role_has_no_update_or_delete_grant(
    audit_database: AuditDatabase,
    statement: str,
) -> None:
    role = f"audit_app_{uuid4().hex[:12]}"
    privilege = statement.partition(" ")[0]
    audit_database.connection.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role))
    )
    try:
        assert audit_database.connection.execute(
            "SELECT has_table_privilege(%s, 'audit_log', %s)", (role, privilege)
        ).fetchone() == (False,)
        audit_database.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with pytest.raises(InsufficientPrivilege):
            audit_database.connection.execute(statement)
    finally:
        audit_database.connection.execute("RESET ROLE")
        audit_database.connection.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
        )


def test_rls_hides_another_tenants_audit_rows_even_from_owner(
    audit_database: AuditDatabase,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    connection = cast(AuditConnection, audit_database.connection)
    for org_id in (org_a, org_b):
        with correlation_context(f"request-{org_id}"), tenant_transaction(
            connection, Scope(org_id)
        ):
            _writer(audit_database, org_id).write(
                actor=AuditActor(ActorKind.SYSTEM),
                action="configuration.create",
                target_type="configuration",
                outcome=Outcome.SUCCESS,
            )

    application_owner = f"audit_owner_{uuid4().hex[:12]}"
    original_owner_row = audit_database.connection.execute(
        "SELECT current_user"
    ).fetchone()
    assert original_owner_row is not None
    original_owner = cast(str, original_owner_row[0])
    partition = monthly_partition(datetime.now(UTC))
    audit_database.connection.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(application_owner))
    )
    audit_database.connection.execute(
        sql.SQL("ALTER TABLE audit_log OWNER TO {}").format(
            sql.Identifier(application_owner)
        )
    )
    audit_database.connection.execute(
        sql.SQL("ALTER TABLE {} OWNER TO {}").format(
            sql.Identifier(partition.name), sql.Identifier(application_owner)
        )
    )
    try:
        audit_database.connection.execute(
            sql.SQL("SET ROLE {}").format(sql.Identifier(application_owner))
        )
        with tenant_transaction(connection, Scope(org_a)):
            rows = audit_database.connection.execute(
                "SELECT org_id FROM audit_log ORDER BY occurred_at"
            ).fetchall()
            direct_partition_rows = audit_database.connection.execute(
                sql.SQL("SELECT org_id FROM {} ORDER BY occurred_at").format(
                    sql.Identifier(partition.name)
                )
            ).fetchall()
        assert rows == [(org_a,)]
        assert direct_partition_rows == [(org_a,)]
    finally:
        audit_database.connection.execute("RESET ROLE")
        audit_database.connection.execute(
            sql.SQL("ALTER TABLE {} OWNER TO {}").format(
                sql.Identifier(partition.name), sql.Identifier(original_owner)
            )
        )
        audit_database.connection.execute(
            sql.SQL("ALTER TABLE audit_log OWNER TO {}").format(
                sql.Identifier(original_owner)
            )
        )
        audit_database.connection.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(application_owner))
        )


def test_older_month_detaches_without_losing_rows(audit_database: AuditDatabase) -> None:
    old_instant = datetime.now(UTC) - timedelta(days=91)
    old_partition = monthly_partition(old_instant)
    connection = cast(AuditConnection, audit_database.connection)
    org_id = uuid4()
    with tenant_transaction(connection, Scope(org_id)):
        assert ensure_monthly_partition(connection, old_instant) == old_partition
        audit_database.connection.execute(
            """
            INSERT INTO audit_log (
                org_id, occurred_at, actor_kind, action, target_type,
                outcome, correlation_id
            ) VALUES (%s, %s, 'system', 'archive.prepare', 'audit_partition',
                      'success', 'archive-correlation')
            """,
            (org_id, old_instant),
        )
        assert detach_monthly_partition(connection, old_instant) == old_partition

    inheritance = audit_database.connection.execute(
        "SELECT count(*) FROM pg_catalog.pg_inherits "
        "WHERE inhparent = 'audit_log'::regclass AND inhrelid = %s::regclass",
        (old_partition.name,),
    ).fetchone()
    retained = audit_database.connection.execute(
        sql.SQL(
            "SELECT correlation_id, column_default FROM {} "
            "CROSS JOIN information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = {} AND column_name = 'id'"
        ).format(
            sql.Identifier(old_partition.name), sql.Literal(old_partition.name)
        )
    ).fetchall()
    assert inheritance == (0,)
    assert retained == [("archive-correlation", None)]


@pytest.mark.parametrize(
    ("actor_id", "actor_kind", "outcome"),
    [
        (None, "user", "success"),
        (None, "unknown", "success"),
        (None, "system", "unknown"),
    ],
)
def test_database_rejects_invalid_actor_and_outcome_values(
    audit_database: AuditDatabase,
    actor_id: UUID | None,
    actor_kind: str,
    outcome: str,
) -> None:
    org_id = uuid4()
    connection = cast(AuditConnection, audit_database.connection)
    with pytest.raises(CheckViolation), tenant_transaction(connection, Scope(org_id)):
        now_row = audit_database.connection.execute("SELECT clock_timestamp()").fetchone()
        assert now_row is not None
        occurred_at = cast(datetime, now_row[0])
        ensure_monthly_partition(connection, occurred_at)
        audit_database.connection.execute(
            """
            INSERT INTO audit_log (
                org_id, occurred_at, actor_id, actor_kind, action, target_type,
                outcome, correlation_id
            ) VALUES (%s, %s, %s, %s, 'invalid.event', 'test', %s, 'invalid-event')
            """,
            (org_id, occurred_at, actor_id, actor_kind, outcome),
        )
    assert _audit_count(audit_database, org_id) == 0


def test_migration_model_revision_and_reversible_data_preservation(
    audit_database: AuditDatabase,
) -> None:
    columns = audit_database.connection.execute(
        "SELECT column_name, data_type, datetime_precision "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'audit_log' "
        "ORDER BY ordinal_position"
    ).fetchall()
    assert [row[0] for row in columns] == [
        "id",
        "org_id",
        "bu_id",
        "occurred_at",
        "actor_id",
        "actor_kind",
        "action",
        "target_type",
        "target_id",
        "outcome",
        "reason",
        "correlation_id",
        "before",
        "after",
        "ip_prefix",
        "user_agent",
    ]
    assert columns[3] == ("occurred_at", "timestamp with time zone", 6)
    assert all("local" not in cast(str, row[0]) for row in columns)
    assert audit_database.connection.execute(
        "SELECT relkind, relrowsecurity, relforcerowsecurity "
        "FROM pg_catalog.pg_class WHERE oid = 'audit_log'::regclass"
    ).fetchone() == ("p", True, True)
    primary_key_columns = audit_database.connection.execute(
        """
        SELECT array_agg(attributes.attname ORDER BY keys.ordinality)
        FROM pg_catalog.pg_constraint AS constraints
        CROSS JOIN LATERAL unnest(constraints.conkey) WITH ORDINALITY AS keys(attnum, ordinality)
        JOIN pg_catalog.pg_attribute AS attributes
          ON attributes.attrelid = constraints.conrelid AND attributes.attnum = keys.attnum
        WHERE constraints.conrelid = 'audit_log'::regclass AND constraints.contype = 'p'
        """
    ).fetchone()
    assert primary_key_columns == (["id", "occurred_at"],)
    trigger = audit_database.connection.execute(
        "SELECT pg_get_triggerdef(oid) FROM pg_catalog.pg_trigger "
        "WHERE tgrelid = 'audit_log'::regclass AND tgname = 'audit_log_immutable'"
    ).fetchone()
    assert trigger is not None
    trigger_definition = cast(str, trigger[0])
    assert "BEFORE" in trigger_definition
    assert "DELETE OR UPDATE" in trigger_definition
    assert "raise_append_only()" in trigger_definition
    assert audit_database.migration.revision == "20260825_0005"
    assert audit_database.migration.down_revision == "20260825_0004"

    sentinel = f"audit_sentinel_{uuid4().hex[:12]}"
    audit_database.connection.execute(
        sql.SQL("CREATE TABLE {} (value text NOT NULL)").format(sql.Identifier(sentinel))
    )
    audit_database.connection.execute(
        sql.SQL("INSERT INTO {} (value) VALUES ('preserved')").format(
            sql.Identifier(sentinel)
        )
    )
    try:
        audit_database.migration.downgrade(audit_database.connection)
        assert audit_database.connection.execute(
            "SELECT to_regclass('public.audit_log'), to_regprocedure('raise_append_only()')"
        ).fetchone() == (None, None)
        assert audit_database.connection.execute(
            sql.SQL("SELECT value FROM {}").format(sql.Identifier(sentinel))
        ).fetchone() == ("preserved",)
    finally:
        audit_database.connection.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(sentinel))
        )
