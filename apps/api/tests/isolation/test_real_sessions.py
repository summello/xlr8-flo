from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

import httpx
import psycopg
import pyotp
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import SecretStr

from flo.api import admin_users, auth, budget, fiscal, master, org, projects
from flo.kernel.authz import install_authorization
from flo.kernel.errors import install_problem_details
from flo.kernel.idempotency import install_idempotency
from flo.kernel.identity import install_mfa_access_gate
from flo.kernel.identity.local import build_local_identity_provider
from flo.kernel.session import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SessionStore,
    install_csrf_protection,
    install_session_authentication,
)
from flo.kernel.tenancy.middleware import install_tenant_context
from flo.modules.identity.resolver import AuthorizationResolver
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import (
    bootstrap_db as bootstrap_db,
)
from tests.org.test_bootstrap import (
    connection_url,
    create,
    settings,
)


def build_app(conn, *, remove_join=False):
    configured = settings(conn)
    configured.mfa_encryption_key = SecretStr("A" * 43)
    url = connection_url(conn)
    app = FastAPI()
    for module in (auth, admin_users, org, master, fiscal, projects, budget):
        app.include_router(module.router)
    app.dependency_overrides[auth.get_auth_settings] = lambda: configured

    @contextmanager
    def connection():
        with psycopg.connect(url, autocommit=True) as database:
            yield database

    def auth_connection():
        with connection() as database:
            yield database

    app.dependency_overrides[auth.get_auth_connection] = auth_connection

    class PlantedConnection:
        def __init__(self, database):
            self.database = database

        def transaction(self):
            return self.database.transaction()

        def execute(self, query, params=()):
            if remove_join:
                query = query.replace("user_agent, org_id", "user_agent, NULL::uuid AS org_id")
            return self.database.execute(query, params)

    @contextmanager
    def sessions():
        with connection() as database:
            yield SessionStore(
                PlantedConnection(database),
                idle_timeout=timedelta(hours=8),
                absolute_timeout=timedelta(hours=12),
            )

    @contextmanager
    def mfa_factory():
        with connection() as database:
            yield auth._mfa_service(database, configured)

    @contextmanager
    def resolver(scope):
        with connection() as database:
            yield AuthorizationResolver(database, scope)

    install_authorization(app, resolver)
    install_idempotency(app, connection)
    install_tenant_context(app)
    install_mfa_access_gate(app, mfa_factory)
    install_session_authentication(app, sessions)
    install_csrf_protection(app)
    install_problem_details(app)
    return app


def headers(client, key="command"):
    return {CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME], "Idempotency-Key": key}


async def login(client, email="admin@example.test", enroll=True):
    await client.get("/api/v1/auth/sessions")
    result = await client.post(
        "/api/v1/auth/login",
        headers=headers(client, "login"),
        json={"email": email, "password": "disposable bootstrap test passphrase"},
    )
    assert result.status_code == 204, result.text
    if enroll:
        blocked = await client.get("/api/v1/org/units")
        assert blocked.status_code == 403
        assert blocked.headers["www-authenticate"] == "mfa-enroll"
        enrollment = await client.post(
            "/api/v1/auth/mfa/enroll",
            headers=headers(client, email + "-enroll"),
            json={"password": "disposable bootstrap test passphrase"},
        )
        assert enrollment.status_code == 200, enrollment.text
        confirmed = await client.post(
            "/api/v1/auth/mfa/confirm",
            headers=headers(client, email + "-confirm"),
            json={"code": pyotp.TOTP(enrollment.json()["secret"]).now()},
        )
        assert confirmed.status_code == 204, confirmed.text


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver")


async def unit(client, code="BU"):
    response = await client.post(
        "/api/v1/org/units",
        headers=headers(client, code),
        json={"code": code, "name": "Business unit", "kind": "bu"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_real_session_bootstrap_login_tenancy_and_idempotency(bootstrap_db):
    conn = bootstrap_db
    tenant = create(conn)
    foreign = create(conn, "SECOND", "second@example.test")
    app = build_app(conn)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            missing = await browser.post(
                "/api/v1/org/units",
                headers={CSRF_HEADER_NAME: browser.cookies[CSRF_COOKIE_NAME]},
                json={"code": "BU", "name": "Business unit", "kind": "bu"},
            )
            assert missing.status_code == 400
            record = await unit(browser)
            replay = await browser.post(
                "/api/v1/org/units",
                headers=headers(browser, "BU"),
                json={"code": "BU", "name": "Business unit", "kind": "bu"},
            )
            assert replay.status_code == 201
            assert replay.json()["id"] == record
            # GET body is ignored, as are undeclared query/header scope claims.
            result = await browser.request(
                "GET",
                "/api/v1/org/units",
                params={"org_id": str(foreign.org_id)},
                headers={"Org-Id": str(foreign.org_id)},
                json={"org_id": str(foreign.org_id)},
            )
            assert result.status_code == 200
            assert [row["id"] for row in result.json()["rows"]] == [record]
            spoof = await browser.post(
                "/api/v1/org/units",
                headers=headers(browser, "spoof"),
                json={"code": "BAD", "name": "Bad", "kind": "bu", "org_id": str(foreign.org_id)},
            )
            assert spoof.status_code == 422  # writes reject extra fields; never select that org
            with conn.transaction():
                assert (
                    conn.execute(
                        "SELECT org_id FROM identity_membership WHERE identity_id = "
                        "(SELECT id FROM identity WHERE email=%s)",
                        ("admin@example.test",),
                    ).fetchone()[0]
                    == tenant.org_id
                )

    asyncio.run(scenario())


def test_real_session_foreign_ids_each_route_family(bootstrap_db):
    conn = bootstrap_db
    create(conn)
    create(conn, "SECOND", "second@example.test")
    app = build_app(conn)

    async def scenario():
        async with client(app) as a, client(app) as b:
            await login(a)
            await login(b, "second@example.test")
            bu = await unit(a)
            masters = {}
            for kind, attributes in [
                ("department", {}),
                ("ledger_account", {"account_type": "expense"}),
            ]:
                response = await a.post(
                    "/api/v1/master/" + kind,
                    headers=headers(a, kind),
                    json={
                        "code": "D",
                        "name": "Master",
                        "attributes": attributes,
                        "effective_from": "2000-01-01",
                    },
                )
                assert response.status_code == 201, response.text
                masters[kind] = response.json()["id"]
            response = await a.post(
                "/api/v1/projects",
                headers=headers(a, "project"),
                json={
                    "bu_id": bu,
                    "name": "Project",
                    "department_code": "D",
                    "ledger_account_code": "D",
                    "currency": "USD",
                },
            )
            assert response.status_code == 201, response.text
            project = response.json()["id"]
            response = await a.post(
                f"/api/v1/org/units/{bu}/addresses",
                headers=headers(a, "address"),
                json={
                    "kind": "ship_to",
                    "line1": "Test",
                    "city": "Test",
                    "country": "IN",
                    "effective_from": "2000-01-01",
                },
            )
            assert response.status_code == 201, response.text
            address = response.json()["id"]
            response = await a.post(
                "/api/v1/fiscal/years/2026:generate", headers=headers(a, "year")
            )
            assert response.status_code == 200, response.text
            period = response.json()[0]["id"]
            identity = conn.execute(
                "SELECT id FROM identity WHERE email='admin@example.test'"
            ).fetchone()[0]
            # Each existing tenant id-bearing route family, using B's real session.
            cases = [
                ("GET", f"/api/v1/org/units/{bu}", None),
                ("GET", f"/api/v1/org/units/{bu}/addresses", None),
                (
                    "PATCH",
                    f"/api/v1/org/units/{bu}/addresses/{address}",
                    {"effective_to": "2026-12-31"},
                ),
                ("GET", f"/api/v1/org/units/{bu}/settings/funding_mode/effective", None),
                ("GET", f"/api/v1/projects/{project}", None),
                ("GET", f"/api/v1/projects/{project}/children", None),
                ("GET", f"/api/v1/projects/{project}/tree", None),
                ("GET", f"/api/v1/projects/{project}/balance", None),
                ("GET", f"/api/v1/projects/{project}/balance/reconcile", None),
                ("GET", f"/api/v1/projects/{project}/ledger", None),
                ("POST", f"/api/v1/master/department/{masters['department']}:deactivate", None),
                ("POST", f"/api/v1/fiscal/periods/{period}:close", None),
                ("GET", f"/api/v1/admin/users/{identity}/effective-access", None),
            ]
            for index, (method, path, body) in enumerate(cases):
                response = await b.request(
                    method, path, json=body, headers=headers(b, "foreign-" + str(index))
                )
                assert response.status_code == 404, (path, response.status_code, response.text)

    asyncio.run(scenario())


def test_no_membership_identity_routes_work_tenant_routes_unauthorized(bootstrap_db):
    conn = bootstrap_db
    provider = asyncio.run(build_local_identity_provider(conn, settings(conn)))
    identity = asyncio.run(
        provider.create_identity("admin@example.test", "disposable bootstrap test passphrase")
    )
    app = build_app(conn)

    async def scenario():
        async with client(app) as browser:
            await login(browser, enroll=False)
            assert (await browser.get("/api/v1/auth/sessions")).status_code == 200
            assert (await browser.get("/api/v1/org/units")).status_code == 401
            # Every tenant route, including record routes and each HTTP method.
            for route in app.routes:
                if not isinstance(route, APIRoute) or route.path.startswith("/api/v1/auth/"):
                    continue
                path = route.path
                for name in route.param_convertors:
                    value = (
                        "funding_mode"
                        if name == "key"
                        else "department"
                        if name == "kind"
                        else "2026"
                        if name == "fiscal_year"
                        else str(UUID(int=1))
                    )
                    path = path.replace("{" + name + "}", value)
                for method in route.methods:
                    response = await browser.request(
                        method, path, headers=headers(browser, path + method)
                    )
                    assert response.status_code == 401, (path, response.status_code, response.text)
            # Plant membership removal after a valid real session: tenant success
            # must fail while the identity route still works.
            assert (
                conn.execute(
                    "SELECT count(*) FROM identity_membership WHERE identity_id=%s", (identity,)
                ).fetchone()[0]
                == 0
            )

    asyncio.run(scenario())


def test_membership_and_session_org_plants_break_end_to_end(bootstrap_db):
    conn = bootstrap_db
    create(conn)
    app = build_app(conn)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert (await browser.get("/api/v1/org/units")).status_code == 200
            planted = build_app(conn, remove_join=True)
            async with client(planted) as mutant:
                mutant.cookies.update(browser.cookies)
                response = await mutant.get("/api/v1/org/units")
                with pytest.raises(AssertionError):
                    assert response.status_code == 200
                assert response.status_code == 401
            # A chosen organization is now session-owned. Plant its removal too.
            conn.execute("DELETE FROM identity_membership")
            conn.execute("UPDATE auth_session SET org_id = NULL")
            response = await browser.get("/api/v1/org/units")
            with pytest.raises(AssertionError):
                assert response.status_code == 200
            assert response.status_code == 401
            assert (await browser.get("/api/v1/auth/sessions")).status_code == 200

    asyncio.run(scenario())
