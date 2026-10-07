"""FastAPI enforcement point for explicit, server-side authorization."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from typing import Protocol, cast
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope, TenantScopeMissing, current_scope


class AuthorizationTarget(Protocol):
    """Structural target contract supplied by a business module."""

    scope_type: object
    scope_id: UUID
    record_id: UUID | None


class AuthorizationContext(Protocol):
    """Successful context returned to a protected route."""


class AuthorizationDecision(Protocol):
    """Structural decision contract supplied by the identity module."""

    context: AuthorizationContext
    scope_matched: bool
    allowed: bool


type TargetResolver = Callable[..., AuthorizationTarget]


class PermissionResolver(Protocol):
    """Request-local, uncached effective-access resolver."""

    def check(
        self,
        user_id: IdentityId,
        permission: str,
        target: AuthorizationTarget,
    ) -> AuthorizationDecision: ...


type PermissionResolverFactory = Callable[
    [Scope], AbstractContextManager[PermissionResolver]
]

_PUBLIC_MARKER = "__flo_explicitly_public__"
_REQUIRE_MARKER = "__flo_required_permission__"
_FACTORY_STATE = "authorization_resolver_factory"


def public_route[**P, R](endpoint: Callable[P, R]) -> Callable[P, R]:
    """Declare that a route intentionally does not use business RBAC.

    Authentication/session lifecycle and origin-authenticated process routes use
    this marker because their own boundary precedes organization role grants.
    """

    setattr(endpoint, _PUBLIC_MARKER, True)
    return endpoint


def install_authorization(
    app: FastAPI,
    resolver_factory: PermissionResolverFactory,
) -> None:
    """Install the request-local resolver factory without opening a connection."""

    setattr(app.state, _FACTORY_STATE, resolver_factory)


def _identity_id(request: Request) -> IdentityId:
    session = getattr(request.state, "session", None)
    identity_id = getattr(session, "identity_id", None)
    if not isinstance(identity_id, UUID):
        raise ProblemError(ErrorCode.UNAUTHORIZED)
    return IdentityId(identity_id)


def _factory(request: Request) -> PermissionResolverFactory:
    factory = getattr(request.app.state, _FACTORY_STATE, None)
    if not callable(factory):
        raise RuntimeError("authorization resolver factory is not installed")
    return cast(PermissionResolverFactory, factory)


def require(
    permission: str,
    target: TargetResolver,
) -> Callable[..., AuthorizationContext]:
    """Build the single dependency used by every protected business route."""

    def enforce(
        request: Request,
        resolved_target: AuthorizationTarget = Depends(target),
    ) -> AuthorizationContext:
        user_id = _identity_id(request)
        try:
            scope = current_scope()
        except TenantScopeMissing as exc:
            raise ProblemError(ErrorCode.UNAUTHORIZED) from exc

        with _factory(request)(scope) as resolver:
            decision = resolver.check(user_id, permission, resolved_target)
        if decision.allowed:
            return decision.context

        target_is_concealed = (
            resolved_target.record_id is not None
            or getattr(resolved_target.scope_type, "value", None) != "org"
            or resolved_target.scope_id != scope.org_id
        )
        if not decision.scope_matched and target_is_concealed:
            raise ProblemError(ErrorCode.NOT_FOUND)
        raise ProblemError(ErrorCode.FORBIDDEN)

    setattr(enforce, _REQUIRE_MARKER, permission)
    return enforce


def _dependency_calls(route: APIRoute) -> Iterator[Callable[..., object]]:
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        call = dependant.call
        if callable(call):
            yield cast(Callable[..., object], call)
        pending.extend(dependant.dependencies)


def _api_routes(routes: Iterable[object]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        nested = getattr(route, "routes", None)
        if nested is None:
            nested = getattr(getattr(route, "original_router", None), "routes", None)
        if isinstance(nested, Iterable):
            yield from _api_routes(nested)


def route_authorization_failures(app: FastAPI) -> list[str]:
    """List routes that are neither explicitly public nor guarded by ``require``."""

    failures: list[str] = []
    for route in _api_routes(app.routes):
        endpoint = route.endpoint
        if getattr(endpoint, _PUBLIC_MARKER, False):
            continue
        if any(getattr(call, _REQUIRE_MARKER, None) for call in _dependency_calls(route)):
            continue
        methods = ",".join(sorted(route.methods or ()))
        failures.append(f"{methods} {route.path}")
    return sorted(failures)
