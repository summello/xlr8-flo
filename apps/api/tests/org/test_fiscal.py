from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timedelta
from threading import Event
from typing import cast
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from psycopg.errors import CheckViolation, ExclusionViolation, ForeignKeyViolation

from flo.api.fiscal import router
from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.org.fiscal import CalendarPut, FiscalService
from flo.modules.org.service import PeriodClosed, assert_postable, period_for, range_for
from tests.authz.conftest import ROOT, AuthorizationDatabase, load_migration
from tests.authz.test_effective_access import NOW, build_app, grant_permissions
from tests.authz.test_resolver import insert_identity

MIGRATION = ROOT / "migrations/20260826_0016_fiscal.py"
REASON = {"reason": "Correct late posting"}


@pytest.fixture
def fiscal(org_database: AuthorizationDatabase) -> Iterator[AuthorizationDatabase]:
    migration = load_migration(MIGRATION, "fiscal_test")
    migration.upgrade(org_database.connection)
    try:
        yield org_database
    finally:
        migration.downgrade(org_database.connection)


def service(db: AuthorizationDatabase, org: UUID | None = None) -> FiscalService:
    return FiscalService(db.connection, Scope(org or db.org_a), db.actor_id)


def admin(db: AuthorizationDatabase) -> None:
    grant_permissions(
        db, db.actor_id, "fiscal.manage", "fiscal.read", "fiscal.close", "fiscal.reopen"
    )


def app_for(
    db: AuthorizationDatabase, viewer: UUID | None = None, *, stale: bool = False
) -> FastAPI:
    from flo.kernel.identity import IdentityId

    app = build_app(
        db,
        IdentityId(viewer or db.actor_id),
        last_auth_at=NOW - timedelta(minutes=16) if stale else NOW,
    )
    app.include_router(router)
    return app


def request(
    db: AuthorizationDatabase,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    viewer: UUID | None = None,
    *,
    stale: bool = False,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(db, viewer, stale=stale)),
            base_url="https://testserver",
        ) as client:
            return await client.request(method, "/api/v1/fiscal" + path, json=body)

    return asyncio.run(send())


def test_read_does_not_generate_and_calendar_locks_on_generation(
    fiscal: AuthorizationDatabase,
) -> None:
    db = fiscal
    admin(db)
    assert request(db, "GET", "/periods?fiscal_year=2027").json() == []
    assert db.connection.execute("SELECT count(*) FROM fiscal_calendar").fetchone() == (0,)
    assert request(db, "PUT", "/calendar", {"start_month": 4}).status_code == 200
    assert request(db, "PUT", "/calendar", {"start_month": 4}).status_code == 200
    generated = request(db, "POST", "/years/2027:generate")
    assert generated.status_code == 200
    rows = generated.json()
    assert len(rows) == 12
    assert rows[0]["starts_on"] == "2026-04-01"
    assert rows[-1]["ends_on"] == "2027-03-31"
    assert request(db, "POST", "/years/2027:generate").json() == rows
    assert request(db, "GET", "/periods?fiscal_year=2027").json() == rows
    locked = request(db, "PUT", "/calendar", {"start_month": 1})
    assert locked.status_code == 409
    assert locked.json()["checks"]["problem"] == "calendar_locked"
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action = 'fiscal_calendar.set'"
    ).fetchone() == (1,)


def test_close_reopen_order_events_audit_and_step_up(fiscal: AuthorizationDatabase) -> None:
    db = fiscal
    admin(db)
    periods = service(db).ensure_year(2026)
    p1, p2, p3 = periods[:3]
    assert request(db, "POST", f"/periods/{p3.id}:close").status_code == 409
    for p in (p1, p2, p3):
        assert request(db, "POST", f"/periods/{p.id}:close").status_code == 200
    assert request(db, "POST", f"/periods/{p3.id}:close").status_code == 409
    assert request(db, "POST", f"/periods/{p2.id}:reopen", REASON).status_code == 409
    stale = request(db, "POST", f"/periods/{p3.id}:reopen", REASON, stale=True)
    assert stale.status_code == 403
    assert stale.json()["type"].endswith("/step-up-required")
    assert stale.headers["WWW-Authenticate"] == "step-up"
    assert request(db, "POST", f"/periods/{p3.id}:reopen", REASON).status_code == 200
    assert request(db, "POST", f"/periods/{p3.id}:reopen", REASON).status_code == 409
    assert db.connection.execute(
        "SELECT action, actor_id, reason FROM fiscal_period_event WHERE period_id = %s ORDER BY at",
        (p3.id,),
    ).fetchall() == [("close", db.actor_id, None), ("reopen", db.actor_id, REASON["reason"])]
    assert db.connection.execute(
        "SELECT action FROM audit_log WHERE target_id = %s ORDER BY occurred_at",
        (p3.id,),
    ).fetchall() == [("fiscal_period.close",), ("fiscal_period.reopen",)]
    assert service(db).get_period(p3.id).closed_by is None
    assert service(db).get_period(p3.id).closed_at is None


def test_cross_year_order_and_missing_predecessor(fiscal: AuthorizationDatabase) -> None:
    db = fiscal
    svc = service(db)
    current = svc.ensure_year(2027)
    # No generated preceding year, so the first period can close.
    svc.transition(current[0].id, "close")
    svc.transition(current[0].id, "reopen", REASON["reason"])
    previous = svc.ensure_year(2026)
    with pytest.raises(ProblemError) as blocked:
        svc.transition(current[0].id, "close")
    assert blocked.value.checks["problem"] == "period_order"
    for period in previous:
        svc.transition(period.id, "close")
    svc.transition(current[0].id, "close")
    with pytest.raises(ProblemError) as blocked:
        svc.transition(previous[-1].id, "reopen", REASON["reason"])
    assert blocked.value.checks["problem"] == "period_order"


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("PUT", "/calendar", {"start_month": 4}),
        ("GET", "/periods?fiscal_year=2026", None),
        ("POST", "/years/2026:generate", None),
        ("POST", "/periods/{id}:close", None),
        ("POST", "/periods/{id}:reopen", REASON),
    ],
)
@pytest.mark.parametrize("granted", [False, True])
def test_permissions(
    fiscal: AuthorizationDatabase,
    method: str,
    path: str,
    body: dict[str, object] | None,
    granted: bool,
) -> None:
    db = fiscal
    p = service(db).ensure_year(2026)[0]
    viewer = insert_identity(db, "fiscal-viewer")
    if granted:
        grant_permissions(db, viewer, "org.unit.read")
    response = request(db, method, path.format(id=p.id), body, viewer)
    assert response.status_code == (404 if "/periods/{id}" in path and not granted else 403)


@pytest.mark.parametrize("action", ["close", "reopen"])
def test_foreign_period_id(fiscal: AuthorizationDatabase, action: str) -> None:
    db = fiscal
    admin(db)
    foreign = service(db, db.org_b).ensure_year(2026)[0]
    response = request(
        db, "POST", f"/periods/{foreign.id}:{action}", REASON if action == "reopen" else None
    )
    assert response.status_code == 404


def test_collection_tenant_isolation(fiscal: AuthorizationDatabase) -> None:
    db = fiscal
    admin(db)
    foreign_service = service(db, db.org_b)
    foreign_service.set_calendar(CalendarPut(start_month=7))
    foreign = foreign_service.ensure_year(2026)
    assert request(db, "GET", "/periods?fiscal_year=2026").json() == []
    assert request(db, "PUT", "/calendar", {"start_month": 4}).status_code == 200
    own = request(db, "POST", "/years/2026:generate").json()
    assert len(own) == 12
    assert not {str(p.id) for p in foreign} & {p["id"] for p in own}
    assert own[0]["starts_on"] == "2025-04-01"
    assert foreign_service.start_month() == 7
    assert foreign_service.list_periods(2026) == foreign


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"reason": "short"},
        {"reason": "          "},
        {"reason": "   short    "},
        REASON | {"org_id": "other"},
    ],
)
def test_reopen_reason_guard(fiscal: AuthorizationDatabase, body: dict[str, object]) -> None:
    db = fiscal
    admin(db)
    p = service(db).ensure_year(2026)[0]
    service(db).transition(p.id, "close")
    assert request(db, "POST", f"/periods/{p.id}:reopen", body).status_code == 422
    assert service(db).get_period(p.id).status == "closed"


@pytest.mark.parametrize("month", [0, 13, True, "4", None])
def test_calendar_month_guard(fiscal: AuthorizationDatabase, month: object) -> None:
    db = fiscal
    admin(db)
    assert request(db, "PUT", "/calendar", {"start_month": month}).status_code == 422


def test_removed_setting_is_unknown(fiscal: AuthorizationDatabase) -> None:
    from .test_units import admin as org_admin
    from .test_units import request as org_request

    db = fiscal
    org_admin(db)
    response = org_request(db, "PUT", "/settings/fiscal_year_start_month", {"value": 4})
    assert response.status_code == 422
    assert response.json()["checks"]["problem"] == "unknown_key"


def test_postable_default_lazy_generation_closed_and_open(fiscal: AuthorizationDatabase) -> None:
    db = fiscal
    scope = Scope(db.org_a)
    with tenant_transaction(db.connection, scope):
        assert_postable(db.connection, scope, date(2026, 1, 1))
    periods = service(db).list_periods(2026)
    assert len(periods) == 12
    assert periods[0].starts_on == date(2026, 1, 1)
    service(db).transition(periods[0].id, "close")
    with pytest.raises(PeriodClosed) as closed:
        with tenant_transaction(db.connection, scope):
            assert_postable(db.connection, scope, date(2026, 1, 31))
    assert closed.value.period.id == periods[0].id
    with tenant_transaction(db.connection, scope):
        assert_postable(db.connection, scope, date(2026, 2, 1))
    assert period_for(db.connection, scope, date(2027, 12, 31)).period_no == 12
    with pytest.raises(RuntimeError, match="active caller transaction"):
        assert_postable(db.connection, scope, date(2026, 2, 1))


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("mtd", (date(2026, 8, 1), date(2026, 8, 20))),
        ("qtd", (date(2026, 7, 1), date(2026, 8, 20))),
        ("ytd", (date(2026, 4, 1), date(2026, 8, 20))),
        ("fiscal_year", (date(2026, 4, 1), date(2027, 3, 31))),
    ],
)
def test_fiscal_ranges(
    fiscal: AuthorizationDatabase, kind: str, expected: tuple[date, date]
) -> None:
    service(fiscal).set_calendar(CalendarPut(start_month=4))
    assert range_for(fiscal.connection, Scope(fiscal.org_a), kind, date(2026, 8, 20)) == expected


def test_concurrent_generation(fiscal: AuthorizationDatabase) -> None:
    db = fiscal
    ready = Event()

    def generate() -> list[UUID]:
        with psycopg.connect(
            db.connection.info.dsn, password=db.connection.info.password, autocommit=True
        ) as conn:
            with correlation_context("fiscal-concurrent"):
                ready.wait(timeout=5)
                return [
                    p.id
                    for p in FiscalService(conn, Scope(db.org_a), db.actor_id).ensure_year(2026)
                ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(generate), pool.submit(generate)
        ready.set()
        assert first.result(timeout=10) == second.result(timeout=10)
    assert db.connection.execute("SELECT count(*) FROM fiscal_period").fetchone() == (12,)


def test_close_waits_for_posting_transaction(fiscal: AuthorizationDatabase) -> None:
    db = fiscal
    period = service(db).ensure_year(2026)[0]
    posting = Event()
    release = Event()
    close_started = Event()
    close_pid: list[int] = []

    def post() -> None:
        with psycopg.connect(
            db.connection.info.dsn, password=db.connection.info.password, autocommit=True
        ) as conn:
            with correlation_context("posting"), tenant_transaction(conn, Scope(db.org_a)):
                assert_postable(conn, Scope(db.org_a), date(2026, 1, 1))
                posting.set()
                assert release.wait(timeout=10)

    def close() -> None:
        with psycopg.connect(
            db.connection.info.dsn, password=db.connection.info.password, autocommit=True
        ) as conn:
            with correlation_context("closing"):
                close_pid.append(conn.info.backend_pid)
                close_started.set()
                FiscalService(conn, Scope(db.org_a), db.actor_id).transition(period.id, "close")

    with ThreadPoolExecutor(max_workers=2) as pool:
        poster = pool.submit(post)
        assert posting.wait(timeout=5)
        closer = pool.submit(close)
        assert close_started.wait(timeout=5)
        # Observe the database wait rather than relying on the worker being slow.
        from time import monotonic, sleep

        try:
            deadline = monotonic() + 5
            while monotonic() < deadline:
                blocked = db.connection.execute(
                    "SELECT cardinality(pg_blocking_pids(%s))", (close_pid[0],)
                ).fetchone()
                if blocked is not None and blocked[0] > 0:
                    break
                sleep(0.01)
            else:
                pytest.fail("close did not wait for the posting transaction's period lock")
            assert not closer.done()
        finally:
            release.set()
        poster.result(timeout=5)
        closer.result(timeout=5)
    assert service(db).get_period(period.id).status == "closed"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE fiscal_period SET starts_on = starts_on + 1 WHERE id = %s",
        "UPDATE fiscal_period SET ends_on = ends_on - 1 WHERE id = %s",
        "UPDATE fiscal_period SET fiscal_year = fiscal_year + 1 WHERE id = %s",
        "UPDATE fiscal_period SET period_no = 2 WHERE id = %s",
        "UPDATE fiscal_period SET org_id = gen_random_uuid() WHERE id = %s",
        "UPDATE fiscal_period SET id = gen_random_uuid() WHERE id = %s",
        "DELETE FROM fiscal_period WHERE id = %s",
    ],
)
def test_period_immutable_guard(fiscal: AuthorizationDatabase, statement: str) -> None:
    db = fiscal
    p = service(db).ensure_year(2026)[0]
    with pytest.raises(CheckViolation):
        db.connection.execute(statement, (p.id,))


@pytest.mark.parametrize("method", ["UPDATE", "DELETE"])
def test_event_append_only_guard(fiscal: AuthorizationDatabase, method: str) -> None:
    db = fiscal
    p = service(db).ensure_year(2026)[0]
    service(db).transition(p.id, "close")
    statement = (
        "UPDATE fiscal_period_event SET reason = 'altered'"
        if method == "UPDATE"
        else "DELETE FROM fiscal_period_event"
    )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        db.connection.execute(statement)


def test_audit_failure_rolls_back_transition_and_event(
    fiscal: AuthorizationDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = fiscal
    p = service(db).ensure_year(2026)[0]

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("planted audit failure")

    monkeypatch.setattr(FiscalService, "_audit", fail)
    with pytest.raises(RuntimeError, match="planted audit failure"):
        service(db).transition(p.id, "close")
    assert service(db).get_period(p.id).status == "open"
    assert db.connection.execute("SELECT count(*) FROM fiscal_period_event").fetchone() == (0,)


def test_migration_preserves_existing_data(org_database: AuthorizationDatabase) -> None:
    db = org_database
    tables = ("organization", "org_unit", "identity", "permission", "audit_log")
    before = {table: db.connection.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    migration = load_migration(MIGRATION, "fiscal_preservation")
    migration.upgrade(db.connection)
    try:
        assert migration.down_revision == "20260826_0015"
        assert db.connection.execute(
            "SELECT code FROM permission WHERE code LIKE 'fiscal.%' ORDER BY code"
        ).fetchall() == [
            ("fiscal.close",),
            ("fiscal.manage",),
            ("fiscal.read",),
            ("fiscal.reopen",),
        ]
    finally:
        migration.downgrade(db.connection)
    for table in tables:
        assert db.connection.execute(f"SELECT * FROM {table}").fetchall() == before[table]
    for table in ("fiscal_calendar", "fiscal_period", "fiscal_period_event"):
        assert db.connection.execute("SELECT to_regclass(%s)", (table,)).fetchone() == (None,)
    migration.upgrade(db.connection)
    migration.downgrade(db.connection)


def test_posts_require_keys_and_replay_single_effect(fiscal: AuthorizationDatabase) -> None:
    from flo.kernel.idempotency import install_idempotency
    from flo.kernel.idempotency.store import IdempotencyConnection

    db = fiscal
    admin(db)
    migration = load_migration(
        ROOT / "migrations/20260825_0003_idempotency.py", "fiscal_idempotency"
    )
    headers = load_migration(
        ROOT / "migrations/20260825_0009_idempotency_response_headers.py", "fiscal_headers"
    )
    db.connection.execute("DROP TABLE IF EXISTS idempotency_key")
    migration.upgrade(db.connection)
    headers.upgrade(db.connection)
    app = app_for(db)

    @contextmanager
    def connection_factory() -> Iterator[IdempotencyConnection]:
        yield cast(IdempotencyConnection, db.connection)

    install_idempotency(app, connection_factory)
    # Install session/scope outside idempotency, matching production middleware order.
    from fastapi import Request
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.responses import Response

    from flo.kernel.tenancy.context import use_scope
    from tests.authz.test_effective_access import session_for

    @app.middleware("http")
    async def outer_scope(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.session = session_for(db.actor_id)
        with use_scope(Scope(db.org_a)):
            return await call_next(request)

    from flo.kernel.errors.handler import CorrelationIdMiddleware

    app.add_middleware(CorrelationIdMiddleware)

    async def send() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            base = "/api/v1/fiscal"
            missing = await client.post(base + "/years/2026:generate")
            assert missing.status_code == 400
            generated = await client.post(
                base + "/years/2026:generate", headers={"Idempotency-Key": "generate"}
            )
            replay = await client.post(
                base + "/years/2026:generate", headers={"Idempotency-Key": "generate"}
            )
            assert generated.status_code == replay.status_code == 200
            assert generated.json() == replay.json()
            p = generated.json()[0]["id"]
            for action in ("close", "reopen"):
                body = REASON if action == "reopen" else None
                path = f"{base}/periods/{p}:{action}"
                assert (await client.post(path, json=body)).status_code == 400
                first = await client.post(path, json=body, headers={"Idempotency-Key": action})
                second = await client.post(path, json=body, headers={"Idempotency-Key": action})
                assert first.status_code == second.status_code == 200
                assert first.json() == second.json()
            conflict = await client.post(
                f"{base}/periods/{p}:reopen",
                json={"reason": "A different reason"},
                headers={"Idempotency-Key": "reopen"},
            )
            assert conflict.status_code == 422
            assert conflict.json()["type"].endswith("/idempotency_key_reused")

    try:
        asyncio.run(send())
        assert db.connection.execute("SELECT count(*) FROM fiscal_period_event").fetchone() == (2,)
        assert db.connection.execute(
            "SELECT count(*) FROM audit_log WHERE action LIKE 'fiscal_period.%'"
        ).fetchone() == (2,)
        assert db.connection.execute(
            "SELECT count(*) FROM audit_log WHERE action = 'fiscal_year.generate'"
        ).fetchone() == (1,)
    finally:
        migration.downgrade(db.connection)


@pytest.mark.parametrize(
    "violation",
    [
        "overlap",
        "month",
        "period_no",
        "status",
        "range",
        "event_action",
        "event_reason",
        "event_tenant",
    ],
)
def test_database_constraints_reject_violations(
    fiscal: AuthorizationDatabase, violation: str
) -> None:
    db = fiscal
    p = service(db).ensure_year(2026)[0]
    if violation == "month":
        statement = "INSERT INTO fiscal_calendar(org_id,start_month) VALUES (%s,13)"
        params = (db.org_a,)
    elif violation.startswith("event_"):
        statement = (
            "INSERT INTO fiscal_period_event(id,org_id,period_id,action,actor_id,reason) "
            "VALUES (%s,%s,%s,%s,%s,%s)"
        )
        params = (
            uuid4(),
            db.org_b if violation == "event_tenant" else db.org_a,
            p.id,
            "invalid" if violation == "event_action" else "reopen",
            db.actor_id,
            "short" if violation == "event_reason" else REASON["reason"],
        )
    else:
        statement = (
            "INSERT INTO fiscal_period(id,org_id,fiscal_year,period_no,starts_on,ends_on,status) "
            "VALUES (%s,%s,2028,%s,%s,%s,%s)"
        )
        params = (
            uuid4(),
            db.org_a,
            13 if violation == "period_no" else 1,
            p.starts_on if violation == "overlap" else date(2028, 1, 1),
            p.ends_on
            if violation == "overlap"
            else date(2027, 12, 31)
            if violation == "range"
            else date(2028, 1, 31),
            "invalid" if violation == "status" else "open",
        )
    expected = (
        ExclusionViolation
        if violation == "overlap"
        else (ForeignKeyViolation if violation == "event_tenant" else CheckViolation)
    )
    with pytest.raises(expected):
        db.connection.execute(statement, params)


def test_database_status_change_requires_event_actor(fiscal: AuthorizationDatabase) -> None:
    from psycopg.errors import NotNullViolation

    db = fiscal
    p = service(db).ensure_year(2026)[0]
    with pytest.raises(NotNullViolation):
        db.connection.execute("UPDATE fiscal_period SET status = 'closed' WHERE id = %s", (p.id,))
    assert service(db).get_period(p.id).status == "open"


def test_rls_rejects_foreign_reads_and_writes(fiscal: AuthorizationDatabase) -> None:
    from psycopg import sql
    from psycopg.errors import InsufficientPrivilege

    db = fiscal
    own = service(db)
    foreign = service(db, db.org_b)
    own.set_calendar(CalendarPut(start_month=1))
    foreign.set_calendar(CalendarPut(start_month=4))
    p = own.ensure_year(2026)[0]
    fp = foreign.ensure_year(2026)[0]
    own.transition(p.id, "close")
    foreign.transition(fp.id, "close")
    role = "fiscal_rls_" + uuid4().hex
    db.connection.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        db.connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
        )
        db.connection.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON fiscal_calendar,fiscal_period,"
                "fiscal_period_event TO {}"
            ).format(sql.Identifier(role))
        )
        db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(db.connection, Scope(db.org_a)):
            assert db.connection.execute("SELECT org_id FROM fiscal_calendar").fetchall() == [
                (db.org_a,)
            ]
            assert db.connection.execute(
                "SELECT DISTINCT org_id FROM fiscal_period"
            ).fetchall() == [(db.org_a,)]
            assert db.connection.execute(
                "SELECT period_id FROM fiscal_period_event"
            ).fetchall() == [(p.id,)]
            assert (
                db.connection.execute(
                    "UPDATE fiscal_calendar SET start_month=5 WHERE org_id=%s", (db.org_b,)
                ).rowcount
                == 0
            )
        for statement, params in [
            ("INSERT INTO fiscal_calendar(org_id,start_month) VALUES (%s,1)", (db.org_b,)),
            (
                "INSERT INTO fiscal_period(id,org_id,fiscal_year,period_no,starts_on,ends_on) "
                "VALUES (%s,%s,2028,1,'2028-01-01','2028-01-31')",
                (uuid4(), db.org_b),
            ),
            (
                "INSERT INTO fiscal_period_event(id,org_id,period_id,action,actor_id) "
                "VALUES (%s,%s,%s,'close',%s)",
                (uuid4(), db.org_b, fp.id, db.actor_id),
            ),
        ]:
            with pytest.raises(InsufficientPrivilege):
                with tenant_transaction(db.connection, Scope(db.org_a)):
                    db.connection.execute(statement, params)
    finally:
        db.connection.execute("RESET ROLE")
        db.connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        db.connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_migration_rls_guard_is_effective() -> None:
    from flo.kernel.tenancy.guards import unprotected_tenant_tables

    source = MIGRATION.read_text()
    assert unprotected_tenant_tables(source) == []
    for table in ("fiscal_calendar", "fiscal_period", "fiscal_period_event"):
        planted = source.replace(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;", "")
        assert table in unprotected_tenant_tables(planted)


@pytest.mark.parametrize("month", range(1, 13))
def test_year_bounds_and_lazy_period_dates(fiscal: AuthorizationDatabase, month: int) -> None:
    from flo.modules.org.fiscal import year_bounds

    db = fiscal
    service(db).set_calendar(CalendarPut(start_month=month))
    start, end = year_bounds(2024, month)
    assert start == date(2024 if month == 1 else 2023, month, 1)
    assert end == (date(2024, 12, 31) if month == 1 else date(2024, month, 1) - timedelta(days=1))
    first = period_for(db.connection, Scope(db.org_a), start)
    last = period_for(db.connection, Scope(db.org_a), end)
    periods = service(db).list_periods(2024)
    assert first.period_no == 1 and last.period_no == 12
    assert first.fiscal_year == last.fiscal_year == 2024
    assert len(periods) == 12
    assert all(
        a.ends_on + timedelta(days=1) == b.starts_on
        for a, b in zip(periods, periods[1:], strict=False)
    )
    leap = next((p for p in periods if p.starts_on == date(2024, 2, 1)), None)
    if month != 2:
        assert leap is not None and leap.ends_on == date(2024, 2, 29)
    assert range_for(db.connection, Scope(db.org_a), "ytd", end) == (start, end)
