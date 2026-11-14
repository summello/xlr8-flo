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
