"""E05-S12 production-stack organization selection and isolation regressions."""

import asyncio
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.session.store import RequestDevice, RotationReason, SessionStore
from flo.kernel.tenancy import selection
from tests.isolation.test_real_sessions import client, headers, login, unit
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import admin_id, connection_url, create
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db


@pytest.fixture
def multi(bootstrap_db, monkeypatch):
    conn = bootstrap_db
    a = create(conn, tenant_label="EMEA")
    b = create(conn, "SECOND", "second@example.test", tenant_label="APAC")
    outsider = create(conn, "THIRD", "third@example.test")
    identity = admin_id(conn)
    conn.execute(
        "INSERT INTO identity_membership(identity_id, org_id) VALUES (%s,%s)", (identity, b.org_id)
    )
    monkeypatch.setenv("DATABASE_URL", connection_url(conn))
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", "A" * 43)
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_MEMORY_COST_KIB", "8192")
    from flo.api.origin_auth import require_origin_secret

    app.dependency_overrides[require_origin_secret] = lambda: None
    app.middleware_stack = None
    yield conn, a, b, outsider, identity
    app.dependency_overrides.pop(require_origin_secret)
    app.middleware_stack = None


async def select(browser, org, key="shared"):
    response = await browser.post(
        "/api/v1/auth/organization", json={"org_id": str(org)}, headers=headers(browser, key)
    )
    assert response.status_code == 204, response.text


async def chosen(browser):
    response = await browser.get("/api/v1/auth/organizations")
    assert response.status_code == 200, response.text
    return response.json()["current_org_id"]


def test_selection_isolation_and_no_replay(multi):
    conn, a, b, outsider, identity = multi

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for path in ("/api/v1/auth/sessions", "/api/v1/org/units"):
                response = await browser.get(path)
                assert response.status_code == 403
                assert response.headers["www-authenticate"] == "org-select"
            listing = (await browser.get("/api/v1/auth/organizations")).json()
            assert {item["org_id"] for item in listing["items"]} == {str(a.org_id), str(b.org_id)}
            assert {item["tenant_label"] for item in listing["items"]} == {"EMEA", "APAC"}
            errors = []
            for invalid in (outsider.org_id, uuid4()):
                response = await browser.post(
                    "/api/v1/auth/organization",
                    json={"org_id": str(invalid)},
                    headers=headers(browser),
                )
                assert response.status_code == 404
                body = response.json()
                body.pop("correlation_id")
                errors.append(body)
                assert await chosen(browser) is None
            assert errors[0] == errors[1]
            await select(browser, a.org_id)
            listing = (await browser.get("/api/v1/auth/organizations")).json()
            assert listing["items"][0]["org_id"] == str(a.org_id)
            assert listing["items"][0]["last_used_at"] is not None
            before = conn.execute("SELECT * FROM session_security_event").fetchall()
            used = conn.execute(
                "SELECT last_used_at FROM identity_membership WHERE identity_id=%s AND org_id=%s",
                (identity, a.org_id),
            ).fetchone()
            # Repeating the same selection is a no-op 204, never a replay.
            await select(browser, a.org_id)
            assert conn.execute("SELECT * FROM session_security_event").fetchall() == before
            assert (
                conn.execute(
                    "SELECT last_used_at FROM identity_membership "
                    "WHERE identity_id=%s AND org_id=%s",
                    (identity, a.org_id),
                ).fetchone()
                == used
            )
            assert before[-1][3] == "organization_switched"
            assert before[-1][2] == identity
            record = await unit(browser, "A")
            response = await browser.request(
                "GET",
                "/api/v1/org/units",
                params={"org_id": str(b.org_id)},
                headers={"Org-Id": str(b.org_id)},
                json={"org_id": str(b.org_id)},
            )
            assert [row["id"] for row in response.json()["rows"]] == [record]
            await select(browser, b.org_id)
            assert await chosen(browser) == str(b.org_id)
            assert (await browser.get(f"/api/v1/org/units/{record}")).status_code == 404
            # The organization admin grant in A grants no permission in B.
            assert (await browser.get("/api/v1/org/units")).status_code == 403
            await select(browser, a.org_id)
            assert await chosen(browser) == str(a.org_id)
            assert (await browser.get(f"/api/v1/org/units/{record}")).status_code == 200
            response = await browser.post(
                "/api/v1/auth/organization",
                json={"org_id": str(b.org_id)},
                headers={"X-CSRF-Token": browser.cookies["flo_csrf"]},
            )
            assert response.status_code == 204
            assert await chosen(browser) == str(b.org_id)
            assert (
                conn.execute(
                    "SELECT count(*) FROM idempotency_key WHERE endpoint LIKE '%auth/organization%'"
                ).fetchone()[0]
                == 0
            )

    asyncio.run(scenario())


def test_multi_org_mfa_gate_wins_and_verification_completes(multi):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await select(browser, multi[1].org_id)
            await browser.post("/api/v1/auth/logout", headers=headers(browser))
            await login(browser, enroll=False)
            response = await browser.get("/api/v1/auth/sessions")
            assert response.status_code == 403
            assert response.headers["www-authenticate"] == "mfa"
            # A real recovery code is generated through the enrollment helper in
            # the first case; verification here uses the persisted TOTP factor.
            import pyotp
            from pydantic import SecretStr

            from flo.api.auth import _mfa_service
            from tests.org.test_bootstrap import settings

            configured = settings(multi[0])
            configured.mfa_encryption_key = SecretStr("A" * 43)
            service = _mfa_service(multi[0], configured)
            factor = (
                multi[0]
                .execute(
                    "SELECT secret_ciphertext FROM mfa_factor WHERE identity_id=%s", (multi[4],)
                )
                .fetchone()
            )
            secret = service._cipher.decrypt(multi[4], factor[0])
            response = await browser.post(
                "/api/v1/auth/mfa/verify",
                headers=headers(browser),
                json={"code": pyotp.TOTP(secret).at(__import__("time").time() + 30)},
            )
            assert response.status_code == 204, response.text
            assert (await browser.get("/api/v1/org/units")).headers[
                "www-authenticate"
            ] == "org-select"
            await select(browser, multi[1].org_id)

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", list(RotationReason))
def test_rotation_preserves_choice_except_login_and_resolution_is_one_query(multi, reason):
    conn, a, b, _, identity = multi
    from datetime import timedelta

    queries = []

    class Counted:
        def transaction(self):
            return conn.transaction()

        def execute(self, query, params=()):
            queries.append(query)
            cursor = conn.execute(query, params)
            if "FROM refreshed" in query:
                rows = cursor.fetchall()
                assert len(rows) == 1, "session resolution duplicated the session"

                class SingleRow:
                    def fetchone(self):
                        return rows[0]

                return SingleRow()
            return cursor

    store = SessionStore(
        Counted(), idle_timeout=timedelta(hours=8), absolute_timeout=timedelta(hours=12)
    )
    issued = store.issue(identity, RequestDevice(None, "test"))
    # Existing active-identity lock plus the INSERT; no membership round trip.
    assert len(queries) == 2
    assert "INSERT INTO auth_session" in queries[1]
    queries.clear()
    current = store.authenticate(issued.cookie_value(), RequestDevice(None, "test"))
    assert len(queries) == 1 and current.org_selection_required and current.org_id is None
    conn.execute("UPDATE auth_session SET org_id=%s WHERE id=%s", (a.org_id, current.id))
    current = store.authenticate(issued.cookie_value(), RequestDevice(None, "test"))
    rotated = store.rotate(current, identity, RequestDevice(None, "test"), reason)
    assert rotated.session.org_id == (None if reason is RotationReason.LOGIN else a.org_id)


def test_selection_allowlist_plant_blocks_selection(multi, monkeypatch):
    monkeypatch.setattr(
        selection,
        "ORGANIZATION_SELECTION_PATHS",
        selection.ORGANIZATION_SELECTION_PATHS - {"/api/v1/auth/organization"},
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            with pytest.raises(AssertionError):
                await select(browser, multi[1].org_id)

    asyncio.run(scenario())


def test_tenant_label_authorization_validation_and_isolation(multi):
    from psycopg.errors import CheckViolation

    # Plant directly in the registry as well: the database guard must bite even
    # if a future caller bypasses the service validator.
    for label in ("", "x" * 61, "bad\nlabel"):
        with pytest.raises(CheckViolation), multi[0].transaction():
            multi[0].execute(
                "UPDATE organization_code SET tenant_label=%s WHERE org_id=%s",
                (label, multi[1].org_id),
            )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await select(browser, multi[1].org_id)
            for label in ("", "x" * 61, "bad\nlabel"):
                response = await browser.put(
                    "/api/v1/org/tenant-label",
                    json={"tenant_label": label},
                    headers=headers(browser),
                )
                assert response.status_code == 422
            for label in ("Europe", None):
                response = await browser.put(
                    "/api/v1/org/tenant-label",
                    json={"tenant_label": label},
                    headers=headers(browser),
                )
                assert response.status_code == 200, response.text
                assert response.json()["tenant_label"] == label
            assert (
                multi[0]
                .execute(
                    "SELECT tenant_label FROM organization_code WHERE org_id=%s", (multi[2].org_id,)
                )
                .fetchone()[0]
                == "APAC"
            )
            await select(browser, multi[2].org_id)
            assert (
                await browser.put(
                    "/api/v1/org/tenant-label",
                    json={"tenant_label": "Forbidden"},
                    headers=headers(browser),
                )
            ).status_code == 403

    asyncio.run(scenario())


def test_migration_backfill_downgrade_preservation_and_guard(multi):
    from flo.kernel.migrate import discover_migrations
    from tests.org.test_bootstrap import ROOT

    conn, a, b, _, identity = multi
    migration = discover_migrations(ROOT / "migrations")[-1]
    snapshot = conn.execute(
        "SELECT * FROM identity_membership ORDER BY identity_id,org_id"
    ).fetchall()
    with pytest.raises(Exception, match="multiple organization memberships"), conn.transaction():
        migration.downgrade(conn)
    assert (
        conn.execute("SELECT * FROM identity_membership ORDER BY identity_id,org_id").fetchall()
        == snapshot
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM organization_code c JOIN organization o ON o.id=c.org_id "
            "WHERE c.display_name<>o.name"
        ).fetchone()[0]
        == 0
    )
    conn.execute(
        "DELETE FROM identity_membership WHERE identity_id=%s AND org_id=%s", (identity, b.org_id)
    )
    from datetime import timedelta

    store = SessionStore(
        conn, idle_timeout=timedelta(hours=8), absolute_timeout=timedelta(hours=12)
    )
    issued = store.issue(identity, RequestDevice(None, "test"))
    with conn.transaction():
        migration.downgrade(conn)
        assert (
            conn.execute(
                "SELECT org_id FROM identity_membership WHERE identity_id=%s", (identity,)
            ).fetchone()[0]
            == a.org_id
        )
        migration.upgrade(conn)
    assert (
        conn.execute(
            "SELECT org_id FROM auth_session WHERE id=%s", (issued.session.id,)
        ).fetchone()[0]
        == a.org_id
    )


@pytest.mark.parametrize("plant", ["membership", "bypass", "mfa_verify", "mfa_order"])
def test_production_selection_and_mfa_plants(multi, monkeypatch, plant):
    if plant == "bypass":
        from flo.kernel.idempotency import middleware

        monkeypatch.setattr(middleware, "IDEMPOTENCY_BYPASS_PATHS", frozenset({"/api/v1/imports"}))
    elif plant == "membership":
        import inspect
        import textwrap

        from flo.api import auth
        from flo.modules.identity import service

        source = textwrap.dedent(inspect.getsource(service.select_identity_organization))
        source = source.replace("if membership is None:", "if False:")
        namespace = dict(service.__dict__)
        exec(source, namespace)
        monkeypatch.setattr(
            auth, "select_identity_organization", namespace["select_identity_organization"]
        )
    elif plant == "mfa_verify":
        monkeypatch.setattr(
            selection,
            "ORGANIZATION_SELECTION_PATHS",
            selection.ORGANIZATION_SELECTION_PATHS - {"/api/v1/auth/mfa/verify"},
        )
    else:
        from flo.kernel.identity.mfa_middleware import MfaAccessMiddleware
        from flo.kernel.tenancy.selection import OrganizationSelectionMiddleware

        original = list(app.user_middleware)
        changed = list(original)
        mfa = next(i for i, item in enumerate(changed) if item.cls is MfaAccessMiddleware)
        org = next(
            i for i, item in enumerate(changed) if item.cls is OrganizationSelectionMiddleware
        )
        changed[mfa], changed[org] = changed[org], changed[mfa]
        monkeypatch.setattr(app, "user_middleware", changed)
        app.middleware_stack = None
    with pytest.raises(AssertionError):
        if plant in {"mfa_verify", "mfa_order"}:
            test_multi_org_mfa_gate_wins_and_verification_completes(multi)
        else:
            test_selection_isolation_and_no_replay(multi)


@pytest.mark.parametrize("plant", ["join", "rotation"])
def test_session_sql_plants(multi, monkeypatch, plant):
    import inspect
    import textwrap

    from flo.kernel.session import store as module

    method = "authenticate" if plant == "join" else "rotate"
    source = textwrap.dedent(inspect.getsource(getattr(SessionStore, method)))
    if plant == "rotation":
        assert "current.org_id," in source
        source = source.replace("current.org_id,", "None,")
    else:
        assert "FROM refreshed" in source
        source = source.replace(
            "FROM refreshed",
            "FROM refreshed LEFT JOIN identity_membership AS membership "
            "ON membership.identity_id = refreshed.identity_id",
        )
    namespace = dict(module.__dict__)
    exec(source, namespace)
    monkeypatch.setattr(SessionStore, method, namespace[method])
    if plant == "rotation":
        with pytest.raises(AssertionError):
            test_rotation_preserves_choice_except_login_and_resolution_is_one_query(
                multi, RotationReason.MFA_COMPLETION
            )
    else:
        from datetime import timedelta

        conn = multi[0]

        class UniqueResult:
            def transaction(self):
                return conn.transaction()

            def execute(self, query, params=()):
                cursor = conn.execute(query, params)
                if "FROM refreshed" in query:
                    rows = cursor.fetchall()
                    assert len(rows) == 1, "session resolution duplicated the session"
                return cursor

        store = SessionStore(
            UniqueResult(), idle_timeout=timedelta(hours=8), absolute_timeout=timedelta(hours=12)
        )
        issued = store.issue(multi[4], RequestDevice(None, "test"))
        with pytest.raises(AssertionError, match="duplicated"):
            store.authenticate(issued.cookie_value(), RequestDevice(None, "test"))


def test_selected_organization_survives_mfa_and_different_identity_login_recomputes(multi):
    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await select(browser, multi[1].org_id)
            # A new identity's LOGIN rotation recomputes its sole membership.
            await login(browser, "second@example.test", enroll=False)
            await login_mfa_enrollment_only(browser)
            assert await chosen(browser) == str(multi[2].org_id)

    asyncio.run(scenario())


async def login_mfa_enrollment_only(browser):
    import pyotp

    result = await browser.post(
        "/api/v1/auth/mfa/enroll",
        headers=headers(browser, "second-enroll"),
        json={"password": "disposable bootstrap test passphrase"},
    )
    assert result.status_code == 200, result.text
    result = await browser.post(
        "/api/v1/auth/mfa/confirm",
        headers=headers(browser, "second-confirm"),
        json={"code": pyotp.TOTP(result.json()["secret"]).now()},
    )
    assert result.status_code == 204, result.text


def test_multi_org_switch_isolates_each_existing_record_family(multi):
    conn = multi[0]
    from flo.kernel.identity.local import build_local_identity_provider
    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction
    from tests.org.test_bootstrap import settings

    provider = asyncio.run(build_local_identity_provider(conn, settings(conn)))
    foreign_identity = asyncio.run(
        provider.create_identity("a-only@example.test", "disposable bootstrap test passphrase")
    )
    conn.execute(
        "INSERT INTO identity_membership(identity_id,org_id) VALUES (%s,%s)",
        (foreign_identity, multi[1].org_id),
    )
    with tenant_transaction(conn, Scope(multi[1].org_id)):
        conn.execute(
            "INSERT INTO user_role(id,org_id,user_id,role_id,scope_type,scope_id) "
            "SELECT %s,org_id,%s,role_id,scope_type,scope_id FROM user_role "
            "WHERE user_id=%s",
            (uuid4(), foreign_identity, multi[4]),
        )
    with tenant_transaction(conn, Scope(multi[2].org_id)):
        conn.execute(
            "INSERT INTO user_role(id,org_id,user_id,role_id,scope_type,scope_id) "
            "SELECT %s,org_id,%s,role_id,scope_type,scope_id FROM user_role "
            "WHERE user_id=%s",
            (uuid4(), multi[4], admin_id(conn, "second@example.test")),
        )

    async def scenario():
        async with client(app) as a, client(app) as b:
            await login(a)
            await select(a, multi[1].org_id)
            await login(b, "second@example.test")
            foreign_bu = await unit(b, "B")
            assert (await a.get(f"/api/v1/org/units/{foreign_bu}")).status_code == 404
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
                ("GET", f"/api/v1/admin/users/{foreign_identity}/effective-access", None),
            ]
            await select(a, multi[2].org_id)
            assert (await a.get(f"/api/v1/org/units/{foreign_bu}")).status_code == 200
            for index, (method, path, body) in enumerate(cases):
                response = await a.request(
                    method, path, json=body, headers=headers(a, "foreign-" + str(index))
                )
                assert response.status_code == 404, (path, response.status_code, response.text)

            await select(a, multi[1].org_id)
            assert (await a.get(f"/api/v1/org/units/{foreign_bu}")).status_code == 404
            for method, path, _ in cases:
                if method == "GET":
                    response = await a.get(path)
                    assert response.status_code == 200, (path, response.text)

    asyncio.run(scenario())


def test_mfa_completion_preserves_an_already_chosen_organization(multi):
    conn, a, b, _, identity = multi
    conn.execute(
        "DELETE FROM identity_membership WHERE identity_id=%s AND org_id=%s", (identity, b.org_id)
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser, enroll=False)
            conn.execute(
                "INSERT INTO identity_membership(identity_id,org_id) VALUES (%s,%s)",
                (identity, b.org_id),
            )
            await login_mfa_enrollment_only(browser)
            assert await chosen(browser) == str(a.org_id)
            assert (await browser.get("/api/v1/org/units")).status_code == 200

    asyncio.run(scenario())
