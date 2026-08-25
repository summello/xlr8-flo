from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import cast

import httpx
import pytest
from fastapi import FastAPI

from flo.api.auth import (
    get_identity_provider,
    get_session_store,
    router,
)
from flo.kernel.errors import install_problem_details
from flo.kernel.identity import AuthResult, IdentityId, IdentityProvider, PolicyResult
from flo.kernel.session import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SECURITY_HEADERS,
    SESSION_COOKIE_NAME,
    RequestDevice,
    SessionStore,
    install_browser_security,
    install_csrf_protection,
    install_session_authentication,
)
from flo.kernel.tenancy.middleware import install_tenant_context

from .conftest import SessionDatabase
from .test_store import START, MutableClock, store


class SuccessfulProvider:
    def __init__(self, identity_id: IdentityId) -> None:
        self._identity_id = identity_id

    async def authenticate(self, email: str, password: str) -> AuthResult:
        if (email, password) == ("person@example.test", "correct safe passphrase"):
            return AuthResult.success(self._identity_id)
        return AuthResult.invalid_credentials()

    async def create_identity(self, email: str, password: str) -> IdentityId:
        del email, password
        return self._identity_id

    async def change_password(self, identity_id: IdentityId, new: str) -> None:
        del identity_id, new

    def verify_password_policy(self, password: str) -> PolicyResult:
        del password
        return PolicyResult()


def build_app(
    session_store: SessionStore,
    provider: IdentityProvider,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.get("/bootstrap")
    async def bootstrap() -> dict[str, str]:
        return {"status": "ok"}

    for method in ("post", "put", "patch", "delete"):

        async def unsafe() -> dict[str, str]:
            return {"unsafe": "accepted"}

        getattr(app, method)(f"/unsafe/{method}")(unsafe)

    @app.get("/explode")
    async def explode() -> None:
        raise RuntimeError("planted response failure")

    app.dependency_overrides[get_identity_provider] = lambda: provider
    app.dependency_overrides[get_session_store] = lambda: session_store

    @contextmanager
    def store_factory() -> Iterator[SessionStore]:
        yield session_store

    install_tenant_context(app)
    install_session_authentication(app, store_factory)
    install_csrf_protection(app)
    install_problem_details(app)
    install_browser_security(app)
    return app


def run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


async def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://testserver",
    )


def csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return {CSRF_HEADER_NAME: token}


def test_all_seven_headers_are_present_by_name_even_on_errors(
    session_database: SessionDatabase,
) -> None:
    expected = {
        "content-security-policy",
        "cross-origin-opener-policy",
        "permissions-policy",
        "referrer-policy",
        "strict-transport-security",
        "x-content-type-options",
        "x-frame-options",
    }
    assert {name.decode() for name in SECURITY_HEADERS} == expected

    app = build_app(
        store(session_database, MutableClock(START)),
        cast(IdentityProvider, SuccessfulProvider(session_database.identity_id)),
    )

    async def requests() -> list[httpx.Response]:
        async with await client_for(app) as client:
            return [
                await client.get("/bootstrap"),
                await client.post("/unsafe/post"),
                await client.get("/explode"),
                await client.get("/missing"),
            ]

    responses = run(requests())
    assert [response.status_code for response in responses] == [200, 403, 500, 404]
    for response in responses:
        missing = expected - set(response.headers)
        assert not missing, f"MISSING security headers: {sorted(missing)}"
        assert "unsafe-inline" not in response.headers["content-security-policy"]
        assert response.headers["strict-transport-security"].startswith("max-age=31536000")


@pytest.mark.parametrize("method", ["DELETE", "PATCH", "POST", "PUT"])
def test_every_unsafe_method_rejects_missing_and_mismatched_csrf(
    session_database: SessionDatabase,
    method: str,
) -> None:
    app = build_app(
        store(session_database, MutableClock(START)),
        cast(IdentityProvider, SuccessfulProvider(session_database.identity_id)),
    )
    path = f"/unsafe/{method.lower()}"

    async def requests() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with await client_for(app) as client:
            missing = await client.request(method, path)
            bootstrap = await client.get("/bootstrap")
            assert bootstrap.status_code == 200
            mismatch = await client.request(
                method, path, headers={CSRF_HEADER_NAME: "not-the-cookie"}
            )
            matching = await client.request(method, path, headers=csrf_headers(client))
            return missing, mismatch, matching

    missing, mismatch, matching = run(requests())
    assert missing.status_code == 403
    assert mismatch.status_code == 403
    assert matching.status_code == 200


def test_login_sets_exact_secure_cookie_attributes_and_rotates_existing_session(
    session_database: SessionDatabase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = MutableClock(START)
    session_store = store(session_database, clock)
    app = build_app(
        session_store,
        cast(IdentityProvider, SuccessfulProvider(session_database.identity_id)),
    )

    async def requests() -> tuple[httpx.Response, str, httpx.Response, str]:
        async with await client_for(app) as client:
            await client.get("/bootstrap")
            first = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "person@example.test",
                    "password": "correct safe passphrase",
                },
                headers=csrf_headers(client),
            )
            first_token = client.cookies[SESSION_COOKIE_NAME]
            clock.now += timedelta(minutes=1)
            second = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "person@example.test",
                    "password": "correct safe passphrase",
                },
                headers=csrf_headers(client),
            )
            return first, first_token, second, client.cookies[SESSION_COOKIE_NAME]

    first, first_token, second, second_token = run(requests())
    assert first.status_code == second.status_code == 204
    session_cookie = next(
        value
        for value in first.headers.get_list("set-cookie")
        if value.startswith(f"{SESSION_COOKIE_NAME}=")
    )
    assert "HttpOnly" in session_cookie
    assert "Secure" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "Path=/" in session_cookie
    assert "Domain=" not in session_cookie
    assert first_token != second_token
    assert first_token not in str(first.request.url)
    assert first_token not in caplog.text

    rows = session_database.connection.execute(
        "SELECT id, revoked_at FROM auth_session ORDER BY created_at, id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] != rows[1][0]
    assert rows[0][1] == clock.now
    assert rows[1][1] is None


def test_invalid_credentials_have_one_generic_unauthorized_response(
    session_database: SessionDatabase,
) -> None:
    app = build_app(
        store(session_database, MutableClock(START)),
        cast(IdentityProvider, SuccessfulProvider(session_database.identity_id)),
    )

    async def login(email: str, password: str) -> httpx.Response:
        async with await client_for(app) as client:
            await client.get("/bootstrap")
            return await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": password},
                headers=csrf_headers(client),
            )

    unknown = run(login("unknown@example.test", "wrong safe passphrase"))
    wrong = run(login("person@example.test", "wrong safe passphrase"))

    assert unknown.status_code == wrong.status_code == 401
    unknown_body = unknown.json()
    wrong_body = wrong.json()
    unknown_body.pop("correlation_id")
    wrong_body.pop("correlation_id")
    assert unknown_body == wrong_body


def test_listing_revocation_and_revoke_all_others_take_effect_on_next_request(
    session_database: SessionDatabase,
) -> None:
    clock = MutableClock(START)
    session_store = store(session_database, clock)
    current = session_store.issue(
        session_database.identity_id,
        RequestDevice("203.0.113.0/24", "Chrome on macOS"),
    )
    other = session_store.issue(
        session_database.identity_id,
        RequestDevice("2001:db8:abcd::/48", "Firefox on Linux"),
    )
    third = session_store.issue(
        session_database.identity_id,
        RequestDevice(None, "Safari on iOS"),
    )
    app = build_app(
        session_store,
        cast(IdentityProvider, SuccessfulProvider(session_database.identity_id)),
    )

    async def requests() -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
        async with await client_for(app) as current_client:
            current_client.cookies.set(SESSION_COOKIE_NAME, current.cookie_value())
            await current_client.get("/bootstrap")
            listing = await current_client.get("/api/v1/auth/sessions")
            revoked = await current_client.delete(
                f"/api/v1/auth/sessions/{other.session.id}",
                headers=csrf_headers(current_client),
            )
            all_others = await current_client.delete(
                "/api/v1/auth/sessions", headers=csrf_headers(current_client)
            )
            after = await current_client.get("/api/v1/auth/sessions")

        async with await client_for(app) as revoked_client:
            revoked_client.cookies.set(SESSION_COOKIE_NAME, other.cookie_value())
            rejected = await revoked_client.get("/api/v1/auth/sessions")
        return listing, revoked, all_others, after, rejected

    listing, revoked, all_others, after, rejected = run(requests())
    assert listing.status_code == 200
    assert len(listing.json()) == 3
    assert sum(item["current"] for item in listing.json()) == 1
    assert {item["ip_prefix"] for item in listing.json()} == {
        "203.0.113.0/24",
        "2001:db8:abcd::/48",
        None,
    }
    assert revoked.status_code == all_others.status_code == 204
    assert after.json() == [
        {
            "id": str(current.session.id),
            "created_at": START.isoformat().replace("+00:00", "Z"),
            "last_seen_at": START.isoformat().replace("+00:00", "Z"),
            "ip_prefix": "203.0.113.0/24",
            "user_agent": "Chrome on macOS",
            "current": True,
        }
    ]
    assert rejected.status_code == 401
    event_count = session_database.connection.execute(
        "SELECT count(*) FROM session_security_event WHERE session_id = %s",
        (other.session.id,),
    ).fetchone()
    assert event_count == (1,)
    assert session_store.authenticate(third.cookie_value(), RequestDevice(None, "Other")) is None


def test_unknown_or_foreign_session_id_is_not_found_and_logout_clears_cookies(
    session_database: SessionDatabase,
) -> None:
    clock = MutableClock(START)
    session_store = store(session_database, clock)
    current = session_store.issue(session_database.identity_id, RequestDevice(None, "Other"))
    app = build_app(
        session_store,
        cast(IdentityProvider, SuccessfulProvider(session_database.identity_id)),
    )

    async def requests() -> tuple[httpx.Response, httpx.Response]:
        async with await client_for(app) as client:
            client.cookies.set(SESSION_COOKIE_NAME, current.cookie_value())
            await client.get("/bootstrap")
            missing = await client.delete(
                "/api/v1/auth/sessions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                headers=csrf_headers(client),
            )
            logged_out = await client.post(
                "/api/v1/auth/logout", headers=csrf_headers(client)
            )
            return missing, logged_out

    missing, logged_out = run(requests())
    assert missing.status_code == 404
    assert logged_out.status_code == 204
    expired = "\n".join(logged_out.headers.get_list("set-cookie"))
    assert f'{SESSION_COOKIE_NAME}="";' in expired
    assert f'{CSRF_COOKIE_NAME}="";' in expired
    assert "Max-Age=0" in expired
