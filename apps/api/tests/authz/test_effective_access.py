from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from flo.api.admin_users import get_admin_connection, router
from flo.kernel.authz import (
    PermissionResolver,
    PermissionResolverFactory,
    install_authorization,
    route_authorization_failures,
)
from flo.kernel.errors import install_problem_details
from flo.kernel.identity import IdentityId
from flo.kernel.logging import correlation_context
from flo.kernel.session import RequestDevice, SessionRecord, SessionStore
from flo.kernel.session.store import SessionId, SessionIssueDenied
from flo.kernel.tenancy.context import Scope, use_scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.explain import EffectiveAccessConnection, EffectiveAccessService
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.identity.resolver import AuthorizationResolver
from flo.modules.identity.service import IdentityAuthorizationService

from .conftest import AuthorizationDatabase
from .test_resolver import insert_identity, service_for

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
DEVICE = RequestDevice("203.0.113.0/24", "Chrome on macOS")


def run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def session_for(user_id: IdentityId, *, last_auth_at: datetime = NOW) -> SessionRecord:
    return SessionRecord(
        id=SessionId(uuid4()),
        identity_id=user_id,
        created_at=NOW - timedelta(hours=1),
        last_seen_at=NOW,
        last_auth_at=last_auth_at,
        mfa_verified_at=last_auth_at,
        idle_expires_at=NOW + timedelta(hours=1),
        absolute_expires_at=NOW + timedelta(hours=8),
        ip_prefix=DEVICE.ip_prefix,
        user_agent=DEVICE.user_agent,
    )


def build_app(
    database: AuthorizationDatabase,
    viewer_id: IdentityId,
    *,
    last_auth_at: datetime = NOW,
) -> FastAPI:
    app = FastAPI()
    scope = Scope(database.org_a)

    @app.middleware("http")
    async def install_test_session(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request.state.session = session_for(viewer_id, last_auth_at=last_auth_at)
        with use_scope(scope):
            return await call_next(request)

    @contextmanager
    def resolver_factory(resolver_scope: Scope) -> Iterator[PermissionResolver]:
        with tenant_transaction(database.connection, resolver_scope):
            yield cast(
                PermissionResolver,
                AuthorizationResolver(database.authorization_connection, resolver_scope),
            )

    install_authorization(app, cast(PermissionResolverFactory, resolver_factory))
    app.include_router(router)
    app.dependency_overrides[get_admin_connection] = lambda: database.connection
    app.state.recent_auth_clock = lambda: NOW
    install_problem_details(app)
    return app


def grant_permissions(
    database: AuthorizationDatabase,
    user_id: IdentityId,
    *permissions: str,
) -> UUID:
    with service_for(database, Scope(database.org_a), f"grant-{user_id}") as service:
        role = service.create_role(f"role-{str(user_id)[:8]}", f"Role {str(user_id)[:8]}")
        for permission in permissions:
            service.grant_permission(role.id, permission)
        return service.grant_role(
            user_id,
            role.id,
            AuthorizationTarget.organization(database.org_a),
        ).id


def scoped_row(
    database: AuthorizationDatabase,
    query: str,
    params: tuple[object, ...] = (),
) -> tuple[object, ...] | None:
    with tenant_transaction(database.connection, Scope(database.org_a)):
        return database.connection.execute(query, params).fetchone()


async def get(client_app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client_app),
        base_url="https://testserver",
    ) as client:
        return await client.get(path)


def test_explanation_names_every_source_and_separates_future_grants(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    user_id = insert_identity(database, "explained")
    future = datetime(2099, 1, 1, tzinfo=UTC)
    with service_for(database, scope, "explainer-setup") as service:
        active_role = service.create_role("org-reader", "Organization reader")
        service.grant_permission(active_role.id, "project.view")
        active_grant = service.grant_role(
            user_id,
            active_role.id,
            AuthorizationTarget.organization(database.org_a),
        )
        pending_role = service.create_role("future-approver", "Future approver")
        service.grant_permission(pending_role.id, "requisition.approve")
        pending_grant = service.grant_role(
            user_id,
            pending_role.id,
            AuthorizationTarget.organization(database.org_a),
            effective_from=future,
        )

    with correlation_context("effective-access-explain"):
        explanation = EffectiveAccessService(
            cast(EffectiveAccessConnection, database.connection),
            scope,
            database.actor_id,
        ).explain(user_id)

    assert [grant.id for grant in explanation.grants] == [active_grant.id]
    assert [grant.id for grant in explanation.pending_grants] == [pending_grant.id]
    assert explanation.pending_grants[0].effective_from == future
    project_view = next(
        permission for permission in explanation.permissions if permission.code == "project.view"
    )
    assert project_view.allowed is True
    assert project_view.source.via_role == "org-reader"
    assert project_view.source.via_scope_type is ScopeType.ORG
    assert project_view.source.via_scope_id == database.org_a
    assert project_view.source.inherited_from == f"org:{database.org_a}"
    assert not [
        permission
        for permission in explanation.permissions
        if permission.code == "requisition.approve"
    ]
    pending_denial = next(
        denial for denial in explanation.denied_examples if denial.code == "requisition.approve"
    )
    assert "becomes active" in pending_denial.reason
    assert future.isoformat() in pending_denial.reason
    ordinary_denial = next(
        denial for denial in explanation.denied_examples if denial.code == "budget.transfer"
    )
    assert ordinary_denial.reason == (
        "No active role grant includes this permission in the organization."
    )

    with tenant_transaction(database.connection, scope):
        audit = database.connection.execute(
            "SELECT actor_id, target_id, occurred_at FROM audit_log "
            "WHERE action = 'user.effective_access.view'"
        ).fetchone()
    assert audit is not None
    assert audit[0] == database.actor_id
    assert audit[1] == user_id
    assert audit[2] is not None


def test_future_grant_is_visible_but_grants_nothing_to_the_resolver(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    user_id = insert_identity(database, "pending-only")
    with service_for(database, scope, "future-resolver") as service:
        role = service.create_role("pending-reader", "Pending reader")
        service.grant_permission(role.id, "project.view")
        service.grant_role(
            user_id,
            role.id,
            AuthorizationTarget.organization(database.org_a),
            effective_from=datetime(2099, 1, 1, tzinfo=UTC),
        )

    with tenant_transaction(database.connection, scope):
        decision = AuthorizationResolver(database.authorization_connection, scope).check(
            user_id,
            "project.view",
            AuthorizationTarget.organization(database.org_a),
        )
    assert decision.scope_matched is False
    assert decision.allowed is False


def test_guarded_endpoint_audits_allowed_view_and_rejects_unauthorized_viewer(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    subject_id = insert_identity(database, "http-subject")
    grant_permissions(database, subject_id, "project.view")
    viewer_id = insert_identity(database, "http-viewer")
    grant_permissions(database, viewer_id, "admin.access.read")
    allowed_app = build_app(database, viewer_id)

    assert route_authorization_failures(allowed_app) == []
    allowed = run(get(allowed_app, f"/api/v1/admin/users/{subject_id}/effective-access"))
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["user"]["id"] == str(subject_id)
    assert payload["permissions"][0]["source"]["via_role"]
    assert payload["permissions"][0]["source"]["via_scope_type"] == "org"
    permission_page = run(
        get(
            allowed_app,
            f"/api/v1/admin/users/{subject_id}/effective-access/permissions"
            "?page_size=50&filter=project&sort=code&direction=asc",
        )
    )
    assert permission_page.status_code == 200
    assert set(permission_page.json()) == {
        "lastCursor",
        "nextCursor",
        "previousCursor",
        "rows",
        "startIndex",
        "total",
    }
    project_view_row = next(
        row
        for row in permission_page.json()["rows"]
        if row["code"] == "project.view"
    )
    assert project_view_row == {
        "access": "allowed",
        "code": "project.view",
        "explanation": f"Inherited from org:{database.org_a}.",
        "id": f"project.view|allowed|role-{str(subject_id)[:8]}|org|{database.org_a}",
        "source": f"role-{str(subject_id)[:8]} at org:{database.org_a}",
    }

    unauthorized_id = insert_identity(database, "http-unauthorized")
    grant_permissions(database, unauthorized_id, "project.view")
    denied = run(
        get(
            build_app(database, unauthorized_id),
            f"/api/v1/admin/users/{subject_id}/effective-access",
        )
    )
    assert denied.status_code == 403
    assert denied.json()["type"].endswith("/forbidden")


def test_effective_access_conceals_a_foreign_tenant_subject(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    viewer_id = insert_identity(database, "tenant-viewer")
    grant_permissions(database, viewer_id, "admin.access.read")
    foreign_id = insert_identity(database, "foreign-subject")
    with service_for(database, Scope(database.org_b), "foreign-subject-grant") as service:
        role = service.create_role("foreign-reader", "Foreign reader")
        service.grant_permission(role.id, "project.view")
        service.grant_role(
            foreign_id,
            role.id,
            AuthorizationTarget.organization(database.org_b),
        )

    response = run(
        get(
            build_app(database, viewer_id),
            f"/api/v1/admin/users/{foreign_id}/effective-access",
        )
    )
    assert response.status_code == 404
    assert response.json()["type"].endswith("/not-found")


def test_deactivation_revokes_sessions_blocks_new_ones_and_retains_attribution(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    scope = Scope(database.org_a)
    user_id = insert_identity(database, "deactivation-subject")
    grant_permissions(database, user_id, "project.view")
    session_clock_row = database.connection.execute("SELECT clock_timestamp()").fetchone()
    assert session_clock_row is not None
    session_now = cast(datetime, session_clock_row[0])
    sessions = SessionStore(
        database.connection,
        idle_timeout=timedelta(hours=1),
        absolute_timeout=timedelta(hours=8),
        clock=lambda: session_now,
    )
    issued = sessions.issue(user_id, DEVICE)
    database.connection.execute(
        "CREATE TABLE historical_record ("
        "id uuid PRIMARY KEY, created_by uuid NOT NULL REFERENCES identity(id) ON DELETE RESTRICT)"
    )
    record_id = uuid4()
    database.connection.execute(
        "INSERT INTO historical_record (id, created_by) VALUES (%s, %s)",
        (record_id, user_id),
    )

    with correlation_context("deactivate-user"):
        service = IdentityAuthorizationService(
            database.service_connection,
            scope,
            database.actor_id,
        )
        assert service.deactivate_user(user_id) is True
        assert service.deactivate_user(user_id) is False

    assert sessions.authenticate(issued.cookie_value(), DEVICE) is None
    with pytest.raises(SessionIssueDenied):
        sessions.issue(user_id, DEVICE)
    retained = database.connection.execute(
        "SELECT identity.email, identity.status "
        "FROM historical_record JOIN identity ON identity.id = historical_record.created_by "
        "WHERE historical_record.id = %s",
        (record_id,),
    ).fetchone()
    assert retained is not None
    assert retained[1] == "deactivated"
    with correlation_context("deactivated-attribution"):
        explanation = EffectiveAccessService(
            cast(EffectiveAccessConnection, database.connection),
            scope,
            database.actor_id,
        ).explain(user_id)
    assert explanation.user.attribution == f"{retained[0]} (deactivated)"
    assert scoped_row(
        database,
        "SELECT count(*) FROM audit_log WHERE action = 'identity.deactivate'",
    ) == (1,)



@pytest.mark.parametrize("operation", ["deactivate", "revoke"])
def test_mutations_require_step_up_and_preserve_state_when_stale(
    authorization_database: AuthorizationDatabase,
    operation: str,
) -> None:
    database = authorization_database
    viewer_id = insert_identity(database, f"stale-{operation}-viewer")
    grant_permissions(database, viewer_id, "organization.admin")
    subject_id = insert_identity(database, f"stale-{operation}-subject")
    grant_id = grant_permissions(database, subject_id, "project.view")
    stale_app = build_app(
        database,
        viewer_id,
        last_auth_at=NOW - timedelta(minutes=16),
    )

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=stale_app),
            base_url="https://testserver",
        ) as client:
            if operation == "deactivate":
                return await client.post(f"/api/v1/admin/users/{subject_id}/deactivate")
            return await client.delete(f"/api/v1/admin/users/{subject_id}/roles/{grant_id}")

    response = run(request())
    assert response.status_code == 403
    assert response.headers["www-authenticate"] == "step-up"
    assert response.json()["type"].endswith("/step-up-required")
    assert database.connection.execute(
        "SELECT status FROM identity WHERE id = %s", (subject_id,)
    ).fetchone() == ("active",)
    assert scoped_row(
        database,
        "SELECT count(*) FROM user_role WHERE id = %s",
        (grant_id,),
    ) == (1,)


def test_fresh_step_up_allows_subject_bound_revoke(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    viewer_id = insert_identity(database, "fresh-viewer")
    grant_permissions(database, viewer_id, "organization.admin")
    subject_id = insert_identity(database, "fresh-subject")
    grant_id = grant_permissions(database, subject_id, "project.view")
    other_id = insert_identity(database, "other-subject")
    grant_permissions(database, other_id, "project.view")
    app = build_app(database, viewer_id)

    async def requests() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://testserver",
        ) as client:
            mismatch = await client.delete(f"/api/v1/admin/users/{other_id}/roles/{grant_id}")
            revoked = await client.delete(f"/api/v1/admin/users/{subject_id}/roles/{grant_id}")
            return mismatch, revoked

    mismatch, revoked = run(requests())
    assert mismatch.status_code == 404
    assert revoked.status_code == 204
    assert scoped_row(
        database,
        "SELECT count(*) FROM user_role WHERE id = %s",
        (grant_id,),
    ) == (0,)


def test_access_migration_revision_and_downgrade_preserve_existing_rows(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    migration = database.access_migration
    assert migration.revision == "20260825_0012", "MISSING required revision"
    assert migration.down_revision == "20260825_0011", "MISSING required down_revision"
    columns = {
        row[0]
        for row in database.connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'user_role'"
        ).fetchall()
    }
    assert {"effective_from", "granted_at", "granted_by"} <= columns, (
        "MISSING grant provenance columns"
    )
    assert database.connection.execute(
        "SELECT count(*) FROM permission WHERE code = 'admin.access.read'"
    ).fetchone() == (1,)

    user_id = insert_identity(database, "migration-preserved")
    grant_id = grant_permissions(database, user_id, "project.view")
    identity_before = database.connection.execute(
        "SELECT id, email FROM identity WHERE id = %s", (user_id,)
    ).fetchone()
    grant_before = scoped_row(
        database,
        "SELECT id, org_id, user_id, role_id, scope_type, scope_id FROM user_role WHERE id = %s",
        (grant_id,),
    )

    migration.downgrade(database.connection)

    assert (
        database.connection.execute(
            "SELECT id, email FROM identity WHERE id = %s", (user_id,)
        ).fetchone()
        == identity_before
    )
    assert (
        scoped_row(
            database,
            "SELECT id, org_id, user_id, role_id, scope_type, scope_id "
            "FROM user_role WHERE id = %s",
            (grant_id,),
        )
        == grant_before
    )
