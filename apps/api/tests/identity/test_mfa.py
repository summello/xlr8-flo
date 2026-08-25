from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
from collections.abc import Awaitable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

import httpx
import psycopg
import pyotp
import pytest
from argon2 import PasswordHasher, Type
from fastapi import Depends, FastAPI, Request
from psycopg.errors import RaiseException

from flo.api.auth import (
    current_session,
    get_identity_provider,
    get_mfa_service,
    get_session_store,
    router,
)
from flo.kernel.errors import ProblemError, install_problem_details
from flo.kernel.identity import (
    AuthResult,
    IdentityId,
    IdentityProvider,
    MfaAccessRequirement,
    MfaConnection,
    MfaService,
    PolicyResult,
    SecretCipher,
    install_mfa_access_gate,
)
from flo.kernel.session import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    RequestDevice,
    SessionRecord,
    SessionStore,
    install_csrf_protection,
    install_session_authentication,
    requires_recent_auth,
)
from flo.kernel.session.store import SessionConnection

ROOT = Path(__file__).resolve().parents[4]
IDENTITY_MIGRATION = ROOT / "migrations" / "20260824_0002_identity.py"
SESSION_MIGRATION = ROOT / "migrations" / "20260825_0004_session.py"
MFA_MIGRATION = ROOT / "migrations" / "20260825_0011_mfa.py"
START = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
DEVICE = RequestDevice("203.0.113.0/24", "Chrome on macOS")


def run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def load_migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def drop_mfa_objects(connection: psycopg.Connection[tuple[object, ...]]) -> None:
    connection.execute("DROP TABLE IF EXISTS mfa_security_event")
    connection.execute("DROP FUNCTION IF EXISTS reject_mfa_security_event_mutation()")
    connection.execute("DROP TABLE IF EXISTS mfa_totp_consumption")
    connection.execute("DROP TABLE IF EXISTS mfa_recovery_code")
    connection.execute("DROP TABLE IF EXISTS mfa_factor")
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
class MfaDatabase:
    connection: psycopg.Connection[tuple[object, ...]]
    connection_url: str = field(repr=False)
    identity_id: IdentityId
    foreign_identity_id: IdentityId
    migration: ModuleType


@pytest.fixture
def mfa_database() -> Iterator[MfaDatabase]:
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
    identity_migration = load_migration(IDENTITY_MIGRATION, "mfa_test_identity")
    session_migration = load_migration(SESSION_MIGRATION, "mfa_test_session")
    mfa_migration = load_migration(MFA_MIGRATION, "mfa_test_migration")
    drop_mfa_objects(connection)
    identity_migration.upgrade(connection)
    session_migration.upgrade(connection)
    mfa_migration.upgrade(connection)
    identity_id = IdentityId(uuid4())
    foreign_identity_id = IdentityId(uuid4())
    for values in (
        (identity_id, "mfa@example.test", "$argon2id$mfa-test-placeholder"),
        (
            foreign_identity_id,
            "foreign-mfa@example.test",
            "$argon2id$mfa-test-placeholder",
        ),
    ):
        connection.execute(
            "INSERT INTO identity (id, email, password_hash) VALUES (%s, %s, %s)",
            values,
        )
    try:
        yield MfaDatabase(
            connection,
            database_url,
            identity_id,
            foreign_identity_id,
            mfa_migration,
        )
    finally:
        drop_mfa_objects(connection)
        connection.close()


def service(
    database: MfaDatabase,
    clock: MutableClock,
    *,
    max_failed_attempts: int = 5,
) -> MfaService:
    return MfaService(
        cast(MfaConnection, database.connection),
        SecretCipher(bytes(range(32))),
        PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1, type=Type.ID),
        argon2_max_concurrency=2,
        max_failed_attempts=max_failed_attempts,
        failure_window=timedelta(minutes=5),
        lock_duration=timedelta(minutes=15),
        clock=clock,
    )


def session_store(database: MfaDatabase, clock: MutableClock) -> SessionStore:
    return SessionStore(
        cast(SessionConnection, database.connection),
        idle_timeout=timedelta(hours=8),
        absolute_timeout=timedelta(hours=12),
        clock=clock,
    )


def _session(database: MfaDatabase, clock: MutableClock) -> tuple[SessionStore, SessionRecord]:
    store = session_store(database, clock)
    issued = store.issue(database.identity_id, DEVICE)
    return store, issued.session


async def _enroll_and_confirm(
    mfa: MfaService,
    identity_id: IdentityId,
    session: SessionRecord,
    clock: MutableClock,
) -> tuple[str, tuple[str, ...]]:
    enrollment = await mfa.enroll(identity_id)
    code = pyotp.TOTP(enrollment.secret).at(clock.now)
    await mfa.confirm(
        identity_id,
        code,
        session_id=session.id,
        device=DEVICE,
    )
    return enrollment.secret, enrollment.recovery_codes


def test_enrollment_secret_is_encrypted_and_activation_requires_a_real_code(
    mfa_database: MfaDatabase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = MutableClock(START)
    mfa = service(mfa_database, clock)
    _, session = _session(mfa_database, clock)
    caplog.set_level(logging.DEBUG)
    enrollment = run(mfa.enroll(mfa_database.identity_id))

    factor = mfa_database.connection.execute(
        "SELECT secret_ciphertext, pending_secret_ciphertext FROM mfa_factor"
    ).fetchone()
    stored_recovery = mfa_database.connection.execute(
        "SELECT code_hash FROM mfa_recovery_code ORDER BY selector"
    ).fetchall()
    assert factor is not None and factor[0] is None
    assert enrollment.secret not in str(factor)
    assert all(code not in str(stored_recovery) for code in enrollment.recovery_codes)
    assert all(row[0].startswith("$argon2id$") for row in stored_recovery)
    assert enrollment.secret not in caplog.text
    assert all(code not in caplog.text for code in enrollment.recovery_codes)
    assert repr(enrollment) == "MfaEnrollment(secret=<redacted>, recovery_codes=<redacted>)"

    with pytest.raises(ProblemError):
        run(
            mfa.confirm(
                mfa_database.identity_id,
                "000000",
                session_id=session.id,
                device=DEVICE,
            )
        )
    assert mfa.access_requirement(mfa_database.identity_id) is MfaAccessRequirement.NONE

    real_code = pyotp.TOTP(enrollment.secret).at(clock.now)
    run(
        mfa.confirm(
            mfa_database.identity_id,
            real_code,
            session_id=session.id,
            device=DEVICE,
        )
    )
    assert mfa.access_requirement(mfa_database.identity_id) is MfaAccessRequirement.VERIFY


def test_totp_drift_window_and_consumed_time_steps_are_enforced(
    mfa_database: MfaDatabase,
) -> None:
    clock = MutableClock(START)
    mfa = service(mfa_database, clock)
    _, session = _session(mfa_database, clock)
    secret, _ = run(_enroll_and_confirm(mfa, mfa_database.identity_id, session, clock))
    generator = pyotp.TOTP(secret)

    with pytest.raises(ProblemError):
        run(
            mfa.verify(
                mfa_database.identity_id,
                generator.at(clock.now),
                session_id=session.id,
                device=DEVICE,
            )
        )
    previous = generator.at(clock.now - timedelta(seconds=30))
    following = generator.at(clock.now + timedelta(seconds=30))
    assert not run(
        mfa.verify(
            mfa_database.identity_id,
            previous,
            session_id=session.id,
            device=DEVICE,
        )
    ).used_recovery_code
    assert not run(
        mfa.verify(
            mfa_database.identity_id,
            following,
            session_id=session.id,
            device=DEVICE,
        )
    ).used_recovery_code
    with pytest.raises(ProblemError):
        run(
            mfa.verify(
                mfa_database.identity_id,
                generator.at(clock.now + timedelta(seconds=60)),
                session_id=session.id,
                device=DEVICE,
            )
        )


def test_concurrent_replay_allows_exactly_one_consumer(
    mfa_database: MfaDatabase,
) -> None:
    clock = MutableClock(START)
    mfa = service(mfa_database, clock)
    _, session = _session(mfa_database, clock)
    secret, _ = run(_enroll_and_confirm(mfa, mfa_database.identity_id, session, clock))
    clock.now += timedelta(seconds=30)
    code = pyotp.TOTP(secret).at(clock.now)
    def consume() -> bool:
        connection = psycopg.connect(mfa_database.connection_url, autocommit=True)
        try:
            worker_database = MfaDatabase(
                connection,
                mfa_database.connection_url,
                mfa_database.identity_id,
                mfa_database.foreign_identity_id,
                mfa_database.migration,
            )
            try:
                run(
                    service(worker_database, clock).verify(
                        mfa_database.identity_id,
                        code,
                        session_id=session.id,
                        device=DEVICE,
                    )
                )
            except ProblemError:
                return False
            return True
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))
    assert sorted(results) == [False, True]
    assert mfa_database.connection.execute(
        "SELECT count(*) FROM mfa_totp_consumption WHERE identity_id = %s AND time_step = %s",
        (mfa_database.identity_id, int(clock.now.timestamp()) // 30),
    ).fetchone() == (1,)


def test_recovery_code_is_single_use_and_warns_at_three_remaining(
    mfa_database: MfaDatabase,
) -> None:
    clock = MutableClock(START)
    mfa = service(mfa_database, clock)
    _, session = _session(mfa_database, clock)
    _, recovery_codes = run(
        _enroll_and_confirm(mfa, mfa_database.identity_id, session, clock)
    )
    results = [
        run(
            mfa.verify(
                mfa_database.identity_id,
                code,
                session_id=session.id,
                device=DEVICE,
            )
        )
        for code in recovery_codes[:7]
    ]
    assert [result.recovery_codes_remaining for result in results] == [9, 8, 7, 6, 5, 4, 3]
    with pytest.raises(ProblemError):
        run(
            mfa.verify(
                mfa_database.identity_id,
                recovery_codes[0],
                session_id=session.id,
                device=DEVICE,
            )
        )
    assert mfa_database.connection.execute(
        "SELECT count(*) FROM mfa_security_event WHERE event_type = 'recovery_codes_low'"
    ).fetchone() == (1,)


def test_repeated_failures_lock_only_the_factor_and_append_security_events(
    mfa_database: MfaDatabase,
) -> None:
    clock = MutableClock(START)
    mfa = service(mfa_database, clock, max_failed_attempts=3)
    _, session = _session(mfa_database, clock)
    secret, _ = run(_enroll_and_confirm(mfa, mfa_database.identity_id, session, clock))
    wrong = pyotp.TOTP(secret).at(clock.now + timedelta(seconds=60))

    for expected_status in (401, 401, 429):
        with pytest.raises(ProblemError) as raised:
            run(
                mfa.verify(
                    mfa_database.identity_id,
                    wrong,
                    session_id=session.id,
                    device=DEVICE,
                )
            )
        assert raised.value.code.value in (
            "unauthorized",
            "too-many-requests",
        )
        if expected_status == 429:
            assert raised.value.headers == {"Retry-After": "900"}

    assert mfa_database.connection.execute(
        "SELECT event_type FROM mfa_security_event ORDER BY id"
    ).fetchall() == [
        ("factor_failure",),
        ("factor_failure",),
        ("factor_failure",),
        ("factor_locked",),
    ]
    assert mfa_database.connection.execute(
        "SELECT privileged_role_grants FROM identity WHERE id = %s",
        (mfa_database.identity_id,),
    ).fetchone() == (0,)


def test_privileged_identity_without_factor_requires_enrollment_by_name(
    mfa_database: MfaDatabase,
) -> None:
    mfa = service(mfa_database, MutableClock(START))
    mfa_database.connection.execute(
        "UPDATE identity SET privileged_role_grants = 1 WHERE id = %s",
        (mfa_database.identity_id,),
    )
    assert mfa.access_requirement(mfa_database.identity_id) is MfaAccessRequirement.ENROLL


class FakeProvider:
    def __init__(self, identity_id: IdentityId) -> None:
        self._identity_id = identity_id

    async def authenticate(self, email: str, password: str) -> AuthResult:
        if (email, password) == ("mfa@example.test", "current password"):
            return AuthResult.success(self._identity_id)
        return AuthResult.invalid_credentials()

    async def create_identity(self, email: str, password: str) -> IdentityId:
        del email, password
        return self._identity_id

    async def change_password(self, identity_id: IdentityId, new: str) -> None:
        del identity_id, new

    async def verify_current_password(
        self, identity_id: IdentityId, password: str
    ) -> bool:
        return identity_id == self._identity_id and password == "current password"

    def verify_password_policy(self, password: str) -> PolicyResult:
        del password
        return PolicyResult()


def build_app(
    database: MfaDatabase,
    clock: MutableClock,
    mfa: MfaService,
) -> FastAPI:
    store = session_store(database, clock)
    app = FastAPI()
    app.include_router(router)

    @app.get("/protected")
    async def protected(
        session: SessionRecord = Depends(current_session),
    ) -> dict[str, str]:
        del session
        return {"status": "ok"}

    @app.post("/high-risk")
    @requires_recent_auth(max_age=timedelta(minutes=15))
    async def high_risk(
        request: Request,
        session: SessionRecord = Depends(current_session),
    ) -> dict[str, str]:
        del request, session
        return {"status": "ok"}

    app.dependency_overrides[get_identity_provider] = lambda: cast(
        IdentityProvider, FakeProvider(database.identity_id)
    )
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_mfa_service] = lambda: mfa

    @contextmanager
    def store_factory() -> Iterator[SessionStore]:
        yield store

    @contextmanager
    def mfa_factory() -> Iterator[MfaService]:
        yield mfa

    install_mfa_access_gate(app, mfa_factory)
    install_session_authentication(app, store_factory)
    install_csrf_protection(app)
    install_problem_details(app)
    app.state.recent_auth_clock = clock
    return app


async def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://testserver",
    )


def csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return {CSRF_HEADER_NAME: token}


def test_privileged_login_forces_enrollment_then_rotates_and_step_up_refreshes(
    mfa_database: MfaDatabase,
) -> None:
    clock = MutableClock(START)
    mfa = service(mfa_database, clock)
    app = build_app(mfa_database, clock, mfa)
    mfa_database.connection.execute(
        "UPDATE identity SET privileged_role_grants = 1 WHERE id = %s",
        (mfa_database.identity_id,),
    )

    async def scenario() -> tuple[httpx.Response, ...]:
        async with await client_for(app) as client:
            await client.get("/protected")
            login = await client.post(
                "/api/v1/auth/login",
                json={"email": "mfa@example.test", "password": "current password"},
                headers=csrf_headers(client),
            )
            before_enrollment = await client.get("/protected")
            enrollment = await client.post(
                "/api/v1/auth/mfa/enroll",
                json={"password": "current password"},
                headers=csrf_headers(client),
            )
            secret = enrollment.json()["secret"]
            first_session = client.cookies[SESSION_COOKIE_NAME]
            confirmation = await client.post(
                "/api/v1/auth/mfa/confirm",
                json={"code": pyotp.TOTP(secret).at(clock.now)},
                headers=csrf_headers(client),
            )
            confirmed_session = client.cookies[SESSION_COOKIE_NAME]
            after_enrollment = await client.get("/protected")
            clock.now += timedelta(minutes=16)
            stale = await client.post("/high-risk", headers=csrf_headers(client))
            verification = await client.post(
                "/api/v1/auth/mfa/verify",
                json={"code": pyotp.TOTP(secret).at(clock.now)},
                headers=csrf_headers(client),
            )
            after_step_up = await client.post(
                "/high-risk", headers=csrf_headers(client)
            )
            return (
                login,
                before_enrollment,
                enrollment,
                confirmation,
                after_enrollment,
                stale,
                verification,
                after_step_up,
                first_session != confirmed_session,
            )

    (
        login,
        before_enrollment,
        enrollment,
        confirmation,
        after_enrollment,
        stale,
        verification,
        after_step_up,
        rotated,
    ) = run(scenario())
    assert login.status_code == 204
    assert before_enrollment.status_code == 403
    assert before_enrollment.headers["www-authenticate"] == "mfa-enroll"
    assert enrollment.status_code == 200
    assert len(enrollment.json()["recovery_codes"]) == 10
    assert confirmation.status_code == 204 and rotated
    assert after_enrollment.status_code == 200
    assert stale.status_code == 403
    assert stale.headers["www-authenticate"] == "step-up"
    assert stale.json()["type"].endswith("/step-up-required")
    assert verification.status_code == 204
    assert after_step_up.status_code == 200


def test_identity_is_derived_from_session_and_foreign_factor_is_unchanged(
    mfa_database: MfaDatabase,
) -> None:
    clock = MutableClock(START)
    mfa = service(mfa_database, clock)
    _, own_session = _session(mfa_database, clock)
    _, foreign_session = _session_for_identity(
        mfa_database, clock, mfa_database.foreign_identity_id
    )
    own_enrollment = run(mfa.enroll(mfa_database.identity_id))
    foreign_enrollment = run(mfa.enroll(mfa_database.foreign_identity_id))
    run(
        mfa.confirm(
            mfa_database.identity_id,
            pyotp.TOTP(own_enrollment.secret).at(clock.now),
            session_id=own_session.id,
            device=DEVICE,
        )
    )
    run(
        mfa.confirm(
            mfa_database.foreign_identity_id,
            pyotp.TOTP(foreign_enrollment.secret).at(clock.now),
            session_id=foreign_session.id,
            device=DEVICE,
        )
    )
    own_ciphertext, foreign_ciphertext = mfa_database.connection.execute(
        "SELECT secret_ciphertext FROM mfa_factor ORDER BY identity_id"
    ).fetchall()
    assert own_ciphertext != foreign_ciphertext
    with pytest.raises(ProblemError):
        run(
            mfa.verify(
                mfa_database.identity_id,
                pyotp.TOTP(foreign_enrollment.secret).at(clock.now + timedelta(seconds=30)),
                session_id=own_session.id,
                device=DEVICE,
            )
        )
    assert mfa_database.connection.execute(
        "SELECT count(*) FROM mfa_factor WHERE identity_id = %s",
        (mfa_database.foreign_identity_id,),
    ).fetchone() == (1,)


def _session_for_identity(
    database: MfaDatabase,
    clock: MutableClock,
    identity_id: IdentityId,
) -> tuple[SessionStore, SessionRecord]:
    store = session_store(database, clock)
    issued = store.issue(identity_id, DEVICE)
    return store, issued.session


def test_migration_chain_constraints_append_only_guard_and_down_preserve_seeded_rows(
    mfa_database: MfaDatabase,
) -> None:
    connection = mfa_database.connection
    assert mfa_database.migration.revision == "20260825_0011"
    assert mfa_database.migration.down_revision == "20260825_0010"
    expected_tables = {
        "mfa_factor",
        "mfa_recovery_code",
        "mfa_security_event",
        "mfa_totp_consumption",
    }
    stored_tables = {
        row[0]
        for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename LIKE 'mfa_%'"
        ).fetchall()
    }
    assert stored_tables == expected_tables, (
        f"MISSING MFA tables: {sorted(expected_tables - stored_tables)}"
    )
    _, session = _session(mfa_database, MutableClock(START))
    connection.execute(
        """
        INSERT INTO mfa_security_event (
            identity_id, session_id, event_type, occurred_at, user_agent
        ) VALUES (%s, %s, 'factor_failure', %s, 'Other browser')
        """,
        (mfa_database.identity_id, session.id, START),
    )
    with pytest.raises(RaiseException, match="append-only"):
        connection.execute("DELETE FROM mfa_security_event")
    identity_before = connection.execute(
        "SELECT email FROM identity ORDER BY email"
    ).fetchall()
    sessions_before = connection.execute("SELECT id FROM auth_session ORDER BY id").fetchall()

    mfa_database.migration.downgrade(connection)
    assert connection.execute(
        "SELECT email FROM identity ORDER BY email"
    ).fetchall() == identity_before
    assert connection.execute("SELECT id FROM auth_session ORDER BY id").fetchall() == (
        sessions_before
    )
    assert connection.execute("SELECT to_regclass('public.mfa_factor')").fetchone() == (
        None,
    )
