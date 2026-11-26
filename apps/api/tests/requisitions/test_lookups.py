"""Requisition form lookups: scoped, effective and real-app reachable."""

import asyncio
from datetime import date, timedelta
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.org.schemas import MasterCreate, MasterPatch, OrgAddressCreate
from flo.modules.requisitions.lookups import LookupService
from tests.requisitions.test_header import (  # noqa: F401  (fixtures)
    bootstrap_db,
    client,
    draft,
    empty_database,
    header_db,
    login,
    org_svc,
    project,
    unit,
)
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401


def activate(db, p):
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE project SET status='active' WHERE id=%s", (p.id,))


def lookups(db, project_ok=lambda id: True, unit_ok=lambda id: True, org=None):
    return LookupService(db[0], Scope(org or db[1]), project_ok, unit_ok)


def test_project_lookup_lists_active_disables_generated_and_filters_by_permit(lines_db):
    db = lines_db
    bu = unit(db, "P")
    plain, generated, draft_only, hidden = (project(db, bu) for _ in range(4))
    for p in (plain, generated, hidden):
        activate(db, p)
    req = draft(db, p=plain)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE project SET generated_by_requisition_id=%s WHERE id=%s", (req.id, generated.id)
        )
    rows = {r.value: r for r in lookups(db).options("project", "", None, 20).rows}
    assert str(plain.id) in rows and not rows[str(plain.id)].disabled
    assert rows[str(generated.id)].disabled and rows[str(generated.id)].note == (
        f"created from {req.number}"
    )
    assert str(draft_only.id) not in rows  # not active

    rows = {
        r.value
        for r in lookups(db, project_ok=lambda id: id != hidden.id)
        .options("project", "", None, 20)
        .rows
    }
    assert str(hidden.id) not in rows and str(plain.id) in rows


def test_bu_lookup_lists_only_units_the_caller_may_raise_in(lines_db):
    db = lines_db
    open_unit, closed = unit(db, "OPEN"), unit(db, "CLOSED")
    rows = lookups(db, unit_ok=lambda id: id != closed.id).options("bu", "", None, 20).rows
    ids = {r.value for r in rows}
    assert str(open_unit.id) in ids and str(closed.id) not in ids


def test_master_lookups_omit_inactive_and_not_yet_effective(lines_db):
    db = lines_db
    org = org_svc(db)
    for code, active, start in [
        ("LIVE", True, date(2000, 1, 1)),
        ("OFF", False, date(2000, 1, 1)),
        ("LATER", True, date.today() + timedelta(days=30)),
    ]:
        record = org.create_master(
            "ledger_account",
            MasterCreate(
                code=code,
                name=code,
                effective_from=start,
                attributes={"account_type": "expense"},
            ),
        )
        if not active:
            org.update_master("ledger_account", record.id, MasterPatch(active=False))
    values = [r.value for r in lookups(db).options("ledger_account", "", None, 50).rows]
    assert "LIVE" in values and "OFF" not in values and "LATER" not in values
    assert [r.value for r in lookups(db).options("uom", "EA", None, 5).rows] == ["EA"]


def test_delivery_address_lookup_is_ship_to_of_the_unit_and_ancestors(lines_db):
    db = lines_db
    parent = unit(db, "PAR")
    child = unit(db, "CHI", parent=parent.id)
    sibling = unit(db, "SIB")
    org = org_svc(db)

    def address(unit_id, kind, line1):
        return org.create_address(
            unit_id,
            OrgAddressCreate(
                kind=kind,
                line1=line1,
                city="Pune",
                country="IN",
                effective_from=date(2000, 1, 1),
            ),
        )

    address(parent.id, "ship_to", "Parent dock")
    address(child.id, "ship_to", "Child dock")
    address(child.id, "bill_to", "Child billing")
    address(sibling.id, "ship_to", "Sibling dock")
    labels = [r.label for r in lookups(db).options("delivery_address", "", child.id, 20).rows]
    assert sorted(label.split(",")[0] for label in labels) == ["Child dock", "Parent dock"]
    with pytest.raises(ProblemError) as missing:
        lookups(db).options("delivery_address", "", None, 20)
    assert missing.value.code == ErrorCode.VALIDATION_FAILED
    with pytest.raises(ProblemError) as foreign:
        lookups(db).options("delivery_address", "", unit(db, "FF", org=db[2]).id, 20)
    assert foreign.value.code == ErrorCode.NOT_FOUND


def test_unknown_kind_422_and_route_reachable(lines_db):
    db = lines_db
    with pytest.raises(ProblemError) as error:
        lookups(db).options("vendor", "", None, 20)
    assert error.value.code == ErrorCode.VALIDATION_FAILED

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            ok = await browser.get("/api/v1/requisitions/lookups/uom")
            assert ok.status_code == 200, ok.text
            assert [r["value"] for r in ok.json()["rows"]] == ["EA"]
            assert (await browser.get("/api/v1/requisitions/lookups/vendor")).status_code == 422
            foreign = await browser.get(
                "/api/v1/requisitions/lookups/delivery_address",
                params={"bu_id": str(uuid4())},
            )
            assert foreign.status_code == 404

    asyncio.run(scenario())


def test_lookups_never_return_another_tenants_rows(lines_db):
    db = lines_db
    foreign_unit = unit(db, "SHARED", org=db[2])
    foreign_project = project(db, foreign_unit, org=db[2])
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute("UPDATE project SET status='active' WHERE id=%s", (foreign_project.id,))
    org_svc(db, db[2]).create_master(
        "ledger_account",
        MasterCreate(
            code="ZZFOREIGN",
            name="Foreign",
            effective_from=date(2000, 1, 1),
            attributes={"account_type": "expense"},
        ),
    )
    own = lookups(db)
    assert str(foreign_unit.id) not in {r.value for r in own.options("bu", "", None, 50).rows}
    assert str(foreign_project.id) not in {
        r.value for r in own.options("project", "", None, 50).rows
    }
    assert "ZZFOREIGN" not in {r.value for r in own.options("ledger_account", "", None, 50).rows}


def test_user_lookup_matches_display_name_and_shows_it_beside_the_email(lines_db):
    db = lines_db
    conn, org, _, admin = db
    conn.execute("UPDATE identity SET display_name='Alice Ng' WHERE id=%s", (admin,))
    by_name = lookups(db).options("user", "alic", None, 10).rows
    assert [(r.label, r.note) for r in by_name] == [("Alice Ng", "admin@example.test")]
    by_email = lookups(db).options("user", "admin@", None, 10).rows
    assert [r.value for r in by_email] == [str(admin)]
    conn.execute("UPDATE identity SET display_name=NULL WHERE id=%s", (admin,))
    plain = lookups(db).options("user", "admin@", None, 10).rows
    assert [(r.label, r.note) for r in plain] == [("admin@example.test", None)]


# --- E22-S15: scope-held callers reach the lookups through real sessions ----------------------

LOOKUPS = "/api/v1/requisitions/lookups/"
HOLDER_KINDS = (
    "project",
    "bu",
    "user",
    "department",
    "ledger_account",
    "uom",
    "item_category",
    "service_category",
)


def as_holder(db, permissions, target):
    """Make the signed-in admin hold only these permissions, granted at ``target``."""
    from flo.modules.identity.models import AuthorizationTarget
    from flo.modules.identity.service import IdentityAuthorizationService

    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    for row in db[0].execute("SELECT id FROM user_role WHERE user_id=%s", (db[3],)).fetchall():
        auth.revoke_role(row[0])
    role = auth.create_role(f"holder-{uuid4().hex[:6]}", "Holder")
    for permission in permissions:
        auth.grant_permission(role.id, permission)
    auth.grant_role(db[3], role.id, AuthorizationTarget(*target))


def statuses(db, permissions, target, *bu_ids):
    """Response per kind, plus one per delivery-address unit asked about, for one holder."""
    out = {}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            as_holder(db, permissions, target)
            for kind in HOLDER_KINDS:
                out[kind] = await browser.get(LOOKUPS + kind)
            for bu_id in bu_ids:
                out[bu_id] = await browser.get(
                    LOOKUPS + "delivery_address", params={"bu_id": str(bu_id)}
                )

    asyncio.run(scenario())
    return out


def rows_of(response):
    return {r["value"]: r for r in response.json()["rows"]}


def two_units(db):
    a, b = unit(db, "UA"), unit(db, "UB")
    pa, pb = project(db, a), project(db, b)
    activate(db, pa), activate(db, pb)
    for u in (a, b):
        org_svc(db).create_address(
            u.id,
            OrgAddressCreate(
                kind="ship_to",
                line1=f"{u.code} St",
                city="X",
                country="US",
                effective_from=date(2000, 1, 1),
            ),
        )
    return a, b, pa, pb


def test_unit_scoped_requester_reaches_every_form_lookup(lines_db):
    from flo.modules.identity.models import ScopeType

    db = lines_db
    a, b, pa, pb = two_units(db)
    perms = ["requisition.create", "project_request.create"]
    got = statuses(db, perms, (ScopeType.BU, a.id), a.id, b.id)
    assert all(got[k].status_code == 200 for k in HOLDER_KINDS), {
        k: v.status_code for k, v in got.items()
    }
    assert set(rows_of(got["project"])) == {str(pa.id)}
    assert set(rows_of(got["bu"])) == {str(a.id)}
    assert got[a.id].status_code == 200 and len(got[a.id].json()["rows"]) == 1
    assert got[b.id].status_code == 404


def test_project_scoped_requester_gets_project_unit_addresses_and_no_bu_rows(lines_db):
    from flo.modules.identity.models import ScopeType

    db = lines_db
    a, b, pa, pb = two_units(db)
    got = statuses(db, ["requisition.create"], (ScopeType.PROJECT, pa.id), a.id, b.id)
    assert set(rows_of(got["project"])) == {str(pa.id)}
    assert got["bu"].status_code == 200 and got["bu"].json()["rows"] == []
    assert got["department"].status_code == 200
    assert got[a.id].status_code == 200  # P_A's unit (decision 1)
    assert got[b.id].status_code == 404


def test_standalone_requester_gets_units_and_addresses_but_no_projects(lines_db):
    from flo.modules.identity.models import ScopeType

    db = lines_db
    a, b, pa, pb = two_units(db)
    got = statuses(db, ["project_request.create"], (ScopeType.BU, a.id), a.id)
    assert set(rows_of(got["bu"])) == {str(a.id)}
    assert got[a.id].status_code == 200
    assert got["project"].status_code == 403


def test_unit_scoped_viewer_sees_projects_view_only_and_nothing_else_wide(lines_db):
    from flo.modules.identity.models import ScopeType

    db = lines_db
    a, b, pa, pb = two_units(db)
    got = statuses(db, ["requisition.view"], (ScopeType.BU, a.id), a.id, b.id)
    rows = rows_of(got["project"])
    assert set(rows) == {str(pa.id)}
    assert rows[str(pa.id)]["disabled"] is True and rows[str(pa.id)]["note"] == "view only"
    assert got[a.id].status_code == 200 and got["department"].status_code == 200
    assert got["user"].status_code == 403 and got["bu"].status_code == 403
    assert got[b.id].status_code == 404


def test_code_substituter_reaches_only_department_and_ledger_account(lines_db):
    from flo.modules.identity.models import ScopeType

    db = lines_db
    a, b, pa, pb = two_units(db)
    got = statuses(db, ["master_code.substitute"], (ScopeType.PROJECT, pa.id), a.id)
    allowed = {"department", "ledger_account"}
    assert {k: v.status_code for k, v in got.items()} == {
        k: 200 if k in allowed else 403 for k in got
    }


def test_caller_with_none_of_the_four_permissions_gets_403_on_every_kind(lines_db):
    from flo.modules.identity.models import ScopeType

    db = lines_db
    a, *_ = two_units(db)
    got = statuses(db, ["record.read"], (ScopeType.BU, a.id), a.id)
    assert {v.status_code for v in got.values()} == {403}
