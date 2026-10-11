from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date
from uuid import uuid4

import httpx
import pytest
from psycopg.errors import CheckViolation, ExclusionViolation, ForeignKeyViolation
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from flo.kernel.tenancy.context import Scope
from flo.modules.org.schemas import OrgAddressCreate
from tests.authz.conftest import ROOT, AuthorizationDatabase, load_migration
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity

from .conftest import app_for, service
from .conftest import org_database as org_database
from .test_units import admin, create, request

BODY = {
    "kind": "bill_to",
    "line1": "1 Main St",
    "city": "Town",
    "country": "US",
    "effective_from": "2026-01-01",
}


@pytest.fixture
def addresses(org_database: AuthorizationDatabase) -> Iterator[AuthorizationDatabase]:
    migration = load_migration(ROOT / "migrations/20260826_0014_org_addresses.py", "addresses")
    migration.upgrade(org_database.connection)
    try:
        yield org_database
    finally:
        migration.downgrade(org_database.connection)


def test_overlap_adjacent_boundaries_and_close_history(addresses: AuthorizationDatabase) -> None:
    db = addresses
    admin(db)
    unit = create(db, "U")
    path = f"/units/{unit.id}/addresses"
    first = request(db, "POST", path, BODY)
    assert first.status_code == 201
    aid = first.json()["id"]
    overlap = request(db, "POST", path, BODY)
    assert overlap.status_code == 409
    assert overlap.json()["type"] == "https://xlr8flo.app/errors/conflict"
    assert overlap.json()["checks"]["problem"] == "address_overlap"
    assert aid in overlap.json()["detail"]
    assert request(db, "PATCH", path + f"/{aid}", {"effective_to": "2025-12-31"}).status_code == 422
    assert request(db, "PATCH", path + f"/{aid}", {"effective_to": "2026-06-30"}).status_code == 200
    for value in ("2026-07-01", "2026-06-29", None):
        response = request(db, "PATCH", path + f"/{aid}", {"effective_to": value})
        assert response.status_code == (422 if value is None else 409)
        if value is not None:
            assert response.json()["checks"]["problem"] == "address_closed"
    assert request(db, "PATCH", path + f"/{aid}", {"line1": "Edited"}).status_code == 422
    same_day = request(db, "POST", path, BODY | {"effective_from": "2026-06-30"})
    assert same_day.status_code == 409
    second = request(db, "POST", path, BODY | {"effective_from": "2026-07-01"})
    assert second.status_code == 201
    for day, expected in [
        ("2025-12-31", []),
        ("2026-01-01", [aid]),
        ("2026-06-30", [aid]),
        ("2026-07-01", [second.json()["id"]]),
    ]:
        response = request(db, "GET", path + f"?kind=bill_to&as_of={day}")
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == expected
    assert request(db, "GET", path + "?kind=ship_to&as_of=2026-01-01").json() == []
    assert db.connection.execute(
        "SELECT action, target_type FROM audit_log "
        "WHERE action LIKE 'org_address.%' ORDER BY occurred_at"
    ).fetchall() == [
        ("org_address.create", "org_address"),
        ("org_address.close", "org_address"),
        ("org_address.create", "org_address"),
    ]


def test_default_today_and_independent_kind(
    addresses: AuthorizationDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = addresses
    unit = create(db, "U")
    svc = service(db)
    for kind in ("bill_to", "ship_to"):
        svc.create_address(unit.id, OrgAddressCreate.model_validate(BODY | {"kind": kind}))
    monkeypatch.setattr("flo.modules.org.service.today", lambda: date(2026, 1, 1))
    assert len(svc.list_addresses(unit.id, None, None)) == 2
    monkeypatch.setattr("flo.modules.org.service.today", lambda: date(2025, 12, 31))
    assert svc.list_addresses(unit.id, None, None) == []


@pytest.mark.parametrize("method", ["POST", "GET", "PATCH"])
def test_foreign_unit_addresses(addresses: AuthorizationDatabase, method: str) -> None:
    db = addresses
    admin(db)
    unit = create(db, "F", org=db.org_b)
    path = f"/units/{unit.id}/addresses"
    body = BODY if method == "POST" else None
    if method == "PATCH":
        path += f"/{uuid4()}"
        body = {"effective_to": "2026-06-30"}
    assert request(db, method, path, body).status_code == 404


def test_foreign_address_id(addresses: AuthorizationDatabase) -> None:
    db = addresses
    admin(db)
    own = create(db, "O")
    foreign = create(db, "F", org=db.org_b)
    address = service(db, db.org_b).create_address(
        foreign.id, OrgAddressCreate.model_validate(BODY)
    )
    assert (
        request(
            db, "PATCH", f"/units/{own.id}/addresses/{address.id}", {"effective_to": "2026-06-30"}
        ).status_code
        == 404
    )
    viewer = insert_identity(db, "foreign-address-viewer")
    grant_permissions(db, viewer, "org.unit.read")
    assert (
        request(
            db,
            "PATCH",
            f"/units/{own.id}/addresses/{address.id}",
            {"effective_to": "2026-06-30"},
            viewer,
        ).status_code
        == 404
    )
    other = create(db, "OTHER")
    local = service(db).create_address(other.id, OrgAddressCreate.model_validate(BODY))
    assert (
        request(
            db, "PATCH", f"/units/{own.id}/addresses/{local.id}", {"effective_to": "2026-06-30"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("method", ["POST", "GET", "PATCH"])
@pytest.mark.parametrize("granted,expected", [(False, 404), (True, 403)])
def test_permissions(
    addresses: AuthorizationDatabase, method: str, granted: bool, expected: int
) -> None:
    db = addresses
    unit = create(db, "U")
    address = service(db).create_address(unit.id, OrgAddressCreate.model_validate(BODY))
    viewer = insert_identity(db, "viewer")
    if granted:
        grant_permissions(db, viewer, "org.unit.manage" if method == "GET" else "org.unit.read")
    path = f"/units/{unit.id}/addresses"
    body = BODY if method == "POST" else None
    if method == "PATCH":
        path += f"/{address.id}"
        body = {"effective_to": "2026-06-30"}
    assert request(db, method, path, body, viewer).status_code == expected


@pytest.mark.parametrize("violation", ["overlap", "kind", "range", "foreign_unit"])
def test_database_guards(addresses: AuthorizationDatabase, violation: str) -> None:
    db = addresses
    unit = create(db, "U")
    service(db).create_address(unit.id, OrgAddressCreate.model_validate(BODY))
    target = create(db, "F", org=db.org_b).id if violation == "foreign_unit" else unit.id
    expected = {
        "overlap": ExclusionViolation,
        "kind": CheckViolation,
        "range": CheckViolation,
        "foreign_unit": ForeignKeyViolation,
    }[violation]
    with pytest.raises(expected):
        db.connection.execute(
            "INSERT INTO org_address(id,org_id,unit_id,kind,line1,city,country,"
            "effective_from,effective_to,created_by) "
            "VALUES (%s,%s,%s,%s,'X','X','US','2026-01-01',%s,%s)",
            (
                uuid4(),
                db.org_a,
                target,
                "bad" if violation == "kind" else "bill_to",
                "2025-12-31" if violation == "range" else None,
                db.actor_id,
            ),
        )


def test_migration_up_down_preserves_existing_data(org_database: AuthorizationDatabase) -> None:
    db = org_database
    create(db, "KEEP")
    tables = ("organization", "org_unit", "identity", "authorization_scope", "audit_log")
    before = {table: db.connection.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    migration = load_migration(
        ROOT / "migrations/20260826_0014_org_addresses.py", "preserve-addresses"
    )
    migration.upgrade(db.connection)
    assert migration.down_revision == "20260826_0013"
    assert db.connection.execute("SELECT count(*) FROM org_address").fetchone() == (0,)
    migration.downgrade(db.connection)
    assert db.connection.execute("SELECT to_regclass('org_address')").fetchone() == (None,)
    for table in tables:
        assert db.connection.execute(f"SELECT * FROM {table}").fetchall() == before[table]
    migration.upgrade(db.connection)
    migration.downgrade(db.connection)


def test_address_post_idempotency_replays_response_and_single_audit(
    addresses: AuthorizationDatabase,
) -> None:
    from contextlib import contextmanager
    from typing import cast

    from fastapi import Request

    from flo.kernel.idempotency import install_idempotency
    from flo.kernel.idempotency.store import IdempotencyConnection
    from flo.kernel.tenancy.context import use_scope
    from tests.authz.conftest import ROOT, load_migration

    db = addresses
    admin(db)
    migration = load_migration(ROOT / "migrations/20260825_0003_idempotency.py", "org-idempotency")
    headers = load_migration(
        ROOT / "migrations/20260825_0009_idempotency_response_headers.py", "org-idempotency-headers"
    )
    db.connection.execute("DROP TABLE IF EXISTS idempotency_key")
    migration.upgrade(db.connection)
    headers.upgrade(db.connection)
    unit = create(db, "U")
    path = f"/api/v1/org/units/{unit.id}/addresses"
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
            body = BODY
            missing = await client.post(path, json=body)
            first = await client.post(path, json=body, headers={"Idempotency-Key": "once"})
            second = await client.post(path, json=body, headers={"Idempotency-Key": "once"})
            conflict = await client.post(
                path,
                json=body | {"line1": "Other"},
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
            "SELECT count(*) FROM audit_log WHERE action = 'org_address.create'"
        ).fetchone() == (1,)
    finally:
        migration.downgrade(db.connection)


def test_rls_rejects_foreign_reads_and_writes(addresses: AuthorizationDatabase) -> None:
    from psycopg import sql
    from psycopg.errors import InsufficientPrivilege

    from flo.kernel.tenancy.context import Scope
    from flo.kernel.tenancy.rls import tenant_transaction

    db = addresses
    own = create(db, "OWN")
    foreign = create(db, "FOREIGN", org=db.org_b)
    a = service(db).create_address(own.id, OrgAddressCreate.model_validate(BODY))
    service(db, db.org_b).create_address(foreign.id, OrgAddressCreate.model_validate(BODY))
    role = "address_rls_" + uuid4().hex
    db.connection.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        db.connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
        )
        db.connection.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE ON org_address TO {}").format(
                sql.Identifier(role)
            )
        )
        db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(db.connection, Scope(db.org_a)):
            assert db.connection.execute("SELECT id FROM org_address").fetchall() == [(a.id,)]
        with pytest.raises(InsufficientPrivilege):
            with tenant_transaction(db.connection, Scope(db.org_a)):
                db.connection.execute(
                    "INSERT INTO org_address(id,org_id,unit_id,kind,line1,city,country,"
                    "effective_from,created_by) "
                    "VALUES (%s,%s,%s,'ship_to','X','X','US','2026-01-01',%s)",
                    (uuid4(), db.org_b, foreign.id, db.actor_id),
                )
    finally:
        db.connection.execute("RESET ROLE")
        db.connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        db.connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_address_audit_failure_rolls_back(
    addresses: AuthorizationDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flo.modules.org.service import OrgService

    db = addresses
    unit = create(db, "U")

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("planted audit failure")

    monkeypatch.setattr(OrgService, "_audit", fail)
    with pytest.raises(RuntimeError, match="planted audit failure"):
        service(db).create_address(unit.id, OrgAddressCreate.model_validate(BODY))
    assert db.connection.execute("SELECT count(*) FROM org_address").fetchone() == (0,)


@pytest.mark.parametrize(
    "change",
    [
        {"effective_to": "2025-12-31"},
        {"kind": "other"},
        {"country": "USA"},
        {"org_id": "ignored"},
    ],
)
def test_create_schema_rejects_planted_invalid_values(change: dict[str, object]) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OrgAddressCreate.model_validate(BODY | change)
