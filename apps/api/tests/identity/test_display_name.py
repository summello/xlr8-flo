"""E22-S07 people have a display name: copied at acceptance, edited on /me, searched by prefix."""

import asyncio
import importlib
import pkgutil
from uuid import uuid4

import pydantic

import flo.api
import flo.modules
from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from tests.identity.test_invitations import accept, issue
from tests.identity.test_invitations import invites as invites
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import admin_id, create
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db


def name_of(conn, email):
    return conn.execute("SELECT display_name FROM identity WHERE email=%s", (email,)).fetchone()[0]


def test_acceptance_copies_name_only_when_null(invites):
    conn, a, b = invites
    c = create(conn, "THIRD", "third@example.test").org_id
    _, fresh = issue(invites, "fresh@example.test")
    _, second = issue(invites, "admin@example.test", org=b)
    _, third = issue(invites, "admin@example.test", org=c)

    async def scenario():
        async with client(app) as browser:
            assert (await accept(browser, fresh)).status_code == 200
            assert name_of(conn, "fresh@example.test") == "Invitee"
            await login(browser)
            assert name_of(conn, "admin@example.test") is None
            result = await accept(browser, second, {"full_name": "  Admin Person "})
            assert result.status_code == 200, result.text
            assert name_of(conn, "admin@example.test") == "Admin Person"
            # a person who joins a further organization keeps the name they already have
            result = await accept(browser, third, {"full_name": "Someone Else"})
            assert result.status_code == 200, result.text
            assert name_of(conn, "admin@example.test") == "Admin Person"

    asyncio.run(scenario())


def test_acceptance_without_a_typed_name_copies_the_name_the_invitation_stores(invites):
    conn, a, b = invites
    named, token = issue(invites, "admin@example.test", org=b)
    with tenant_transaction(conn, Scope(b)):
        conn.execute("UPDATE invitation SET full_name='Admin Given' WHERE id=%s", (named.id,))

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            assert name_of(conn, "admin@example.test") is None
            result = await accept(browser, token, {})  # no typed name
            assert result.status_code == 200, result.text
            assert name_of(conn, "admin@example.test") == "Admin Given"

    asyncio.run(scenario())


def test_patch_me_sets_trims_validates_clears_and_audits(invites):
    conn, a, _ = invites

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            first = await browser.get("/api/v1/auth/me")
            assert first.status_code == 200, first.text
            assert first.json()["display_name"] is None and first.headers["etag"] == '"1"'

            def patch(body, version='"1"', key=None):
                send = headers(browser, key or str(uuid4()))
                if version is not None:
                    send["If-Match"] = version
                return browser.patch("/api/v1/auth/me", json=body, headers=send)

            assert (await patch({"display_name": "Alice"}, None)).status_code == 400
            ok = await patch({"display_name": "  Alice Ng  "})
            assert ok.status_code == 200, ok.text
            assert ok.json()["display_name"] == "Alice Ng" and ok.headers["etag"] == '"2"'
            stale = await patch({"display_name": "Other"})
            assert stale.status_code == 409
            assert stale.json()["checks"]["problem"] == "stale_version"
            for bad in ("", "   ", "x" * 201):
                assert (await patch({"display_name": bad}, '"2"')).status_code == 422
            assert (await patch({}, '"2"')).status_code == 422
            assert (await patch({"display_name": "x" * 200}, '"2"')).status_code == 200
            cleared = await patch({"display_name": None}, '"3"')
            assert cleared.status_code == 200 and cleared.json()["display_name"] is None

    asyncio.run(scenario())
    assert name_of(conn, "admin@example.test") is None
    rows = conn.execute(
        "SELECT org_id, target_id FROM audit_log WHERE action='identity.display_name.set'"
    ).fetchall()
    assert len(rows) == 3
    assert {r[0] for r in rows} == {a} and {r[1] for r in rows} == {admin_id(conn)}


def test_patch_me_for_another_org_member_only_touches_own_identity(invites):
    conn, a, b = invites

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            send = headers(browser, "own")
            send["If-Match"] = '"1"'
            result = await browser.patch(
                "/api/v1/auth/me",
                json={"display_name": "Mine", "identity_id": str(uuid4())},
                headers=send,
            )
            assert result.status_code == 422  # extra fields are refused, never an identity id

    asyncio.run(scenario())
    assert name_of(conn, "second@example.test") is None


def _models():
    seen = {}
    for package in (flo.modules, flo.api):
        for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            module = importlib.import_module(info.name)
            for cls in vars(module).values():
                if (
                    isinstance(cls, type)
                    and issubclass(cls, pydantic.BaseModel)
                    and cls.__module__ == module.__name__
                ):
                    seen[f"{cls.__module__}.{cls.__name__}"] = cls
    return seen


# Addresses of people who are not yet members: an invitee has no display name until acceptance.
NOT_A_PERSON = {
    "flo.modules.identity.schemas.InvitationRecord.email",
    "flo.modules.identity.schemas.InvitationValid.email",
}


def sibling(field):
    if field == "email":
        return "name"
    return field.replace("_email", "_name")  # also turns approver_emails into approver_names


def test_every_response_field_with_a_person_email_has_a_name_sibling():
    missing = []
    checked = 0
    for path, cls in _models().items():
        if path.endswith(("Create", "Update", "Patch", "Request", "Accept", "Body")):
            continue  # request bodies carry an address the caller typed
        for field in cls.model_fields:
            if f"{path}.{field}" in NOT_A_PERSON:
                continue
            if field == "email" or "_email" in field:
                checked += 1
                # display_name answers only a bare `email`; `*_email` needs its exact `*_name`
                wanted = {"name", "display_name"} if field == "email" else {sibling(field)}
                if not wanted & set(cls.model_fields):
                    missing.append(f"{path}.{field}")
    assert checked >= 25
    assert not missing, missing
