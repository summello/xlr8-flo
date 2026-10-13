"""Header contract through production middleware, real sessions and Postgres."""

import asyncio
from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.service import (
    AuthorizationTarget,
    IdentityAuthorizationService,
    ScopeType,
)
from flo.modules.org.schemas import (
    BaseCurrencyPut,
    MasterCreate,
    MasterPatch,
    OrgAddressCreate,
    OrgUnitCreate,
)
from flo.modules.org.service import NumberingService, OrgService, today
from flo.modules.projects.schemas import ProjectCreate
from flo.modules.projects.service import ProjectService
from flo.modules.requisitions.schemas import RequisitionCreate
from flo.modules.requisitions.service import RequisitionService
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import admin_id, connection_url, create, settings
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db


@pytest.fixture
def header_db(bootstrap_db, monkeypatch):
    conn = bootstrap_db
    a = create(conn)
    b = create(conn, "SECOND", "second@example.test")
    monkeypatch.setenv("DATABASE_URL", connection_url(conn))
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", "A" * 43)
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("FLO_IDENTITY_ARGON2_MEMORY_COST_KIB", "8192")
    from flo.api.origin_auth import require_origin_secret

    app.dependency_overrides[require_origin_secret] = lambda: None
    app.middleware_stack = None
    with correlation_context("requisition-tests"):
        db = (conn, a.org_id, b.org_id, admin_id(conn))
        for org_id in (a.org_id, b.org_id):
            org = OrgService(conn, Scope(org_id), db[3])
            org.set_base_currency(BaseCurrencyPut(currency="USD"))
            for kind, code in [("department", "D"), ("ledger_account", "L")]:
                org.create_master(
                    kind,
                    MasterCreate(
                        code=code,
                        name=code,
                        effective_from=date(2000, 1, 1),
                        attributes={"account_type": "expense"} if kind == "ledger_account" else {},
                    ),
                )
        yield db
    app.dependency_overrides.clear()
    app.middleware_stack = None


def org_svc(db, org=None):
    return OrgService(db[0], Scope(org or db[1]), db[3])


def unit(db, code="BU", org=None, parent=None):
    return org_svc(db, org).create_unit(
        OrgUnitCreate(code=code, name=code, kind="bu", parent_id=parent)
    )


def project(db, bu=None, org=None):
    bu = bu or unit(db, org=org)
    return ProjectService(
        db[0],
        Scope(org or db[1]),
        admin_id(db[0], "second@example.test") if org == db[2] else db[3],
        settings(db[0]),
    ).create(
        ProjectCreate(
            bu_id=bu.id,
            name="Project",
            currency="USD",
            department_code="D",
            ledger_account_code="L",
        )
    )


def svc(db, org=None):
    return RequisitionService(db[0], Scope(org or db[1]), db[3], settings(db[0]))


def draft(db, bu=None, p=None, org=None, **changes):
    return svc(db, org).create(
        RequisitionCreate(
            title="Request",
            **({"project_id": p.id} if p else {"bu_id": (bu or unit(db, org=org)).id}),
            **changes,
        )
    )


async def send(browser, method, suffix="", data=None, **kwargs):
    return await browser.request(
        method,
        "/api/v1/requisitions" + suffix,
        json=data,
        headers=headers(browser, kwargs.pop("key", uuid4().hex)) | kwargs.pop("extra", {}),
        **kwargs,
    )


def test_project_defaults_standalone_currency_estimate_audit_and_idempotency(header_db):
    db = header_db
    p = project(db)
    bu = unit(db, "OTHER")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for data in (
                {"title": "Linked", "project_id": str(p.id)},
                {
                    "title": "Standalone",
                    "bu_id": str(bu.id),
                    "department_code": " d ",
                    "ledger_account_code": " l ",
                },
            ):
                key = uuid4().hex
                response = await send(browser, "POST", data=data, key=key)
                assert response.status_code == 201, response.text
                row = response.json()
                assert row["currency"] == "USD" and row["estimate"] == "0.0000"
                assert response.headers["etag"] == '"1"'
                assert row["requester_email"] == "admin@example.test"
                assert row["department_code"] == "D" and row["ledger_account_code"] == "L"
                if "project_id" in data:
                    assert row["bu_id"] == str(p.bu_id)
                    assert row["department_code"] == "D" and row["ledger_account_code"] == "L"
                    assert not row["standalone"]
                else:
                    assert row["project_id"] is None and row["standalone"]
                replay = await send(browser, "POST", data=data, key=key)
                assert replay.json() == row
                get = await send(browser, "GET", "/" + row["id"])
                assert get.json() == row and get.headers["etag"] == '"1"'
                updated = await send(
                    browser,
                    "PATCH",
                    "/" + row["id"],
                    {"justification": "Reason", "department_code": " d "},
                    extra={"If-Match": '"1"'},
                )
                assert updated.status_code == 200 and updated.headers["etag"] == '"2"'
                audit = (
                    db[0]
                    .execute(
                        "SELECT action,before,after FROM audit_log WHERE target_id=%s ORDER BY id",
                        (row["id"],),
                    )
                    .fetchall()
                )
                assert [r[0] for r in audit] == ["requisition.create", "requisition.update"]
                assert audit[1][1] == {"justification": None} and audit[1][2] == {
                    "justification": "Reason"
                }

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["currency", "estimate"])
def test_create_rejects_derived_fields(header_db, field):
    db = header_db
    bu = unit(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            result = await send(
                browser,
                "POST",
                data={
                    "title": "Request",
                    "bu_id": str(bu.id),
                    field: "USD" if field == "currency" else "1.0000",
                },
            )
            assert result.status_code == 422
            assert result.json()["checks"]["problem"] == "derived_field"
            assert result.json()["errors"][0]["field"] == field

    asyncio.run(scenario())


def test_create_requires_exactly_one_mode_and_base_currency(header_db):
    db = header_db
    p = project(db)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE organization SET base_currency=NULL WHERE id=%s", (db[1],))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for data in (
                {"title": "X"},
                {"title": "X", "project_id": str(p.id), "bu_id": str(p.bu_id)},
            ):
                result = await send(browser, "POST", data=data)
                assert (
                    result.status_code == 422
                    and result.json()["errors"][0]["field"] == "project_id"
                )
            result = await send(browser, "POST", data={"title": "X", "bu_id": str(p.bu_id)})
            assert (
                result.status_code == 409
                and result.json()["checks"]["problem"] == "base_currency_unset"
            )
            assert "administrator" in result.json()["detail"]

    asyncio.run(scenario())


def test_numbering_scope_per_type_and_rollback_gap(header_db):
    db = header_db
    a, b = unit(db, "A"), unit(db, "B")
    p = project(db, a)
    first = draft(db, a)
    assert first.number == "REQ-00001" and p.number.endswith("0001")
    assert draft(db, b).number == first.number
    with patch.object(
        RequisitionService, "_audit", side_effect=RuntimeError("forced after allocation")
    ):
        with pytest.raises(RuntimeError):
            draft(db, a)
    assert draft(db, a).number == "REQ-00003"
    assert len(svc(db).list(bu_id=a.id).rows) == 2
    with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "INSERT INTO requisition SELECT %s,org_id,number,title,standalone,project_id,"
            "bu_id,requester_id,justification,needed_by,department_code,ledger_account_code,"
            "delivery_address_id,estimate,currency,status,version,submitted_at,decided_at,"
            "created_by,created_at,updated_at FROM requisition WHERE id=%s",
            (uuid4(), first.id),
        )
    numbering = NumberingService(settings(db[0]), Scope(db[1]))
    numbering.add_format(
        db[0],
        a.id,
        "requisition",
        prefix="CUSTOM",
        include_year=False,
        width=2,
        effective_from=date(2000, 1, 1),
    )
    assert draft(db, a).number == "CUSTOM04"


def test_generated_project_rejected_on_create_and_patch_switch(header_db):
    db = header_db
    p = project(db)
    source = draft(db, p=p)
    generated = project(db, unit(db, "GEN"))
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE project SET generated_by_requisition_id=%s WHERE id=%s",
            (source.id, generated.id),
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, suffix, data in [
                ("POST", "", {"title": "X", "project_id": str(generated.id)}),
                ("PATCH", "/" + str(source.id), {"project_id": str(generated.id)}),
            ]:
                response = await send(browser, method, suffix, data, extra={"If-Match": "1"})
                assert response.status_code == 422, response.text
                assert response.json()["checks"]["problem"] == "requisition_generated_project"
                assert source.number in response.json()["detail"]

    asyncio.run(scenario())


def test_patch_if_match_stale_status_and_immutable_guards(header_db):
    db = header_db
    row = draft(db)
    p = project(db, unit(db, "PROJECT"))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for extra, status, problem in [
                ({}, 400, "if_match_required"),
                ({"If-Match": "2"}, 409, "stale_version"),
            ]:
                response = await send(
                    browser, "PATCH", "/" + str(row.id), {"title": "Changed"}, extra=extra
                )
                assert (
                    response.status_code == status
                    and response.json()["checks"]["problem"] == problem
                )
            for field in (
                "bu_id",
                "project_id",
                "standalone",
                "currency",
                "estimate",
                "number",
                "status",
                "requester_id",
            ):
                response = await send(
                    browser,
                    "PATCH",
                    "/" + str(row.id),
                    {field: str(p.id) if field == "project_id" else None},
                    extra={"If-Match": "1"},
                )
                assert (
                    response.status_code == 422
                    and response.json()["checks"]["problem"] == "immutable_field"
                )
            response = await send(
                browser, "PATCH", "/" + str(row.id), {"title": None}, extra={"If-Match": "1"}
            )
            assert response.status_code == 422
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "UPDATE requisition SET status='approval_pending' WHERE id=%s", (row.id,)
                )
            response = await send(
                browser, "PATCH", "/" + str(row.id), {"title": "Changed"}, extra={"If-Match": "1"}
            )
            assert (
                response.status_code == 409
                and response.json()["checks"]["problem"] == "not_editable"
            )

    asyncio.run(scenario())


def test_codes_and_delivery_address_guards(header_db):
    db = header_db
    root = unit(db, "ROOT")
    bu = unit(db, "CHILD", parent=root.id)
    unrelated = unit(db, "UNRELATED")
    foreign = unit(db, "FOREIGN", org=db[2])
    org = org_svc(db)
    ledger = org.assert_usable("ledger_account", "L", today())
    org.update_master("ledger_account", ledger.id, MasterPatch(active=False))

    def address(unit_id, kind="ship_to", org_id=None, closed=False):
        return org_svc(db, org_id).create_address(
            unit_id,
            OrgAddressCreate(
                kind=kind,
                line1="One road",
                city="City",
                country="US",
                effective_from=date(2000, 1, 1),
                effective_to=today() - timedelta(days=1) if closed else None,
            ),
        )

    valid = address(root.id)
    invalid_addresses = [
        address(bu.id, "bill_to"),
        address(bu.id, closed=True),
        address(unrelated.id),
        address(foreign.id, org_id=db[2]),
    ]

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            data = {"title": "Request", "bu_id": str(bu.id)}
            result = await send(browser, "POST", data=data | {"ledger_account_code": "L"})
            assert result.status_code == 422 and result.json()["checks"]["problem"] == "inactive"
            for item in invalid_addresses:
                result = await send(
                    browser, "POST", data=data | {"delivery_address_id": str(item.id)}
                )
                assert (
                    result.status_code == 422
                    and result.json()["errors"][0]["field"] == "delivery_address_id"
                )
            result = await send(browser, "POST", data=data | {"delivery_address_id": str(valid.id)})
            assert result.status_code == 201, result.text
            for item in invalid_addresses:
                result = await send(
                    browser,
                    "PATCH",
                    "/" + str(row.id),
                    {"delivery_address_id": str(item.id)},
                    extra={"If-Match": "1"},
                )
                assert result.status_code == 422

    # A separate persisted row lets each failed PATCH preserve input and version.
    row = draft(db, bu, delivery_address_id=valid.id)
    asyncio.run(scenario())


def test_foreign_tenant_routes_and_list_isolation(header_db):
    db = header_db
    foreign_bu = unit(db, org=db[2])
    foreign_project = project(db, foreign_bu, org=db[2])
    foreign = draft(db, foreign_bu, org=db[2])
    own = draft(db)
    own_project = project(db, unit(db, "OWN-PROJECT"))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, suffix, data in [
                ("POST", "", {"title": "X", "bu_id": str(foreign_bu.id)}),
                ("POST", "", {"title": "X", "project_id": str(foreign_project.id)}),
                ("GET", "/" + str(foreign.id), None),
                ("PATCH", "/" + str(foreign.id), {"title": "Changed"}),
            ]:
                response = await send(browser, method, suffix, data, extra={"If-Match": "1"})
                assert response.status_code == 404, response.text
            result = await send(browser, "GET")
            assert [row["id"] for row in result.json()["rows"]] == [str(own.id)]
            result = await send(browser, "GET", "?bu_id=" + str(foreign_bu.id))
            assert result.json()["rows"] == [] and result.json()["total"] == 0
            assert result.json()["totals"] == []
            # A foreign tenant's estimate never reaches this tenant's filtered total.
            own_totals = (await send(browser, "GET")).json()["totals"]
            assert all(t["amount"] == "0.0000" for t in own_totals)

    asyncio.run(scenario())
    with pytest.raises(ProblemError) as error:
        svc(db).get(foreign.id)
    assert error.value.code.value == "not-found"
    with pytest.raises(psycopg.errors.ForeignKeyViolation), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE requisition SET project_id=%s WHERE id=%s", (foreign_project.id, own.id)
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE project SET generated_by_requisition_id=%s WHERE id=%s",
            (foreign.id, own_project.id),
        )


def test_project_switch_requires_create_at_new_scope(header_db):
    db = header_db
    shared = unit(db, "SHARED")  # a cross-unit move is 422 unit_change_blocked (amendment 6)
    p = project(db, shared)
    new = project(db, shared)
    row = draft(db, p=p)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
        identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
        role = identity.create_role("editor", "Editor")
        identity.grant_permission(role.id, "requisition.edit")
        identity.grant_permission(role.id, "requisition.view")
        identity.grant_role(db[3], role.id, AuthorizationTarget(ScopeType.PROJECT, p.id))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            result = await send(
                browser,
                "PATCH",
                "/" + str(row.id),
                {"project_id": str(new.id)},
                extra={"If-Match": "1"},
            )
            assert result.status_code == 403, result.text
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "UPDATE project SET generated_by_requisition_id=%s WHERE id=%s",
                    (row.id, new.id),
                )
            generated = await send(
                browser,
                "PATCH",
                "/" + str(row.id),
                {"project_id": str(new.id)},
                extra={"If-Match": "1"},
            )
            assert generated.status_code == 403
            assert row.number not in generated.json()["detail"]
            with tenant_transaction(db[0], Scope(db[1])):
                db[0].execute(
                    "UPDATE project SET generated_by_requisition_id=NULL WHERE id=%s", (new.id,)
                )
            with tenant_transaction(db[0], Scope(db[1])):
                identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
                role = identity.create_role("creator", "Creator")
                identity.grant_permission(role.id, "requisition.create")
                identity.grant_role(db[3], role.id, AuthorizationTarget(ScopeType.PROJECT, new.id))
            result = await send(
                browser,
                "PATCH",
                "/" + str(row.id),
                {"project_id": str(new.id)},
                extra={"If-Match": "1"},
            )
            assert result.status_code == 200, result.text
            assert result.json()["bu_id"] == str(new.bu_id) and not result.json()["standalone"]
            assert result.json()["currency"] == new.currency

    asyncio.run(scenario())


@pytest.mark.parametrize("grant", ["none", "wrong", "wrong_org", "bu_view"])
def test_authorization_outcomes_and_unauthenticated_routes(header_db, grant):
    db = header_db
    bu = unit(db)
    row = draft(db, bu)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
        if grant != "none":
            identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
            role = identity.create_role("limited", "Limited")
            identity.grant_permission(
                role.id, "project.read" if grant in ("wrong", "wrong_org") else "requisition.view"
            )
            identity.grant_role(
                db[3],
                role.id,
                AuthorizationTarget.organization(db[1])
                if grant == "wrong_org"
                else AuthorizationTarget(ScopeType.BU, bu.id),
            )

    async def scenario():
        async with client(app) as browser:
            # Bootstrap CSRF so authentication, rather than CSRF, determines these results.
            await browser.get("/api/v1/auth/sessions")
            for method, suffix, data in [
                ("POST", "", {"title": "X", "bu_id": str(bu.id)}),
                ("GET", "", None),
                ("GET", "/" + str(row.id), None),
                ("PATCH", "/" + str(row.id), {"title": "X"}),
            ]:
                result = await send(browser, method, suffix, data, extra={"If-Match": "1"})
                assert result.status_code == 401, result.text
            await login(browser)
            for method, suffix, data in [
                ("POST", "", {"title": "X", "bu_id": str(bu.id)}),
                ("GET", "/" + str(row.id), None),
                ("PATCH", "/" + str(row.id), {"title": "X"}),
            ]:
                result = await send(browser, method, suffix, data, extra={"If-Match": "1"})
                expected = (
                    404
                    if grant == "none"
                    else 200
                    if grant == "bu_view" and method == "GET"
                    else 403
                )
                assert result.status_code == expected, result.text
            result = await send(browser, "GET")
            assert result.status_code == 403

    asyncio.run(scenario())


@pytest.mark.parametrize("sort", ["number", "title", "estimate", "needed_by", "updated_at"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_list_keyset_filters_and_null_dates(header_db, sort, direction):
    db = header_db
    a, b = unit(db, "A"), unit(db, "B")
    p = project(db, a)
    rows = []
    for i in range(53):
        rows.append(
            svc(db).create(
                RequisitionCreate(
                    title=f"Need {i:03}",
                    project_id=p.id,
                    needed_by=date(2027, 1, 1) if i < 25 else None,
                )
            )
        )
    other = draft(db, b)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE requisition SET status='cancelled' WHERE id=%s", (other.id,))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            query = (
                f"?status=draft&project_id={p.id}&bu_id={a.id}&requester=me"
                f"&q=Need&sort={sort}&direction={direction}"
            )
            first = await send(browser, "GET", query)
            assert first.status_code == 200, first.text
            page = first.json()
            assert page["total"] == 53 and len(page["rows"]) == 50 and page["next_cursor"]
            second = await send(browser, "GET", query + "&cursor=" + page["next_cursor"])
            assert second.status_code == 200, second.text
            assert len(second.json()["rows"]) == 3
            ids = [row["id"] for row in page["rows"] + second.json()["rows"]]
            assert len(set(ids)) == 53 and set(ids) == {str(row.id) for row in rows}
            mismatch = await send(
                browser,
                "GET",
                query.replace(
                    "direction=" + direction,
                    "direction=" + ("desc" if direction == "asc" else "asc"),
                )
                + "&cursor="
                + page["next_cursor"],
            )
            assert mismatch.status_code == 422
            prefix = await send(browser, "GET", "?q=" + rows[0].number + "&bu_id=" + str(a.id))
            assert prefix.json()["total"] == 1
            bad = await send(browser, "GET", "?cursor=bad")
            assert bad.status_code == 422

    asyncio.run(scenario())


def test_patch_lock_serializes_competing_versions(header_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from flo.modules.requisitions.schemas import RequisitionPatch

    db = header_db
    row = draft(db)
    started = Event()
    with psycopg.connect(connection_url(db[0]), autocommit=True) as other:
        worker = RequisitionService(other, Scope(db[1]), db[3])

        def update():
            with correlation_context("competing-update"):
                started.set()
                return worker.update(row.id, RequisitionPatch(title="Second"), "1", lambda _: True)

        with ThreadPoolExecutor(max_workers=1) as executor:
            with tenant_transaction(db[0], Scope(db[1])):
                svc(db).update(row.id, RequisitionPatch(title="First"), "1", lambda _: True)
                future = executor.submit(update)
                assert started.wait(5)
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    state = (
                        db[0]
                        .execute(
                            "SELECT wait_event FROM pg_stat_activity WHERE pid=%s",
                            (other.info.backend_pid,),
                        )
                        .fetchone()
                    )
                    if state and state[0] == "transactionid":
                        break
                    sleep(0.01)
                else:
                    pytest.fail("Competing writer did not wait on the row lock")
                assert not future.done()
            with pytest.raises(ProblemError) as error:
                future.result(timeout=5)
            assert error.value.checks["problem"] == "stale_version"
    assert svc(db).get(row.id).title == "First"


def test_project_switch_cross_unit_is_blocked_same_unit_keeps_number(header_db):
    from flo.modules.requisitions.schemas import RequisitionPatch

    db = header_db
    a, b = unit(db, "A"), unit(db, "B")
    p, same, other_unit = project(db, a), project(db, a), project(db, b)
    first = draft(db, p=p)
    moved = svc(db).update(first.id, RequisitionPatch(project_id=same.id), "1", lambda _: True)
    assert moved.project_id == same.id
    assert moved.number == first.number
    with pytest.raises(ProblemError) as error:
        svc(db).update(
            first.id, RequisitionPatch(project_id=other_unit.id), str(moved.version), lambda _: True
        )
    assert error.value.checks["problem"] == "unit_change_blocked"
    assert svc(db).get(first.id) == moved


def test_header_bounds_extra_fields_and_incomplete_past_date_draft(header_db):
    db = header_db
    bu = unit(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for data in (
                {"title": ""},
                {"title": "X" * 201},
                {"title": "X", "justification": "X" * 4001},
                {"title": "X", "unknown": "value"},
            ):
                result = await send(browser, "POST", data=data | {"bu_id": str(bu.id)})
                assert result.status_code == 422
            result = await send(
                browser,
                "POST",
                data={"title": "Incomplete", "bu_id": str(bu.id), "needed_by": "2000-01-01"},
            )
            assert result.status_code == 201 and result.json()["needed_by"] == "2000-01-01"
            row = result.json()
            for data in ({"title": ""}, {"title": "X" * 201}, {"justification": "X" * 4001}):
                result = await send(
                    browser, "PATCH", "/" + row["id"], data, extra={"If-Match": "1"}
                )
                assert result.status_code == 422
            result = await send(
                browser, "PATCH", "/" + row["id"], {"title": "X"}, extra={"If-Match": "invalid"}
            )
            assert result.status_code == 400

    asyncio.run(scenario())


def test_create_duplicate_number_is_safe_conflict(header_db):
    db = header_db
    bu = unit(db)
    row = draft(db, bu)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            with patch.object(NumberingService, "allocate", return_value=row.number):
                result = await send(browser, "POST", data={"title": "X", "bu_id": str(bu.id)})
            assert (
                result.status_code == 409
                and result.json()["checks"]["problem"] == "duplicate_number"
            )

    asyncio.run(scenario())
    assert svc(db).list().total == 1


def test_standalone_origin_keeps_bu_authorization_after_project_assignment(header_db):
    db = header_db
    bu = unit(db)
    row = draft(db, bu)
    assigned = project(db, unit(db, "ASSIGNED"))
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE requisition SET project_id=%s WHERE id=%s", (assigned.id, row.id))
        db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
        identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
        role = identity.create_role("bu-reader", "BU reader")
        identity.grant_permission(role.id, "requisition.view")
        identity.grant_role(db[3], role.id, AuthorizationTarget(ScopeType.BU, bu.id))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            result = await send(browser, "GET", "/" + str(row.id))
            assert result.status_code == 200 and result.json()["standalone"]
            assert result.json()["project_id"] == str(assigned.id)

    asyncio.run(scenario())


def test_list_page_size_and_filtered_totals_per_currency(header_db):
    db = header_db
    p = project(db, unit(db, "TOT"))
    for i in range(5):
        svc(db).create(RequisitionCreate(title=f"Total {i}", project_id=p.id))
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE requisition SET estimate = 10.5000 * (1 + (number::text <> '')::int) "
            "WHERE org_id=%s AND title LIKE 'Total %%'",
            (db[1],),
        )
        db[0].execute(
            "UPDATE requisition SET status='cancelled', estimate=1.2500 "
            "WHERE org_id=%s AND title='Total 0'",
            (db[1],),
        )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            page = (await send(browser, "GET", "?q=Total&page_size=2&sort=title")).json()
            assert len(page["rows"]) == 2 and page["total"] == 5 and page["next_cursor"]
            # The sum covers the whole filtered set, not the page: 4 x 21.0000 + 1.2500.
            assert page["totals"] == [{"currency": p.currency, "amount": "85.2500"}]
            drafts = (await send(browser, "GET", "?q=Total&status=draft&page_size=2")).json()
            assert drafts["total"] == 4 and drafts["totals"][0]["amount"] == "84.0000"
            nxt = await send(
                browser, "GET", "?q=Total&page_size=2&sort=title&cursor=" + page["next_cursor"]
            )
            assert len(nxt.json()["rows"]) == 2
            for bad in ("0", "51"):
                assert (await send(browser, "GET", "?page_size=" + bad)).status_code == 422

    asyncio.run(scenario())
