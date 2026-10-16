"""Vendor tax and banking data: masking, audited reveal, write rules, one repo door (E11-S04)."""

import asyncio
import logging
import re
from pathlib import Path
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import BASELINE_ROLE_PERMISSIONS, role_code
from flo.modules.vendors.schemas import VendorCreate
from flo.modules.vendors.service import VendorService
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.vendors.test_vendors import regrant

SRC = Path(__file__).resolve().parents[2] / "src"
TAX, ACCOUNT, ROUTING, NAME = "12-3456789", "000123454821", "SORT-20-00-00", "Acme Operating"
SECRETS = (TAX, ACCOUNT, ROUTING, "3456789", "00012345")
PAYLOAD = {
    "tax_id": TAX,
    "tax_id_country": "US",
    "bank_name": NAME,
    "bank_account": ACCOUNT,
    "bank_routing": ROUTING,
}


def new_vendor(db, org=None):
    return VendorService(db[0], Scope(org or db[1]), db[3]).create(
        VendorCreate(trading_name="V", legal_name="V Ltd", availability="global")
    )


async def call(browser, method, path, data=None):
    return await browser.request(
        method, "/api/v1/vendors" + path, json=data, headers=headers(browser, uuid4().hex)
    )


def audit_rows(db, action):
    with tenant_transaction(db[0], Scope(db[1])):
        cursor = db[0].execute(
            "SELECT actor_id, target_id, before::text, after::text, reason FROM audit_log "
            "WHERE action=%s ORDER BY occurred_at",
            (action,),
        )
        return cursor.fetchall()


def all_audit_text(db):
    with tenant_transaction(db[0], Scope(db[1])):
        return " ".join(
            str(r) for r in db[0].execute("SELECT before, after, reason FROM audit_log").fetchall()
        )


def test_list_and_detail_carry_only_the_masked_form(header_db, caplog):
    db = header_db
    vendor = new_vendor(db)
    caplog.set_level(logging.DEBUG)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            put = await call(browser, "PUT", f"/{vendor.id}/sensitive", PAYLOAD)
            assert put.status_code == 200
            assert sorted(put.json()["changed"]) == sorted(PAYLOAD)
            caplog.clear()
            listed = await call(browser, "GET", "")
            detail = await call(browser, "GET", f"/{vendor.id}")
            assert listed.status_code == detail.status_code == 200
            return listed, detail

    listed, detail = asyncio.run(scenario())
    shown = detail.json()["sensitive"]
    assert shown == {
        "tax_id": "•••• 6789",
        "tax_id_country": "US",
        "bank_name": "•••• ting",
        "bank_account": "•••• 4821",
        "bank_routing": "•••• 0-00",
    }
    assert listed.json()["rows"][0]["sensitive"] == shown
    for place, text in {
        "list": listed.text,
        "detail": detail.text,
        "logs": caplog.text,
        "audit": all_audit_text(db),
    }.items():
        for secret in (*SECRETS, NAME):
            assert secret not in text, (place, secret)


def test_short_values_are_fully_masked_and_unset_is_null(header_db):
    db = header_db
    vendor, bare = new_vendor(db), new_vendor(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await call(browser, "PUT", f"/{vendor.id}/sensitive", {"tax_id": "1234"})
            one = await call(browser, "GET", f"/{vendor.id}")
            two = await call(browser, "GET", f"/{bare.id}")
            return one.json()["sensitive"], two.json()["sensitive"]

    short, unset = asyncio.run(scenario())
    assert short["tax_id"] == "••••" and short["bank_account"] is None
    assert unset == dict.fromkeys(PAYLOAD)


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("vendor-manager", True),
        ("budget-finance-user", True),
        ("auditor", False),
        ("sourcing-specialist", False),
        ("requestor", False),
    ],
)
def test_reveal_is_refused_by_the_server_for_every_other_role(header_db, role, allowed):
    db = header_db
    vendor = new_vendor(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await call(browser, "PUT", f"/{vendor.id}/sensitive", PAYLOAD)
            regrant(db, [str(p) for p in BASELINE_ROLE_PERMISSIONS[role_code(role)]])
            return await call(
                browser,
                "POST",
                f"/{vendor.id}/sensitive/reveal",
                {"fields": ["tax_id", "bank"], "reason": "payment check"},
            )

    response = asyncio.run(scenario())
    events = audit_rows(db, "vendor.sensitive.reveal")
    if not allowed:
        assert response.status_code == 403 and events == []
        assert all(secret not in response.text for secret in SECRETS)
        return
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == PAYLOAD
    assert len(events) == 1
    actor, target, _, after, reason = events[0]
    assert (actor, target, reason) == (db[3], vendor.id, "payment check")
    assert re.search(r'"fields": \["tax_id", "bank"\]', after)
    assert all(secret not in all_audit_text(db) for secret in (*SECRETS, NAME))


def test_reveal_returns_only_the_requested_group(header_db):
    db = header_db
    vendor = new_vendor(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await call(browser, "PUT", f"/{vendor.id}/sensitive", PAYLOAD)
            return await call(
                browser, "POST", f"/{vendor.id}/sensitive/reveal", {"fields": ["tax_id"]}
            )

    got = asyncio.run(scenario()).json()
    assert got["tax_id"] == TAX and got["bank_account"] is None and got["bank_routing"] is None


def test_the_reveal_response_is_not_logged(header_db, caplog):
    db = header_db
    vendor = new_vendor(db)
    caplog.set_level(logging.DEBUG)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await call(browser, "PUT", f"/{vendor.id}/sensitive", PAYLOAD)
            await call(browser, "POST", f"/{vendor.id}/sensitive/reveal", {"fields": ["bank"]})

    asyncio.run(scenario())
    for secret in SECRETS:
        assert secret not in caplog.text


def test_write_needs_edit_and_reveal_and_audits_field_names_only(header_db):
    db = header_db
    vendor = new_vendor(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            path = f"/{vendor.id}/sensitive"
            regrant(db, ["vendor.edit"])
            edit_only = await call(browser, "PUT", path, PAYLOAD)
            regrant(db, ["vendor.sensitive.reveal"])
            reveal_only = await call(browser, "PUT", path, PAYLOAD)
            regrant(db, ["vendor.edit", "vendor.sensitive.reveal"])
            both = await call(browser, "PUT", path, PAYLOAD)
            again = await call(browser, "PUT", path, {**PAYLOAD, "tax_id": "9"})
            bad = await call(browser, "PUT", path, {"tax_id_country": "usa"})
            return edit_only, reveal_only, both, again, bad

    edit_only, reveal_only, both, again, bad = asyncio.run(scenario())
    assert edit_only.status_code == reveal_only.status_code == 403
    assert both.status_code == 200 and sorted(both.json()["changed"]) == sorted(PAYLOAD)
    assert again.json()["changed"] == ["tax_id"]  # the other four are unchanged
    assert bad.status_code == 422
    assert len(audit_rows(db, "vendor.sensitive.update")) == 2
    assert all(secret not in all_audit_text(db) for secret in (*SECRETS, NAME))


def test_sensitive_routes_conceal_foreign_tenants(header_db):
    db = header_db
    foreign = new_vendor(db, db[2])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            reveal = await call(
                browser, "POST", f"/{foreign.id}/sensitive/reveal", {"fields": ["bank"]}
            )
            write = await call(browser, "PUT", f"/{foreign.id}/sensitive", PAYLOAD)
            missing = await call(
                browser, "POST", f"/{uuid4()}/sensitive/reveal", {"fields": ["bank"]}
            )
            return reveal, write, missing

    assert [r.status_code for r in asyncio.run(scenario())] == [404, 404, 404]
    assert audit_rows(db, "vendor.sensitive.reveal") == []


# -- the repo door ---------------------------------------------------------------------------

DOOR = re.compile(r"vendor_sensitive|bank_account_number")


def outside_the_door(root: Path) -> list[str]:
    return [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*.py"))
        if not (path.name == "repo.py" and path.parent.name == "vendors")
        if DOOR.search(path.read_text(encoding="utf-8"))
    ]


def test_only_the_vendors_repo_names_the_sensitive_columns():
    assert (SRC / "flo" / "modules" / "vendors" / "repo.py").exists()
    assert outside_the_door(SRC) == []


@pytest.mark.parametrize("planted", ["vendor_sensitive", "bank_account_number"])
def test_the_door_test_fails_on_a_planted_second_reader(tmp_path, planted):
    vendors = tmp_path / "flo" / "modules" / "vendors"
    vendors.mkdir(parents=True)
    (vendors / "repo.py").write_text(f"X = '{planted}'\n", encoding="utf-8")
    assert outside_the_door(tmp_path) == []
    (vendors / "service.py").write_text(f"SELECT {planted} FROM t\n", encoding="utf-8")
    assert outside_the_door(tmp_path) == ["flo/modules/vendors/service.py"]
