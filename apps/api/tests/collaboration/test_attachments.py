"""Uploads and downloads through the production stack with real CSRF and sessions."""

import asyncio
from hashlib import sha256
from uuid import UUID, uuid4

import pytest

from flo.api.health import app
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_scan import ALLOWED, REFUSED


def document(row, kind="requisition"):
    return f"/api/v1/documents/{kind}/{row.id}"


async def upload(
    browser,
    url,
    name="file.pdf",
    data=b"%PDF-1.7",
    key=None,
    client_type="application/octet-stream",
):
    return await browser.post(
        url,
        files={"file": (name, data, client_type)},
        headers=headers(browser, key or uuid4().hex)
        | {"Content-Type": "multipart/form-data; boundary=collaboration-test"},
    )


def audits(db, action):
    return (
        db[0]
        .execute("SELECT count(*) FROM audit_log WHERE org_id=%s AND action=%s", (db[1], action))
        .fetchone()[0]
    )


@pytest.mark.parametrize("name,data,media", ALLOWED)
def test_allowed_upload_types_and_idempotency(collaboration_db, name, data, media):
    db, store, req, _, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            key = uuid4().hex
            response = await upload(browser, document(req) + "/attachments", name, data, key)
            assert response.status_code == 201, response.text
            row = response.json()
            assert row["media_type"] == media and row["scan_status"] == "basic_checks_passed"
            assert row["sha256"] == sha256(data).hexdigest()
            assert row["uploaded_by_email"] == "admin@example.test"
            replay = await upload(browser, document(req) + "/attachments", name, data, key)
            assert replay.json() == row and len(store.objects) == 1
            changed = await upload(browser, document(req) + "/attachments", name, data + b"x", key)
            assert changed.status_code == 422
            assert len(store.objects) == 1
            assert audits(db, "attachment.upload") == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("name,data", REFUSED)
def test_refused_upload_stores_nothing_and_audits(collaboration_db, name, data):
    db, store, req, _, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            response = await upload(
                browser, document(req) + "/attachments", name, data, client_type="application/pdf"
            )
            assert response.status_code == 422, response.text
            assert response.json()["checks"]["problem"] == "unsupported_file"
            assert not store.objects
            assert audits(db, "attachment.refused") == 1
            assert db[0].execute("SELECT count(*) FROM attachment").fetchone() == (0,)

    asyncio.run(scenario())


def test_size_boundary_multipart_and_opaque_key(collaboration_db):
    db, store, req, _, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            url = document(req) + "/attachments"
            data = b"%PDF-" + b"x" * (10 * 1024 * 1024 - 5)
            response = await upload(browser, url, "../../etc/passwd.pdf", data)
            assert response.status_code == 201, response.text
            row = response.json()
            assert row["display_name"] == "passwd.pdf"
            assert row["original_filename"] == "../../etc/passwd.pdf"
            key = db[0].execute("SELECT storage_key FROM attachment_version").fetchone()[0]
            assert str(UUID(key)) == key and key != row["original_filename"]
            assert (await upload(browser, url, data=data + b"x")).status_code == 413
            assert len(store.objects) == 1
            assert (
                await browser.post(url, json={}, headers=headers(browser, uuid4().hex))
            ).status_code == 415

    asyncio.run(scenario())


def test_download_headers_checksum_versions_soft_delete_and_audit(collaboration_db):
    db, store, req, _, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            first = (await upload(browser, document(req) + "/attachments")).json()
            url = "/api/v1/attachments/" + first["id"]
            second = await upload(browser, url + "/versions", data=b"%PDF-new")
            assert second.status_code == 201 and second.json()["version"] == 2
            listed = (await browser.get(document(req) + "/attachments")).json()
            assert listed[0]["versions"] == 2 and listed[0]["version"] == 2
            for version, expected in ((1, b"%PDF-1.7"), (2, b"%PDF-new")):
                response = await browser.get(url + f"/content?version={version}")
                assert response.status_code == 200, response.text
                assert response.content == expected
                assert (
                    sha256(response.content).hexdigest()
                    == (first if version == 1 else second.json())["sha256"]
                )
                assert response.headers["content-disposition"].startswith(
                    "attachment; filename*=UTF-8''"
                )
                assert response.headers["x-content-type-options"] == "nosniff"
                assert "sandbox" in response.headers.get_list("content-security-policy")
                assert response.headers["cache-control"] == "private, no-store"
            assert audits(db, "attachment.download") == 2
            for _ in range(2):
                assert (await browser.delete(url, headers=headers(browser))).status_code == 204
            assert audits(db, "attachment.delete") == 1
            assert len(store.objects) == 2
            assert (
                db[0].execute("SELECT deleted_at,deleted_by FROM attachment").fetchone()[0]
                is not None
            )
            assert (await browser.get(url + "/content")).status_code == 404
            assert (await upload(browser, url + "/versions")).status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["pending", "quarantined", "rejected", "clean"])
@pytest.mark.parametrize("role", ["organization-administrator", "requestor", "approver", "auditor"])
def test_unreadable_scan_states_block_every_role(collaboration_db, state, role):
    db, _, req, _, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            row = (await upload(browser, document(req) + "/attachments")).json()
            db[0].execute("UPDATE attachment_version SET scan_status=%s", (state,))
            db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
            db[0].execute(
                "INSERT INTO user_role(id,org_id,user_id,role_id,scope_type,scope_id) "
                "SELECT gen_random_uuid(),%s,%s,id,'org',%s FROM role WHERE org_id=%s AND code=%s",
                (db[1], db[3], db[1], db[1], role),
            )
            response = await browser.get("/api/v1/attachments/" + row["id"] + "/content")
            assert response.status_code == (200 if state == "clean" else 409), response.text
            if state != "clean":
                assert response.json()["checks"]["problem"] == "file_unavailable"
            assert audits(db, "attachment.download") == (1 if state == "clean" else 0)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("organization-administrator", True),
        ("requestor", True),
        ("approver", False),
        ("auditor", False),
    ],
)
def test_delete_and_new_version_need_the_write_permission(collaboration_db, role, allowed):
    """Plant: checking the read permission on delete or version upload must fail here."""
    db, _, req, _, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            row = (await upload(browser, document(req) + "/attachments")).json()
            url = "/api/v1/attachments/" + row["id"]
            db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
            db[0].execute(
                "INSERT INTO user_role(id,org_id,user_id,role_id,scope_type,scope_id) "
                "SELECT gen_random_uuid(),%s,%s,id,'org',%s FROM role WHERE org_id=%s AND code=%s",
                (db[1], db[3], db[1], db[1], role),
            )
            version = await upload(browser, url + "/versions", data=b"%PDF-new")
            assert version.status_code == (201 if allowed else 403), version.text
            deleted = await browser.delete(url, headers=headers(browser))
            assert deleted.status_code == (204 if allowed else 403), deleted.text
            assert audits(db, "attachment.delete") == (1 if allowed else 0)

    asyncio.run(scenario())


def test_foreign_tenant_every_collaboration_route(collaboration_db):
    db, _, req, foreign, _ = collaboration_db

    async def scenario():
        async with client(app) as browser:
            await login(browser, email="second@example.test")
            attachment = (await upload(browser, document(foreign) + "/attachments")).json()["id"]
            note = (
                await browser.post(
                    document(foreign) + "/notes",
                    json={"body": "foreign", "visibility": "internal"},
                    headers=headers(browser, uuid4().hex),
                )
            ).json()["id"]
        async with client(app) as browser:
            await login(browser)
            for suffix in ("notes", "attachments"):
                url = document(foreign) + "/" + suffix
                assert (await browser.get(url)).status_code == 404
                response = (
                    await upload(browser, url)
                    if suffix == "attachments"
                    else await browser.post(
                        url,
                        json={"body": "x", "visibility": "internal"},
                        headers=headers(browser, uuid4().hex),
                    )
                )
                assert response.status_code == 404
            assert (
                await browser.patch(
                    f"/api/v1/notes/{note}",
                    json={"body": "x"},
                    headers=headers(browser) | {"If-Match": '"1"'},
                )
            ).status_code == 404
            assert (await browser.get(f"/api/v1/notes/{note}/revisions")).status_code == 404
            assert (
                await upload(browser, f"/api/v1/attachments/{attachment}/versions")
            ).status_code == 404
            assert (
                await browser.get(f"/api/v1/attachments/{attachment}/content")
            ).status_code == 404
            assert (
                await browser.delete(f"/api/v1/attachments/{attachment}", headers=headers(browser))
            ).status_code == 404
            assert (await browser.get(document(req, "unknown") + "/notes")).status_code == 404

    asyncio.run(scenario())
