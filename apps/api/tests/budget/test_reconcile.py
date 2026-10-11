"""BUD-013/OBS-004: real snapshots, immutable reports and origin-only repeatable triggers."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from psycopg import sql

from flo.api.internal import get_internal_settings
from flo.api.internal import router as internal_router
from flo.api.origin_auth import get_origin_settings
from flo.kernel.errors import install_problem_details
from flo.kernel.errors.handler import CorrelationIdMiddleware
from flo.kernel.idempotency import install_idempotency
from flo.kernel.jobs import JobQueue, JobRunner
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget import reconcile
from tests.authz.conftest import ROOT, load_migration
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity
from tests.budget.test_allocation import allocation_db as allocation_db
from tests.budget.test_allocation import app, project
from tests.budget.test_concurrency import connect
from tests.budget.test_post_entry import post
from tests.budget.test_post_entry import posting_db as posting_db
from tests.isolation.conftest import tenant_database as tenant_database
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import body, service, settings, unit
from tests.projects.conftest import project_db as project_db

MIGRATION = load_migration(
    ROOT / "migrations/20261008_0026_budget_reconcile.py", "reconcile_migration"
)
JOBS = load_migration(ROOT / "migrations/20260825_0007_jobs_outbox.py", "reconcile_jobs")


@pytest.fixture
def reconcile_db(allocation_db):
    db = allocation_db
    MIGRATION.upgrade(db.connection)
    grant_permissions(db, db.actor_id, "ledger.read")
    for org in (db.org_a, db.org_b):
        member = insert_identity(db, "reconcile-member")
        db.connection.execute(
            "INSERT INTO identity_membership(identity_id,org_id) VALUES (%s,%s)",
            (member, org),
        )
    try:
        yield db
    finally:
        db.connection.execute("RESET ROLE")
        MIGRATION.downgrade(db.connection)


def snapshot(db):
    # Binary COPY compares every column, including balance version and timestamps.
    def contents(table):
        with db.connection.cursor().copy(
            f"COPY (SELECT * FROM {table} ORDER BY 1) TO STDOUT (FORMAT BINARY)"
        ) as copy:
            return b"".join(bytes(chunk) for chunk in copy)

    return contents("project_balance"), contents("ledger_entry")


def run(db, org=None):
    return reconcile.reconcile_org(db.connection, Scope(org or db.org_a))


def record(db, run_id):
    return db.connection.execute(
        "SELECT status,projects_checked,drift_rows,error_class FROM reconcile_run WHERE id=%s",
        (run_id,),
    ).fetchone()


def tamper(db, row, amount="7"):
    db.connection.execute(
        "UPDATE project_balance SET allocated=allocated+%s WHERE project_id=%s",
        (Decimal(amount), row.id),
    )


def send(db, path, params=None, viewer=None, method="GET", headers=None):
    if path.startswith("/internal/") and not (headers or {}).get("Cookie"):
        result = FastAPI()
        install_problem_details(result)

        @contextmanager
        def factory():
            yield db.connection

        install_idempotency(result, factory)
        result.add_middleware(CorrelationIdMiddleware)
    else:
        result = app(db, viewer)
    result.include_router(internal_router)
    config = settings(db).model_copy(update={"origin_shared_secret": None})
    # A synthetic test token, never an operator credential.
    from pydantic import SecretStr

    config = config.model_copy(update={"origin_shared_secret": SecretStr("test-origin")})
    result.dependency_overrides[get_origin_settings] = lambda: config
    result.dependency_overrides[get_internal_settings] = lambda: config

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=result), base_url="https://testserver"
        ) as client:
            return await client.request(method, path, params=params, headers=headers or {})

    return asyncio.run(request())


def test_clean_and_planted_drift_repeat_without_repair(reconcile_db, caplog):
    db = reconcile_db
    row = project(db)
    post(db, row)
    before = snapshot(db)
    clean = run(db)
    assert record(db, clean) == ("ok", 1, 0, None)
    assert snapshot(db) == before
    tamper(db, row)
    before = snapshot(db)
    with caplog.at_level(logging.ERROR):
        first, second = run(db), run(db)
    assert first != second
    assert record(db, first) == record(db, second) == ("drift", 1, 1, None)
    assert snapshot(db) == before
    rows = db.connection.execute(
        "SELECT run_id,project_id,bucket,currency,ledger_total,balance_total,difference FROM "
        "balance_drift_report ORDER BY run_id"
    ).fetchall()
    assert len(rows) == 2
    assert {r[0] for r in rows} == {first, second}
    assert all(
        r[1:] == (row.id, "allocated", "USD", Decimal("100"), Decimal("107"), Decimal("7"))
        for r in rows
    )
    logs = [
        r for r in caplog.records if getattr(r, "business_monitor", None) == "unreconciled_balance"
    ]
    assert len(logs) == 2
    assert all(
        r.org_id == str(db.org_a) and r.projects == 1 and r.difference == "7.0000" for r in logs
    )
    assert "100.0000" not in caplog.text and "107.0000" not in caplog.text
    assert "test-origin" not in caplog.text


def test_difference_decimal_sign():
    assert reconcile.difference(Decimal("1.0001"), Decimal("2.0002")) == Decimal("-1.0001")


def test_foreign_reconciliation_drift(reconcile_db):
    db = reconcile_db
    local = project(db)
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    tamper(db, local, "7")
    tamper(db, foreign, "9")
    local_run, foreign_run = run(db), run(db, db.org_b)
    path = "/api/v1/budget/reconciliation/drift"
    result = send(db, path, {"run_id": str(local_run)})
    assert result.status_code == 200, result.text
    assert [r["project_id"] for r in result.json()["entries"]] == [str(local.id)]
    assert send(db, path, {"run_id": str(foreign_run)}).status_code == 404
    with tenant_transaction(db.connection, Scope(db.org_b)):
        page = reconcile.reconciliation_drift(db.connection, Scope(db.org_b), foreign_run, None, 50)
        assert [r.project_id for r in page.entries] == [foreign.id]


def test_reconciliation_status_tenant_isolation(reconcile_db):
    db = reconcile_db
    project(db)
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    tamper(db, foreign)
    run(db, db.org_b)
    path = "/api/v1/budget/reconciliation/status"
    assert send(db, path).json() == {"last_run_at": None, "status": None, "drift_rows": 0}
    run(db)
    result = send(db, path)
    assert result.status_code == 200
    assert result.json()["status"] == "ok" and result.json()["drift_rows"] == 0
    assert result.json()["last_run_at"] is not None


@pytest.mark.parametrize("path", ["status", "drift"])
def test_read_permission_guard(reconcile_db, path):
    db = reconcile_db
    run_id = run(db)
    viewer = insert_identity(db, "reconcile-no-permission")
    url = "/api/v1/budget/reconciliation/" + path
    params = {"run_id": str(run_id)}
    assert send(db, url, params, viewer).status_code == 403
    grant_permissions(db, viewer, "project.read")
    assert send(db, url, params, viewer).status_code == 403
    # Role names are per viewer; add permission to its existing role.
    db.connection.execute(
        "INSERT INTO role_permission(org_id,role_id,permission_code) SELECT "
        "org_id,id,'ledger.read' FROM role WHERE code=%s",
        (f"role-{str(viewer)[:8]}",),
    )
    assert send(db, url, params, viewer).status_code == 200


@pytest.mark.parametrize("table", ["reconcile_run", "balance_drift_report"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
def test_append_only_planted_mutations(reconcile_db, table, operation):
    db = reconcile_db
    tamper(db, project(db))
    run(db)
    statement = (
        f"UPDATE {table} SET id=id"
        if operation == "UPDATE"
        else f"{operation} {table} CASCADE"
        if operation == "TRUNCATE"
        else f"DELETE FROM {table}"
    )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        db.connection.execute(statement)


def test_failed_org_rolls_back_and_next_org_runs(reconcile_db, monkeypatch):
    db = reconcile_db
    tamper(db, project(db))
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    tamper(db, foreign)
    original = reconcile._record_run

    def fail(repo, *args, **kwargs):
        if repo.scope.org_id == db.org_a and args[4] != "failed":
            original(repo, *args, **kwargs)
            raise RuntimeError("private diagnostic must never be recorded")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(reconcile, "_record_run", fail)
    before = snapshot(db)
    assert reconcile.reconcile_all(db.connection) == 2
    assert db.connection.execute(
        "SELECT status,error_class FROM reconcile_run WHERE org_id=%s", (db.org_a,)
    ).fetchall() == [("failed", "RuntimeError")]
    assert db.connection.execute(
        "SELECT status FROM reconcile_run WHERE org_id=%s", (db.org_b,)
    ).fetchall() == [("drift",)]
    assert db.connection.execute(
        "SELECT count(*) FROM balance_drift_report WHERE org_id=%s", (db.org_a,)
    ).fetchone() == (0,)
    assert snapshot(db) == before


@pytest.mark.parametrize("same_key", [False, True])
def test_internal_trigger_repeat_leaves_non_reports_unchanged(reconcile_db, same_key):
    db = reconcile_db
    row = project(db)
    post(db, row)
    tamper(db, row)
    before = snapshot(db)
    # Compare all existing tables, including sessions, audit, identity and role grants.
    tables = [
        r[0]
        for r in db.connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN "
            "('reconcile_run','balance_drift_report') ORDER BY tablename"
        ).fetchall()
    ]

    def all_rows():
        rows = {}
        for table in tables:
            with db.connection.cursor().copy(
                sql.SQL(
                    "COPY (SELECT * FROM {} t ORDER BY row_to_json(t)::text) "
                    "TO STDOUT (FORMAT BINARY)"
                ).format(sql.Identifier(table))
            ) as copy:
                rows[table] = b"".join(bytes(chunk) for chunk in copy)
        return rows

    existing = all_rows()
    key = uuid4().hex
    for _ in range(2):
        response = send(
            db,
            "/internal/jobs/budget-reconcile",
            method="POST",
            headers={
                "X-FLO-Origin-Secret": "test-origin",
                "Idempotency-Key": key if same_key else uuid4().hex,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"organizations": 2}
    assert snapshot(db) == before and all_rows() == existing
    assert db.connection.execute(
        "SELECT org_id,count(*) FROM reconcile_run GROUP BY org_id"
    ).fetchall() in ([(db.org_a, 2), (db.org_b, 2)], [(db.org_b, 2), (db.org_a, 2)])


def test_internal_trigger_rejects_tenant_session(reconcile_db):
    db = reconcile_db
    # The cookie case uses app(db), which installs a valid tenant session.
    # Even that session cannot authorize an origin-only route.
    for headers in (
        {"Idempotency-Key": uuid4().hex},
        {"Idempotency-Key": uuid4().hex, "X-FLO-Origin-Secret": "incorrect"},
        {"Idempotency-Key": uuid4().hex, "Cookie": "__Host-flo-session=test-session"},
    ):
        result = send(db, "/internal/jobs/budget-reconcile", method="POST", headers=headers)
        assert result.status_code == 404
    assert db.connection.execute("SELECT count(*) FROM reconcile_run").fetchone() == (0,)


def test_internal_trigger_requires_key(reconcile_db):
    result = send(
        reconcile_db,
        "/internal/jobs/budget-reconcile",
        method="POST",
        headers={"X-FLO-Origin-Secret": "test-origin"},
    )
    assert result.status_code == 400
    assert reconcile_db.connection.execute("SELECT count(*) FROM reconcile_run").fetchone() == (0,)


def test_drift_pagination_and_input_guards(reconcile_db):
    db = reconcile_db
    row = project(db)
    tamper(db, row)
    db.connection.execute(
        "UPDATE project_balance SET reserved=2,committed=3,actual=4 WHERE project_id=%s", (row.id,)
    )
    run_id = run(db)
    path = "/api/v1/budget/reconciliation/drift"
    ids, cursor = [], None
    for _ in range(4):
        response = send(
            db,
            path,
            {"run_id": str(run_id), "page_size": 1, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        ids += [r["id"] for r in data["entries"]]
        cursor = data["next_cursor"]
    assert len(set(ids)) == 4 and cursor is None
    for params in ({"page_size": 0}, {"page_size": 51}, {"cursor": "bad"}, {"run_id": "bad"}):
        assert send(db, path, {"run_id": str(run_id), **params}).status_code == 422
    assert send(db, path, {"run_id": str(uuid4())}).status_code == 404


def test_concurrent_reconciliation_does_not_change_money(reconcile_db):
    db = reconcile_db
    row = project(db)
    post(db, row)
    tamper(db, row)
    before = snapshot(db)

    def attempt(_):
        with connect(db) as conn:
            return reconcile.reconcile_org(conn, Scope(db.org_a))

    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(attempt, range(2)))
    assert len(set(runs)) == 2
    assert all(record(db, r) == ("drift", 1, 1, None) for r in runs)
    assert snapshot(db) == before


def test_migration_down_up_preserves_money(reconcile_db):
    db = reconcile_db
    row = project(db)
    post(db, row)
    before = snapshot(db)
    run(db)
    MIGRATION.downgrade(db.connection)
    assert snapshot(db) == before
    MIGRATION.upgrade(db.connection)
    assert snapshot(db) == before
    assert record(db, run(db)) == ("ok", 1, 0, None)


def test_report_rls_guard(reconcile_db):
    db = reconcile_db
    tamper(db, project(db))
    local_run = run(db)
    foreign_run = run(db, db.org_b)
    role = "reconcile_reader_" + uuid4().hex[:12]
    db.connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    db.connection.execute(
        sql.SQL("GRANT SELECT,INSERT ON reconcile_run,balance_drift_report TO {}").format(
            sql.Identifier(role)
        )
    )
    try:
        with db.connection.transaction():
            db.connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            with tenant_transaction(db.connection, Scope(db.org_a)):
                assert db.connection.execute("SELECT id FROM reconcile_run").fetchall() == [
                    (local_run,)
                ]
                assert db.connection.execute(
                    "SELECT count(*) FROM balance_drift_report"
                ).fetchone() == (1,)
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with db.connection.transaction():
                        db.connection.execute(
                            "INSERT INTO "
                            "reconcile_run(id,org_id,started_at,finished_at,projects_checked,"
                            "drift_rows,status) VALUES (%s,%s,now(),now(),0,0,'ok')",
                            (uuid4(), db.org_b),
                        )
                assert (
                    db.connection.execute(
                        "SELECT id FROM reconcile_run WHERE id=%s", (foreign_run,)
                    ).fetchall()
                    == []
                )
    finally:
        db.connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        db.connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_registered_queue_handler_reports_without_repair(reconcile_db):
    db = reconcile_db
    JOBS.upgrade(db.connection)
    try:
        row = project(db)
        tamper(db, row)
        before = snapshot(db)
        with tenant_transaction(db.connection, Scope(db.org_a)):
            JobQueue(db.connection, Scope(db.org_a)).enqueue("budget-reconcile", {})
        runner = JobRunner(
            db.connection,
            {"budget-reconcile": reconcile.job_handler(lambda: connect(db))},
            random_fraction=lambda: 0,
        )
        assert runner.run().done == 1
        assert snapshot(db) == before
        assert db.connection.execute("SELECT status FROM reconcile_run").fetchall() == [("drift",)]
    finally:
        JOBS.downgrade(db.connection)


def test_report_constraints_reject_planted_violations(reconcile_db):
    db = reconcile_db
    row = project(db)
    tamper(db, row)
    run_id = run(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.connection.execute(
            "INSERT INTO reconcile_run(id,org_id,started_at,finished_at,projects_checked,"
            "drift_rows,status) VALUES (%s,%s,now(),now(),0,0,'invalid')",
            (uuid4(), db.org_a),
        )
    template = (
        "INSERT INTO balance_drift_report "
        "SELECT %s,%s,%s,%s,bucket,currency,ledger_total,balance_total,difference,detected_at "
        "FROM balance_drift_report LIMIT 1"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.connection.execute(template, (uuid4(), db.org_a, run_id, row.id))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.connection.execute(template, (uuid4(), db.org_b, uuid4(), row.id))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.connection.execute(template, (uuid4(), db.org_a, run_id, uuid4()))


def test_recompute_uses_writable_repeatable_read(reconcile_db, monkeypatch):
    db = reconcile_db
    original = reconcile._snapshot
    observed = []

    def inspect(repo):
        observed.append(
            repo.execute(
                "SELECT current_setting('transaction_isolation'),"
                "current_setting('transaction_read_only')"
            ).fetchone()
        )
        return original(repo)

    monkeypatch.setattr(reconcile, "_snapshot", inspect)
    assert record(db, run(db)) == ("ok", 0, 0, None)
    assert observed == [("repeatable read", "off")]


def test_enumeration_only_distinct_organizations_with_members(reconcile_db, monkeypatch):
    db = reconcile_db
    db.connection.execute("DELETE FROM identity_membership WHERE org_id=%s", (db.org_b,))
    member = insert_identity(db, "duplicate-org-member")
    db.connection.execute(
        "INSERT INTO identity_membership(identity_id,org_id) VALUES (%s,%s)", (member, db.org_a)
    )
    enumerated = []
    monkeypatch.setattr(
        reconcile, "reconcile_org", lambda conn, scope: enumerated.append(scope.org_id)
    )
    assert reconcile.reconcile_all(db.connection) == 1
    assert enumerated == [db.org_a]
    assert db.connection.execute("SELECT id FROM organization WHERE id=%s", (db.org_b,)).fetchone()


def test_enumeration_and_reconcile_without_rls_bypass(reconcile_db):
    db = reconcile_db
    row = project(db)
    post(db, row)
    tamper(db, row)
    db.connection.execute("DELETE FROM identity_membership WHERE org_id=%s", (db.org_b,))
    role = "reconcile_worker_" + uuid4().hex[:12]
    identifier = sql.Identifier(role)
    db.connection.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(identifier)
    )
    db.connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(identifier))
    db.connection.execute(
        sql.SQL("GRANT SELECT ON identity_membership,project_balance,ledger_entry TO {}").format(
            identifier
        )
    )
    db.connection.execute(
        sql.SQL("GRANT SELECT,INSERT ON reconcile_run,balance_drift_report TO {}").format(
            identifier
        )
    )
    before = snapshot(db)
    try:
        # Fresh connection has no app.org_id; enumerate before tenant scope exists.
        with connect(db) as conn:
            conn.autocommit = True
            conn.execute(sql.SQL("SET ROLE {}").format(identifier))
            assert conn.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone() == (False, False)
            assert reconcile.reconcile_all(conn) == 1
            with tenant_transaction(conn, Scope(db.org_a)):
                assert conn.execute(
                    "SELECT status,projects_checked,drift_rows,error_class FROM reconcile_run"
                ).fetchall() == [("drift", 1, 1, None)]
                assert conn.execute(
                    "SELECT project_id,difference FROM balance_drift_report"
                ).fetchall() == [(row.id, Decimal("7"))]
            with tenant_transaction(conn, Scope(db.org_b)):
                assert conn.execute("SELECT id FROM reconcile_run").fetchall() == []
                assert conn.execute("SELECT id FROM balance_drift_report").fetchall() == []
        assert snapshot(db) == before
    finally:
        db.connection.execute(sql.SQL("DROP OWNED BY {}").format(identifier))
        db.connection.execute(sql.SQL("DROP ROLE {}").format(identifier))
