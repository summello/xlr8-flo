"""Production browser login regressions and real-Postgres failure evidence."""

import asyncio
import concurrent.futures
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg
import pytest
from pydantic import ValidationError

from flo.api.auth import get_auth_connection, get_auth_settings, get_login_throttle
from flo.api.health import app
from flo.api.origin_auth import get_origin_settings
from flo.kernel.config import Settings
from flo.kernel.errors import ProblemError
from flo.kernel.identity.throttle import LoginThrottle, _key
from flo.kernel.migrate import discover_migrations

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def db():
    import os

    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from flo.kernel.migrate import apply_migrations

    url = (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql://flo:flo-local@127.0.0.1:5432/flo_test"
    )
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    name = f"throttle_{uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    params = conninfo_to_dict(url)
    params["dbname"] = name
    isolated = make_conninfo(**params)
    apply_migrations(discover_migrations(ROOT / "migrations"), isolated)
    with psycopg.connect(isolated, autocommit=True) as connection:
        yield connection, isolated
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def browser(db, monkeypatch):
    connection, url = db
    settings = Settings(
        database_url=url,
        origin_shared_secret="test-only-origin-credential-123456789",
        identity_argon2_time_cost=1,
        identity_argon2_memory_cost_kib=8192,
        identity_argon2_parallelism=1,
        mfa_encryption_key="A" * 43,
        login_throttle_jitter_min_ms=0,
        login_throttle_jitter_max_ms=0,
    )
    now = [datetime.now(UTC)]
    throttle = LoginThrottle(connection, settings, clock=lambda: now[0])
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_auth_settings] = lambda: settings
    app.dependency_overrides[get_origin_settings] = lambda: settings
    app.dependency_overrides[get_auth_connection] = lambda: connection
    app.dependency_overrides[get_login_throttle] = lambda: throttle
    monkeypatch.setattr("flo.kernel.session.client_address._settings", lambda: settings)
    try:
        yield settings, now, throttle
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


@asynccontextmanager
async def client(settings):
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
    await c.get("/healthz")
    c.headers.update(
        {
            "X-CSRF-Token": c.cookies.get("flo_csrf"),
            "X-FLO-Origin-Secret": settings.origin_shared_secret.get_secret_value(),
            "X-FLO-Client-IP": "203.0.113.27",
        }
    )
    try:
        yield c
    finally:
        await c.aclose()


async def attempt(c, email="person@example.test", password="wrong safe password"):
    return await c.post("/api/v1/auth/login", json={"email": email, "password": password})


def uniform(response):
    assert response.status_code == 401, response.text
    body = response.json()
    correlation_id = body["correlation_id"]
    assert "@example.test" not in response.text
    assert "203.0.113.27" not in response.text
    return response.content.replace(correlation_id.encode(), b"<correlation>")


def test_identity_limit_uniformity_expiry_and_no_argon2(db, browser, caplog):
    from flo.kernel.identity import build_local_identity_provider

    connection, _ = db
    settings, now, _ = browser

    async def scenario():
        provider = await build_local_identity_provider(connection, settings)
        await provider.create_identity("person@example.test", "correct safe password")
        async with client(settings) as c:
            unknown = await attempt(c, "missing@example.test")
            wrong = await attempt(c)
            for _ in range(9):
                assert (await attempt(c)).status_code == 401
            with patch("argon2.PasswordHasher.verify", side_effect=AssertionError("argon2 called")):
                blocked = await attempt(c, password="correct safe password")
            assert "retry-after" not in blocked.headers
            assert uniform(unknown) == uniform(wrong) == uniform(blocked)
            now[0] += timedelta(seconds=901)
            assert (await attempt(c, password="correct safe password")).status_code == 204

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    rows = db[0].execute("SELECT kind,key_hash,outcome FROM login_attempt").fetchall()
    evidence = repr(rows) + caplog.text
    assert "person@example.test" not in evidence
    assert "203.0.113.27" not in evidence
    assert "auth.login_throttled" in caplog.text
    assert any(row[2] == "throttled" for row in rows)


def test_client_limit_retry_and_identity_reset(db, browser):
    settings, _, throttle = browser

    async def scenario():
        async with client(settings) as c:
            for i in range(31):
                response = await attempt(c, f"missing{i}@example.test")
                assert response.status_code == 401
            response = await attempt(c, "another@example.test")
            assert response.headers["retry-after"] == "900"

    asyncio.run(scenario())
    assert throttle.check_client("203.0.113.27")
    for _ in range(4):
        throttle.record_failure("reset@example.test", "198.51.100.2")
    throttle.record_success("reset@example.test")
    assert throttle._count("identity", _key("identity", "reset@example.test")) == 0
    assert throttle._count("client", _key("client", "198.51.100.2")) == 4


def test_identity_variants_share_failure_budget_and_skip_argon2(db, browser):
    from flo.kernel.identity import build_local_identity_provider

    connection, _ = db
    settings, _, _ = browser

    async def scenario():
        provider = await build_local_identity_provider(connection, settings)
        await provider.create_identity("alice@example.test", "correct safe password")
        variants = ["Alice@Example.test", "aLICE@example.test", "  ALICE@EXAMPLE.TEST  "]
        async with client(settings) as c:
            wrong = None
            for i in range(10):
                response = await attempt(c, variants[i % len(variants)])
                assert response.status_code == 401
                if wrong is None:
                    wrong = response
                assert uniform(response) == uniform(wrong)
            for email in ["AlIcE@eXaMpLe.test", "  AlIcE@eXaMpLe.test  "]:
                with patch(
                    "argon2.PasswordHasher.verify", side_effect=AssertionError("argon2 called")
                ) as verify:
                    blocked = await attempt(c, email, "correct safe password")
                verify.assert_not_called()
                assert "retry-after" not in blocked.headers
                assert uniform(blocked) == uniform(wrong)

    asyncio.run(scenario())
    assert connection.execute(
        "SELECT key_hash, count(*) FROM login_attempt "
        "WHERE kind='identity' AND outcome='failed' GROUP BY key_hash"
    ).fetchall() == [(_key("identity", "alice@example.test"), 10)]


@pytest.mark.parametrize("method", ["check", "record_failure"])
@pytest.mark.parametrize("normalization", [".casefold()", ".strip()"])
def test_identity_variant_regression_detects_missing_normalization(
    db, browser, monkeypatch, method, normalization
):
    import inspect
    import textwrap

    from flo.kernel.identity import throttle

    source = textwrap.dedent(inspect.getsource(getattr(LoginThrottle, method)))
    assert normalization in source
    namespace = dict(vars(throttle))
    exec(compile(source.replace(normalization, ""), "<planted-normalization>", "exec"), namespace)
    monkeypatch.setattr(LoginThrottle, method, namespace[method])
    with pytest.raises(AssertionError, match="Expected 'verify' to not have been called"):
        test_identity_variants_share_failure_budget_and_skip_argon2(db, browser)


def test_concurrent_failures_keep_every_row_and_purge(db):
    connection, url = db
    settings = Settings()
    now = datetime.now(UTC)

    def failure(_):
        with psycopg.connect(url, autocommit=True) as conn:
            LoginThrottle(conn, settings, clock=lambda: now).record_failure(
                "burst@example.test", "192.0.2.1"
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(failure, range(20)))
    assert connection.execute(
        "SELECT count(*) FROM login_attempt WHERE kind='identity'"
    ).fetchone() == (20,)
    throttle = LoginThrottle(connection, settings, clock=lambda: now)
    with pytest.raises(ProblemError) as error:
        asyncio.run(throttle.check("burst@example.test", "192.0.2.1"))
    assert error.value.code.value == "unauthorized"
    connection.execute("UPDATE login_attempt SET occurred_at=%s", (now - timedelta(hours=25),))
    throttle.record_client_failure("192.0.2.2")
    assert connection.execute("SELECT count(*) FROM login_attempt").fetchone() == (1,)


@pytest.mark.parametrize(
    "field,low,high",
    [
        ("login_identity_max_failures", 3, 100),
        ("login_client_max_failures", 5, 1000),
        ("login_window_seconds", 60, 86400),
        ("login_throttle_jitter_min_ms", 0, 2000),
        ("login_throttle_jitter_max_ms", 0, 5000),
    ],
)
@pytest.mark.parametrize("side", ["below", "above"])
def test_settings_bounds(field, low, high, side):
    with pytest.raises(ValidationError):
        Settings(**{field: low - 1 if side == "below" else high + 1})


def test_jitter_order():
    with pytest.raises(ValidationError):
        Settings(login_throttle_jitter_min_ms=301)
    Settings(login_throttle_jitter_min_ms=300, login_throttle_jitter_max_ms=300)


def test_migration_reverses_and_preserves_existing_data(db):
    connection, _ = db
    revision = next(
        item for item in discover_migrations(ROOT / "migrations")
        if item.revision == "20261009_0029"
    )
    connection.execute("CREATE TABLE preserved (value text)")
    connection.execute("INSERT INTO preserved VALUES ('keep')")
    revision.downgrade(connection)
    assert connection.execute("SELECT to_regclass('login_attempt')").fetchone() == (None,)
    assert connection.execute("SELECT value FROM preserved").fetchone() == ("keep",)
    revision.upgrade(connection)
    from psycopg.errors import CheckViolation

    for kind, key, outcome in [
        ("bad", "a" * 64, "failed"),
        ("client", "plaintext", "failed"),
        ("client", "a" * 64, "bad"),
    ]:
        with pytest.raises(CheckViolation):
            connection.execute(
                "INSERT INTO login_attempt(kind,key_hash,outcome,occurred_at) "
                "VALUES (%s,%s,%s,now())",
                (kind, key, outcome),
            )


@pytest.mark.parametrize("mutation", ["unlimited", "different_response"])
def test_login_regression_detects_planted_bypasses(db, browser, caplog, monkeypatch, mutation):
    settings, _, _ = browser
    if mutation == "unlimited":
        settings.login_identity_max_failures = 10**9
    else:
        from flo.kernel.errors import ProblemError

        def distinct(code, **kwargs):
            return ProblemError(code, detail="Login throttled", **kwargs)

        monkeypatch.setattr("flo.kernel.identity.throttle.ProblemError", distinct)
    with pytest.raises(AssertionError):
        test_identity_limit_uniformity_expiry_and_no_argon2(db, browser, caplog)


def test_setting_guard_detects_relaxed_bound(tmp_path, monkeypatch):
    import importlib.util
    import sys

    source = (ROOT / "apps/api/src/flo/kernel/config.py").read_text()
    planted = tmp_path / "planted_config.py"
    planted.write_text(source.replace("default=10, ge=3, le=100", "default=10, ge=3, le=101"))
    spec = importlib.util.spec_from_file_location("planted_login_config", planted)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setitem(globals(), "Settings", module.Settings)
    with pytest.raises(pytest.fail.Exception):
        test_settings_bounds("login_identity_max_failures", 3, 100, "above")


def test_twenty_production_login_failures_preserve_rows(db, browser):
    settings, now, _ = browser
    settings.login_identity_max_failures = 100
    settings.login_client_max_failures = 100
    _, url = db

    def connection():
        with psycopg.connect(url, autocommit=True) as conn:
            yield conn

    app.dependency_overrides[get_auth_connection] = connection
    app.dependency_overrides.pop(get_login_throttle)

    async def scenario():
        async with client(settings) as c:
            responses = await asyncio.gather(*(attempt(c, "burst@example.test") for _ in range(20)))
            assert all(response.status_code == 401 for response in responses)

    asyncio.run(scenario())
    rows = db[0].execute("SELECT kind, count(*) FROM login_attempt GROUP BY kind").fetchall()
    assert dict(rows) == {"identity": 20, "client": 20}
    settings.login_identity_max_failures = 20
    with pytest.raises(ProblemError) as error:
        asyncio.run(
            LoginThrottle(db[0], settings, clock=lambda: now[0]).check(
                "burst@example.test", "203.0.113.27"
            )
        )
    assert error.value.code.value == "unauthorized"


def test_jitter_and_ipv6_client_counter(db, monkeypatch):
    from starlette.requests import Request

    from flo.kernel.session.client_address import throttle_client_value

    connection, _ = db
    settings = Settings(login_identity_max_failures=3, login_client_max_failures=5)
    delays = []
    bounds = []

    async def sleep(delay):
        delays.append(delay)

    def rng(low, high):
        bounds.append((low, high))
        return low

    monkeypatch.setattr("flo.kernel.identity.throttle.asyncio.sleep", sleep)
    throttle = LoginThrottle(connection, settings, rng=rng)

    def value(address):
        return throttle_client_value(
            Request({"type": "http", "headers": [], "client": (address, 1)})
        )

    first = value("2001:db8:abcd:1234::1")
    for _ in range(5):
        throttle.record_client_failure(first)
    assert throttle.check_client(value("2001:db8:abcd:1234::2"))
    assert not throttle.check_client(value("2001:db8:abcd:1235::1"))
    with pytest.raises(ProblemError):
        asyncio.run(throttle.check("unknown@example.test", first))
    assert bounds == [(150, 300)]
    assert delays == [0.15]


def test_successful_production_login_resets_only_identity(db, browser):
    from flo.kernel.identity import build_local_identity_provider

    connection, _ = db
    settings, _, throttle = browser

    async def scenario():
        provider = await build_local_identity_provider(connection, settings)
        await provider.create_identity("person@example.test", "correct safe password")
        async with client(settings) as c:
            for _ in range(2):
                assert (await attempt(c)).status_code == 401
            assert (await attempt(c, password="correct safe password")).status_code == 204

    asyncio.run(scenario())
    assert throttle._count("identity", _key("identity", "person@example.test")) == 0
    assert throttle._count("client", _key("client", "203.0.113.27")) == 2
    row = connection.execute("SELECT ip_prefix FROM auth_session").fetchone()
    assert str(row[0]) == "203.0.113.0/24"
