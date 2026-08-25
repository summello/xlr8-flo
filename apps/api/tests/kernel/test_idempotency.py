from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
from collections.abc import Awaitable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi import FastAPI, Request
from psycopg import sql
from psycopg.errors import CheckViolation

from flo.kernel.errors import install_problem_details
from flo.kernel.idempotency import (
    IdempotencyStore,
    install_idempotency,
    request_hash,
    transaction_connection,
)
from flo.kernel.idempotency.middleware import ConnectionFactory
from flo.kernel.idempotency.store import IdempotencyConnection
from flo.kernel.tenancy.context import Scope, current_scope
from flo.kernel.tenancy.middleware import install_tenant_context
from flo.kernel.tenancy.rls import tenant_transaction

ROOT = Path(__file__).resolve().parents[4]
MIGRATION_PATH = ROOT / "migrations" / "20260825_0003_idempotency.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("idempotency_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@dataclass(frozen=True, slots=True)
class IdempotencyDatabase:
    url: str
    admin: psycopg.Connection[tuple[object, ...]]
    effect_table: str
    migration: ModuleType


@pytest.fixture
def idempotency_database() -> Iterator[IdempotencyDatabase]:
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
    effect_table = f"idempotency_effect_{uuid4().hex[:12]}"
    connection.execute("DROP TABLE IF EXISTS idempotency_key")
    connection.execute(
        sql.SQL(
            "CREATE TABLE {} (id uuid PRIMARY KEY, org_id uuid NOT NULL, amount integer NOT NULL)"
        ).format(sql.Identifier(effect_table))
    )
    migration.upgrade(connection)
    database = IdempotencyDatabase(database_url, connection, effect_table, migration)
    try:
        yield database
    finally:
        connection.execute("DROP TABLE IF EXISTS idempotency_key")
        connection.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(effect_table))
        )
        connection.close()


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    org_id: UUID


@dataclass(slots=True)
class CommandControl:
    fail_next: bool = False
    pause_entered: asyncio.Event | None = None
    pause_release: asyncio.Event | None = None


@contextmanager
def _connection(database_url: str) -> Iterator[IdempotencyConnection]:
    connection = psycopg.connect(database_url, autocommit=True)
    try:
        yield cast(IdempotencyConnection, connection)
    finally:
        connection.close()


def _connection_factory(database_url: str) -> ConnectionFactory:
    return lambda: _connection(database_url)


def _app(
    database: IdempotencyDatabase,
    org_a: UUID,
    org_b: UUID,
    control: CommandControl | None = None,
) -> FastAPI:
    command_control = control or CommandControl()
    sessions = {
        "tenant-a-session": AuthenticatedSession(org_a),
        "tenant-b-session": AuthenticatedSession(org_b),
    }
    app = FastAPI()

    def resolve_session(request: Request) -> AuthenticatedSession | None:
        return sessions.get(request.cookies.get("flo_session", ""))

    @app.post("/ledger/adjust", status_code=201)
    async def adjust(request: Request, body: dict[str, object]) -> dict[str, object]:
        amount = body.get("amount")
        if not isinstance(amount, int):
            raise ValueError("test command requires an integer amount")
        connection = transaction_connection(request)
        connection.execute(
            sql.SQL("INSERT INTO {} (id, org_id, amount) VALUES (%(id)s, %(org_id)s, %(amount)s)")
            .format(sql.Identifier(database.effect_table)),
            {"id": uuid4(), "org_id": current_scope().org_id, "amount": amount},
        )
        if command_control.fail_next:
            command_control.fail_next = False
            raise RuntimeError("simulated crash between claim and completion")
        if command_control.pause_entered is not None:
            command_control.pause_entered.set()
            assert command_control.pause_release is not None
            await command_control.pause_release.wait()
        return {"accepted": True, "amount": amount}

    @app.post("/ledger/issue", status_code=202)
    async def issue(request: Request, body: dict[str, object]) -> dict[str, object]:
        del request
        return {"issued": body.get("amount")}

    @app.post("/notes")
    async def notes(body: dict[str, object]) -> dict[str, object]:
        return body

    @app.post("/ledger/{action}")
    async def other_money_action(action: str) -> dict[str, str]:
        return {"action": action}

    install_idempotency(app, _connection_factory(database.url))
    install_tenant_context(app, resolve_session)
    install_problem_details(app)
    return app


def _key_requirement_app() -> FastAPI:
    app = FastAPI()

    @app.post("/notes")
    async def notes(body: dict[str, object]) -> dict[str, object]:
        return body

    @app.post("/api/v1/auth/{action}")
    async def auth(action: str) -> dict[str, str]:
        return {"action": action}

    def unexpected_connection() -> AbstractContextManager[IdempotencyConnection]:
        raise AssertionError("key requirement checks must not open a database connection")

    install_idempotency(app, unexpected_connection)
    install_problem_details(app)
    return app


def _run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


async def _post(
    app: FastAPI,
    path: str,
    *,
    key: str | None = None,
    content: str | None = None,
    session: str = "tenant-a-session",
) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["Idempotency-Key"] = key
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"flo_session": session},
    ) as client:
        return await client.post(
            path,
            content=content,
            headers=headers,
        )


def _effect_count(database: IdempotencyDatabase) -> int:
    row = database.admin.execute(
        sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(database.effect_table))
    ).fetchone()
    assert row is not None
    return cast(int, row[0])


def _key_rows(database: IdempotencyDatabase, org_id: UUID) -> list[tuple[object, ...]]:
    connection = cast(IdempotencyConnection, database.admin)
    with tenant_transaction(connection, Scope(org_id)):
        rows = database.admin.execute(
            "SELECT key, state, status_code FROM idempotency_key "
            "WHERE org_id = %s ORDER BY key",
            (org_id,),
        ).fetchall()
    return rows


def test_canonical_hash_ignores_json_key_order_and_insignificant_whitespace() -> None:
    first = b'{"memo": "capital", "amount": 125}'
    reordered = b'{\n  "amount":125,\n  "memo":"capital"\n}'

    assert request_hash(first) == request_hash(reordered)
    assert request_hash(first) != request_hash(b'{"memo":"capital","amount":126}')


def test_same_key_and_canonical_body_replays_original_201_with_one_effect(
    idempotency_database: IdempotencyDatabase,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    app = _app(idempotency_database, org_a, org_b)

    first = _run(
        _post(
            app,
            "/ledger/adjust",
            key="same-command",
            content='{"memo": "capital", "amount": 125}',
        )
    )
    replay = _run(
        _post(
            app,
            "/ledger/adjust",
            key="same-command",
            content='{\n "amount":125, "memo":"capital" }',
        )
    )

    assert first.status_code == replay.status_code == 201
    assert first.content == replay.content
    assert replay.json() == {"accepted": True, "amount": 125}
    assert _effect_count(idempotency_database) == 1
    assert _key_rows(idempotency_database, org_a) == [("same-command", "completed", 201)]


def test_reusing_a_completed_key_for_a_different_body_is_422_without_an_effect(
    idempotency_database: IdempotencyDatabase,
) -> None:
    app = _app(idempotency_database, uuid4(), uuid4())
    first = _run(
        _post(app, "/ledger/adjust", key="reused", content='{"amount": 10}')
    )
    reused = _run(
        _post(app, "/ledger/adjust", key="reused", content='{"amount": 11}')
    )

    assert first.status_code == 201
    assert reused.status_code == 422
    assert reused.json()["type"].endswith("/idempotency_key_reused")
    assert _effect_count(idempotency_database) == 1


def test_reusing_a_key_on_a_different_endpoint_is_422(
    idempotency_database: IdempotencyDatabase,
) -> None:
    app = _app(idempotency_database, uuid4(), uuid4())
    assert (
        _run(_post(app, "/ledger/adjust", key="wrong-endpoint", content='{"amount": 10}'))
        .status_code
        == 201
    )

    response = _run(
        _post(app, "/ledger/issue", key="wrong-endpoint", content='{"amount": 10}')
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/idempotency_key_reused")
    assert _effect_count(idempotency_database) == 1


def test_concurrent_same_key_returns_409_with_retry_after_while_winner_commits_once(
    idempotency_database: IdempotencyDatabase,
) -> None:
    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        entered = asyncio.Event()
        release = asyncio.Event()
        app = _app(
            idempotency_database,
            uuid4(),
            uuid4(),
            CommandControl(pause_entered=entered, pause_release=release),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"flo_session": "tenant-a-session"},
        ) as client:
            request = {
                "content": '{"amount": 88}',
                "headers": {
                    "Content-Type": "application/json",
                    "Idempotency-Key": "concurrent",
                },
            }
            winner = asyncio.create_task(client.post("/ledger/adjust", **request))
            await asyncio.wait_for(entered.wait(), timeout=2)
            in_flight = await client.post("/ledger/adjust", **request)
            release.set()
            return await winner, in_flight

    winner, in_flight = _run(scenario())

    assert winner.status_code == 201
    assert in_flight.status_code == 409
    assert in_flight.headers["retry-after"] == "1"
    assert in_flight.json()["type"].endswith("/request_in_flight")
    assert _effect_count(idempotency_database) == 1


def test_crash_rolls_back_claim_and_effect_so_the_same_key_is_reusable(
    idempotency_database: IdempotencyDatabase,
) -> None:
    org_a = uuid4()
    app = _app(
        idempotency_database,
        org_a,
        uuid4(),
        CommandControl(fail_next=True),
    )

    crashed = _run(
        _post(app, "/ledger/adjust", key="retry-after-crash", content='{"amount": 41}')
    )
    retried = _run(
        _post(app, "/ledger/adjust", key="retry-after-crash", content='{"amount": 41}')
    )

    assert crashed.status_code == 500
    assert retried.status_code == 201
    assert _effect_count(idempotency_database) == 1
    assert _key_rows(idempotency_database, org_a) == [
        ("retry-after-crash", "completed", 201)
    ]


@pytest.mark.parametrize(
    "path",
    [
        "/budgets/budget-id/allocate",
        "/requisitions/requisition-id/submit",
        "/purchase-orders/purchase-order-id/send",
        "/notes",
    ],
)
def test_every_unexempted_post_requires_the_idempotency_header(
    path: str,
) -> None:
    app = _key_requirement_app()

    missing = _run(_post(app, path, content='{"amount": 9}'))

    assert missing.status_code == 400
    assert "Idempotency-Key" in missing.text
    assert missing.json()["errors"] == [
        {"field": "Idempotency-Key", "message": "This header is required."}
    ]


@pytest.mark.parametrize("action", ["login", "logout", "refresh"])
def test_auth_post_exemptions_pass_without_an_idempotency_key(
    action: str,
) -> None:
    app = _key_requirement_app()

    response = _run(
        _post(
            app,
            f"/api/v1/auth/{action}",
            content="{}",
        )
    )

    assert response.status_code == 200
    assert response.json() == {"action": action}


def test_missing_tenant_scope_logs_the_unprotected_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _key_requirement_app()
    caplog.set_level(logging.WARNING, logger="flo.kernel.idempotency.middleware")

    response = _run(
        _post(
            app,
            "/notes",
            key="missing-scope",
            content='{"message":"saved"}',
            session="missing-session",
        )
    )

    assert response.status_code == 200
    assert response.json() == {"message": "saved"}
    assert "tenant scope is missing for /notes" in caplog.text


def test_same_key_is_independent_between_organizations(
    idempotency_database: IdempotencyDatabase,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    app = _app(idempotency_database, org_a, org_b)

    tenant_a = _run(
        _post(app, "/ledger/adjust", key="org-key", content='{"amount": 3}')
    )
    tenant_b = _run(
        _post(
            app,
            "/ledger/adjust",
            key="org-key",
            content='{"amount": 3}',
            session="tenant-b-session",
        )
    )

    assert tenant_a.status_code == tenant_b.status_code == 201
    assert _effect_count(idempotency_database) == 2
    assert _key_rows(idempotency_database, org_a) == [("org-key", "completed", 201)]
    assert _key_rows(idempotency_database, org_b) == [("org-key", "completed", 201)]


def test_cleanup_removes_only_keys_older_than_24_hours(
    idempotency_database: IdempotencyDatabase,
) -> None:
    org_id = uuid4()
    now = datetime.now(UTC)
    connection = cast(IdempotencyConnection, idempotency_database.admin)
    with tenant_transaction(connection, Scope(org_id)):
        idempotency_database.admin.execute(
            """
            INSERT INTO idempotency_key
                (org_id, key, endpoint, request_hash, state, status_code,
                 response_body, created_at, completed_at)
            VALUES
                (%s, 'expired', '/ledger/adjust', 'hash', 'completed', 201,
                 '{}'::jsonb, %s, %s),
                (%s, 'retained', '/ledger/adjust', 'hash', 'completed', 201,
                 '{}'::jsonb, %s, %s)
            """,
            (
                org_id,
                now - timedelta(hours=25),
                now - timedelta(hours=25),
                org_id,
                now - timedelta(hours=23),
                now - timedelta(hours=23),
            ),
        )
        deleted = IdempotencyStore(connection, Scope(org_id)).delete_expired(now)

    assert deleted == 1
    assert _key_rows(idempotency_database, org_id) == [("retained", "completed", 201)]


@pytest.mark.parametrize(
    ("state", "status_code", "response_body", "completed_at"),
    [
        ("unknown", None, None, None),
        ("completed", None, None, None),
        ("in_progress", 201, "{}", datetime.now(UTC)),
    ],
)
def test_database_rejects_invalid_state_and_completion_combinations(
    idempotency_database: IdempotencyDatabase,
    state: str,
    status_code: int | None,
    response_body: str | None,
    completed_at: datetime | None,
) -> None:
    org_id = uuid4()
    connection = cast(IdempotencyConnection, idempotency_database.admin)

    with pytest.raises(CheckViolation), tenant_transaction(connection, Scope(org_id)):
        idempotency_database.admin.execute(
            """
            INSERT INTO idempotency_key
                (org_id, key, endpoint, request_hash, state, status_code,
                 response_body, completed_at)
            VALUES (%s, 'invalid', '/ledger/adjust', 'hash', %s, %s, %s::jsonb, %s)
            """,
            (org_id, state, status_code, response_body, completed_at),
        )


def test_idempotency_table_rls_hides_foreign_rows_even_from_its_owner(
    idempotency_database: IdempotencyDatabase,
) -> None:
    admin = idempotency_database.admin
    application_owner = f"idempotency_owner_{uuid4().hex[:12]}"
    org_a, org_b = uuid4(), uuid4()
    admin_user_row = admin.execute("SELECT current_user").fetchone()
    assert admin_user_row is not None
    admin_user = cast(str, admin_user_row[0])
    admin.execute(
        """
        INSERT INTO idempotency_key
            (org_id, key, endpoint, request_hash, state)
        VALUES (%s, 'tenant-a', '/ledger/adjust', 'hash-a', 'in_progress'),
               (%s, 'tenant-b', '/ledger/adjust', 'hash-b', 'in_progress')
        """,
        (org_a, org_b),
    )
    admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(application_owner)))
    admin.execute(
        sql.SQL("ALTER TABLE idempotency_key OWNER TO {}").format(
            sql.Identifier(application_owner)
        )
    )
    try:
        admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(application_owner)))
        connection = cast(IdempotencyConnection, admin)
        with tenant_transaction(connection, Scope(org_a)):
            rows = admin.execute("SELECT key FROM idempotency_key ORDER BY key").fetchall()
        assert rows == [("tenant-a",)]
    finally:
        admin.execute("RESET ROLE")
        admin.execute(
            sql.SQL("ALTER TABLE idempotency_key OWNER TO {}").format(
                sql.Identifier(admin_user)
            )
        )
        admin.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(application_owner))
        )


def test_migration_has_the_complete_model_rls_and_reversible_data_preservation(
    idempotency_database: IdempotencyDatabase,
) -> None:
    columns = idempotency_database.admin.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'idempotency_key' "
        "ORDER BY ordinal_position"
    ).fetchall()
    protection = idempotency_database.admin.execute(
        """
        SELECT tables.relrowsecurity, tables.relforcerowsecurity, policies.polname
        FROM pg_catalog.pg_class AS tables
        JOIN pg_catalog.pg_namespace AS namespaces ON namespaces.oid = tables.relnamespace
        LEFT JOIN pg_catalog.pg_policy AS policies ON policies.polrelid = tables.oid
        WHERE namespaces.nspname = 'public' AND tables.relname = 'idempotency_key'
        """
    ).fetchone()
    constraints = idempotency_database.admin.execute(
        "SELECT conname FROM pg_catalog.pg_constraint "
        "WHERE conrelid = 'public.idempotency_key'::regclass ORDER BY conname"
    ).fetchall()
    indexes = idempotency_database.admin.execute(
        "SELECT indexname FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = 'idempotency_key' ORDER BY indexname"
    ).fetchall()

    assert [row[0] for row in columns] == [
        "org_id",
        "key",
        "endpoint",
        "request_hash",
        "state",
        "status_code",
        "response_body",
        "created_at",
        "completed_at",
    ]
    assert protection == (True, True, "tenant_isolation")
    assert [row[0] for row in constraints] == [
        "idempotency_key_completion_valid",
        "idempotency_key_pkey",
        "idempotency_key_state_valid",
    ]
    assert [row[0] for row in indexes] == [
        "idempotency_key_created_at_idx",
        "idempotency_key_pkey",
    ]

    sentinel = f"idempotency_sentinel_{uuid4().hex[:12]}"
    idempotency_database.admin.execute(
        sql.SQL("CREATE TABLE {} (value text NOT NULL)").format(sql.Identifier(sentinel))
    )
    idempotency_database.admin.execute(
        sql.SQL("INSERT INTO {} (value) VALUES ('preserved')").format(sql.Identifier(sentinel))
    )
    try:
        idempotency_database.migration.downgrade(idempotency_database.admin)
        assert idempotency_database.admin.execute(
            "SELECT to_regclass('public.idempotency_key')"
        ).fetchone() == (None,)
        assert idempotency_database.admin.execute(
            sql.SQL("SELECT value FROM {}").format(sql.Identifier(sentinel))
        ).fetchone() == ("preserved",)
    finally:
        idempotency_database.admin.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(sentinel))
        )
