"""Versioned notes, author-only editing and document permissions."""

import asyncio
from uuid import UUID, uuid4

import pytest

from flo.api.health import app
from flo.kernel.tenancy.context import Scope
from flo.modules.collaboration.service import lock_note
from tests.collaboration.test_attachments import document, upload
from tests.isolation.test_real_sessions import client, headers, login


def set_role(db, role):
    db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
    db[0].execute(
        "INSERT INTO user_role(id,org_id,user_id,role_id,scope_type,scope_id) "
        "SELECT gen_random_uuid(),%s,%s,id,'org',%s FROM role WHERE org_id=%s AND code=%s",
        (db[1], db[3], db[1], db[1], role),
    )


def test_notes_history_if_match_lock_and_idempotency(collaboration_db):
    db, _, req, _, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            key = uuid4().hex
            url = document(req) + "/notes"
            data = {"body": "first", "visibility": "internal"}
            response = await browser.post(url, json=data, headers=headers(browser, key))
            assert response.status_code == 201, response.text
            note = response.json()
            assert (
                note["author_email"] == "admin@example.test" and response.headers["etag"] == '"1"'
            )
            assert (
                await browser.post(url, json=data, headers=headers(browser, key))
            ).json() == note
            path = "/api/v1/notes/" + note["id"]
            null_patch = await browser.patch(
                path, json={"body": None}, headers=headers(browser) | {"If-Match": "1"}
            )
            assert null_patch.status_code == 422
            assert (
                await browser.patch(path, json={"body": "x"}, headers=headers(browser))
            ).status_code == 400
            for version, body in ((1, "second"), (2, "third")):
                updated = await browser.patch(
                    path,
                    json={"body": body, "visibility": "shareable"},
                    headers=headers(browser) | {"If-Match": str(version)},
                )
                assert updated.status_code == 200, updated.text
                assert updated.json()["version"] == version + 1
            stale = await browser.patch(
                path, json={"body": "x"}, headers=headers(browser) | {"If-Match": "1"}
            )
            assert stale.status_code == 409 and stale.json()["checks"]["problem"] == "stale_version"
            revisions = (await browser.get(path + "/revisions")).json()
            assert [r["body"] for r in revisions] == ["first", "second"]
            assert [r["visibility"] for r in revisions] == ["internal", "shareable"]
            listed = (await browser.get(url)).json()
            assert len(listed) == 1 and listed[0]["body"] == "third"
            lock_note(db[0], Scope(db[1]), UUID(note["id"]))
            locked = await browser.patch(
                path, json={"body": "x"}, headers=headers(browser) | {"If-Match": "3"}
            )
            assert locked.status_code == 409 and locked.json()["checks"]["problem"] == "note_locked"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "role,can_read,can_write",
    [
        ("organization-administrator", True, True),
        ("requestor", True, True),
        ("approver", True, False),
        ("auditor", True, False),
    ],
)
def test_role_permissions_and_non_author(collaboration_db, role, can_read, can_write):
    db, _, req, _, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            created = (
                await browser.post(
                    document(req) + "/notes",
                    json={"body": "original", "visibility": "internal"},
                    headers=headers(browser, uuid4().hex),
                )
            ).json()
            # Keep the same document but make another identity the author.
            other = (
                db[0]
                .execute("SELECT id FROM identity WHERE email='second@example.test'")
                .fetchone()[0]
            )
            db[0].execute("UPDATE note SET author_id=%s WHERE id=%s", (other, created["id"]))
            set_role(db, role)
            assert (await browser.get(document(req) + "/notes")).status_code == (
                200 if can_read else 403
            )
            comment = await browser.post(
                document(req) + "/notes",
                json={"body": "comment", "visibility": "internal"},
                headers=headers(browser, uuid4().hex),
            )
            assert comment.status_code == 201
            edited = await browser.patch(
                "/api/v1/notes/" + created["id"],
                json={"body": "x"},
                headers=headers(browser) | {"If-Match": "1"},
            )
            assert edited.status_code == 403
            file = await upload(browser, document(req) + "/attachments")
            assert file.status_code == (201 if can_write else 403), file.text

    asyncio.run(scenario())


def test_no_scope_grant_conceals_records(collaboration_db):
    db, _, req, _, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            note = (
                await browser.post(
                    document(req) + "/notes",
                    json={"body": "x", "visibility": "internal"},
                    headers=headers(browser, uuid4().hex),
                )
            ).json()["id"]
            attachment = (await upload(browser, document(req) + "/attachments")).json()["id"]
            db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
            for suffix in ("notes", "attachments"):
                assert (await browser.get(document(req) + "/" + suffix)).status_code == 404
            assert (await browser.get(f"/api/v1/notes/{note}/revisions")).status_code == 404
            assert (
                await browser.get(f"/api/v1/attachments/{attachment}/content")
            ).status_code == 404

    asyncio.run(scenario())


def test_project_registration(collaboration_db):
    _, _, _, _, project = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            response = await upload(browser, document(project, "project") + "/attachments")
            assert response.status_code == 201, response.text
            response = await browser.post(
                document(project, "project") + "/notes",
                json={"body": "project note", "visibility": "shareable"},
                headers=headers(browser, uuid4().hex),
            )
            assert response.status_code == 201, response.text

    asyncio.run(scenario())


def test_wrong_document_permission_forbids_all_operations(collaboration_db):
    from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService

    db, _, req, _, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            note = (
                await browser.post(
                    document(req) + "/notes",
                    json={"body": "x", "visibility": "internal"},
                    headers=headers(browser, uuid4().hex),
                )
            ).json()["id"]
            attachment = (await upload(browser, document(req) + "/attachments")).json()["id"]
            db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
            identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
            role = identity.create_role("record-only", "Record only")
            identity.grant_permission(role.id, "record.read")
            identity.grant_role(db[3], role.id, AuthorizationTarget.organization(db[1]))
            for suffix in ("notes", "attachments"):
                assert (await browser.get(document(req) + "/" + suffix)).status_code == 403
            assert (
                await browser.post(
                    document(req) + "/notes",
                    json={"body": "x", "visibility": "internal"},
                    headers=headers(browser, uuid4().hex),
                )
            ).status_code == 403
            assert (await upload(browser, document(req) + "/attachments")).status_code == 403
            assert (
                await browser.patch(
                    f"/api/v1/notes/{note}",
                    json={"body": "x"},
                    headers=headers(browser) | {"If-Match": "1"},
                )
            ).status_code == 403
            assert (await browser.get(f"/api/v1/notes/{note}/revisions")).status_code == 403
            assert (
                await upload(browser, f"/api/v1/attachments/{attachment}/versions")
            ).status_code == 403
            assert (
                await browser.get(f"/api/v1/attachments/{attachment}/content")
            ).status_code == 403
            assert (
                await browser.delete(f"/api/v1/attachments/{attachment}", headers=headers(browser))
            ).status_code == 403

    asyncio.run(scenario())
