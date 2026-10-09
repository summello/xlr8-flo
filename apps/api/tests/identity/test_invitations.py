"""Production-app invitation state, privacy, tenancy and concurrency regressions."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.identity.local import build_local_identity_provider
from flo.kernel.logging import correlation_context
from flo.kernel.session.store import RequestDevice
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.invitation import InvitationService, token_hash
from flo.modules.identity.schemas import InvitationAccept, InvitationCreate
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import admin_id, connection_url, create, settings
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db

DEVICE = RequestDevice("203.0.113.0/24", "test")
BODY = {
    "full_name": "Invitee",
    "password": "disposable invited test passphrase",
    "accepted_terms": True,
}


@pytest.fixture
def invites(bootstrap_db, monkeypatch):
    conn = bootstrap_db
    a = create(conn, tenant_label="EMEA")
    b = create(conn, "SECOND", "second@example.test", tenant_label="APAC")
    monkeypatch.setenv("DATABASE_URL", connection_url(conn))
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", "A" * 43)
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_MEMORY_COST_KIB", "8192")
    monkeypatch.setenv("FLO_LOGIN_CLIENT_MAX_FAILURES", "100")
    from flo.api.origin_auth import require_origin_secret

    app.dependency_overrides[require_origin_secret] = lambda: None
    app.middleware_stack = None
    yield conn, a.org_id, b.org_id
    app.dependency_overrides.clear()
    app.middleware_stack = None


def queued_token(conn, org, invitation_id):
    with tenant_transaction(conn, Scope(org)):
        payload = conn.execute(
            "SELECT payload FROM outbox WHERE idempotency_key LIKE %s ORDER BY created_at DESC",
            (f"invitation:{invitation_id}:%",),
        ).fetchone()[0]
    return payload["context"]["link"].rsplit("/", 1)[1]


def issue(invites, email="invitee@example.test", role="executive-viewer", org=None, clock=None):
    conn, a, b = invites
    org = org or a
    actor_row = conn.execute(
        "SELECT identity_id FROM identity_membership WHERE org_id=%s ORDER BY identity_id LIMIT 1",
        (org,),
    ).fetchone()
    actor = actor_row[0]
    service = InvitationService(conn, settings(conn), **({"clock": clock} if clock else {}))
    with correlation_context("invitation-test"):
        row = service.issue(
            Scope(org),
            actor,
            InvitationCreate(email=email, role_code=role, scope_type="org"),
            DEVICE,
        )
    return row, queued_token(conn, org, row.id)


async def accept(browser, token, body=None, key=None):
    await browser.get("/api/v1/auth/csrf")
    return await browser.post(
        "/api/v1/invitations/accept",
        json=BODY if body is None else body,
        headers={**headers(browser, key or str(uuid4())), "X-Invitation-Token": token},
    )


def assert_unavailable(response):
    assert response.status_code == 404, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "retry-after" not in response.headers
    data = response.json()
    data.pop("correlation_id")
    assert "First tenant" not in response.text
    # Compare the actual serialized bytes, removing only the per-request id.
    normalized = response.content.replace(
        response.json()["correlation_id"].encode(), b"CORRELATION"
    )
    public_headers = tuple(
        sorted((key, value) for key, value in response.headers.items() if key != "x-correlation-id")
    )
    return normalized, public_headers


@pytest.mark.parametrize(
    "role,next_page", [("executive-viewer", "home"), ("organization-administrator", "enroll")]
)
def test_new_accept_session_role_membership_and_single_use(invites, role, next_page, caplog):
    conn, org, _ = invites
    row, token = issue(invites, role=role)
    assert conn.execute("SELECT token_hash FROM invitation_token").fetchone()[0] == token_hash(
        token
    )

    async def scenario():
        async with client(app) as browser:
            read = await browser.get(
                "/api/v1/invitations/by-token", headers={"X-Invitation-Token": token}
            )
            assert read.status_code == 200, read.text
            assert read.json()["tenant_label"] == "EMEA"
            assert read.headers["cache-control"] == "no-store"
            result = await accept(browser, token)
            assert result.status_code == 200, result.text
            assert result.json() == {"next": next_page}
            assert "__Host-flo_session" in browser.cookies
            assert result.headers["cache-control"] == "no-store"
            if next_page == "enroll":
                assert (await browser.get("/api/v1/auth/sessions")).headers[
                    "www-authenticate"
                ] == "mfa-enroll"
        async with client(app) as anonymous:
            assert_unavailable(await accept(anonymous, token))

    asyncio.run(scenario())
    assert (
        conn.execute("SELECT id FROM identity WHERE email=%s", (row.email,)).fetchone() is not None
    )
    identity = admin_id(conn, row.email)
    assert (
        conn.execute(
            "SELECT org_id FROM identity_membership WHERE identity_id=%s", (identity,)
        ).fetchone()[0]
        == org
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM invitation_token WHERE invitation_id=%s", (row.id,)
        ).fetchone()[0]
        == 0
    )
    with tenant_transaction(conn, Scope(org)):
        assert (
            conn.execute("SELECT used_by FROM invitation WHERE id=%s", (row.id,)).fetchone()[0]
            == identity
        )
        assert (
            conn.execute("SELECT count(*) FROM user_role WHERE user_id=%s", (identity,)).fetchone()[
                0
            ]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM outbox WHERE idempotency_key LIKE %s "
                "AND topic = 'email' AND state IN ('pending', 'failed', 'dead')",
                (f"invitation:{row.id}:%",),
            ).fetchone()[0]
            == 0
        )
        audit = conn.execute(
            "SELECT action, after FROM audit_log WHERE target_id=%s ORDER BY id", (row.id,)
        ).fetchall()
        assert [r[0] for r in audit] == ["invitation.issued", "invitation.accepted"]
        assert token not in str(audit) and token_hash(token) not in str(audit)
    assert token not in caplog.text and BODY["password"] not in caplog.text


def test_uniform_bytes_for_all_unavailable_states_on_both_routes(invites):
    conn, org, _ = invites
    used, used_token = issue(invites, "used@example.test")
    withdrawn, withdrawn_token = issue(invites, "withdrawn@example.test")
    resent, old_token = issue(invites, "resent@example.test")
    service = InvitationService(conn, settings(conn))
    with correlation_context("state-test"):
        service.withdraw(Scope(org), admin_id(conn), withdrawn.id, DEVICE)
        service.resend(Scope(org), admin_id(conn), resent.id, DEVICE)

    async def scenario():
        async with client(app) as browser:
            assert (await accept(browser, used_token)).status_code == 200
        async with client(app) as browser:
            for method, path in [
                ("GET", "/api/v1/invitations/by-token"),
                ("POST", "/api/v1/invitations/accept"),
            ]:
                responses = []
                for token in (used_token, withdrawn_token, old_token, "malformed", "x" * 43, ""):
                    await browser.get("/api/v1/auth/csrf")
                    result = await browser.request(
                        method,
                        path,
                        headers={**headers(browser, str(uuid4())), "X-Invitation-Token": token},
                        **({"json": BODY} if method == "POST" else {}),
                    )
                    responses.append(assert_unavailable(result))
                assert len(set(responses)) == 1

    asyncio.run(scenario())


def test_weak_password_forged_values_existing_identity_and_expiry(invites):
    conn, org, _ = invites
    row, token = issue(invites)
    existing, existing_token = issue(invites, "admin@example.test")
    now = datetime.now(UTC)
    expired, expired_token = issue(
        invites, "expired@example.test", clock=lambda: now - timedelta(days=7)
    )

    async def scenario():
        async with client(app) as browser:
            weak = await accept(browser, token, {**BODY, "password": "short"})
            assert weak.status_code == 422, weak.text
            assert weak.json()["errors"][0]["field"] == "password"
            for field, value in (
                ("accepted_terms", False),
                ("full_name", None),
                ("password", None),
            ):
                invalid = await accept(browser, token, {**BODY, field: value})
                assert invalid.status_code == 422
                assert invalid.json()["errors"][0]["field"] == field
            for field, value in [
                ("email", "forged@example.test"),
                ("org_id", str(uuid4())),
                ("role_code", "organization-administrator"),
            ]:
                forged = await accept(browser, token, {**BODY, field: value})
                assert forged.status_code == 422
            assert_unavailable(await accept(browser, existing_token))
            result = await browser.get(
                "/api/v1/invitations/by-token", headers={"X-Invitation-Token": expired_token}
            )
            assert result.json() == {"state": "expired"}
            assert_unavailable(await accept(browser, expired_token))

    asyncio.run(scenario())
    assert (
        conn.execute(
            "SELECT count(*) FROM identity WHERE email IN (%s,%s)",
            (row.email, "forged@example.test"),
        ).fetchone()[0]
        == 0
    )
    with tenant_transaction(conn, Scope(org)):
        assert (
            conn.execute("SELECT used_at FROM invitation WHERE id=%s", (row.id,)).fetchone()[0]
            is None
        )
    service = InvitationService(conn, settings(conn), clock=lambda: now - timedelta(hours=1))
    assert service.by_token(expired_token).state == "valid"
    configured = settings(conn)
    configured.invitation_ttl_days = 2
    with correlation_context("ttl"):
        changed = InvitationService(conn, configured, clock=lambda: now).issue(
            Scope(org),
            admin_id(conn),
            InvitationCreate(
                email="ttl@example.test", role_code="executive-viewer", scope_type="org"
            ),
            DEVICE,
        )
    assert changed.expires_at == now + timedelta(days=2)


@pytest.mark.parametrize("state", ["open", "expired", "accepted", "withdrawn"])
@pytest.mark.parametrize("action", ["issue", "resend", "withdraw"])
def test_admin_state_machine(invites, state, action):
    conn, org, _ = invites
    row, token = issue(invites)
    scope = Scope(org)
    service = InvitationService(conn, settings(conn))
    with tenant_transaction(conn, scope):
        if state == "expired":
            conn.execute(
                "UPDATE invitation SET expires_at=now()-interval '1 day' WHERE id=%s", (row.id,)
            )
        if state == "accepted":
            conn.execute(
                "UPDATE invitation SET used_at=now(), used_by=%s WHERE id=%s",
                (admin_id(conn), row.id),
            )
            conn.execute("DELETE FROM invitation_token WHERE invitation_id=%s", (row.id,))
    with correlation_context("transitions"):
        if state == "withdrawn":
            service.withdraw(scope, admin_id(conn), row.id, DEVICE)
        before = service.list(scope)
        if (
            (action == "issue" and state == "open")
            or (action == "resend" and state in {"accepted", "withdrawn"})
            or (action == "withdraw" and state == "accepted")
        ):
            with pytest.raises(ProblemError) as raised:
                if action == "issue":
                    issue(invites)
                else:
                    getattr(service, action)(scope, admin_id(conn), row.id, DEVICE)
            assert raised.value.code.value == "conflict"
            assert service.list(scope) == before
        elif action == "issue":
            new, _ = issue(invites)
            assert new.id != row.id
        elif action == "resend":
            new = service.resend(scope, admin_id(conn), row.id, DEVICE)
            assert new.resend_counter == 1 and new.expires_at > row.expires_at
            with pytest.raises(ProblemError):
                service.by_token(token)
            assert service.by_token(queued_token(conn, org, row.id)).state == "valid"
        else:
            service.withdraw(scope, admin_id(conn), row.id, DEVICE)
            with pytest.raises(ProblemError):
                service.by_token(token)
            with tenant_transaction(conn, scope):
                assert (
                    conn.execute(
                        "SELECT count(*) FROM audit_log WHERE target_id=%s "
                        "AND action='invitation.withdrawn'",
                        (row.id,),
                    ).fetchone()[0]
                    == 1
                )


def test_admin_tenant_isolation_and_guards(invites):
    conn, org, other = invites
    foreign, token = issue(invites, org=other)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            made = await browser.post(
                "/api/v1/invitations",
                json={
                    "email": "new@example.test",
                    "role_code": "executive-viewer",
                    "scope_type": "org",
                },
                headers=headers(browser, "issue"),
            )
            assert made.status_code == 201, made.text
            for action in ("resend", "withdraw"):
                result = await browser.post(
                    f"/api/v1/invitations/{foreign.id}/{action}", headers=headers(browser, action)
                )
                assert result.status_code == 404, result.text
            listed = await browser.get("/api/v1/invitations", params={"org_id": str(other)})
            assert listed.status_code == 200
            assert {r["org_id"] for r in listed.json()} == {str(org)}
            wrong = await browser.post(
                "/api/v1/invitations",
                json={"email": "wrong@example.test", "role_code": "absent", "scope_type": "org"},
                headers=headers(browser, "wrong"),
            )
            assert wrong.status_code == 422
        async with client(app) as anonymous:
            for method, path, body in [
                ("GET", "/api/v1/invitations", None),
                ("POST", "/api/v1/invitations", {}),
                ("POST", f"/api/v1/invitations/{foreign.id}/resend", None),
                ("POST", f"/api/v1/invitations/{foreign.id}/withdraw", None),
            ]:
                await anonymous.get("/api/v1/auth/csrf")
                result = await anonymous.request(
                    method, path, json=body, headers=headers(anonymous, str(uuid4()))
                )
                assert result.status_code == 401, result.text

    asyncio.run(scenario())


def test_existing_account_accept_into_b_preserves_a_and_never_replays(invites):
    conn, a, b = invites
    row, token = issue(invites, "admin@example.test", org=b)
    already, already_token = issue(invites, "admin@example.test", org=a)
    mismatch, mismatch_token = issue(invites, "someone@example.test", org=b)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            before = (await browser.get("/api/v1/auth/organizations")).json()
            assert_unavailable(await accept(browser, mismatch_token, {}))
            assert_unavailable(await accept(browser, already_token, {}))
            result = await accept(browser, token, {}, key="same")
            assert result.status_code == 200, result.text
            assert result.json() == {"next": "home"}
            assert_unavailable(await accept(browser, token, {}, key="same"))
            after = (await browser.get("/api/v1/auth/organizations")).json()
            assert after["current_org_id"] == before["current_org_id"] == str(a)
            assert {r["org_id"] for r in after["items"]} == {str(a), str(b)}

    asyncio.run(scenario())
    with tenant_transaction(conn, Scope(a)):
        assert (
            conn.execute("SELECT used_at FROM invitation WHERE id=%s", (already.id,)).fetchone()[0]
            is None
        )


def test_mfa_pending_session_cannot_accept(invites):
    _, token = issue(invites, "admin@example.test", org=invites[2])

    async def scenario():
        async with client(app) as browser:
            await login(browser, enroll=False)
            result = await accept(browser, token, {})
            assert result.status_code == 403
            assert result.headers["www-authenticate"] == "mfa-enroll"

    asyncio.run(scenario())


def test_concurrent_accept_and_resend_never_leave_a_live_accepted_token(invites):
    conn, org, _ = invites
    row, token = issue(invites)
    url = connection_url(conn)

    async def scenario():
        async def run():
            with psycopg.connect(url, autocommit=True) as database:
                service = InvitationService(database, settings(database))
                provider = await build_local_identity_provider(database, settings(database))
                with correlation_context(str(uuid4())):
                    try:
                        await service.accept(
                            token, InvitationAccept(**BODY), None, provider, DEVICE
                        )
                        return "accepted"
                    except ProblemError as exc:
                        assert exc.code.value == "not-found"
                        return "unavailable"

        results = await asyncio.wait_for(asyncio.gather(run(), run()), timeout=10)
        assert sorted(results) == ["accepted", "unavailable"]

    asyncio.run(scenario())
    assert (
        conn.execute(
            "SELECT count(*) FROM invitation_token WHERE invitation_id=%s", (row.id,)
        ).fetchone()[0]
        == 0
    )


def test_outbox_discard_dead_preserves_sent_and_foreign_rows(invites):
    from flo.kernel.outbox.store import OutboxStore

    conn, org, other = invites
    row, _ = issue(invites)
    foreign, _ = issue(invites, org=other)
    with tenant_transaction(conn, Scope(org)):
        key = f"invitation:{row.id}:0"
        conn.execute("UPDATE outbox SET state='dead' WHERE idempotency_key=%s", (key,))
        store = OutboxStore(conn, Scope(org))
        assert store.discard_email(f"invitation:{foreign.id}:0") == 0
        assert store.discard_email(key) == 1
        with correlation_context("sent-test"):
            sent = store.add_email(
                to="test@example.test",
                template="invitation.html",
                context={},
                idempotency_key="sent",
            )
        conn.execute(
            "UPDATE outbox SET state='sent',provider_message_id='test',sent_at=now(),"
            "payload='{}' WHERE id=%s",
            (sent.id,),
        )
        assert store.discard_email("sent") == 0
        assert (
            conn.execute("SELECT state FROM outbox WHERE id=%s", (sent.id,)).fetchone()[0] == "sent"
        )


def test_client_throttle_is_uniform_and_invitation_selection_paths_work(invites, monkeypatch):
    from flo.kernel.identity.throttle import LoginThrottle
    from flo.kernel.tenancy import selection

    conn, org, other = invites
    _, token = issue(invites)
    monkeypatch.setenv("FLO_LOGIN_CLIENT_MAX_FAILURES", "5")
    jitter_calls = []

    async def jitter(self):
        jitter_calls.append(True)

    monkeypatch.setattr(LoginThrottle, "jitter", jitter)

    async def scenario():
        async with client(app) as browser:
            for _ in range(5):
                first = await browser.get(
                    "/api/v1/invitations/by-token", headers={"X-Invitation-Token": "unknown"}
                )
            second = await browser.get(
                "/api/v1/invitations/by-token", headers={"X-Invitation-Token": token}
            )
            assert assert_unavailable(first) == assert_unavailable(second)
            assert_unavailable(await accept(browser, token))

    asyncio.run(scenario())
    assert len(jitter_calls) == 7
    assert {
        "/api/v1/invitations/by-token",
        "/api/v1/invitations/accept",
    } <= selection.ORGANIZATION_SELECTION_PATHS


@pytest.mark.parametrize(
    "role",
    [
        "project-administrator",
        "requestor",
        "approver",
        "sourcing-specialist",
        "purchasing-user",
        "vendor-manager",
        "budget-finance-user",
        "warehouse-asset-user",
        "executive-viewer",
        "auditor",
    ],
)
def test_member_without_admin_permission_gets_guard_403(invites, role):
    conn, org, _ = invites
    row, token = issue(invites, role=role)

    async def scenario():
        async with client(app) as member:
            accepted = await accept(member, token)
            assert accepted.status_code == 200
            if accepted.json()["next"] == "enroll":
                import pyotp

                enrolled = await member.post(
                    "/api/v1/auth/mfa/enroll",
                    headers=headers(member, str(uuid4())),
                    json={"password": BODY["password"]},
                )
                assert enrolled.status_code == 200, enrolled.text
                confirmed = await member.post(
                    "/api/v1/auth/mfa/confirm",
                    headers=headers(member, str(uuid4())),
                    json={"code": pyotp.TOTP(enrolled.json()["secret"]).now()},
                )
                assert confirmed.status_code == 204, confirmed.text
            for method, path, body in [
                ("GET", "/api/v1/invitations", None),
                (
                    "POST",
                    "/api/v1/invitations",
                    {
                        "email": "other@example.test",
                        "role_code": "executive-viewer",
                        "scope_type": "org",
                    },
                ),
                ("POST", f"/api/v1/invitations/{row.id}/resend", None),
                ("POST", f"/api/v1/invitations/{row.id}/withdraw", None),
            ]:
                result = await member.request(
                    method, path, json=body, headers=headers(member, str(uuid4()))
                )
                assert result.status_code == 403, result.text
                assert "www-authenticate" not in result.headers

    asyncio.run(scenario())


def test_resend_races_accept_under_real_row_lock(invites):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    conn, org, _ = invites
    row, token = issue(invites)
    barrier = Barrier(2)
    actor = admin_id(conn)
    url = connection_url(conn)

    def run(action):
        with psycopg.connect(url, autocommit=True) as database, correlation_context(str(uuid4())):
            service = InvitationService(database, settings(database))
            barrier.wait(timeout=5)
            try:
                if action == "resend":
                    service.resend(Scope(org), actor, row.id, DEVICE)
                else:

                    async def accepting():
                        provider = await build_local_identity_provider(database, settings(database))
                        await service.accept(
                            token, InvitationAccept(**BODY), None, provider, DEVICE
                        )

                    asyncio.run(accepting())
                return action
            except ProblemError as exc:
                assert exc.code.value == ("conflict" if action == "resend" else "not-found")
                return "lost"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ("resend", "accept")))
    assert results.count("lost") == 1
    with tenant_transaction(conn, Scope(org)):
        accepted = conn.execute("SELECT used_at FROM invitation WHERE id=%s", (row.id,)).fetchone()[
            0
        ]
        count = conn.execute(
            "SELECT count(*) FROM invitation_token WHERE invitation_id=%s", (row.id,)
        ).fetchone()[0]
        assert count == (0 if accepted else 1)


def test_accept_lock_serializes_before_creating_identity(invites):
    conn, org, _ = invites
    row, token = issue(invites)
    url = connection_url(conn)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def run():
            with (
                psycopg.connect(url, autocommit=True) as database,
                correlation_context(str(uuid4())),
            ):
                service = InvitationService(database, settings(database))
                actual = await build_local_identity_provider(database, settings(database))

                class PausedProvider:
                    async def create_identity(self, email, password):
                        calls.append(email)
                        entered.set()
                        await release.wait()
                        return await actual.create_identity(email, password)

                try:
                    await service.accept(
                        token, InvitationAccept(**BODY), None, PausedProvider(), DEVICE
                    )
                    return "accepted"
                except ProblemError as exc:
                    assert exc.code.value == "not-found"
                    return "unavailable"

        first = asyncio.create_task(run())
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(run())
        await asyncio.sleep(0.1)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 5)
        assert len(calls) == 1, "FOR UPDATE must serialize before identity creation"
        assert sorted(results) == ["accepted", "unavailable"]

    asyncio.run(scenario())


@pytest.mark.parametrize("plant", ["lock", "token_delete", "email", "response", "bypass", "revive"])
def test_security_plants_fail_their_regressions(invites, monkeypatch, plant):
    import inspect
    from textwrap import dedent

    from flo.kernel.idempotency import middleware
    from flo.modules.identity import invitation

    if plant == "bypass":
        monkeypatch.setattr(
            middleware,
            "IDEMPOTENCY_BYPASS_PATHS",
            middleware.IDEMPOTENCY_BYPASS_PATHS - {"/api/v1/invitations/accept"},
        )

        def check():
            return test_existing_account_accept_into_b_preserves_a_and_never_replays(invites)
    elif plant == "response":
        original = InvitationService.lookup

        def lookup(self, token):
            try:
                return original(self, token)
            except ProblemError:
                raise ProblemError(
                    invitation.ErrorCode.NOT_FOUND, detail="This invitation was used."
                )

        # Differ only for tokens whose retained tenant invitation is accepted.
        def altered(self, token):
            count = self.connection.execute(
                "SELECT count(*) FROM identity WHERE email='used@example.test'"
            ).fetchone()[0]
            if count and token not in {"malformed", "x" * 43, ""}:
                return lookup(self, token)
            return original(self, token)

        monkeypatch.setattr(InvitationService, "lookup", altered)

        def check():
            return test_uniform_bytes_for_all_unavailable_states_on_both_routes(invites)
    elif plant == "revive":
        source = dedent(inspect.getsource(InvitationService.resend)).replace(
            "if row.used_at is not None or row.withdrawn_at is not None:",
            "if row.withdrawn_at is not None:",
        )
        namespace = dict(invitation.__dict__)
        exec(source, namespace)
        monkeypatch.setattr(InvitationService, "resend", namespace["resend"])

        def check():
            return test_admin_state_machine(invites, "accepted", "resend")
    elif plant == "lock":
        original = invitation.InvitationRepository.get

        def unlocked(self, invitation_id, *, lock=False):
            return original(self, invitation_id, lock=False)

        monkeypatch.setattr(invitation.InvitationRepository, "get", unlocked)

        def check():
            return test_accept_lock_serializes_before_creating_identity(invites)
    elif plant == "token_delete":
        original = InvitationService._discard

        def retained(self, row, scope):
            used = invitation.InvitationRepository(self.connection, scope).get(row.id).used_at
            if used is None:
                original(self, row, scope)

        monkeypatch.setattr(InvitationService, "_discard", retained)

        def check():
            return test_concurrent_accept_and_resend_never_leave_a_live_accepted_token(invites)
    else:
        source = dedent(inspect.getsource(InvitationService.accept)).replace(
            "provider.create_identity(row.email, body.password)",
            "provider.create_identity('forged@example.test', body.password)",
        )
        namespace = dict(invitation.__dict__)
        exec(source, namespace)
        monkeypatch.setattr(InvitationService, "accept", namespace["accept"])

        def check():
            return test_new_accept_session_role_membership_and_single_use(
                invites, "executive-viewer", "home", type("Logs", (), {"text": ""})()
            )

    with pytest.raises((AssertionError, pytest.fail.Exception)):
        check()


def test_unselected_multi_tenant_session_can_read_and_accept(invites):
    conn, a, b = invites
    third = create(conn, "THIRD", "third@example.test")
    identity = admin_id(conn)
    conn.execute(
        "INSERT INTO identity_membership(identity_id,org_id) VALUES (%s,%s)", (identity, b)
    )
    row, token = issue((conn, third.org_id, a), "admin@example.test", org=third.org_id)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert (await browser.get("/api/v1/auth/organizations")).json()[
                "current_org_id"
            ] is None
            read = await browser.get(
                "/api/v1/invitations/by-token", headers={"X-Invitation-Token": token}
            )
            assert read.status_code == 200, read.text
            result = await accept(browser, token, {})
            assert result.status_code == 200, result.text
            organizations = (await browser.get("/api/v1/auth/organizations")).json()
            assert organizations["current_org_id"] is None
            assert len(organizations["items"]) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/api/v1/invitations/by-token", "/api/v1/invitations/accept"])
def test_selection_allowlist_plant(invites, monkeypatch, path):
    from flo.kernel.tenancy import selection

    monkeypatch.setattr(
        selection, "ORGANIZATION_SELECTION_PATHS", selection.ORGANIZATION_SELECTION_PATHS - {path}
    )
    with pytest.raises(AssertionError):
        test_unselected_multi_tenant_session_can_read_and_accept(invites)


def test_invitation_scope_validation_and_authoritative_grant(invites):
    from flo.modules.identity.models import ScopeType
    from flo.modules.identity.service import IdentityAuthorizationService
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService

    conn, a, b = invites
    actor = admin_id(conn)
    with correlation_context("scopes"), tenant_transaction(conn, Scope(a)):
        unit = OrgService(conn, Scope(a), actor).create_unit(
            OrgUnitCreate(code="BU", name="Invited BU", kind="bu")
        )
    with correlation_context("foreign-role"), tenant_transaction(conn, Scope(b)):
        IdentityAuthorizationService(
            conn, Scope(b), admin_id(conn, "second@example.test")
        ).create_role("foreign-only", "Foreign role")
    service = InvitationService(conn, settings(conn))
    for code, kind, scope_id, field in [
        ("foreign-only", ScopeType.ORG, None, "role_code"),
        ("executive-viewer", ScopeType.ORG, b, "scope_id"),
        ("executive-viewer", ScopeType.BU, uuid4(), "scope_id"),
        ("executive-viewer", ScopeType.PROJECT, unit.id, "scope_id"),
    ]:
        with correlation_context("invalid-scope"), pytest.raises(ProblemError) as raised:
            service.issue(
                Scope(a),
                actor,
                InvitationCreate(
                    email="scope@example.test", role_code=code, scope_type=kind, scope_id=scope_id
                ),
                DEVICE,
            )
        assert raised.value.code.value == "validation-failed"
        assert raised.value.errors[0].field == field
    with correlation_context("scoped-invite"):
        row = service.issue(
            Scope(a),
            actor,
            InvitationCreate(
                email="scope@example.test",
                role_code="executive-viewer",
                scope_type=ScopeType.BU,
                scope_id=unit.id,
            ),
            DEVICE,
        )
    token = queued_token(conn, a, row.id)

    async def scenario():
        async with client(app) as browser:
            read = await browser.get(
                "/api/v1/invitations/by-token", headers={"X-Invitation-Token": token}
            )
            assert read.json()["access_words"] == "business unit: Invited BU"
            response = await browser.request(
                "POST",
                "/api/v1/invitations/accept?org_id=" + str(b),
                json=BODY,
                headers={
                    **headers_after_bootstrap(browser),
                    "X-Invitation-Token": token,
                    "Org-Id": str(b),
                },
            )
            assert response.status_code == 200, response.text

    # The read arms the CSRF pair used by the subsequent accept.
    def headers_after_bootstrap(browser):
        return headers(browser, str(uuid4()))

    asyncio.run(scenario())
    with tenant_transaction(conn, Scope(a)):
        grant = conn.execute(
            "SELECT scope_type,scope_id,granted_by FROM user_role WHERE user_id=%s",
            (admin_id(conn, row.email),),
        ).fetchone()
        assert grant == ("bu", unit.id, actor)
    assert (
        conn.execute(
            "SELECT org_id FROM identity_membership WHERE identity_id=%s",
            (admin_id(conn, row.email),),
        ).fetchone()[0]
        == a
    )


def test_invitation_ttl_setting_rejects_out_of_range_values():
    from pydantic import ValidationError

    from flo.kernel.config import Settings

    assert Settings().invitation_ttl_days == 7
    for days in (0, 31):
        with pytest.raises(ValidationError):
            Settings(invitation_ttl_days=days)
    assert Settings(invitation_ttl_days=1).invitation_ttl_days == 1
    assert Settings(invitation_ttl_days=30).invitation_ttl_days == 30
