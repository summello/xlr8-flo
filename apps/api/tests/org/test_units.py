from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Literal
from uuid import UUID, uuid4

import httpx
import pytest
from psycopg.errors import ForeignKeyViolation
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.org.schemas import OrgUnitCreate, OrgUnitPatch, OrgUnitRead
from tests.authz.conftest import AuthorizationDatabase
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity, service_for

from .conftest import app_for, service


def create(
    db: AuthorizationDatabase,
    code: str,
    parent: UUID | None = None,
    org: UUID | None = None,
    kind: Literal["bu", "ou"] = "bu",
) -> OrgUnitRead:
    return service(db, org).create_unit(
        OrgUnitCreate(code=code, name=code, parent_id=parent, kind=kind)
    )


def request(
    db: AuthorizationDatabase,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    viewer: IdentityId | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(db, viewer or db.actor_id)),
            base_url="https://testserver",
        ) as client:
            return await client.request(method, "/api/v1/org" + path, json=body)

    return asyncio.run(send())


def admin(db: AuthorizationDatabase) -> None:
    grant_permissions(db, db.actor_id, "org.unit.manage", "org.unit.read", "org.setting.manage")


def test_codes_case_insensitive_and_tenant_unique(org_database: AuthorizationDatabase) -> None:
    db = org_database
    admin(db)
    first = request(db, "POST", "/units", {"code": "fin", "name": "Finance", "kind": "bu"})
    assert first.status_code == 201
    assert first.json()["code"] == "FIN"
    duplicate = request(db, "POST", "/units", {"code": "FIN", "name": "F", "kind": "ou"})
    assert duplicate.status_code == 409
    assert duplicate.json()["checks"]["problem"] == "duplicate_code"
    assert create(db, "fin", org=db.org_b).code == "FIN"


def test_depth_guard_rejects_sixth_level(org_database: AuthorizationDatabase) -> None:
    db = org_database
    admin(db)
    parent = None
    for level in range(5):
        parent = create(db, f"L{level}", parent).id
    response = request(
        db,
        "POST",
        "/units",
        {"code": "L6", "name": "Too deep", "kind": "ou", "parent_id": str(parent)},
    )
    assert response.status_code == 422
    assert "5" in response.json()["detail"]
    assert response.json()["checks"]["problem"] == "depth_limit"


def test_deactivate_guard_names_active_children_and_code_is_immutable(
    org_database: AuthorizationDatabase,
) -> None:
    db = org_database
    admin(db)
    parent = create(db, "P")
    child = create(db, "C", parent.id)
    denied = request(db, "PATCH", f"/units/{parent.id}", {"active": False})
    assert denied.status_code == 409
    assert "C" in denied.json()["detail"]
    assert request(db, "PATCH", f"/units/{parent.id}", {"code": "NEW"}).status_code == 422
    assert request(db, "PATCH", f"/units/{child.id}", {"active": False}).status_code == 200
    assert request(db, "PATCH", f"/units/{parent.id}", {"active": False}).status_code == 200
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action = 'org_unit.deactivate'"
    ).fetchone() == (2,)


def test_get_foreign_unit(org_database: AuthorizationDatabase) -> None:
    db = org_database
    admin(db)
    foreign = create(db, "FOREIGN", org=db.org_b)
    assert request(db, "GET", f"/units/{foreign.id}").status_code == 404


def test_patch_foreign_unit(org_database: AuthorizationDatabase) -> None:
    db = org_database
    admin(db)
    foreign = create(db, "FOREIGN", org=db.org_b)
    assert request(db, "PATCH", f"/units/{foreign.id}", {"name": "No"}).status_code == 404


def test_create_foreign_parent(org_database: AuthorizationDatabase) -> None:
    db = org_database
    admin(db)
    foreign = create(db, "FOREIGN", org=db.org_b)
    assert (
        request(
            db,
            "POST",
            "/units",
            {"code": "X", "name": "X", "kind": "bu", "parent_id": str(foreign.id)},
        ).status_code
        == 404
    )


def test_list_tenant_filter_and_cursor(org_database: AuthorizationDatabase) -> None:
    db = org_database
    admin(db)
    units = [create(db, f"U{n}", kind="ou") for n in range(3)]
    create(db, "BU")
    create(db, "FOREIGN", org=db.org_b, kind="ou")
    service(db).update_unit(units[0].id, OrgUnitPatch(active=False))
    first = request(db, "GET", "/units?kind=ou&active=true&page_size=1")
    assert first.status_code == 200
    page = first.json()
    assert len(page["rows"]) == 1
    second = request(
        db, "GET", "/units?kind=ou&active=true&page_size=1&cursor=" + page["next_cursor"]
    ).json()
    assert second["next_cursor"] is None
    assert page["rows"][0]["id"] != second["rows"][0]["id"]
    assert {page["rows"][0]["id"], second["rows"][0]["id"]} == {str(u.id) for u in units[1:]}


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/units", {"code": "X", "name": "X", "kind": "bu"}),
        ("PATCH", "/units/{id}", {"name": "New"}),
        ("PUT", "/settings/funding_mode", {"unit_id": "{id}", "value": "roll_up"}),
        ("DELETE", "/settings/funding_mode?unit_id={id}", None),
    ],
)
def test_each_write_requires_its_own_permission(
    org_database: AuthorizationDatabase, method: str, path: str, body: dict[str, object] | None
) -> None:
    db = org_database
    unit = create(db, "U")
    viewer = insert_identity(db, "wrong-permission")
    wrong = "org.setting.manage" if method in ("POST", "PATCH") else "org.unit.manage"
    grant_permissions(db, viewer, wrong)
    payload = dict(body) if body else None
    if payload and payload.get("unit_id"):
        payload["unit_id"] = str(unit.id)
    assert request(db, method, path.format(id=unit.id), payload, viewer).status_code == 403


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/units/{id}", None),
        ("PATCH", "/units/{id}", {"name": "New"}),
        ("PUT", "/settings/funding_mode", {"value": "roll_up"}),
        ("DELETE", "/settings/funding_mode?unit_id={id}", None),
        ("GET", "/units/{id}/settings/funding_mode/effective", None),
    ],
)
def test_no_covering_grant_conceals_records(
    org_database: AuthorizationDatabase, method: str, path: str, body: dict[str, object] | None
) -> None:
    db = org_database
    unit = create(db, "U")
    viewer = insert_identity(db, "ungranted")
    payload = dict(body) if body else None
    if method == "PUT":
        payload["unit_id"] = str(unit.id)
    assert request(db, method, path.format(id=unit.id), payload, viewer).status_code == 404


def test_parent_grant_does_not_cascade_but_org_grant_does(
    org_database: AuthorizationDatabase,
) -> None:
    db = org_database
    parent = create(db, "P")
    child = create(db, "C", parent.id, kind="ou")
    viewer = insert_identity(db, "unit-reader")
    with service_for(db, Scope(db.org_a), "unit-grant") as identity:
        role = identity.create_role("unit-reader", "Unit reader")
        identity.grant_permission(role.id, "org.unit.read")
        identity.grant_role(viewer, role.id, AuthorizationTarget(ScopeType.BU, parent.id))
    assert request(db, "GET", f"/units/{parent.id}", viewer=viewer).status_code == 200
    assert request(db, "GET", f"/units/{child.id}", viewer=viewer).status_code == 404
    grant_permissions(db, viewer, "org.unit.read")
    assert request(db, "GET", f"/units/{child.id}", viewer=viewer).status_code == 200


def test_cross_tenant_parent_fk_rejects_violation(org_database: AuthorizationDatabase) -> None:
    db = org_database
    foreign = create(db, "F", org=db.org_b)
    with pytest.raises(ForeignKeyViolation):
        db.connection.execute(
            "INSERT INTO org_unit(id, org_id, parent_id, code, name, kind) "
            "VALUES (%s, %s, %s, 'X', 'X', 'bu')",
            (uuid4(), db.org_a, foreign.id),
        )


def test_post_idempotency_replays_response_and_single_audit(
    org_database: AuthorizationDatabase,
) -> None:
    from contextlib import contextmanager
    from typing import cast

    from fastapi import Request

    from flo.kernel.idempotency import install_idempotency
    from flo.kernel.idempotency.store import IdempotencyConnection
    from flo.kernel.tenancy.context import use_scope
    from tests.authz.conftest import ROOT, load_migration

    db = org_database
    admin(db)
    migration = load_migration(ROOT / "migrations/20260825_0003_idempotency.py", "org-idempotency")
    headers = load_migration(
        ROOT / "migrations/20260825_0009_idempotency_response_headers.py", "org-idempotency-headers"
    )
    db.connection.execute("DROP TABLE IF EXISTS idempotency_key")
    migration.upgrade(db.connection)
    headers.upgrade(db.connection)
    app = app_for(db, db.actor_id)

    @contextmanager
    def connection_factory() -> Iterator[IdempotencyConnection]:
        yield cast(IdempotencyConnection, db.connection)

    install_idempotency(app, connection_factory)

    @app.middleware("http")
    async def outer_scope(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.session = type("Session", (), {"identity_id": db.actor_id})()
        with use_scope(Scope(db.org_a)):
            return await call_next(request)

    from flo.kernel.errors.handler import CorrelationIdMiddleware

    app.add_middleware(CorrelationIdMiddleware)

    async def send() -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            body = {"code": "ONCE", "name": "Once", "kind": "bu"}
            missing = await client.post("/api/v1/org/units", json=body)
            first = await client.post(
                "/api/v1/org/units", json=body, headers={"Idempotency-Key": "once"}
            )
            second = await client.post(
                "/api/v1/org/units", json=body, headers={"Idempotency-Key": "once"}
            )
            conflict = await client.post(
                "/api/v1/org/units",
                json=body | {"code": "OTHER"},
                headers={"Idempotency-Key": "once"},
            )
            return missing, first, second, conflict

    try:
        missing, first, second, conflict = asyncio.run(send())
        assert missing.status_code == 400
        assert first.status_code == second.status_code == 201
        assert first.json() == second.json()
        assert conflict.status_code == 422
        assert db.connection.execute(
            "SELECT count(*) FROM audit_log WHERE action = 'org_unit.create'"
        ).fetchone() == (1,)
        assert db.connection.execute(
            "SELECT count(*) FROM authorization_scope WHERE scope_id = %s", (first.json()["id"],)
        ).fetchone() == (1,)
    finally:
        migration.downgrade(db.connection)


def test_unit_creation_rolls_back_scope_and_row_when_audit_fails(
    org_database: AuthorizationDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flo.modules.org.service import OrgService

    db = org_database

    def fail_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("planted audit failure")

    monkeypatch.setattr(OrgService, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="planted audit failure"):
        create(db, "ROLLBACK")
    assert db.connection.execute("SELECT count(*) FROM org_unit").fetchone() == (0,)
    assert db.connection.execute("SELECT count(*) FROM authorization_scope").fetchone() == (0,)


def test_ou_setting_grant_covers_unit_and_projects_only(
    org_database: AuthorizationDatabase,
) -> None:
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.identity.resolver import AuthorizationResolver

    db = org_database
    unit = create(db, "OU", kind="ou")
    child = create(db, "CHILD", unit.id)
    viewer = insert_identity(db, "ou-manager")
    project, subproject = uuid4(), uuid4()
    with service_for(db, Scope(db.org_a), "ou-grant") as identity:
        role = identity.create_role("setting-manager", "Setting manager")
        identity.grant_permission(role.id, "org.setting.manage")
        identity.grant_role(viewer, role.id, AuthorizationTarget(ScopeType.BU, unit.id))
        identity.register_scope(ScopeType.PROJECT, project, ScopeType.BU, unit.id)
        identity.register_scope(ScopeType.PROJECT, subproject, ScopeType.PROJECT, project)
    assert (
        request(
            db,
            "PUT",
            "/settings/funding_mode",
            {"unit_id": str(unit.id), "value": "roll_up"},
            viewer,
        ).status_code
        == 200
    )
    assert (
        request(
            db, "DELETE", f"/settings/funding_mode?unit_id={unit.id}", viewer=viewer
        ).status_code
        == 204
    )
    assert (
        request(
            db,
            "PUT",
            "/settings/funding_mode",
            {"unit_id": str(child.id), "value": "roll_up"},
            viewer,
        ).status_code
        == 404
    )
    assert (
        request(
            db, "POST", "/units", {"code": "NO", "name": "No", "kind": "bu"}, viewer
        ).status_code
        == 403
    )
    with tenant_transaction(db.connection, Scope(db.org_a)):
        for target in (project, subproject):
            assert (
                AuthorizationResolver(db.authorization_connection, Scope(db.org_a))
                .check(viewer, "org.setting.manage", AuthorizationTarget(ScopeType.PROJECT, target))
                .allowed
            )


@pytest.mark.parametrize(
    "path", ["/units", "/units/{id}", "/units/{id}/settings/funding_mode/effective"]
)
def test_reads_require_read_permission(org_database: AuthorizationDatabase, path: str) -> None:
    db = org_database
    unit = create(db, "U")
    viewer = insert_identity(db, "manager-without-read")
    grant_permissions(db, viewer, "org.unit.manage")
    assert request(db, "GET", path.format(id=unit.id), viewer=viewer).status_code == 403
