from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date
from uuid import UUID, uuid4

import httpx
import pytest
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from flo.api.master import router
from flo.kernel.errors import ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.org.master_kinds import RULES, validate_attributes
from flo.modules.org.schemas import MasterCreate, MasterPatch
from flo.modules.org.service import MasterCodeUnusable, OrgService, assert_usable
from tests.authz.conftest import ROOT, AuthorizationDatabase, load_migration
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity

from .conftest import app_for


@pytest.fixture
def master_db(org_database: AuthorizationDatabase) -> Iterator[AuthorizationDatabase]:
    migration = load_migration(ROOT / "migrations/20260826_0015_master_records.py", "master")
    migration.upgrade(org_database.connection)
    try:
        yield org_database
    finally:
        migration.downgrade(org_database.connection)


def svc(db: AuthorizationDatabase, org: UUID | None = None) -> OrgService:
    return OrgService(db.connection, Scope(org or db.org_a), db.actor_id)


def create(db: AuthorizationDatabase, code: str = "D", org: UUID | None = None):
    return svc(db, org).create_master("department", MasterCreate(code=code, name="Department"))


def request(db, method, path, body=None, viewer=None):
    app = app_for(db, viewer or db.actor_id)
    app.include_router(router)

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            return await client.request(method, "/api/v1/master" + path, json=body)

    return asyncio.run(send())


def admin(db):
    grant_permissions(db, db.actor_id, "master.manage", "master.read")


GOOD = {
    "department": {},
    "ledger_account": {"account_type": "expense"},
    "uom": {"dimension": "mass"},
    "tax_code": {"rate": "7.5"},
    "payment_term": {"net_days": 30, "discount_percent": "2", "discount_days": 10},
    "item_category": {},
    "service_category": {},
}


@pytest.mark.parametrize("kind", RULES)
def test_each_kind_valid_and_unknown_attribute_rejected(kind):
    validate_attributes(kind, GOOD[kind])
    with pytest.raises(ProblemError):
        validate_attributes(kind, GOOD[kind] | {"bad": "value"})


@pytest.mark.parametrize(
    "kind,attrs",
    [
        ("ledger_account", {}),
        ("ledger_account", {"account_type": "other"}),
        ("uom", {}),
        ("uom", {"dimension": "weight"}),
        ("tax_code", {}),
        *[
            ("tax_code", {"rate": v})
            for v in [
                20,
                True,
                "NaN",
                "-1",
                "101",
                "100.0001",
                "1.00001",
                "1e1",
                " 20",
                "20\n",
                "١",
            ]
        ],
        *[("payment_term", {"net_days": v}) for v in ["30", True, -1, 366]],
        ("payment_term", {}),
        ("payment_term", {"net_days": 30, "discount_days": 1}),
        ("payment_term", {"net_days": 30, "discount_percent": "2"}),
        ("payment_term", {"net_days": 30, "discount_days": 31, "discount_percent": "2"}),
        ("payment_term", {"net_days": 30, "discount_days": True, "discount_percent": "2"}),
        ("payment_term", {"net_days": 30, "discount_days": 1, "discount_percent": 2}),
    ],
)
def test_attribute_rules_reject_planted_values(master_db, kind, attrs):
    admin(master_db)
    response = request(
        master_db, "POST", "/" + kind, {"code": "BAD", "name": "Bad", "attributes": attrs}
    )
    assert response.status_code == 422
    assert response.json()["checks"]["problem"] == "invalid_attributes"


@pytest.mark.parametrize("value", ["0", "100", "100.0000", "99.9999", "00.1"])
def test_percentage_boundaries(value):
    validate_attributes("tax_code", {"rate": value})


@pytest.mark.parametrize("value", [0, 365])
def test_days_boundaries(value):
    validate_attributes("payment_term", {"net_days": value})


def test_lifecycle_dates_audit_and_immutable_code(master_db):
    db = master_db
    admin(db)
    row = create(db, " d ")
    assert row.code == "D"
    assert request(db, "POST", "/department", {"code": "d", "name": "Dup"}).status_code == 409
    assert create(db, "D", db.org_b).code == "D"
    assert request(db, "PATCH", f"/department/{row.id}", {"code": "NEW"}).status_code == 422
    changed = svc(db).update_master(
        "department",
        row.id,
        MasterPatch(effective_from=date(2026, 1, 1), effective_to=date(2026, 1, 31)),
    )
    for day in [date(2026, 1, 1), date(2026, 1, 31)]:
        assert assert_usable(db.connection, Scope(db.org_a), "department", " d ", day) == changed
    for code, day, reason in [
        ("missing", date(2026, 1, 1), "unknown"),
        ("D", date(2025, 12, 31), "not_effective"),
        ("D", date(2026, 2, 1), "not_effective"),
    ]:
        with pytest.raises(MasterCodeUnusable) as caught:
            svc(db).assert_usable("department", code, day)
        assert caught.value.reason == reason
    assert (
        request(db, "PATCH", f"/department/{row.id}", {"effective_to": "2025-01-01"}).status_code
        == 422
    )
    assert request(db, "PATCH", f"/department/{row.id}", {"effective_to": None}).status_code == 200
    for _ in range(2):
        assert request(db, "POST", f"/department/{row.id}:deactivate").status_code == 200
    with pytest.raises(MasterCodeUnusable) as caught:
        svc(db).assert_usable("department", "D", date(2026, 1, 1))
    assert caught.value.reason == "inactive"
    assert request(db, "GET", "/department?active=false").json()["rows"][0]["code"] == "D"
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action LIKE 'master_record.%'"
    ).fetchone() == (5,)


def test_foreign_master_id(master_db):
    db = master_db
    admin(db)
    row = create(db, org=db.org_b)
    assert request(db, "PATCH", f"/department/{row.id}", {"name": "No"}).status_code == 404
    assert request(db, "POST", f"/department/{row.id}:deactivate").status_code == 404


def test_collection_tenant_isolation(master_db):
    db = master_db
    admin(db)
    create(db, "FOREIGN", db.org_b)
    assert request(db, "GET", "/department").json()["rows"] == []
    own = request(db, "POST", "/department", {"code": "FOREIGN", "name": "Own"})
    assert own.status_code == 201
    assert svc(db, db.org_b).list_master("department").rows[0].name == "Department"
    assert request(db, "GET", "/currency?q=usd").json() == [{"code": "USD", "exponent": 2}]


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/department", None),
        ("GET", "/currency", None),
        ("POST", "/department", {"code": "X", "name": "X"}),
        ("PATCH", "/department/{id}", {"name": "New"}),
        ("POST", "/department/{id}:deactivate", None),
    ],
)
@pytest.mark.parametrize("grant", [False, True])
def test_permissions(master_db, method, path, body, grant):
    db = master_db
    row = create(db)
    viewer = insert_identity(db, "viewer")
    if grant:
        grant_permissions(db, viewer, "master.manage" if method == "GET" else "master.read")
    expected = 404 if "{id}" in path and not grant else 403
    assert request(db, method, path.format(id=row.id), body, viewer).status_code == expected


def test_unknown_kind_and_currency_read_only(master_db):
    db = master_db
    admin(db)
    for method in ["GET", "POST"]:
        response = request(
            db, method, "/unknown", {"code": "X", "name": "X"} if method == "POST" else None
        )
        assert response.status_code == 422
        assert response.json()["checks"]["problem"] == "unknown_kind"
    assert request(db, "GET", "/currency?q=usd").json() == [{"code": "USD", "exponent": 2}]
    assert request(db, "POST", "/currency", {"code": "USD", "name": "Dollar"}).status_code == 422


def delete_routes(routes):
    return [r.path for r in routes if "DELETE" in r.methods and "/master" in r.path]


def test_no_delete_route_and_gate_rejects_planted_delete():
    from fastapi import APIRouter

    assert delete_routes(router.routes) == []
    planted = APIRouter()
    planted.add_api_route("/api/v1/master/{kind}/{id}", lambda: None, methods=["DELETE"])
    assert delete_routes(router.routes + planted.routes)


def test_large_typeahead_is_server_filtered_and_bounded(master_db, monkeypatch):
    db = master_db
    admin(db)
    db.connection.execute(
        """INSERT INTO master_record(id, org_id, kind, code, name)
        SELECT gen_random_uuid(), %s, 'department', 'CODE' || n, 'Name' || n
        FROM generate_series(1,10000) n""",
        (db.org_a,),
    )
    from flo.modules.org.models import OrgRepository

    original = OrgRepository.execute
    sizes = []

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchall(self):
            rows = self.cursor.fetchall()
            sizes.append(len(rows))
            return rows

        def fetchone(self):
            return self.cursor.fetchone()

    def execute(self, query, params=None):
        return Cursor(original(self, query, params))

    monkeypatch.setattr(OrgRepository, "execute", execute)
    page = request(db, "GET", "/department?q=code1&active=true&as_of=2099-01-01").json()
    assert len(page["rows"]) == 50
    assert all(row["code"].startswith("CODE1") for row in page["rows"])
    assert sizes == [51]
    second = request(db, "GET", "/department?q=code1&cursor=" + page["next_cursor"]).json()
    assert not {r["id"] for r in page["rows"]} & {r["id"] for r in second["rows"]}
    assert request(db, "GET", "/department?page_size=51").status_code == 422
    assert request(db, "GET", "/department?q=%").json()["rows"] == []
    assert request(db, "GET", "/department?q=name9999").json()["rows"][0]["code"] == "CODE9999"


def test_migration_preserves_existing_data(org_database):
    db = org_database
    migration = load_migration(
        ROOT / "migrations/20260826_0015_master_records.py", "master-preserve"
    )
    tables = ("identity", "organization", "org_unit", "org_setting", "audit_log", "permission")
    before = {t: db.connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    migration.upgrade(db.connection)
    try:
        assert migration.down_revision == "20260826_0014"
        assert create(db).code == "D"
        assert db.connection.execute(
            "SELECT code FROM permission WHERE code LIKE 'master.%' ORDER BY code"
        ).fetchall() == [("master.manage",), ("master.read",)]
    finally:
        migration.downgrade(db.connection)
    for t in tables:
        if t != "audit_log":
            assert db.connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() == before[t]
    assert (
        db.connection.execute("SELECT count(*) FROM audit_log").fetchone()[0]
        == len(before["audit_log"]) + 1
    )


def test_rls_rejects_planted_foreign_write(master_db):
    from psycopg import sql
    from psycopg.errors import InsufficientPrivilege

    from flo.kernel.tenancy.rls import tenant_transaction

    db = master_db
    own = create(db)
    create(db, org=db.org_b)
    role = "master_rls_" + uuid4().hex
    db.connection.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        db.connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
        )
        db.connection.execute(
            sql.SQL("GRANT SELECT, INSERT ON master_record TO {}").format(sql.Identifier(role))
        )
        db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        with tenant_transaction(db.connection, Scope(db.org_a)):
            assert db.connection.execute("SELECT id FROM master_record").fetchall() == [(own.id,)]
        with pytest.raises(InsufficientPrivilege):
            with tenant_transaction(db.connection, Scope(db.org_a)):
                db.connection.execute(
                    "INSERT INTO master_record(id,org_id,kind,code,name) "
                    "VALUES (%s,%s,'department','BAD','Bad')",
                    (uuid4(), db.org_b),
                )
    finally:
        db.connection.execute("RESET ROLE")
        db.connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        db.connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_audit_failure_rolls_back_master(master_db, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("planted audit failure")

    monkeypatch.setattr(OrgService, "_audit", fail)
    with pytest.raises(RuntimeError, match="planted audit"):
        create(master_db)
    assert svc(master_db).list_master("department").rows == []


def test_post_idempotency_replays_response_and_single_audit(
    master_db: AuthorizationDatabase,
) -> None:
    from contextlib import contextmanager
    from typing import cast

    from fastapi import Request

    from flo.kernel.idempotency import install_idempotency
    from flo.kernel.idempotency.store import IdempotencyConnection
    from flo.kernel.tenancy.context import use_scope
    from tests.authz.conftest import ROOT, load_migration

    db = master_db
    admin(db)
    migration = load_migration(ROOT / "migrations/20260825_0003_idempotency.py", "org-idempotency")
    headers = load_migration(
        ROOT / "migrations/20260825_0009_idempotency_response_headers.py", "org-idempotency-headers"
    )
    db.connection.execute("DROP TABLE IF EXISTS idempotency_key")
    migration.upgrade(db.connection)
    headers.upgrade(db.connection)
    app = app_for(db, db.actor_id)
    app.include_router(router)

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
            body = {"code": "ONCE", "name": "Once"}
            missing = await client.post("/api/v1/master/department", json=body)
            first = await client.post(
                "/api/v1/master/department", json=body, headers={"Idempotency-Key": "once"}
            )
            second = await client.post(
                "/api/v1/master/department", json=body, headers={"Idempotency-Key": "once"}
            )
            conflict = await client.post(
                "/api/v1/master/department",
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
            "SELECT count(*) FROM audit_log WHERE action = 'master_record.create'"
        ).fetchone() == (1,)
    finally:
        migration.downgrade(db.connection)


def test_unique_and_tenant_fk_reject_planted_records(master_db):
    from psycopg.errors import ForeignKeyViolation, UniqueViolation

    db = master_db
    create(db)
    statement = (
        "INSERT INTO master_record(id, org_id, kind, code, name) "
        "VALUES (%s, %s, 'department', 'D', 'Duplicate')"
    )
    with pytest.raises(UniqueViolation):
        db.connection.execute(statement, (uuid4(), db.org_a))
    with pytest.raises(ForeignKeyViolation):
        db.connection.execute(statement, (uuid4(), uuid4()))


def test_patch_attributes_rejects_bad_values(master_db):
    db = master_db
    admin(db)
    row = svc(db).create_master(
        "tax_code", MasterCreate(code="VAT", name="Tax", attributes={"rate": "20"})
    )
    response = request(db, "PATCH", f"/tax_code/{row.id}", {"attributes": {"rate": 20}})
    assert response.status_code == 422
    assert response.json()["checks"]["problem"] == "invalid_attributes"
    assert svc(db).get_master("tax_code", row.id).attributes == {"rate": "20"}
