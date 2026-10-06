import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg
import pytest

from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.identity.resolver import AuthorizationResolver
from flo.modules.org.schemas import MasterPatch
from flo.modules.org.service import OrgService
from flo.modules.projects.schemas import ProjectPatch
from flo.modules.projects.service import ProjectService, get_status
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity, service_for

from .conftest import app_for, body, service, settings, unit


def request(db, method, path="", json=None, headers=None, viewer=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_for(db, viewer)), base_url="https://testserver"
        ) as client:
            return await client.request(
                method, "/api/v1/projects" + path, json=json, headers=headers
            )

    return asyncio.run(send())


def test_create_details_scope_audit_and_status(project_db):
    db = project_db
    bu = unit(db)
    response = request(db, "POST", json=body(bu).model_dump(mode="json"))
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["owner_id"] == str(db.actor_id)
    assert row["status"] == "draft" and row["version"] == 1
    assert row["number"].endswith("0001")
    assert set(row) == set(body(bu).model_dump()) | {
        "id",
        "number",
        "status",
        "version",
        "created_at",
    }
    assert request(db, "GET", "/" + row["id"]).json() == row
    from uuid import UUID

    assert get_status(db.connection, Scope(db.org_a), UUID(row["id"])) == "draft"
    with tenant_transaction(db.connection, Scope(db.org_a)):
        target = AuthorizationTarget(ScopeType.PROJECT, UUID(row["id"]))
        assert (
            AuthorizationResolver(db.authorization_connection, Scope(db.org_a))
            .check(db.actor_id, "project.read", target)
            .allowed
        )
        assert db.connection.execute(
            "SELECT action FROM audit_log WHERE target_id = %s", (row["id"],)
        ).fetchall() == [("project.create",)]
    updated = request(
        db,
        "PATCH",
        "/" + row["id"],
        json={"name": "Changed", "description": None},
        headers={"If-Match": "1"},
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert updated.json()["name"] == "Changed"
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action='project.update'"
    ).fetchone() == (1,)


@pytest.mark.parametrize(
    "field", ["number", "bu_id", "parent_id", "currency", "department_code", "ledger_account_code"]
)
def test_patch_rejects_each_immutable_field(project_db, field):
    db = project_db
    row = service(db).create(body(unit(db)))
    result = request(db, "PATCH", f"/{row.id}", json={field: None}, headers={"If-Match": "1"})
    assert result.status_code == 422
    assert result.json()["checks"]["problem"] == "immutable_field"
    assert result.json()["errors"][0]["field"] == field
    assert service(db).get(row.id) == row


@pytest.mark.parametrize(
    "headers,status,label",
    [({}, 400, "if_match_required"), ({"If-Match": "2"}, 409, "stale_version")],
)
def test_if_match_guard(project_db, headers, status, label):
    db = project_db
    row = service(db).create(body(unit(db)))
    result = request(db, "PATCH", f"/{row.id}", json={"name": "X"}, headers=headers)
    assert result.status_code == status and result.json()["checks"]["problem"] == label
    assert service(db).get(row.id) == row


def test_parent_currency_users_and_dates_are_validated(project_db):
    db = project_db
    bu = unit(db)
    foreign = insert_identity(db, "foreign-owner")
    with service_for(db, Scope(db.org_b), "foreign-membership") as identity:
        role = identity.create_role("member", "Member")
        identity.grant_role(foreign, role.id, AuthorizationTarget.organization(db.org_b))
    for changes, field, label in [
        ({"parent_id": str(uuid4())}, "parent_id", "parenting_not_supported"),
        ({"currency": "ZZZ"}, "currency", "invalid_project"),
        ({"owner_id": str(foreign)}, "owner_id", "invalid_project"),
        ({"sponsor_id": str(foreign)}, "sponsor_id", "invalid_project"),
        (
            {"planned_start": "2026-02-01", "planned_end": "2026-01-01"},
            "planned_end",
            "invalid_project",
        ),
    ]:
        result = request(db, "POST", json=body(bu).model_dump(mode="json") | changes)
        assert result.status_code == 422, result.text
        assert result.json()["errors"][0]["field"] == field
        assert result.json()["checks"]["problem"] == label
    row = service(db).create(body(bu, planned_start=date(2026, 2, 1)))
    for changes, field in [
        ({"planned_end": "2026-01-01"}, "planned_end"),
        ({"owner_id": None}, "owner_id"),
        ({"name": None}, "name"),
        ({"sponsor_id": str(foreign)}, "sponsor_id"),
    ]:
        result = request(db, "PATCH", f"/{row.id}", json=changes, headers={"If-Match": "1"})
        assert result.status_code == 422 and result.json()["errors"][0]["field"] == field


@pytest.mark.parametrize(
    "kind,field", [("department", "department_code"), ("ledger_account", "ledger_account_code")]
)
@pytest.mark.parametrize("reason", ["unknown", "inactive", "not_effective"])
def test_unusable_accounting_codes(project_db, kind, field, reason):
    db = project_db
    bu = unit(db)
    code = "D" if kind == "department" else "L"
    svc = OrgService(db.connection, Scope(db.org_a), db.actor_id)
    if reason != "unknown":
        record = svc.assert_usable(kind, code, date.today())
        svc.update_master(
            kind,
            record.id,
            MasterPatch(active=False)
            if reason == "inactive"
            else MasterPatch(effective_from=date.today() + timedelta(days=1)),
        )
    else:
        code = "MISSING"
    result = request(db, "POST", json=body(bu).model_dump(mode="json") | {field: code})
    assert result.status_code == 422, result.text
    assert result.json()["errors"][0]["field"] == field
    assert code in result.json()["errors"][0]["message"]
    assert reason in result.json()["errors"][0]["message"]


def test_create_foreign_bu(project_db):
    db = project_db
    foreign = unit(db, org=db.org_b)
    assert request(db, "POST", json=body(foreign).model_dump(mode="json")).status_code == 404


def foreign_project(db):
    bu = unit(db, org=db.org_b)
    return service(db, db.org_b).create(body(bu))


def test_get_foreign_project(project_db):
    row = foreign_project(project_db)
    assert request(project_db, "GET", f"/{row.id}").status_code == 404


def test_patch_foreign_project(project_db):
    row = foreign_project(project_db)
    assert (
        request(
            project_db, "PATCH", f"/{row.id}", json={"name": "Wrong"}, headers={"If-Match": "1"}
        ).status_code
        == 404
    )


def test_list_tenant_isolation(project_db):
    db = project_db
    own = service(db).create(body(unit(db)))
    foreign_project(db)
    assert [row["id"] for row in request(db, "GET").json()["rows"]] == [str(own.id)]


@pytest.mark.parametrize("method", ["POST", "GET", "PATCH"])
@pytest.mark.parametrize("grant", [False, True])
def test_wrong_permission_or_absent_scope(project_db, method, grant):
    db = project_db
    bu = unit(db)
    row = service(db).create(body(bu))
    viewer = insert_identity(db, "denied")
    if grant:
        grant_permissions(db, viewer, "master.read")
    result = request(
        db,
        method,
        "" if method == "POST" else f"/{row.id}",
        json=body(bu).model_dump(mode="json")
        if method == "POST"
        else {"name": "X"}
        if method == "PATCH"
        else None,
        headers={"If-Match": "1"},
        viewer=viewer,
    )
    assert result.status_code == (403 if grant else 404)


def test_bu_grant_covers_project_and_conceals_other_bu(project_db):
    db = project_db
    a, b = unit(db, "A"), unit(db, "B")
    viewer = insert_identity(db, "bu-author")
    with service_for(db, Scope(db.org_a), "bu-grant") as identity:
        role = identity.create_role("bu-author", "BU author")
        for permission in ("project.create", "project.read", "project.update"):
            identity.grant_permission(role.id, permission)
        identity.grant_role(viewer, role.id, AuthorizationTarget(ScopeType.BU, a.id))
    created = request(db, "POST", json=body(a).model_dump(mode="json"), viewer=viewer)
    assert created.status_code == 201, created.text
    assert request(db, "GET", "/" + created.json()["id"], viewer=viewer).status_code == 200
    assert (
        request(db, "POST", json=body(b).model_dump(mode="json"), viewer=viewer).status_code == 404
    )
    other = service(db).create(body(b))
    assert request(db, "GET", f"/{other.id}", viewer=viewer).status_code == 404
    assert (
        request(
            db,
            "PATCH",
            f"/{other.id}",
            json={"name": "X"},
            headers={"If-Match": "1"},
            viewer=viewer,
        ).status_code
        == 404
    )
    assert request(db, "GET", viewer=viewer).status_code == 403


def test_twenty_concurrent_creates_and_failed_create_leaves_gap(project_db):
    db = project_db
    bu = unit(db)

    def create_one(i):
        with psycopg.connect(
            settings(db).database_url.get_secret_value(), autocommit=True
        ) as connection:
            with correlation_context(f"concurrent-create-{i}"):
                return service(db, connection=connection).create(body(bu))

    with ThreadPoolExecutor(max_workers=20) as pool:
        rows = list(pool.map(create_one, range(20)))
    assert sorted(int(row.number.split("-")[-1]) for row in rows) == list(range(1, 21))
    with patch.object(ProjectService, "_audit", side_effect=RuntimeError("planted failure")):
        with pytest.raises(RuntimeError, match="planted failure"):
            service(db).create(body(bu))
    next_row = service(db).create(body(bu))
    assert next_row.number.endswith("0022")
    assert db.connection.execute("SELECT count(*) FROM project").fetchone() == (21,)


def test_two_concurrent_updates_only_one_succeeds(project_db):
    db = project_db
    row = service(db).create(body(unit(db)))

    def update_one(i):
        with psycopg.connect(
            settings(db).database_url.get_secret_value(), autocommit=True
        ) as connection:
            with correlation_context(f"concurrent-patch-{i}"):
                try:
                    return (
                        service(db, connection=connection)
                        .update(row.id, ProjectPatch(name=f"Name{i}"), "1")
                        .version
                    )
                except ProblemError as exc:
                    return exc.checks["problem"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update_one, range(2)))
    assert sorted(map(str, results)) == ["2", "stale_version"]


@pytest.mark.parametrize("sort", ["number", "name", "status", "created_at"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_keyset_paging_never_repeats_or_skips(project_db, sort, direction):
    db = project_db
    bu = unit(db)
    db.connection.execute(
        """INSERT INTO project
        (id, org_id, bu_id, number, name, owner_id, department_code, ledger_account_code,
         currency, created_by)
        SELECT gen_random_uuid(), %s, %s, 'P' || n, 'Name' || (n / 3), %s, 'D', 'L', 'USD', %s
        FROM generate_series(1,120) n""",
        (db.org_a, bu.id, db.actor_id, db.actor_id),
    )
    expected = [
        str(row[0])
        for row in db.connection.execute(
            f"SELECT id FROM project ORDER BY {sort} {direction}, id {direction}"
        ).fetchall()
    ]
    ids, cursor = [], None
    while True:
        page = service(db).list(sort=sort, direction=direction, cursor=cursor)
        ids.extend(str(row.id) for row in page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert ids == expected and len(set(ids)) == 120
    assert request(db, "GET", "?page_size=51").status_code == 422
    assert service(db).list(q="%").rows == []
    assert len(service(db).list(q="name39").rows) == 3
    assert service(db).list(bu_id=uuid4()).rows == []
    assert service(db).list(status="active").rows == []
    for cursor in ["broken", "W10=", "e30="]:
        with pytest.raises(ProblemError):
            service(db).list(cursor=cursor)


def test_create_idempotency_consumes_one_number(
    project_db,
) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager
    from typing import cast

    from fastapi import Request
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.responses import Response

    from flo.kernel.idempotency import install_idempotency
    from flo.kernel.idempotency.store import IdempotencyConnection
    from flo.kernel.tenancy.context import use_scope
    from tests.authz.conftest import ROOT, load_migration

    db = project_db
    bu = unit(db)
    migration = load_migration(ROOT / "migrations/20260825_0003_idempotency.py", "org-idempotency")
    headers = load_migration(
        ROOT / "migrations/20260825_0009_idempotency_response_headers.py", "org-idempotency-headers"
    )
    db.connection.execute("DROP TABLE IF EXISTS idempotency_key")
    migration.upgrade(db.connection)
    headers.upgrade(db.connection)
    app = app_for(db)

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
            payload = body(bu).model_dump(mode="json")
            missing = await client.post("/api/v1/projects", json=payload)
            first = await client.post(
                "/api/v1/projects", json=payload, headers={"Idempotency-Key": "once"}
            )
            second = await client.post(
                "/api/v1/projects", json=payload, headers={"Idempotency-Key": "once"}
            )
            conflict = await client.post(
                "/api/v1/projects",
                json=payload | {"name": "OTHER"},
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
            "SELECT count(*) FROM audit_log WHERE action = 'project.create'"
        ).fetchone() == (1,)
        assert db.connection.execute("SELECT count(*) FROM project").fetchone() == (1,)
        assert db.connection.execute("SELECT last_value FROM numbering_counter").fetchall() == [
            (1,)
        ]
    finally:
        migration.downgrade(db.connection)


def test_malformed_version_cursor_sort_and_page_guards(project_db):
    db = project_db
    row = service(db).create(body(unit(db)))
    assert (
        request(
            db, "PATCH", f"/{row.id}", json={"name": "X"}, headers={"If-Match": "bad"}
        ).status_code
        == 400
    )
    assert (
        request(
            db, "PATCH", f"/{row.id}", json={"status": "active"}, headers={"If-Match": "1"}
        ).status_code
        == 422
    )
    for options in [
        {"sort": "invalid"},
        {"direction": "invalid"},
        {"page_size": 0},
        {"page_size": 51},
        {"cursor": "☃"},
        {"cursor": "bnVsbA=="},
    ]:
        with pytest.raises(ProblemError):
            service(db).list(**options)
    assert service(db).get(row.id) == row


def test_duplicate_number_conflict_leaves_gap_and_no_project_scope(project_db):
    from datetime import UTC, datetime

    from flo.modules.org.service import NumberingService

    db = project_db
    bu = unit(db)
    row = service(db).create(body(bu))
    # Change counter scope with an organization format that renders an existing number.
    NumberingService(settings(db), Scope(db.org_a)).add_format(
        db.connection,
        db.org_a,
        "project",
        prefix="PRJ",
        include_year=True,
        width=4,
        effective_from=date(2000, 1, 1),
    )
    response = request(db, "POST", json=body(bu).model_dump(mode="json"))
    assert response.status_code == 409
    assert response.json()["checks"]["problem"] == "duplicate_number"
    assert db.connection.execute("SELECT id FROM project").fetchall() == [(row.id,)]
    assert db.connection.execute(
        "SELECT count(*) FROM authorization_scope WHERE scope_type='project'"
    ).fetchone() == (1,)
    assert service(db).create(body(bu)).number == f"PRJ-{datetime.now(UTC).year}-0002"
