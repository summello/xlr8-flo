from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import logging
import os
import statistics
import time
from collections.abc import Awaitable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import ModuleType
from typing import cast
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from psycopg.errors import CheckViolation, RaiseException

from flo.api.auth import (
    get_identity_provider,
    get_password_reset_service,
    get_session_store,
    router,
)
from flo.kernel.authz import public_route, route_authorization_failures
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError, install_problem_details
from flo.kernel.identity import (
    IdentityConnection,
    IdentityId,
    IdentityProvider,
    build_local_identity_provider,
)
from flo.kernel.identity.reset import (
    PASSWORD_RESET_TEMPLATE,
    PasswordResetService,
    ResetConnection,
    hash_reset_token,
)
from flo.kernel.logging import correlation_context
from flo.kernel.outbox import IdentityOutboxStore, OutboxDispatcher, email_handler
from flo.kernel.outbox.dispatcher import DispatcherConnection
from flo.kernel.outbox.store import OutboxConnection
from flo.kernel.ports.email import EmailSender
from flo.kernel.session import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    RequestDevice,
    SessionStore,
    install_browser_security,
    install_csrf_protection,
)
from flo.kernel.session.store import SessionConnection

ROOT = Path(__file__).resolve().parents[4]
MIGRATION_DIRECTORY = ROOT / "migrations"
RESET_REVISION = "20260825_0010"
EMAIL_TEMPLATE = ROOT / "apps" / "api" / "templates" / "email" / "password_reset.html"
START = datetime.now(UTC).replace(microsecond=0)
DEVICE = RequestDevice("203.0.113.0/24", "Chrome on macOS")
PASSWORD = "the original safe passphrase"
REPLACEMENT = "the replacement safe passphrase"


def run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def _load_migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_migration_chain() -> tuple[ModuleType, ...]:
    return tuple(
        _load_migration(path, f"reset_{path.stem}")
        for path in sorted(MIGRATION_DIRECTORY.glob("[0-9]*.py"))
    )


def _drop_objects(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    connection.execute("DROP TABLE IF EXISTS outbox")
    connection.execute("DROP TABLE IF EXISTS job")
    connection.execute("DROP TABLE IF EXISTS password_reset_rate_limit")
    connection.execute("DROP TABLE IF EXISTS password_reset")
    connection.execute("DROP TABLE IF EXISTS session_security_event")
    connection.execute("DROP TABLE IF EXISTS auth_session")
    connection.execute("DROP FUNCTION IF EXISTS reject_session_security_event_mutation()")
    connection.execute("DROP TABLE IF EXISTS identity")


@dataclass
class MutableClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


@dataclass(frozen=True, slots=True)
class ResetDatabase:
    database_url: str
    connection: psycopg.Connection[tuple[object, ...]]
    identity_id: IdentityId
    provider: IdentityProvider
    sessions: SessionStore
    service: PasswordResetService
    settings: Settings
    clock: MutableClock
    migration: ModuleType


def _settings(**overrides: int) -> Settings:
    values = {
        "identity_argon2_time_cost": 1,
        "identity_argon2_memory_cost_kib": 8 * 1024,
        "identity_argon2_parallelism": 1,
        "password_reset_email_limit": 1000,
        "password_reset_ip_limit": 5000,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def reset_database() -> Iterator[ResetDatabase]:
    configured = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    database_url = configured or "postgresql://flo:flo-local@127.0.0.1:5432/flo_test"
    database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        connection = psycopg.connect(database_url, autocommit=True)
    except psycopg.Error as error:
        if configured:
            pytest.fail(f"configured Postgres is unavailable: {type(error).__name__}")
        pytest.skip("local Postgres is unavailable; run the repository stack")
        raise

    _drop_objects(connection)
    migrations = _load_migration_chain()
    for migration in migrations:
        migration.upgrade(connection)
    reset_migration = next(
        migration for migration in migrations if migration.revision == RESET_REVISION
    )
    settings = _settings()
    provider = run(
        build_local_identity_provider(cast(IdentityConnection, connection), settings)
    )
    identity_id = run(provider.create_identity("person@example.test", PASSWORD))
    clock = MutableClock(START)
    sessions = SessionStore(
        cast(SessionConnection, connection),
        idle_timeout=timedelta(hours=8),
        absolute_timeout=timedelta(hours=12),
        clock=clock,
    )
    service = PasswordResetService(cast(ResetConnection, connection), settings, clock=clock)
    try:
        yield ResetDatabase(
            database_url,
            connection,
            identity_id,
            provider,
            sessions,
            service,
            settings,
            clock,
            reset_migration,
        )
    finally:
        for migration in reversed(migrations):
            migration.downgrade(connection)
        connection.close()


def _request(database: ResetDatabase, email: str = "person@example.test") -> None:
    with correlation_context(uuid4().hex):
        database.service.request_reset(email, DEVICE)


def _outbox_token(connection: psycopg.Connection[tuple[object, ...]]) -> str:
    row = connection.execute(
        "SELECT payload->'context'->>'reset_url' FROM outbox "
        "WHERE state = 'pending' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert row is not None, "MISSING password-reset outbox row"
    values = parse_qs(urlsplit(cast(str, row[0])).query).get("token", [])
    assert len(values) == 1, "MISSING password-reset token in email context"
    return values[0]


def test_token_is_256_bits_hashed_at_rest_and_confined_to_expiring_outbox(
    reset_database: ResetDatabase,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []
    plaintext = "one-time-reset-token-visible-only-to-email-delivery"

    def fixed_token(byte_count: int) -> str:
        calls.append(byte_count)
        return plaintext

    monkeypatch.setattr("flo.kernel.identity.reset.secrets.token_urlsafe", fixed_token)
    caplog.set_level(logging.DEBUG)
    _request(reset_database)

    reset_row = reset_database.connection.execute(
        "SELECT token_hash, expires_at, created_at FROM password_reset"
    ).fetchone()
    outbox_row = reset_database.connection.execute(
        "SELECT payload::text, expires_at FROM outbox"
    ).fetchone()
    events = reset_database.connection.execute(
        "SELECT event_type, identity_id, user_agent FROM session_security_event"
    ).fetchall()

    assert calls == [32]
    assert reset_row == (hash_reset_token(plaintext), START + timedelta(minutes=30), START)
    assert outbox_row is not None
    assert plaintext in cast(str, outbox_row[0])
    assert outbox_row[1] == reset_row[1]
    assert plaintext not in repr(reset_row)
    assert plaintext not in repr(events)
    assert plaintext not in caplog.text


def test_token_works_once_and_success_revokes_every_session(
    reset_database: ResetDatabase,
) -> None:
    first_session = reset_database.sessions.issue(reset_database.identity_id, DEVICE)
    second_session = reset_database.sessions.issue(reset_database.identity_id, DEVICE)
    _request(reset_database)
    token = _outbox_token(reset_database.connection)

    run(
        reset_database.service.complete_reset(
            token,
            REPLACEMENT,
            DEVICE,
            reset_database.provider,
            reset_database.sessions,
        )
    )
    with pytest.raises(ProblemError) as reused:
        run(
            reset_database.service.complete_reset(
                token,
                REPLACEMENT,
                DEVICE,
                reset_database.provider,
                reset_database.sessions,
            )
        )

    assert reused.value.code is ErrorCode.INVALID_OR_EXPIRED
    assert not run(
        reset_database.provider.authenticate("person@example.test", PASSWORD)
    ).authenticated
    assert run(
        reset_database.provider.authenticate("person@example.test", REPLACEMENT)
    ).authenticated
    assert reset_database.sessions.authenticate(first_session.cookie_value(), DEVICE) is None
    assert reset_database.sessions.authenticate(second_session.cookie_value(), DEVICE) is None
    assert reset_database.connection.execute(
        "SELECT event_type FROM session_security_event ORDER BY id"
    ).fetchall() == [
        ("password_reset_requested",),
        ("password_reset_completed",),
        ("revoked_token_reuse",),
        ("revoked_token_reuse",),
    ]


def test_two_concurrent_consumers_allow_exactly_one_password_reset(
    reset_database: ResetDatabase,
) -> None:
    _request(reset_database)
    token = _outbox_token(reset_database.connection)
    barrier = Barrier(2)

    def consume() -> str:
        connection = psycopg.connect(reset_database.database_url, autocommit=True)
        try:
            provider = run(
                build_local_identity_provider(
                    cast(IdentityConnection, connection), reset_database.settings
                )
            )
            sessions = SessionStore(
                cast(SessionConnection, connection),
                idle_timeout=timedelta(hours=8),
                absolute_timeout=timedelta(hours=12),
                clock=reset_database.clock,
            )
            service = PasswordResetService(
                cast(ResetConnection, connection),
                reset_database.settings,
                clock=reset_database.clock,
            )
            barrier.wait()
            try:
                run(service.complete_reset(token, REPLACEMENT, DEVICE, provider, sessions))
            except ProblemError as error:
                return error.code.value
            return "success"
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _: consume(), range(2)))

    assert outcomes == ["invalid_or_expired", "success"]
    assert reset_database.connection.execute(
        "SELECT count(*) FROM session_security_event "
        "WHERE event_type = 'password_reset_completed'"
    ).fetchone() == (1,)


def test_expired_invalid_and_already_used_tokens_share_one_error(
    reset_database: ResetDatabase,
) -> None:
    _request(reset_database)
    expired_token = _outbox_token(reset_database.connection)
    reset_database.clock.now += timedelta(minutes=30)

    failures: list[ErrorCode] = []
    for token in (expired_token, "not-a-token", expired_token):
        with pytest.raises(ProblemError) as failure:
            run(
                reset_database.service.complete_reset(
                    token,
                    REPLACEMENT,
                    DEVICE,
                    reset_database.provider,
                    reset_database.sessions,
                )
            )
        failures.append(failure.value.code)

    assert failures == [ErrorCode.INVALID_OR_EXPIRED] * 3


def test_requesting_a_second_reset_invalidates_first_and_discards_unsent_email(
    reset_database: ResetDatabase,
) -> None:
    _request(reset_database)
    first = _outbox_token(reset_database.connection)
    reset_database.clock.now += timedelta(seconds=1)
    _request(reset_database)
    second = _outbox_token(reset_database.connection)

    assert first != second
    assert reset_database.connection.execute("SELECT count(*) FROM outbox").fetchone() == (1,)
    assert reset_database.connection.execute(
        "SELECT count(*) FROM password_reset WHERE used_at IS NULL"
    ).fetchone() == (1,)
    with pytest.raises(ProblemError) as invalidated:
        run(
            reset_database.service.complete_reset(
                first,
                REPLACEMENT,
                DEVICE,
                reset_database.provider,
                reset_database.sessions,
            )
        )
    assert invalidated.value.code is ErrorCode.INVALID_OR_EXPIRED
    run(
        reset_database.service.complete_reset(
            second,
            REPLACEMENT,
            DEVICE,
            reset_database.provider,
            reset_database.sessions,
        )
    )


def test_email_rate_limit_bites_even_when_ip_limit_has_capacity(
    reset_database: ResetDatabase,
) -> None:
    limited = PasswordResetService(
        cast(ResetConnection, reset_database.connection),
        _settings(password_reset_email_limit=1, password_reset_ip_limit=100),
        clock=reset_database.clock,
    )
    with correlation_context("rate-first"):
        limited.request_reset("person@example.test", DEVICE)
    first_outbox = reset_database.connection.execute("SELECT id FROM outbox").fetchone()
    different_ip = RequestDevice("198.51.100.0/24", DEVICE.user_agent)
    with correlation_context("email-rate-planted-violation"):
        limited.request_reset("person@example.test", different_ip)

    assert first_outbox is not None, "MISSING first allowed reset effect"
    assert reset_database.connection.execute("SELECT id FROM outbox").fetchone() == first_outbox
    assert reset_database.connection.execute(
        "SELECT count(*) FROM password_reset WHERE used_at IS NULL"
    ).fetchone() == (1,)
    assert reset_database.connection.execute(
        "SELECT dimension, count(*) FROM password_reset_rate_limit "
        "GROUP BY dimension ORDER BY dimension"
    ).fetchall() == [("email", 2), ("ip", 2)]


def test_ip_rate_limit_bites_across_two_existing_email_addresses(
    reset_database: ResetDatabase,
) -> None:
    second_identity = run(
        reset_database.provider.create_identity("second@example.test", PASSWORD)
    )
    limited = PasswordResetService(
        cast(ResetConnection, reset_database.connection),
        _settings(password_reset_email_limit=100, password_reset_ip_limit=1),
        clock=reset_database.clock,
    )
    with correlation_context("ip-rate-first"):
        limited.request_reset("person@example.test", DEVICE)
    with correlation_context("ip-rate-planted-violation"):
        limited.request_reset("second@example.test", DEVICE)

    assert reset_database.connection.execute("SELECT count(*) FROM outbox").fetchone() == (1,)
    assert reset_database.connection.execute(
        "SELECT count(*) FROM password_reset WHERE identity_id = %s", (second_identity,)
    ).fetchone() == (0,)
    assert reset_database.connection.execute(
        "SELECT count(*) FROM password_reset_rate_limit WHERE dimension = 'ip'"
    ).fetchone() == (2,)


class CapturingSender:
    def __init__(self) -> None:
        self.context: Mapping[str, object] | None = None

    def send(
        self,
        to: str,
        template: str,
        context: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        assert to == "person@example.test"
        assert template == PASSWORD_RESET_TEMPLATE
        assert idempotency_key.startswith("password-reset:")
        self.context = context
        return "provider-reset-message"


def test_identity_outbox_delivers_through_email_port_then_clears_secret_payload(
    reset_database: ResetDatabase,
) -> None:
    _request(reset_database)
    token = _outbox_token(reset_database.connection)
    sender = CapturingSender()
    dispatcher = OutboxDispatcher(
        cast(DispatcherConnection, reset_database.connection),
        {"email": email_handler(cast(EmailSender, sender))},
        random_fraction=lambda: 0.0,
    )

    summary = dispatcher.run()
    row = reset_database.connection.execute(
        "SELECT state, payload, provider_message_id FROM outbox"
    ).fetchone()

    assert summary.sent == 1
    assert sender.context is not None
    assert token in cast(str, sender.context["reset_url"])
    assert row == ("sent", {}, "provider-reset-message")
    assert token not in repr(row)


def test_identity_outbox_database_failure_suppresses_secret_error_context(
    reset_database: ResetDatabase,
) -> None:
    plaintext = "planted-token-that-a-check-violation-must-not-log"
    with pytest.raises(RuntimeError, match="identity-scoped outbox write failed") as failure:
        with correlation_context("planted-secret-write-failure"), (
            reset_database.connection.transaction()
        ):
            IdentityOutboxStore(
                cast(OutboxConnection, reset_database.connection),
                reset_database.identity_id,
            ).add_email(
                to="person@example.test",
                template=PASSWORD_RESET_TEMPLATE,
                context={"reset_url": f"https://example.test/reset?token={plaintext}"},
                idempotency_key="planted-secret-failure",
                created_at=datetime(2020, 1, 2, tzinfo=UTC),
                expires_at=datetime(2020, 1, 1, tzinfo=UTC),
            )

    assert plaintext not in str(failure.value)
    assert plaintext not in repr(failure.value)
    assert failure.value.__suppress_context__


def test_expired_identity_outbox_row_is_deleted_without_delivery(
    reset_database: ResetDatabase,
) -> None:
    _request(reset_database)
    reset_database.connection.execute(
        "UPDATE outbox "
        "SET created_at = CURRENT_TIMESTAMP - INTERVAL '2 seconds', "
        "expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'"
    )

    class ForbiddenSender:
        def send(self, *_: object) -> str:
            raise AssertionError("expired reset email must not be delivered")

    summary = OutboxDispatcher(
        cast(DispatcherConnection, reset_database.connection),
        {"email": email_handler(ForbiddenSender())},
        random_fraction=lambda: 0.0,
    ).run()

    assert summary.claimed == 0
    assert reset_database.connection.execute("SELECT count(*) FROM outbox").fetchone() == (0,)


def _build_app(database: ResetDatabase, service: PasswordResetService | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.get("/bootstrap")
    @public_route
    def bootstrap() -> dict[str, str]:
        return {"status": "ok"}

    app.dependency_overrides[get_password_reset_service] = lambda: service or database.service
    app.dependency_overrides[get_identity_provider] = lambda: database.provider
    app.dependency_overrides[get_session_store] = lambda: database.sessions
    install_csrf_protection(app)
    install_problem_details(app)
    install_browser_security(app)
    assert route_authorization_failures(app) == []
    return app


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://testserver",
    )


async def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.get("/bootstrap")
    assert response.status_code == 200
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return {CSRF_HEADER_NAME: token}


def _observable_headers(response: httpx.Response) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name != "x-correlation-id"
    }


def test_existing_and_unknown_accounts_have_identical_http_contract_and_timing(
    reset_database: ResetDatabase,
) -> None:
    app = _build_app(reset_database)

    async def trials() -> tuple[list[float], list[float]]:
        known_times: list[float] = []
        unknown_times: list[float] = []
        async with await _client(app) as client:
            headers = await _csrf(client)
            for index in range(100):
                started = time.perf_counter()
                known = await client.post(
                    "/api/v1/auth/reset-request",
                    json={"email": "person@example.test"},
                    headers=headers,
                )
                known_times.append(time.perf_counter() - started)
                started = time.perf_counter()
                unknown = await client.post(
                    "/api/v1/auth/reset-request",
                    json={"email": f"unknown-{index}@example.test"},
                    headers=headers,
                )
                unknown_times.append(time.perf_counter() - started)
                assert known.status_code == unknown.status_code == 202
                assert known.content == unknown.content == b""
                assert _observable_headers(known) == _observable_headers(unknown)
        return known_times, unknown_times

    known_times, unknown_times = run(trials())
    known_median = statistics.median(known_times)
    unknown_median = statistics.median(unknown_times)
    difference = abs(known_median - unknown_median) / max(known_median, unknown_median)
    assert difference < 0.10, (
        f"timing difference {difference:.1%} exceeds 10%: "
        f"known={known_median:.6f}s unknown={unknown_median:.6f}s"
    )


def test_rate_limited_request_still_returns_the_same_202_contract(
    reset_database: ResetDatabase,
) -> None:
    limited = PasswordResetService(
        cast(ResetConnection, reset_database.connection),
        _settings(password_reset_email_limit=1, password_reset_ip_limit=1),
        clock=reset_database.clock,
    )
    app = _build_app(reset_database, limited)

    async def requests() -> tuple[httpx.Response, httpx.Response]:
        async with await _client(app) as client:
            headers = await _csrf(client)
            first = await client.post(
                "/api/v1/auth/reset-request",
                json={"email": "person@example.test"},
                headers=headers,
            )
            second = await client.post(
                "/api/v1/auth/reset-request",
                json={"email": "person@example.test"},
                headers=headers,
            )
            return first, second

    first, second = run(requests())
    assert first.status_code == second.status_code == 202
    assert first.content == second.content == b""
    assert _observable_headers(first) == _observable_headers(second)


def test_invalid_and_expired_http_errors_are_identical(
    reset_database: ResetDatabase,
) -> None:
    _request(reset_database)
    expired = _outbox_token(reset_database.connection)
    reset_database.clock.now += timedelta(minutes=30)
    app = _build_app(reset_database)

    async def requests() -> tuple[httpx.Response, httpx.Response]:
        async with await _client(app) as client:
            headers = await _csrf(client)
            invalid = await client.post(
                "/api/v1/auth/reset",
                json={"token": "invalid", "password": REPLACEMENT},
                headers=headers,
            )
            expired_response = await client.post(
                "/api/v1/auth/reset",
                json={"token": expired, "password": REPLACEMENT},
                headers=headers,
            )
            return invalid, expired_response

    invalid, expired_response = run(requests())
    invalid_body = invalid.json()
    expired_body = expired_response.json()
    invalid_body.pop("correlation_id")
    expired_body.pop("correlation_id")
    assert invalid.status_code == expired_response.status_code == 400
    assert invalid_body == expired_body
    assert invalid_body["type"].endswith("/invalid_or_expired")


def test_password_policy_failure_preserves_the_unused_reset_token(
    reset_database: ResetDatabase,
) -> None:
    _request(reset_database)
    token = _outbox_token(reset_database.connection)
    app = _build_app(reset_database)

    async def request() -> httpx.Response:
        async with await _client(app) as client:
            headers = await _csrf(client)
            return await client.post(
                "/api/v1/auth/reset",
                json={"token": token, "password": "too short"},
                headers=headers,
            )

    response = run(request())
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {
            "field": "password",
            "message": "The password does not meet the password policy.",
        }
    ]
    assert reset_database.connection.execute(
        "SELECT used_at FROM password_reset WHERE token_hash = %s",
        (hash_reset_token(token),),
    ).fetchone() == (None,)
    run(
        reset_database.service.complete_reset(
            token,
            REPLACEMENT,
            DEVICE,
            reset_database.provider,
            reset_database.sessions,
        )
    )


def test_migration_contracts_are_present_bite_and_downgrade_preserves_seeded_rows(
    reset_database: ResetDatabase,
) -> None:
    connection = reset_database.connection
    assert reset_database.migration.revision == "20260825_0010", "MISSING required revision"
    assert reset_database.migration.down_revision == "20260825_0009", (
        "MISSING required down_revision"
    )
    columns = {
        cast(str, row[0])
        for row in connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'password_reset'"
        ).fetchall()
    }
    expected_columns = {
        "id",
        "identity_id",
        "token_hash",
        "created_at",
        "expires_at",
        "used_at",
    }
    missing_columns = expected_columns - columns
    assert not missing_columns, f"MISSING password_reset columns: {sorted(missing_columns)}"

    event_constraint = connection.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'session_security_event'::regclass "
        "AND conname = 'session_security_event_event_type_check'"
    ).fetchone()
    assert event_constraint is not None, "MISSING session security event type constraint"
    expected_events = {
        "revoked_token_reuse",
        "password_reset_requested",
        "password_reset_completed",
    }
    missing_events = {
        event for event in expected_events if event not in cast(str, event_constraint[0])
    }
    assert not missing_events, f"MISSING reset security events: {sorted(missing_events)}"
    nullable = connection.execute(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'session_security_event' AND column_name = 'session_id'"
    ).fetchone()
    assert nullable == ("YES",), "MISSING nullable pre-authentication session_id"
    triggers = {
        cast(str, row[0])
        for row in connection.execute(
            "SELECT tgname FROM pg_trigger "
            "WHERE tgrelid = 'session_security_event'::regclass AND NOT tgisinternal"
        ).fetchall()
    }
    assert "session_security_event_append_only" in triggers, (
        "MISSING session security append-only trigger"
    )

    with pytest.raises(CheckViolation):
        connection.execute(
            """
            INSERT INTO password_reset
                (id, identity_id, token_hash, created_at, expires_at)
            VALUES (%s, %s, 'plaintext', %s, %s)
            """,
            (uuid4(), reset_database.identity_id, START, START + timedelta(minutes=30)),
        )
    with pytest.raises(CheckViolation):
        connection.execute(
            """
            INSERT INTO session_security_event
                (identity_id, event_type, occurred_at, user_agent)
            VALUES (%s, 'planted_invalid_event', %s, 'Other browser')
            """,
            (reset_database.identity_id, START),
        )
    with pytest.raises(CheckViolation):
        connection.execute(
            """
            INSERT INTO outbox
                (id, org_id, identity_id, topic, payload, idempotency_key,
                 correlation_id, expires_at)
            VALUES (%s, %s, %s, 'email', '{}', 'planted-double-scope',
                    'planted-constraint', %s)
            """,
            (uuid4(), uuid4(), reset_database.identity_id, START + timedelta(minutes=30)),
        )
    connection.execute(
        """
        INSERT INTO session_security_event
            (identity_id, event_type, occurred_at, user_agent)
        VALUES (%s, 'password_reset_requested', %s, 'Other browser')
        """,
        (reset_database.identity_id, START),
    )
    with pytest.raises(RaiseException, match="append-only"):
        connection.execute(
            "UPDATE session_security_event SET user_agent = 'changed' "
            "WHERE event_type = 'password_reset_requested'"
        )

    org_id = uuid4()
    existing_id = uuid4()
    connection.execute(
        """
        INSERT INTO outbox
            (id, org_id, topic, payload, idempotency_key, correlation_id)
        VALUES (%s, %s, 'email', '{}', 'existing', 'existing-correlation')
        """,
        (existing_id, org_id),
    )
    with connection.transaction(force_rollback=True):
        reset_database.migration.downgrade(connection)

        assert connection.execute(
            "SELECT id, org_id FROM outbox WHERE id = %s", (existing_id,)
        ).fetchone() == (existing_id, org_id)
        assert connection.execute(
            "SELECT to_regclass('public.password_reset')"
        ).fetchone() == (None,)
        restored = connection.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'session_security_event' AND column_name = 'session_id'"
        ).fetchone()
        assert restored == ("NO",)


def test_email_template_names_every_required_user_message() -> None:
    rendered = EMAIL_TEMPLATE.read_text(encoding="utf-8")
    required = {
        "{{ reset_url }}",
        "{{ expires_minutes }} minutes",
        "used only once",
        "ignore this email",
        "Your password is unchanged",
    }
    missing = {message for message in required if message not in rendered}
    assert not missing, f"MISSING password-reset email content: {sorted(missing)}"
