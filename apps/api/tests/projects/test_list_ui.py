"""E06-S07 list projection, grouped cursors and session identity contracts."""

import asyncio
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity, service_for
from tests.isolation.test_multi_org import multi as multi
from tests.isolation.test_multi_org import select
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db
from tests.projects.conftest import body, service, unit
from tests.projects.test_projects import request


def test_list_balance_requires_organization_ledger_read(project_db):
    db = project_db
    row = service(db).create(body(unit(db)))
    db.connection.execute(
        "UPDATE project_balance SET allocated=123.4567 WHERE project_id=%s", (row.id,)
    )
    first = request(db, "GET").json()
    assert first["total"] == 1
    assert first["rows"][0]["allocated"] is None
    assert first["rows"][0]["available"] is None
    assert first["rows"][0]["bu_name"] == "BU"
    assert first["rows"][0]["health"] == "unknown"
    with service_for(db, db_scope(db), "ledger-project") as identity:
        role = identity.create_role("ledger-project", "Ledger project reader")
        identity.grant_permission(role.id, "ledger.read")
        identity.grant_role(db.actor_id, role.id, AuthorizationTarget(ScopeType.PROJECT, row.id))
    assert request(db, "GET").json()["rows"][0]["allocated"] is None
    grant_permissions(db, db.actor_id, "ledger.read")
    visible = request(db, "GET").json()["rows"][0]
    assert visible["allocated"] == visible["available"] == "123.4567"
    viewer = insert_identity(db, "wrong-permission")
    grant_permissions(db, viewer, "ledger.read")
    assert request(db, "GET", viewer=viewer).status_code == 403


def db_scope(db):
    from flo.kernel.tenancy.context import Scope

    return Scope(db.org_a)


@pytest.mark.parametrize("group_by", ["status", "bu"])
def test_grouped_cursor_total_filters_and_tenant_isolation(project_db, group_by):
    db = project_db
    a, b = unit(db, "A"), unit(db, "B")
    own = [service(db).create(body(a if index % 2 else b)) for index in range(7)]
    for index, row in enumerate(own):
        db.connection.execute(
            "UPDATE project SET status=%s WHERE id=%s",
            ("active" if index % 2 else "draft", row.id),
        )
    foreign = service(db, db.org_b).create(body(unit(db, "F", db.org_b)))
    ids = []
    rows = []
    cursor = None
    while True:
        path = f"?group_by={group_by}&sort=name&direction=desc&page_size=2"
        if cursor:
            path += "&cursor=" + cursor
        response = request(db, "GET", path)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == 7 and len(page["rows"]) <= 2
        rows.extend(page["rows"])
        ids.extend(row["id"] for row in page["rows"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 7
    assert str(foreign.id) not in ids
    key = "status" if group_by == "status" else "bu_id"
    ordering = [(row[key], row["number"], row["id"]) for row in rows]
    assert ordering == sorted(ordering)
    filtered = request(db, "GET", f"?group_by={group_by}&status=active&bu_id={a.id}").json()
    assert filtered["total"] == 3 and len(filtered["rows"]) == 3
    assert request(db, "GET", f"?bu_id={foreign.bu_id}").json()["total"] == 0
    assert request(db, "GET", "?group_by=bad").status_code == 422
    page = request(db, "GET", "?group_by=status&page_size=2").json()
    mismatch = request(db, "GET", "?group_by=bu&cursor=" + page["next_cursor"])
    assert mismatch.status_code == 422
    bu_page = request(db, "GET", "?group_by=bu&page_size=2").json()
    assert (
        request(db, "GET", "?group_by=status&cursor=" + bu_page["next_cursor"]).status_code == 422
    )
    assert request(db, "GET", "?cursor=bad").status_code == 422
    assert request(db, "GET", "?page_size=51").status_code == 422


def test_list_missing_balance_and_search_count(project_db):
    db = project_db
    row = service(db).create(body(unit(db)))
    db.connection.execute("DELETE FROM project_balance WHERE project_id=%s", (row.id,))
    grant_permissions(db, db.actor_id, "ledger.read")
    result = request(db, "GET", "?q=Pro").json()
    assert result["total"] == 1 and result["rows"][0]["allocated"] is None
    assert request(db, "GET", "?q=missing").json()["total"] == 0


def test_auth_me_unauthenticated_and_session_identity_isolation(multi):
    conn, a, b, outsider, identity = multi

    async def scenario():
        async with client(app) as browser:
            response = await browser.get("/api/v1/auth/me")
            assert response.status_code == 401
            await login(browser)
            response = await browser.get("/api/v1/auth/me")
            assert response.status_code == 403
            assert response.headers["www-authenticate"] == "org-select"
            await select(browser, a.org_id)
            response = await browser.get(
                "/api/v1/auth/me",
                params={"identity_id": str(uuid4()), "org_id": str(outsider.org_id)},
                headers={"Org-Id": str(b.org_id)},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"identity_id": str(identity)}
            await select(browser, b.org_id)
            assert (await browser.get("/api/v1/auth/me")).json() == {"identity_id": str(identity)}

    asyncio.run(scenario())


def test_list_joins_balance_once_without_per_row_queries(project_db):
    from unittest.mock import patch

    db = project_db
    bu = unit(db)
    for _ in range(3):
        service(db).create(body(bu))
    svc = service(db)
    with patch.object(svc.repo, "execute", wraps=svc.repo.execute) as execute:
        page = svc.list(balances=True)
    assert len(page.rows) == 3 and execute.call_count == 2
    query = execute.call_args_list[1].args[0]
    assert query.count("LEFT JOIN project_balance") == 1
    assert "b.org_id = p.org_id" in query


def test_detail_business_unit_name_is_project_metadata(project_db):
    from flo.modules.identity.resolver import AuthorizationResolver

    db = project_db
    unit(db, "AA", db.org_b)
    row = service(db).create(body(unit(db)))
    viewer = insert_identity(db, "project-reader")
    grant_permissions(db, viewer, "project.read")
    resolver = AuthorizationResolver(db.authorization_connection, db_scope(db))
    assert not resolver.check(
        viewer, "org.unit.read", AuthorizationTarget.organization(db.org_a)
    ).allowed
    response = request(db, "GET", f"/{row.id}", viewer=viewer)
    assert response.status_code == 200 and response.json()["bu_name"] == "BU"
    assert response.json()["allocated"] is None and response.json()["available"] is None


def test_group_guard_rejects_invalid_service_input(project_db):
    from flo.kernel.errors import ErrorCode, ProblemError

    with pytest.raises(ProblemError) as error:
        service(project_db).list(group_by="invalid")
    assert error.value.code == ErrorCode.VALIDATION_FAILED
    assert error.value.errors[0].field == "group_by"


def test_group_cursor_rejects_non_string_and_invalid_uuid_keys(project_db):
    import base64
    import json

    db = project_db
    row = service(db).create(body(unit(db)))
    for group_by, key in [("status", 7), ("bu", "not-a-uuid")]:
        cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sort": "number",
                    "direction": "asc",
                    "id": str(row.id),
                    "key": row.number,
                    "group_by": group_by,
                    "group_key": key,
                }
            ).encode()
        ).decode()
        response = request(db, "GET", f"?group_by={group_by}&cursor={cursor}")
        assert response.status_code == 422, response.text
        assert response.json()["errors"][0]["field"] == "cursor"
