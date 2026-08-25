from __future__ import annotations

import importlib.util
import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

from flo.kernel.jobs import (
    JobProgress,
    JobQueue,
    JobRunner,
    PermanentJobError,
    TransientJobError,
    WorkerJobQueue,
    backoff_seconds,
)
from flo.kernel.jobs.queue import JobConnection
from flo.kernel.jobs.runner import RunnerConnection
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction

ROOT = Path(__file__).resolve().parents[4]
AUDIT_MIGRATION = ROOT / "migrations" / "20260825_0005_audit_log.py"
JOBS_MIGRATION = ROOT / "migrations" / "20260825_0007_jobs_outbox.py"


def _load_migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _database_url() -> tuple[str, bool]:
    configured = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    url = configured or "postgresql://flo:flo-local@127.0.0.1:5432/flo_test"
    return url.replace("postgresql+psycopg://", "postgresql://", 1), configured is not None


def _drop_objects(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    connection.execute("RESET ROLE")
    connection.execute("DROP TABLE IF EXISTS outbox")
    connection.execute("DROP TABLE IF EXISTS job")
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
def jobs_database() -> Iterator[tuple[psycopg.Connection[tuple[object, ...]], str]]:
    database_url, configured = _database_url()
    try:
        connection = psycopg.connect(database_url, autocommit=True)
    except psycopg.Error as error:
        if configured:
            pytest.fail(f"configured Postgres is unavailable: {type(error).__name__}")
        pytest.skip("local Postgres is unavailable; run the repository stack")
        raise
    _drop_objects(connection)
    _load_migration(AUDIT_MIGRATION, "job_audit_migration").upgrade(connection)
    _load_migration(JOBS_MIGRATION, "jobs_migration").upgrade(connection)
    try:
        yield connection, database_url
    finally:
        _drop_objects(connection)
        connection.close()


def _enqueue(
    connection: psycopg.Connection[tuple[object, ...]],
    org_id: UUID,
    *,
    kind: str = "test.execute",
    correlation_id: str = "originating-request",
) -> UUID:
    with (
        correlation_context(correlation_id),
        tenant_transaction(cast(JobConnection, connection), Scope(org_id)),
    ):
        return (
            JobQueue(cast(JobConnection, connection), Scope(org_id))
            .enqueue(kind, {"record": str(uuid4())})
            .id
        )


def test_backoff_uses_exact_bases_and_positive_bounded_jitter() -> None:
    bases = (1, 4, 16, 64, 256)
    assert [backoff_seconds(attempt, lambda: 0.0) for attempt in range(1, 6)] == list(bases)
    jittered = [backoff_seconds(attempt, lambda: 0.999) for attempt in range(1, 6)]
    assert all(base < delay < base * 1.25 for base, delay in zip(bases, jittered, strict=True))


@pytest.mark.parametrize("attempt", (0, 6))
def test_backoff_rejects_an_attempt_outside_the_closed_schedule(attempt: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 5"):
        backoff_seconds(attempt)


def test_backoff_rejects_an_invalid_random_source() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\)"):
        backoff_seconds(1, lambda: 1.0)


def test_one_hundred_jobs_are_each_executed_once_by_four_workers(
    jobs_database: tuple[psycopg.Connection[tuple[object, ...]], str],
) -> None:
    connection, database_url = jobs_database
    org_id = uuid4()
    expected = {
        _enqueue(connection, org_id, correlation_id=f"request-{index}") for index in range(100)
    }
    executed: list[UUID] = []
    executed_lock = threading.Lock()

    def worker() -> None:
        worker_connection = psycopg.connect(database_url, autocommit=True)
        try:

            def execute(job: object) -> None:
                job_id = cast(UUID, getattr(job, "id"))
                with executed_lock:
                    executed.append(job_id)

            JobRunner(
                cast(RunnerConnection, worker_connection),
                {"test.execute": execute},
                random_fraction=lambda: 0.0,
            ).run()
        finally:
            worker_connection.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker) for _ in range(4)]
        for future in futures:
            future.result()

    assert len(executed) == 100
    assert set(executed) == expected
    assert len(set(executed)) == len(executed)
    assert connection.execute("SELECT state, count(*) FROM job GROUP BY state").fetchall() == [
        ("done", 100)
    ]


def test_claim_skips_a_job_locked_by_an_uncommitted_worker(
    jobs_database: tuple[psycopg.Connection[tuple[object, ...]], str],
) -> None:
    connection, database_url = jobs_database
    org_id = uuid4()
    first_id = _enqueue(connection, org_id, correlation_id="first-request")
    second_id = _enqueue(connection, org_id, correlation_id="second-request")
    connection.execute(
        "UPDATE job SET run_after = '2000-01-01 00:00:00+00' WHERE id IN (%s, %s)",
        (first_id, second_id),
    )
    ordered_ids = [
        cast(UUID, row[0])
        for row in connection.execute(
            "SELECT id FROM job WHERE id IN (%s, %s) ORDER BY run_after, id",
            (first_id, second_id),
        ).fetchall()
    ]

    worker_a = psycopg.connect(database_url)
    worker_b = psycopg.connect(database_url)
    try:
        first = WorkerJobQueue(cast(JobConnection, worker_a)).claim()
        assert first is not None and first.id == ordered_ids[0]

        worker_b.execute("SET LOCAL statement_timeout = '250ms'")
        second = WorkerJobQueue(cast(JobConnection, worker_b)).claim()

        assert second is not None and second.id == ordered_ids[1]
    finally:
        worker_b.rollback()
        worker_a.rollback()
        worker_b.close()
        worker_a.close()


def test_fifth_transient_failure_is_dead_and_visible_with_origin_correlation(
    jobs_database: tuple[psycopg.Connection[tuple[object, ...]], str],
) -> None:
    connection, _ = jobs_database
    org_id = uuid4()
    job_id = _enqueue(connection, org_id, correlation_id="request-five-failures")

    def fail(_: object) -> None:
        raise TransientJobError("temporary test dependency failure")

    runner = JobRunner(
        cast(RunnerConnection, connection),
        {"test.execute": fail},
        random_fraction=lambda: 0.0,
    )
    for attempt in range(1, 6):
        summary = runner.run()
        if attempt < 5:
            assert summary.retried == 1
            connection.execute(
                "UPDATE job SET run_after = CURRENT_TIMESTAMP WHERE id = %s", (job_id,)
            )
        else:
            assert summary.dead == 1

    row = connection.execute(
        "SELECT state, attempts, last_error FROM job WHERE id = %s", (job_id,)
    ).fetchone()
    assert row == ("dead", 5, "temporary test dependency failure")
    with tenant_transaction(cast(JobConnection, connection), Scope(org_id)):
        dead = JobQueue(cast(JobConnection, connection), Scope(org_id)).list_dead()
        correlations = connection.execute(
            "SELECT action, correlation_id FROM audit_log "
            "WHERE org_id = %s AND target_id = %s ORDER BY occurred_at",
            (org_id, job_id),
        ).fetchall()
    assert [job.id for job in dead] == [job_id]
    assert correlations == [
        ("job.retry", "request-five-failures"),
        ("job.retry", "request-five-failures"),
        ("job.retry", "request-five-failures"),
        ("job.retry", "request-five-failures"),
        ("job.dead", "request-five-failures"),
    ]


def test_validation_error_goes_dead_on_first_attempt_without_retry(
    jobs_database: tuple[psycopg.Connection[tuple[object, ...]], str],
) -> None:
    connection, _ = jobs_database
    org_id = uuid4()
    job_id = _enqueue(connection, org_id)

    def invalid(_: object) -> None:
        raise PermanentJobError("validation failed")

    summary = JobRunner(
        cast(RunnerConnection, connection),
        {"test.execute": invalid},
        random_fraction=lambda: 0.0,
    ).run()

    assert summary.dead == 1
    assert summary.retried == 0
    assert connection.execute(
        "SELECT state, attempts, last_error FROM job WHERE id = %s", (job_id,)
    ).fetchone() == ("dead", 1, "validation failed")


def test_missing_job_handler_is_not_silently_dropped(
    jobs_database: tuple[psycopg.Connection[tuple[object, ...]], str],
) -> None:
    connection, _ = jobs_database
    job_id = _enqueue(connection, uuid4(), kind="missing.handler")

    summary = JobRunner(
        cast(RunnerConnection, connection),
        {},
        random_fraction=lambda: 0.0,
    ).run()

    assert summary.dead == 1
    assert connection.execute("SELECT last_error FROM job WHERE id = %s", (job_id,)).fetchone() == (
        "job kind has no registered handler",
    )


def test_progress_contract_is_visible_and_rejects_invalid_values(
    jobs_database: tuple[psycopg.Connection[tuple[object, ...]], str],
) -> None:
    connection, _ = jobs_database
    org_id = uuid4()
    job_id = _enqueue(connection, org_id)
    with connection.transaction():
        claimed = WorkerJobQueue(cast(JobConnection, connection)).claim()
    assert claimed is not None and claimed.id == job_id
    with tenant_transaction(cast(JobConnection, connection), Scope(org_id)):
        JobQueue(cast(JobConnection, connection), Scope(org_id)).update_progress(
            job_id, JobProgress(current=7, total=10, message="Validated rows")
        )
    assert connection.execute("SELECT progress FROM job WHERE id = %s", (job_id,)).fetchone() == (
        {"current": 7, "total": 10, "message": "Validated rows"},
    )
    with pytest.raises(ValueError, match="0 <= current <= total"):
        JobProgress(current=11, total=10, message="invalid")
    with pytest.raises(ValueError, match="message must be non-empty"):
        JobProgress(current=0, total=0, message="")


def test_job_enqueue_requires_correlation_and_closed_retry_bounds(
    jobs_database: tuple[psycopg.Connection[tuple[object, ...]], str],
) -> None:
    connection, _ = jobs_database
    queue = JobQueue(cast(JobConnection, connection), Scope(uuid4()))
    with pytest.raises(RuntimeError, match="correlation"):
        queue.enqueue("test.execute", {})
    with correlation_context("request-invalid"):
        with pytest.raises(ValueError, match="kind"):
            queue.enqueue("", {})
        with pytest.raises(ValueError, match="between 1 and 5"):
            queue.enqueue("test.execute", {}, max_attempts=6)


def test_worker_scope_is_transaction_local_and_application_reads_stay_tenant_scoped(
    jobs_database: tuple[psycopg.Connection[tuple[object, ...]], str],
) -> None:
    connection, _ = jobs_database
    org_a, org_b = uuid4(), uuid4()
    _enqueue(connection, org_a, correlation_id="tenant-a-job")
    _enqueue(connection, org_b, correlation_id="tenant-b-job")
    role = f"job_application_{uuid4().hex[:12]}"
    connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    connection.execute(
        sql.SQL("GRANT SELECT, UPDATE ON job, outbox TO {}").format(sql.Identifier(role))
    )
    try:
        connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with connection.transaction():
            connection.execute(sql.SQL("SET LOCAL app.org_id = {}").format(sql.Literal(str(org_a))))
            assert connection.execute("SELECT org_id FROM job").fetchall() == [(org_a,)]

        with connection.transaction():
            claimed = WorkerJobQueue(cast(JobConnection, connection)).claim()
            assert claimed is not None
            assert set(connection.execute("SELECT org_id FROM job").fetchall()) == {
                (org_a,),
                (org_b,),
            }

        with connection.transaction():
            connection.execute(sql.SQL("SET LOCAL app.org_id = {}").format(sql.Literal(str(org_a))))
            assert connection.execute("SELECT org_id FROM job").fetchall() == [(org_a,)]
    finally:
        connection.execute("RESET ROLE")
        connection.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON job, outbox FROM {}").format(sql.Identifier(role))
        )
        connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def test_migration_revision_rls_down_and_unrelated_data_preservation(
    jobs_database: tuple[psycopg.Connection[tuple[object, ...]], str],
) -> None:
    connection, _ = jobs_database
    migration = _load_migration(JOBS_MIGRATION, "jobs_migration_contract")
    assert migration.revision == "20260825_0007"
    assert migration.down_revision == "20260825_0006"
    sentinel = f"migration_sentinel_{uuid4().hex[:12]}"
    connection.execute(
        sql.SQL("CREATE TABLE {} (value text NOT NULL)").format(sql.Identifier(sentinel))
    )
    connection.execute(
        sql.SQL("INSERT INTO {} VALUES ('preserved')").format(sql.Identifier(sentinel))
    )
    try:
        for table in ("job", "outbox"):
            assert connection.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                (table,),
            ).fetchone() == (True, True)
        migration.downgrade(connection)
        assert connection.execute("SELECT to_regclass('public.job')").fetchone() == (None,)
        assert connection.execute("SELECT to_regclass('public.outbox')").fetchone() == (None,)
        assert connection.execute(
            sql.SQL("SELECT value FROM {}").format(sql.Identifier(sentinel))
        ).fetchone() == ("preserved",)
        migration.upgrade(connection)
    finally:
        connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(sentinel)))
