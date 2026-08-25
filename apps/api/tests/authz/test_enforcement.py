from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from flo.kernel.authz import (
    PermissionResolver,
    PermissionResolverFactory,
    install_authorization,
    public_route,
    require,
    route_authorization_failures,
)
from flo.kernel.errors import install_problem_details
from flo.kernel.identity import IdentityId
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope, use_scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.identity.resolver import AuthorizationResolver
from flo.modules.identity.service import IdentityAuthorizationService

from .conftest import AuthorizationDatabase
from .test_resolver import insert_identity


@dataclass(frozen=True, slots=True)
class StubSession:
    identity_id: IdentityId
    org_id: UUID


def run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def build_app(
    database: AuthorizationDatabase,
    user_id: IdentityId,
    target_by_record: dict[UUID, AuthorizationTarget],
) -> FastAPI:
    app = FastAPI()
    scope = Scope(database.org_a)

    @app.middleware("http")
    async def install_test_session(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request.state.session = StubSession(user_id, database.org_a)
        with use_scope(scope):
            return await call_next(request)

    @contextmanager
    def resolver_factory(resolver_scope: Scope) -> Iterator[PermissionResolver]:
        with tenant_transaction(database.connection, resolver_scope):
            yield cast(PermissionResolver, AuthorizationResolver(
                database.authorization_connection, resolver_scope
            ))

    install_authorization(app, cast(PermissionResolverFactory, resolver_factory))

    def record_target(record_id: UUID) -> AuthorizationTarget:
        target = target_by_record.get(record_id)
        if target is None:
            return AuthorizationTarget(ScopeType.BU, uuid4(), "requisition", record_id)
        return target

    guard = require("requisition.create", record_target)

    @app.get("/records/{record_id}")
    def read_record(
        context: AuthorizationContext = Depends(guard),
    ) -> dict[str, str | None]:
        return {
            "record": str(context.target.record_id),
            "created_by": None if context.created_by is None else str(context.created_by),
        }

    @app.get("/public")
    @public_route
    def public() -> dict[str, str]:
        return {"status": "ok"}

    install_problem_details(app)
    return app


def test_bu_authorization_returns_404_outside_scope_and_403_for_missing_action(
    authorization_database: AuthorizationDatabase,
) -> None:
    database = authorization_database
    user_id = insert_identity(database, "http-authorization")
    bu_allowed, bu_foreign = uuid4(), uuid4()
    allowed_record, foreign_record, foreign_org_record = uuid4(), uuid4(), uuid4()
    scope = Scope(database.org_a)
    with correlation_context("http-authorization-setup"):
        service = IdentityAuthorizationService(
            database.service_connection, scope, database.actor_id
        )
        role = service.create_role("http-requestor", "HTTP requestor")
        service.grant_permission(role.id, "requisition.create")
        service.register_scope(ScopeType.BU, bu_allowed, ScopeType.ORG, database.org_a)
        service.register_scope(ScopeType.BU, bu_foreign, ScopeType.ORG, database.org_a)
        service.grant_role(user_id, role.id, AuthorizationTarget(ScopeType.BU, bu_allowed))

    app = build_app(
        database,
        user_id,
        {
            allowed_record: AuthorizationTarget(
                ScopeType.BU,
                bu_allowed,
                "requisition",
                allowed_record,
                created_by=user_id,
            ),
            foreign_record: AuthorizationTarget(
                ScopeType.BU,
                bu_foreign,
                "requisition",
                foreign_record,
            ),
            foreign_org_record: AuthorizationTarget(
                ScopeType.ORG,
                database.org_b,
                "requisition",
                foreign_org_record,
            ),
        },
    )
    assert route_authorization_failures(app) == []

    async def requests() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            return (
                await client.get(f"/records/{allowed_record}"),
                await client.get(f"/records/{foreign_record}"),
                await client.get(f"/records/{foreign_org_record}"),
            )

    allowed, foreign, foreign_org = run(requests())
    assert allowed.status_code == 200
    assert allowed.json() == {"record": str(allowed_record), "created_by": str(user_id)}
    assert foreign.status_code == 404
    assert foreign.json()["type"] == "https://xlr8flo.app/errors/not-found"
    assert foreign_org.status_code == 404
    assert foreign_org.json()["type"] == "https://xlr8flo.app/errors/not-found"

    with correlation_context("http-permission-revoke"):
        service = IdentityAuthorizationService(
            database.service_connection, scope, database.actor_id
        )
        assert service.revoke_permission(role.id, "requisition.create")

    async def denied_request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            return await client.get(f"/records/{allowed_record}")

    denied = run(denied_request())
    assert denied.status_code == 403
    assert denied.json()["type"] == "https://xlr8flo.app/errors/forbidden"


def test_route_guard_rejects_a_planted_unclassified_route_then_passes() -> None:
    app = FastAPI()

    @app.get("/planted")
    def planted() -> dict[str, str]:
        return {"guard": "missing"}

    assert route_authorization_failures(app) == ["GET /planted"]

    clean = FastAPI()

    @clean.get("/planted")
    @public_route
    def classified() -> dict[str, str]:
        return {"guard": "declared"}

    assert route_authorization_failures(clean) == []
