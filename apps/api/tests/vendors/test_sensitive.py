"""Vendor tax and banking data: masking, audited reveal, write rules, one repo door (E11-S04)."""

import asyncio
import logging
import re
from pathlib import Path
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.idempotency.middleware import IDEMPOTENCY_BYPASS_PATTERN
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


async def call(browser, method, path, data=None, version=None, key=True):
    sent = headers(browser, uuid4().hex)
    if not key:
        del sent["Idempotency-Key"]
    if version is not None:
        sent["If-Match"] = f'"{version}"'
    return await browser.request(method, "/api/v1/vendors" + path, json=data, headers=sent)


def reveal(fields, code="verify_payment", **extra):
    return {"fields": fields, "reason_code": code, **extra}


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
            put = await call(browser, "PUT", f"/{vendor.id}/sensitive", PAYLOAD, 0)
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
        "version": 1,
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
            await call(browser, "PUT", f"/{vendor.id}/sensitive", {"tax_id": "1234"}, 0)
            one = await call(browser, "GET", f"/{vendor.id}")
            two = await call(browser, "GET", f"/{bare.id}")
            return one.json()["sensitive"], two.json()["sensitive"]

    short, unset = asyncio.run(scenario())
    assert short["tax_id"] == "••••" and short["bank_account"] is None
    assert unset == {**dict.fromkeys(PAYLOAD), "version": 0}


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
            await call(browser, "PUT", f"/{vendor.id}/sensitive", PAYLOAD, 0)
            regrant(db, [str(p) for p in BASELINE_ROLE_PERMISSIONS[role_code(role)]])
            return await call(
                browser,
                "POST",
                f"/{vendor.id}/sensitive/reveal",
                reveal(["tax_id", "bank"], "verify_payment", note="payment check"),
            )

    response = asyncio.run(scenario())
    events = audit_rows(db, "vendor.sensitive.reveal")
    if not allowed:
        assert response.status_code == 403 and events == []
        assert all(secret not in response.text for secret in SECRETS)
        return
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {**PAYLOAD, "version": 1}
    assert len(events) == 1
    actor, target, _, after, reason = events[0]
    assert (actor, target, reason) == (db[3], vendor.id, "verify_payment: payment check")
    assert re.search(r'"fields": \["tax_id", "bank"\]', after)
    assert all(secret not in all_audit_text(db) for secret in (*SECRETS, NAME))


def test_reveal_returns_only_the_requested_group(header_db):
    db = header_db
    vendor = new_vendor(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await call(browser, "PUT", f"/{vendor.id}/sensitive", PAYLOAD, 0)
            return await call(browser, "POST", f"/{vendor.id}/sensitive/reveal", reveal(["tax_id"]))

    got = asyncio.run(scenario()).json()
    assert got["tax_id"] == TAX and got["bank_account"] is None and got["bank_routing"] is None


def test_the_reveal_response_is_not_logged(header_db, caplog):
    db = header_db
    vendor = new_vendor(db)
    caplog.set_level(logging.DEBUG)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await call(browser, "PUT", f"/{vendor.id}/sensitive", PAYLOAD, 0)
            await call(browser, "POST", f"/{vendor.id}/sensitive/reveal", reveal(["bank"]))

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
            edit_only = await call(browser, "PUT", path, PAYLOAD, 0)
            regrant(db, ["vendor.sensitive.reveal"])
            reveal_only = await call(browser, "PUT", path, PAYLOAD, 0)
            regrant(db, ["vendor.edit", "vendor.sensitive.reveal"])
            both = await call(browser, "PUT", path, PAYLOAD, 0)
            again = await call(browser, "PUT", path, {**PAYLOAD, "tax_id": "9"}, 1)
            bad = await call(browser, "PUT", path, {"tax_id_country": "usa"}, 2)
            return edit_only, reveal_only, both, again, bad

    edit_only, reveal_only, both, again, bad = asyncio.run(scenario())
    assert edit_only.status_code == reveal_only.status_code == 403
    assert both.status_code == 200 and sorted(both.json()["changed"]) == sorted(PAYLOAD)
    assert both.headers["etag"] == '"1"' and both.json()["version"] == 1
    assert again.json()["changed"] == ["tax_id"]  # the other four are unchanged
    assert again.headers["etag"] == '"2"'
    assert bad.status_code == 422
    assert len(audit_rows(db, "vendor.sensitive.update")) == 2
    assert all(secret not in all_audit_text(db) for secret in (*SECRETS, NAME))


def test_write_needs_if_match_and_a_current_version_and_reads_carry_the_etag(header_db):
    db = header_db
    vendor = new_vendor(db)
    path = f"/{vendor.id}/sensitive"

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            missing = await call(browser, "PUT", path, PAYLOAD)
            junk = await call(browser, "PUT", path, PAYLOAD, "abc")
            first = await call(browser, "PUT", path, PAYLOAD, 0)
            stale = await call(browser, "PUT", path, {"tax_id": "OVERWRITE-1"}, 0)  # blind write
            detail = await call(browser, "GET", f"/{vendor.id}")
            shown = await call(browser, "POST", path + "/reveal", reveal(["tax_id"]))
            ok = await call(
                browser, "PUT", path, {"tax_id": "NEW-4455"}, detail.json()["sensitive"]["version"]
            )
            return missing, junk, first, stale, detail, shown, ok

    missing, junk, first, stale, detail, shown, ok = asyncio.run(scenario())
    assert (missing.status_code, junk.status_code) == (400, 400)
    assert first.status_code == 200
    assert stale.status_code == 409 and stale.json()["checks"]["problem"] == "stale_version"
    # the detail's ETag is the vendor's version (PATCH); the sensitive record's is in the body
    assert detail.headers["etag"] == f'"{detail.json()["version"]}"'
    assert detail.json()["sensitive"]["version"] == 1
    assert shown.headers["etag"] == '"1"' and shown.json()["tax_id"] == TAX  # the blind write lost
    assert ok.status_code == 200 and ok.json()["version"] == 2


def test_reveal_is_never_stored_or_replayed_by_the_idempotency_layer(header_db):
    db = header_db
    vendor = new_vendor(db)
    # every textual form of the id (and a trailing slash) that the router accepts
    paths = [
        f"/{vendor.id}/sensitive/reveal",
        f"/{vendor.id.hex}/sensitive/reveal",
        f"/{vendor.id}/sensitive/reveal/",
    ]

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await call(browser, "PUT", f"/{vendor.id}/sensitive", PAYLOAD, 0)
            sent = headers(browser, "same-key")
            revealed = []
            for path in paths:
                for _ in range(2):
                    revealed.append(
                        await browser.post(
                            "/api/v1/vendors" + path, headers=sent, json=reveal(["bank"])
                        )
                    )
            keyless = await call(browser, "POST", paths[0], reveal(["bank"]), key=False)
            regrant(db, ["vendor.view"])
            replays = [
                await browser.post("/api/v1/vendors" + p, headers=sent, json=reveal(["bank"]))
                for p in paths
            ]
            return revealed, keyless, replays

    revealed, keyless, replays = asyncio.run(scenario())
    # the router redirects a trailing slash (307) and never reveals on it
    assert [r.status_code for r in (*revealed, keyless)] == [200] * 4 + [307] * 2 + [200]
    assert [r.status_code for r in replays] == [403, 403, 307]
    assert all(ACCOUNT not in r.text for r in replays)
    assert len(audit_rows(db, "vendor.sensitive.reveal")) == 5  # every reveal is audited
    with tenant_transaction(db[0], Scope(db[1])):
        stored = db[0].execute("SELECT response_body::text FROM idempotency_key").fetchall()
    assert all(secret not in str(stored) for secret in (*SECRETS, NAME))


@pytest.mark.parametrize("leak", [ACCOUNT, TAX, ROUTING, NAME, "acct 0001 2345 4821"])
def test_the_reveal_note_cannot_carry_a_stored_value(header_db, leak):
    db = header_db
    vendor = new_vendor(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            await call(browser, "PUT", f"/{vendor.id}/sensitive", PAYLOAD, 0)
            return await call(
                browser, "POST", f"/{vendor.id}/sensitive/reveal", reveal(["bank"], note=leak)
            ), await call(
                browser,
                "POST",
                f"/{vendor.id}/sensitive/reveal",
                reveal(["bank"], code="free text"),
            )

    refused, bad_code = asyncio.run(scenario())
    assert refused.status_code == 422 and bad_code.status_code == 422
    assert audit_rows(db, "vendor.sensitive.reveal") == []
    assert ACCOUNT not in all_audit_text(db) and NAME not in all_audit_text(db)


def test_sensitive_routes_conceal_foreign_tenants(header_db):
    db = header_db
    foreign = new_vendor(db, db[2])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            revealed = await call(
                browser, "POST", f"/{foreign.id}/sensitive/reveal", reveal(["bank"])
            )
            write = await call(browser, "PUT", f"/{foreign.id}/sensitive", PAYLOAD, 0)
            missing = await call(browser, "POST", f"/{uuid4()}/sensitive/reveal", reveal(["bank"]))
            return revealed, write, missing

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


def test_only_the_reveal_route_skips_idempotency():
    base = "/api/v1/vendors/" + str(uuid4())
    assert IDEMPOTENCY_BYPASS_PATTERN.match(base + "/sensitive/reveal")
    for other in ("/sensitive", "/documents", "/sensitive/reveal/x", "/units/1/disable"):
        assert not IDEMPOTENCY_BYPASS_PATTERN.match(base + other)
    assert IDEMPOTENCY_BYPASS_PATTERN.match("/api/v1/vendors/abc/sensitive/reveal")
    assert not IDEMPOTENCY_BYPASS_PATTERN.match("/api/v1/vendors//sensitive/reveal")
    assert not IDEMPOTENCY_BYPASS_PATTERN.match("/api/v1/vendors/a/b/sensitive/reveal")
