"""Vendor documents: state rule, verification, expiry notices (E11-S03)."""

import asyncio
from datetime import date, timedelta
from uuid import uuid4

import pytest

from flo.api.collaboration import get_collaboration_storage
from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
from flo.modules.org.schemas import MasterCreate, SettingPut
from flo.modules.vendors import expiry
from flo.modules.vendors.schemas import DocumentCreate, VendorCreate
from flo.modules.vendors.service import VendorService
from tests.collaboration.test_attachments import upload
from tests.imports.test_upload import MemoryStorage
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_header import org_svc
from tests.vendors.test_vendors import body, regrant, send

ON = date(2026, 10, 16)
TEMPLATE = "vendor-document-expiry-v1"


def types(db, org=None, **codes):
    """codes: CODE=required flag."""
    for code, required in codes.items():
        org_svc(db, org).create_master(
            "vendor_document_type",
            MasterCreate(
                code=code,
                name=code,
                effective_from=date(2000, 1, 1),
                attributes={"required": required},
            ),
        )


def service(db, org=None):
    return VendorService(db[0], Scope(org or db[1]), db[3])


def new_vendor(db, org=None):
    return service(db, org).create(
        VendorCreate(trading_name="V", legal_name="V", availability="global")
    )


def attach(db, vendor_id, org=None, kind="vendor", versions=1):
    """An attachment with `versions` stored versions; returns (attachment id, version ids)."""
    org = org or db[1]
    attachment, ids = uuid4(), []
    with tenant_transaction(db[0], Scope(org)):
        db[0].execute(
            "INSERT INTO attachment(id,org_id,document_type,document_id,created_by) "
            "VALUES(%s,%s,%s,%s,%s)",
            (attachment, org, kind, vendor_id, db[3]),
        )
        for version in range(1, versions + 1):
            ids.append(uuid4())
            db[0].execute(
                "INSERT INTO attachment_version(id,org_id,attachment_id,version,original_filename,"
                "display_name,storage_key,media_type,size_bytes,sha256,scan_status,uploaded_by) "
                "VALUES(%s,%s,%s,%s,'a.pdf','a.pdf',%s,'application/pdf',5,%s,"
                "'basic_checks_passed',%s)",
                (ids[-1], org, attachment, version, uuid4().hex, "0" * 64, db[3]),
            )
    return attachment, ids


def add(db, vendor_id, code, expiry=None, verification="unverified"):
    attachment, _ = attach(db, vendor_id)
    row = service(db).add_document(
        vendor_id, DocumentCreate(type_code=code, attachment_id=attachment, expiry_date=expiry)
    )
    if verification != "unverified":
        sql(
            db,
            "UPDATE vendor_document SET verification=%s, reviewed_by=%s, reviewed_at=now(),"
            " rejection_reason=%s WHERE id=%s",
            (verification, db[3], "bad" if verification == "rejected" else None, row.document_id),
        )
    return row.document_id


def sql(db, query, args=()):
    with tenant_transaction(db[0], Scope(db[1])):
        cursor = db[0].execute(query, args)
        return cursor.fetchall() if cursor.description else []


def states(db, vendor_id, on=ON):
    return {r.type_code: r for r in service(db).document_state(vendor_id, on)}


def test_document_state_precedence_boundaries_and_vendor_status(header_db):
    db = header_db
    types(db, TAX=True, INS=True, CERT=False, ISO=False, OLD=True)
    vendor = new_vendor(db)
    assert set(states(db, vendor.id)) == {"TAX", "INS", "OLD"}  # missing required are rows
    assert states(db, vendor.id)["TAX"].state == "missing"
    # missing optional types are not rows; a supplied optional one is
    rejected_expired = add(db, vendor.id, "TAX", ON - timedelta(days=1), "rejected")
    add(db, vendor.id, "CERT", None, "verified")
    got = states(db, vendor.id)
    assert got["TAX"].state == "rejected" and got["TAX"].document_id == rejected_expired
    assert got["CERT"].state == "ok" and "ISO" not in got and got["CERT"].required is False
    assert got["INS"].state == "missing" and got["INS"].required is True

    cases = [
        (30, "expiring", 30),
        (31, "ok", None),
        (0, "expiring", 0),
        (-1, "expired", None),
    ]
    for offset, name, left in cases:
        sql(
            db,
            "UPDATE vendor_document SET verification='verified', rejection_reason=NULL,"
            " expiry_date=%s WHERE id=%s",
            (ON + timedelta(days=offset), rejected_expired),
        )
        row = states(db, vendor.id)["TAX"]
        assert (row.state, row.days_left) == (name, left), offset
    # unverified ranks below expiring and expired, above ok
    sql(
        db,
        "UPDATE vendor_document SET verification='unverified', expiry_date=NULL WHERE id=%s",
        (rejected_expired,),
    )
    assert states(db, vendor.id)["TAX"].state == "unverified"
    sql(
        db,
        "UPDATE vendor_document SET expiry_date=%s WHERE id=%s",
        (ON - timedelta(days=5), rejected_expired),
    )
    assert states(db, vendor.id)["TAX"].state == "expired"
    # an expired required document leaves the vendor's status untouched (decision 123)
    assert service(db).get(vendor.id).status == vendor.status
    assert expiry.expired_required_count(db[0], Scope(db[1]), ON) == 1


def test_add_validates_type_attachment_and_duplicates(header_db):
    db = header_db
    types(db, TAX=True)
    vendor, other = new_vendor(db), new_vendor(db)
    svc = service(db)
    mine, _ = attach(db, vendor.id)
    for bad in (
        DocumentCreate(type_code="NOPE", attachment_id=mine),
        DocumentCreate(type_code="TAX", attachment_id=uuid4()),
        DocumentCreate(type_code="TAX", attachment_id=attach(db, other.id)[0]),  # another vendor's
        DocumentCreate(type_code="TAX", attachment_id=attach(db, vendor.id, kind="requisition")[0]),
        DocumentCreate(
            type_code="TAX", attachment_id=mine, issue_date=ON, expiry_date=ON - timedelta(days=1)
        ),
    ):
        with pytest.raises(ProblemError) as caught:
            svc.add_document(vendor.id, bad)
        assert caught.value.code is ErrorCode.VALIDATION_FAILED, bad
    foreign_attachment, _ = attach(db, uuid4(), org=db[2])
    with pytest.raises(ProblemError) as caught:
        svc.add_document(
            vendor.id, DocumentCreate(type_code="TAX", attachment_id=foreign_attachment)
        )
    assert caught.value.code is ErrorCode.VALIDATION_FAILED
    added = svc.add_document(vendor.id, DocumentCreate(type_code="tax", attachment_id=mine))
    assert added.type_code == "TAX" and added.verification == "unverified"
    with pytest.raises(ProblemError) as caught:  # same type again, or the same file again
        svc.add_document(
            vendor.id, DocumentCreate(type_code="TAX", attachment_id=attach(db, vendor.id)[0])
        )
    assert (
        caught.value.code is ErrorCode.CONFLICT and caught.value.checks["problem"] == "use_replace"
    )
    assert sql(db, "SELECT count(*) FROM vendor_document")[0][0] == 1


def test_document_routes_end_to_end_with_separation_of_duty_and_roles(header_db):
    db = header_db
    types(db, TAX=True)
    org_svc(db).set_setting("approval.separation_of_duty", SettingPut(value=True))
    app.dependency_overrides[get_collaboration_storage] = lambda: MemoryStorage()

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            vid = (await send(browser, "POST", data=body())).json()["id"]
            files = f"/api/v1/documents/vendor/{vid}/attachments"
            first = await upload(browser, files)
            assert first.status_code == 201, first.text
            path = f"/{vid}/documents"
            made = await send(
                browser,
                "POST",
                path,
                {
                    "type_code": "TAX",
                    "attachment_id": first.json()["id"],
                    "expiry_date": "2099-01-01",
                },
            )
            assert made.status_code == 201, made.text
            doc = made.json()
            assert (
                doc["verification"] == "unverified" and doc["scan_status"] == "basic_checks_passed"
            )
            listed = (await send(browser, "GET", path)).json()
            assert [r["state"] for r in listed] == ["unverified"]
            again = await send(
                browser, "POST", path, {"type_code": "TAX", "attachment_id": first.json()["id"]}
            )
            assert again.status_code == 422  # the file is already linked
            # the uploader cannot verify their own file
            own = await send(browser, "POST", f"{path}/{doc['document_id']}/verify")
            assert (
                own.status_code == 409 and own.json()["checks"]["problem"] == "separation_of_duty"
            )
            rejected = await send(
                browser, "POST", f"{path}/{doc['document_id']}/reject", {"reason": "Blurry scan"}
            )
            assert rejected.status_code == 200 and rejected.json()["state"] == "rejected"
            assert rejected.json()["rejection_reason"] == "Blurry scan"
            assert rejected.json()["reviewed_by"] is not None
            blank = await send(
                browser, "POST", f"{path}/{doc['document_id']}/reject", {"reason": " "}
            )
            assert blank.status_code == 422
            # a rejected file is replaced with a new version, which resets verification
            version = await browser.post(
                f"/api/v1/attachments/{first.json()['id']}/versions",
                files={"file": ("b.pdf", b"%PDF-1.7 b", "application/pdf")},
                headers=headers(browser, uuid4().hex)
                | {"Content-Type": "multipart/form-data; boundary=collaboration-test"},
            )
            assert version.status_code == 201, version.text
            stale = await send(
                browser,
                "POST",
                f"{path}/{doc['document_id']}/replace",
                {"attachment_version_id": str(uuid4())},
            )
            assert stale.status_code == 422
            latest = (await send(browser, "GET", path)).json()[0]["latest_version_id"]
            replaced = await send(
                browser,
                "POST",
                f"{path}/{doc['document_id']}/replace",
                {"attachment_version_id": latest, "expiry_date": "2099-06-01"},
            )
            assert replaced.status_code == 200, replaced.text
            assert replaced.json()["verification"] == "unverified"
            assert replaced.json()["rejection_reason"] is None
            assert replaced.json()["attachment_version"] == 2
            assert replaced.json()["expiry_date"] == "2099-06-01"
            same = await send(
                browser,
                "POST",
                f"{path}/{doc['document_id']}/replace",
                {"attachment_version_id": latest},
            )
            assert same.status_code == 422  # no newer version than the one in force
            # with the rule off the uploader may verify; a verified file is not reviewed again
            org_svc(db).set_setting("approval.separation_of_duty", SettingPut(value=False))
            ok = await send(browser, "POST", f"{path}/{doc['document_id']}/verify")
            assert ok.status_code == 200 and ok.json()["state"] == "ok", ok.text
            twice = await send(browser, "POST", f"{path}/{doc['document_id']}/verify")
            assert (
                twice.status_code == 409 and twice.json()["checks"]["problem"] == "not_unverified"
            )
            audited = sql(
                db,
                "SELECT action FROM audit_log WHERE action LIKE 'vendor.document.%%' ORDER BY id",
            )
            assert [a[0] for a in audited] == [
                "vendor.document.add",
                "vendor.document.reject",
                "vendor.document.replace",
                "vendor.document.verify",
            ]
            # roles: view reads only; edit writes but cannot review
            regrant(db, ["vendor.view"])
            assert (await send(browser, "GET", path)).status_code == 200
            assert (
                await send(
                    browser, "POST", path, {"type_code": "TAX", "attachment_id": str(uuid4())}
                )
            ).status_code == 403
            regrant(db, ["vendor.view", "vendor.edit"])
            for action, data in (("verify", None), ("reject", {"reason": "x"})):
                denied = await send(browser, "POST", f"{path}/{doc['document_id']}/{action}", data)
                assert denied.status_code == 403, action
            regrant(db, ["record.read"])
            assert (await send(browser, "GET", path)).status_code in (403, 404)

    asyncio.run(scenario())


def test_vendor_document_routes_conceal_foreign_tenants(header_db):
    db = header_db
    types(db, db[1], TAX=True)
    types(db, db[2], TAX=True)
    foreign = new_vendor(db, db[2])
    attachment, _ = attach(db, foreign.id, org=db[2])
    theirs = VendorService(db[0], Scope(db[2]), db[3]).add_document(
        foreign.id, DocumentCreate(type_code="TAX", attachment_id=attachment)
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            mine = (await send(browser, "POST", data=body())).json()["id"]
            for method, suffix, data in [
                ("GET", f"/{foreign.id}/documents", None),
                (
                    "POST",
                    f"/{foreign.id}/documents",
                    {"type_code": "TAX", "attachment_id": str(attachment)},
                ),
                (
                    "POST",
                    f"/{foreign.id}/documents/{theirs.document_id}/replace",
                    {"attachment_version_id": str(uuid4())},
                ),
                ("POST", f"/{foreign.id}/documents/{theirs.document_id}/verify", None),
                ("POST", f"/{foreign.id}/documents/{theirs.document_id}/reject", {"reason": "x"}),
                # a foreign document id under my own vendor
                (
                    "POST",
                    f"/{mine}/documents/{theirs.document_id}/replace",
                    {"attachment_version_id": str(uuid4())},
                ),
                ("POST", f"/{mine}/documents/{theirs.document_id}/verify", None),
                ("POST", f"/{mine}/documents/{theirs.document_id}/reject", {"reason": "x"}),
            ]:
                response = await send(browser, method, suffix, data)
                assert response.status_code == 404, (method, suffix, response.text)
            # a foreign attachment id under my own vendor is never linked
            linked = await send(
                browser,
                "POST",
                f"/{mine}/documents",
                {"type_code": "TAX", "attachment_id": str(attachment)},
            )
            assert linked.status_code == 422
            assert sql(db, "SELECT count(*) FROM vendor_document")[0][0] == 1  # theirs only

    asyncio.run(scenario())


def manager(db):
    """The admin holds the vendor-manager role at organization scope."""
    identity = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    with tenant_transaction(db[0], Scope(db[1])):
        row = db[0].execute("SELECT id FROM role WHERE code='vendor-manager'").fetchone()
    role = row[0] if row else identity.create_role("vendor-manager", "Vendor Manager").id
    identity.grant_role(db[3], role, AuthorizationTarget.organization(db[1]))


def digests(db):
    return [
        r[0]
        for r in sql(
            db,
            "SELECT payload FROM outbox WHERE payload->>'template'=%s ORDER BY created_at, id",
            (TEMPLATE,),
        )
    ]


def test_expiry_job_notifies_once_per_threshold_through_the_outbox(header_db):
    db = header_db
    types(db, TAX=True, INS=True)
    manager(db)
    vendor = new_vendor(db)
    scope = Scope(db[1])
    add(db, vendor.id, "TAX", ON + timedelta(days=30))
    add(db, vendor.id, "INS", ON + timedelta(days=31))
    assert expiry.run_org(db[0], scope, on=ON) == 1  # TAX only, INS is 31 days out
    [first] = digests(db)
    assert first["to"] == "admin@example.test"
    assert [(i["type_code"], i["threshold"]) for i in first["context"]["items"]] == [
        ("TAX", "expiring")
    ]
    assert expiry.run_org(db[0], scope, on=ON) == 0  # the same day sends nothing
    assert len(digests(db)) == 1
    # INS crosses 30 days a day later, TAX is still stamped
    assert expiry.run_org(db[0], scope, on=ON + timedelta(days=1)) == 1
    assert [i["type_code"] for i in digests(db)[1]["context"]["items"]] == ["INS"]
    # both expire: one digest per recipient carries both, and TAX notifies once more
    assert expiry.run_org(db[0], scope, on=ON + timedelta(days=32)) == 1
    last = digests(db)[2]["context"]["items"]
    assert sorted((i["type_code"], i["threshold"]) for i in last) == [
        ("INS", "expired"),
        ("TAX", "expired"),
    ]
    assert expiry.run_org(db[0], scope, on=ON + timedelta(days=33)) == 0
    assert len(digests(db)) == 3
    # no file name or content in what is queued
    assert "a.pdf" not in str(digests(db))


def test_expiry_job_without_a_vendor_manager_retries_later(header_db):
    db = header_db
    types(db, TAX=True)
    vendor = new_vendor(db)
    add(db, vendor.id, "TAX", ON)
    sql(db, "DELETE FROM user_role WHERE user_id=%s", (db[3],))
    assert expiry.run_org(db[0], Scope(db[1]), on=ON) == 0
    assert (
        sql(db, "SELECT count(*) FROM vendor_document WHERE notified_expiring_at IS NULL")[0][0]
        == 1
    )
    manager(db)
    assert expiry.run_org(db[0], Scope(db[1]), on=ON) == 1


def test_replace_resets_notification_stamps(header_db):
    db = header_db
    types(db, TAX=True)
    manager(db)
    vendor = new_vendor(db)
    attachment, versions = attach(db, vendor.id, versions=2)
    doc = service(db).add_document(
        vendor.id, DocumentCreate(type_code="TAX", attachment_id=attachment, expiry_date=ON)
    )
    sql(
        db,
        "UPDATE vendor_document SET attachment_version_id=%s WHERE id=%s",
        (versions[0], doc.document_id),
    )
    assert expiry.run_org(db[0], Scope(db[1]), on=ON) == 1
    from flo.modules.vendors.schemas import DocumentReplace

    service(db).replace_document(
        vendor.id,
        doc.document_id,
        DocumentReplace(attachment_version_id=versions[1], expiry_date=ON + timedelta(days=10)),
    )
    # the new date notifies afresh (the next day: one digest per recipient per day)
    assert expiry.run_org(db[0], Scope(db[1]), on=ON + timedelta(days=1)) == 1


def test_jobs_tick_runs_registered_expiry_handler(header_db, monkeypatch):
    from flo.api import document_types, internal
    from flo.kernel.config import Settings
    from flo.kernel.outbox.dispatcher import OutboxSummary

    db = header_db
    types(db, TAX=True)
    manager(db)
    vendor = new_vendor(db)
    add(db, vendor.id, "TAX", date.today() + timedelta(days=3))
    assert expiry.enqueue_all(db[0]) == 2
    monkeypatch.setattr(document_types, "register_document_types", lambda: None)
    monkeypatch.setattr(internal, "create_email_sender", lambda settings: object())
    monkeypatch.setattr(internal.OutboxDispatcher, "run", lambda self, budget: OutboxSummary())
    report = internal._run_jobs_tick(Settings())
    assert report.jobs.done == 2 and report.jobs.dead == 0
    assert len(digests(db)) == 1


def test_internal_expiry_trigger_origin_key_and_nonreplay(header_db, monkeypatch):
    from flo.api.origin_auth import require_origin_secret

    db = header_db
    app.dependency_overrides.pop(require_origin_secret)
    token = "A" * 32
    monkeypatch.setenv("ORIGIN_SHARED_SECRET", token)

    async def scenario():
        async with client(app) as browser:
            await browser.get("/api/v1/auth/sessions", headers={"X-FLO-Origin-Secret": token})
            path = "/internal/jobs/vendor-document-expiry"
            for secret in (None, "incorrect"):
                h = headers(browser, uuid4().hex)
                if secret:
                    h["X-FLO-Origin-Secret"] = secret
                assert (await browser.post(path, headers=h)).status_code == 404
            h = headers(browser) | {"X-FLO-Origin-Secret": token}
            h.pop("Idempotency-Key")
            assert (await browser.post(path, headers=h)).status_code == 400
            assert db[0].execute("SELECT count(*) FROM job").fetchone() == (0,)
            for key in (uuid4().hex, uuid4().hex):
                response = await browser.post(
                    path, headers=headers(browser, key) | {"X-FLO-Origin-Secret": token}
                )
                assert response.status_code == 200, response.text
                assert response.json() == {"organizations": 2}
            assert db[0].execute(
                "SELECT count(*) FROM job WHERE kind='vendor-document-expiry'"
            ).fetchone() == (4,)
            assert db[0].execute("SELECT count(*) FROM outbox").fetchone() == (0,)

    asyncio.run(scenario())


def test_document_type_master_attributes(header_db):
    db = header_db
    org = org_svc(db)
    org.create_master(
        "vendor_document_type",
        MasterCreate(code="A", name="A", attributes={"required": True, "category": "CONSTRUCTION"}),
    )
    for attributes in (
        {},
        {"required": "yes"},
        {"required": True, "category": 3},
        {"required": True, "x": 1},
    ):
        with pytest.raises(ProblemError) as caught:
            org.create_master(
                "vendor_document_type", MasterCreate(code="B", name="B", attributes=attributes)
            )
        assert caught.value.code is ErrorCode.VALIDATION_FAILED, attributes


def test_expired_required_count_honours_effective_window_and_emits_business_monitor(
    header_db, caplog
):
    db = header_db
    types(db, TAX=True)
    manager(db)
    vendor = new_vendor(db)
    scope = Scope(db[1])
    add(db, vendor.id, "TAX", ON - timedelta(days=1))
    assert expiry.expired_required_count(db[0], scope, ON) == 1
    with caplog.at_level("ERROR"):
        expiry.run_org(db[0], scope, on=ON)
    [event] = [r for r in caplog.records if getattr(r, "business_monitor", None)]
    assert event.business_monitor == "expired_vendor_documents"
    assert event.org_id == str(db[1]) and event.count == 1
    # the requirement ended before the reporting date: not counted, no event
    sql(
        db,
        "UPDATE master_record SET effective_to=%s WHERE kind='vendor_document_type'",
        (ON - timedelta(days=10),),
    )
    assert expiry.expired_required_count(db[0], scope, ON) == 0
    # and not yet started
    sql(
        db,
        "UPDATE master_record SET effective_from=%s, effective_to=NULL "
        "WHERE kind='vendor_document_type'",
        (ON + timedelta(days=10),),
    )
    assert expiry.expired_required_count(db[0], scope, ON) == 0
    caplog.clear()
    with caplog.at_level("ERROR"):
        expiry.run_org(db[0], scope, on=ON)
    assert not [r for r in caplog.records if getattr(r, "business_monitor", None)]


def test_second_same_day_run_sends_no_second_digest_and_defers_new_documents(header_db):
    db = header_db
    types(db, TAX=True, INS=True)
    manager(db)
    vendor = new_vendor(db)
    scope = Scope(db[1])
    add(db, vendor.id, "TAX", ON + timedelta(days=5))
    assert expiry.run_org(db[0], scope, on=ON) == 1
    add(db, vendor.id, "INS", ON + timedelta(days=6))  # found later the same day
    assert expiry.run_org(db[0], scope, on=ON) == 0
    assert len(digests(db)) == 1
    keys = sql(db, "SELECT idempotency_key FROM outbox WHERE payload->>'template'=%s", (TEMPLATE,))
    assert keys == [(f"vendor-doc-digest:{db[1]}:{db[3]}:{ON.isoformat()}",)]
    # the deferred document goes out in the next day's digest
    assert expiry.run_org(db[0], scope, on=ON + timedelta(days=1)) == 1
    assert [i["type_code"] for i in digests(db)[1]["context"]["items"]] == ["INS"]
