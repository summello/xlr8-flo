"""RFQ content, requirements, attachment visibility, invitations (E12-S02: SRC-002, VEN-012/013)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.org.schemas import MasterCreate
from flo.modules.org.service import OrgService
from flo.modules.sourcing.schemas import (
    InviteeCreate,
    InviteePatch,
    RequirementsFromTemplate,
    RequirementsPut,
    RequirementWaive,
    RfqContentPatch,
    VisibilityPut,
)
from flo.modules.vendors.schemas import ContactCreate, VendorCreate
from flo.modules.vendors.service import VendorService
from tests.isolation.test_real_sessions import client, headers, login
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import connection_url, unit
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import (
    approved,
    draft_rfq,
    entry,
    refused,
    send,
    service,
    sql,
)
from tests.vendors.test_documents import attach
from tests.vendors.test_vendors import regrant


def vendors(db, org=None):
    return VendorService(db[0], Scope(org or db[1]), db[3])


def vendor(db, name="V", status="active", **kwargs):
    """A vendor with a Service and Delivery contact who receives documents."""
    made = vendors(db).create(
        VendorCreate(trading_name=name, legal_name=f"{name} Ltd", availability="global", **kwargs)
    )
    vendors(db).create_contact(
        made.id, ContactCreate(type="service_delivery", name="S", email=f"{uuid4().hex}@x.test")
    )
    sql(db, "UPDATE vendor SET status=%s WHERE id=%s RETURNING 1", (status, made.id))
    return made


def new_rfq(db):
    row, line = approved(db)
    return draft_rfq(db, entry(row, line, "1"))


def invite(db, rfq, v, can_override=False, reason=None):
    return service(db).add_invitee(
        rfq.id, InviteeCreate(vendor_id=v.id, override_reason=reason), can_override
    )


def audit_actions(db, rfq):
    rows = sql(db, "SELECT action FROM audit_log WHERE target_id=%s ORDER BY id", (rfq.id,))
    return [r[0] for r in rows]


def test_content_patch_sets_fields_line_specification_and_refuses_non_draft(lifecycle_db):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    deadline = datetime.now(UTC) + timedelta(days=7)
    done = service(db).update_content(
        rfq.id,
        RfqContentPatch(
            specifications="Pump spec",
            delivery_terms="DAP",
            deadline_at=deadline,
            deadline_tz="Europe/London",
            line_specifications={rfq.lines[0].id: "ISO 5199"},
        ),
        str(rfq.edit_revision),
    )
    assert done.specifications == "Pump spec" and done.delivery_terms == "DAP"
    assert done.deadline_tz == "Europe/London" and done.deadline_at == deadline
    assert done.lines[0].specification == "ISO 5199" and done.edit_revision == 2
    assert "rfq.update" in audit_actions(db, rfq)
    stale = refused(lambda: service(db).update_content(rfq.id, RfqContentPatch(terms="x"), "1"))
    assert stale.checks["problem"] == "stale_version"
    refused(
        lambda: service(db).update_content(
            rfq.id, RfqContentPatch(line_specifications={uuid4(): "x"}), "2"
        ),
        ErrorCode.NOT_FOUND,
    )
    sql(db, "UPDATE rfq SET status='published' WHERE id=%s RETURNING 1", (rfq.id,))
    error = refused(lambda: service(db).update_content(rfq.id, RfqContentPatch(terms="x"), "2"))
    assert error.checks["problem"] == "not_draft"
    for call in (
        lambda: service(db).put_requirements(rfq.id, RequirementsPut(requirements=["a"]), "2"),
        lambda: invite(db, rfq, vendor(db)),
    ):
        assert refused(call).checks["problem"] == "not_draft"


def test_five_invitees_accepted_sixth_refused_even_with_an_override(lifecycle_db):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    for n in range(5):
        invite(db, rfq, vendor(db, f"V{n}"))
    sixth = vendor(db, "Sixth")
    error = refused(lambda: invite(db, rfq, sixth))
    assert error.checks["problem"] == "invitee_limit"
    assert "Remove one to invite Sixth." in str(error.detail)
    suspended = vendor(db, "Late", status="suspended")
    over = refused(lambda: invite(db, rfq, suspended, True, "urgent"))
    assert over.checks["problem"] == "invitee_limit"
    assert len(service(db).get(rfq.id).invitees) == 5


def test_two_invitations_for_the_fifth_slot_let_exactly_one_win(lifecycle_db):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    for n in range(4):
        invite(db, rfq, vendor(db, f"V{n}"))
    contenders = [vendor(db, "A"), vendor(db, "B")]
    barrier = Barrier(2)

    def attempt(v):
        with (
            psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
            correlation_context("invite-race"),
        ):
            barrier.wait(timeout=10)
            try:
                service(db, conn).add_invitee(rfq.id, InviteeCreate(vendor_id=v.id), False)
                return "success"
            except Exception as exc:  # noqa: BLE001
                return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        outcomes = sorted(pool.map(attempt, contenders))
    assert outcomes == ["invitee_limit", "success"]
    assert len(service(db).get(rfq.id).invitees) == 5


def test_ineligible_vendor_needs_an_override_reason_and_rfq_approve(lifecycle_db):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    v = vendor(db, "Susp", status="suspended")
    plain = refused(lambda: invite(db, rfq, v))
    assert (
        plain.checks["problem"] == "vendor_not_eligible" and "Suspended" in plain.checks["reasons"]
    )
    no_right = refused(lambda: invite(db, rfq, v, can_override=False, reason="needed"))
    assert no_right.checks["problem"] == "vendor_not_eligible"
    no_reason = refused(lambda: invite(db, rfq, v, can_override=True))
    assert no_reason.checks["problem"] == "vendor_not_eligible"
    assert service(db).get(rfq.id).invitees == []
    done = invite(db, rfq, v, can_override=True, reason="sole source")
    [row] = done.invitees
    assert row.override_reason == "sole source" and row.eligible is False
    assert row.vendor_snapshot["eligibility"]["eligible"] is False
    assert {"rfq.invitee.add", "rfq.invitee.override"} <= set(audit_actions(db, rfq))
    # an eligible vendor never records an override, whatever the body carried
    ok = invite(db, rfq, vendor(db, "Fine"), can_override=True, reason="ignored")
    assert [i.override_reason for i in ok.invitees].count(None) == 1


def test_eligibility_is_asked_for_the_rfqs_unit_not_the_vendors(lifecycle_db):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    elsewhere = unit(db, "ELSE")
    made = vendors(db).create(
        VendorCreate(
            trading_name="Local",
            legal_name="Local Ltd",
            availability="restricted",
            unit_ids=[elsewhere.id],
        )
    )
    vendors(db).create_contact(
        made.id, ContactCreate(type="service_delivery", name="S", email="l@x.test")
    )
    sql(db, "UPDATE vendor SET status='active' WHERE id=%s RETURNING 1", (made.id,))
    error = refused(lambda: invite(db, rfq, made))
    assert error.checks["problem"] == "vendor_not_eligible"
    assert "Not available in" in error.checks["reasons"]


def test_snapshot_survives_a_rename_and_recipients_are_required(lifecycle_db):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    v = vendor(db, "Before")
    invite(db, rfq, v)
    sql(
        db,
        "UPDATE vendor SET trading_name='After', status='inactive' WHERE id=%s RETURNING 1",
        (v.id,),
    )
    [row] = service(db).get(rfq.id).invitees
    assert row.vendor_snapshot["trading_name"] == "Before"
    assert row.vendor_snapshot["status"] == "active"
    assert row.eligible is False  # live state moved on; the snapshot did not
    bare = vendors(db).create(
        VendorCreate(trading_name="Bare", legal_name="B", availability="global")
    )
    sql(db, "UPDATE vendor SET status='active' WHERE id=%s RETURNING 1", (bare.id,))
    error = refused(lambda: invite(db, rfq, bare))
    assert error.checks["problem"] == "no_recipient"
    assert "Service and Delivery contact" in str(error.detail)


def test_contacts_default_to_recipients_and_edit_to_the_vendors_own_contacts(lifecycle_db):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    v = vendor(db)
    other = vendor(db, "Other")
    mine = vendors(db).create_contact(
        v.id, ContactCreate(type="billing", name="B", email="b@x.test")
    )
    [row] = invite(db, rfq, v).invitees
    assert len(row.contact_ids) == 1 and mine.id not in row.contact_ids
    done = service(db).update_invitee(rfq.id, v.id, InviteePatch(contact_ids=[mine.id]), "2")
    assert done.invitees[0].contact_ids == [mine.id]
    foreign_contact = vendors(db).list_contacts(other.id)[0].id
    error = refused(
        lambda: service(db).update_invitee(
            rfq.id, v.id, InviteePatch(contact_ids=[foreign_contact]), "3"
        ),
        ErrorCode.VALIDATION_FAILED,
    )
    assert error.checks["problem"] == "invalid_contacts"
    removed = service(db).remove_invitee(rfq.id, v.id, "3")
    assert removed.invitees == []
    refused(lambda: service(db).remove_invitee(rfq.id, v.id, "4"), ErrorCode.NOT_FOUND)


def test_requirements_from_template_replace_keeping_waivers_and_waive_with_reason(lifecycle_db):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    template = OrgService(db[0], Scope(db[1]), db[3]).create_master(
        "rfq_requirement_template",
        MasterCreate(
            code="STD",
            name="Standard",
            effective_from=datetime(2000, 1, 1).date(),
            attributes={"requirements": ["Insurance", "ISO 9001", "Insurance"]},
        ),
    )
    done = service(db).requirements_from_template(
        rfq.id, RequirementsFromTemplate(template_id=template.id)
    )
    assert [r.text for r in done.requirements] == ["Insurance", "ISO 9001"]
    rid = done.requirements[0].id
    waived = service(db).waive_requirement(
        rfq.id, rid, RequirementWaive(reason="covered by parent")
    )
    assert waived.requirements[0].waived and waived.requirements[0].waived_reason
    assert (
        refused(
            lambda: service(db).waive_requirement(rfq.id, rid, RequirementWaive(reason="x"))
        ).checks["problem"]
        == "already_waived"
    )
    refused(
        lambda: service(db).waive_requirement(rfq.id, uuid4(), RequirementWaive(reason="x")),
        ErrorCode.NOT_FOUND,
    )
    replaced = service(db).put_requirements(
        rfq.id, RequirementsPut(requirements=["Insurance", "New"]), str(waived.edit_revision)
    )
    assert [(r.text, r.waived) for r in replaced.requirements] == [
        ("Insurance", True),
        ("New", False),
    ]
    assert {"rfq.requirement.waive", "rfq.requirement.replace"} <= set(audit_actions(db, rfq))


def test_attachment_visibility_defaults_to_internal_and_is_per_rfq(lifecycle_db):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    other = new_rfq(db)
    attachment, _ = attach(db, rfq.id, kind="rfq")
    foreign_doc, _ = attach(db, other.id, kind="rfq")
    [listed] = service(db).get(rfq.id).attachments
    assert listed.vendor_visible is False
    done = service(db).set_visibility(rfq.id, attachment, VisibilityPut(vendor_visible=True), "1")
    assert done.attachments[0].vendor_visible is True
    refused(
        lambda: service(db).set_visibility(
            rfq.id, foreign_doc, VisibilityPut(vendor_visible=True), "2"
        ),
        ErrorCode.NOT_FOUND,
    )


def test_content_permissions_and_a_hidden_override_is_refused_by_the_server(lifecycle_db):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    susp = vendor(db, "Susp", status="suspended")
    req = service(db).put_requirements(rfq.id, RequirementsPut(requirements=["Insurance"]), "1")
    rid = req.requirements[0].id
    body = {"vendor_id": str(susp.id), "override_reason": "sole source"}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            # viewer, then editor (the override is an approver's act; no If-Match is a 400),
            # then approver
            for permissions, invite_status, waive_status, patch_status in [
                (["rfq.view"], 403, 403, 403),
                (["rfq.view", "rfq.edit"], 409, 403, 400),
                (["rfq.view", "rfq.edit", "rfq.approve"], 201, 200, 400),
            ]:
                regrant(db, permissions)
                invited = await send(browser, "POST", f"/rfqs/{rfq.id}/invitees", body)
                assert invited.status_code == invite_status, invited.text
                waive = await send(
                    browser, "POST", f"/rfqs/{rfq.id}/requirements/{rid}/waive", {"reason": "ok"}
                )
                assert waive.status_code == waive_status, waive.text
                patched = await send(browser, "PATCH", f"/rfqs/{rfq.id}", {"terms": "t"})
                assert patched.status_code == patch_status, patched.text

    asyncio.run(scenario())
    assert [i.vendor_id for i in service(db).get(rfq.id).invitees] == [susp.id]


def test_candidates_need_rfq_edit_on_the_unit_and_show_nothing_beyond_the_four_fields(
    lifecycle_db,
):
    db, _ = lifecycle_db
    rfq = new_rfq(db)
    ok, bad, taken = vendor(db, "Okay"), vendor(db, "Bad", status="suspended"), vendor(db, "Taken")
    invite(db, rfq, taken)
    muted = vendors(db).create_contact(
        taken.id,
        ContactCreate(type="billing", name="M", email="m@x.test", receives_documents=False),
    )
    [before] = service(db).get(rfq.id).invitees
    assert muted.id not in {c.id for c in before.contacts}  # does not receive documents, not chosen
    chosen = service(db).update_invitee(rfq.id, taken.id, InviteePatch(contact_ids=[muted.id]), "2")
    listed = {c.id: c.chosen for c in chosen.invitees[0].contacts}
    assert listed[muted.id] is True  # already chosen, so offered even though it does not receive
    assert set(chosen.invitees[0].contacts[0].model_dump()) == {
        "id", "name", "email", "type", "receives_documents", "chosen",
    }  # fmt: skip

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            url = f"/rfqs/{rfq.id}/invitee-candidates"
            # org-wide grants without vendor.view: unit-scoped rfq.edit works, view-only does not
            regrant(db, ["rfq.view"], AuthorizationTarget(ScopeType.BU, rfq.unit_id))
            assert (await send(browser, "GET", url)).status_code == 403
            regrant(db, ["rfq.view", "rfq.edit"], AuthorizationTarget(ScopeType.BU, rfq.unit_id))
            page = await send(browser, "GET", url)
            assert page.status_code == 200, page.text
            rows = {r["trading_name"]: r for r in page.json()["rows"]}
            assert set(page.json()) == {"rows", "next_cursor"}
            assert all(
                set(r) == {"vendor_id", "trading_name", "eligibility", "invited"}
                for r in rows.values()
            )
            assert rows["Okay"]["eligibility"]["eligible"] is True and not rows["Okay"]["invited"]
            assert rows["Bad"]["eligibility"]["eligible"] is False
            assert rows["Bad"]["eligibility"]["reasons"] and rows["Taken"]["invited"]
            narrowed = await send(browser, "GET", url + "?q=Okay&limit=1")
            assert [r["trading_name"] for r in narrowed.json()["rows"]] == ["Okay"]
            assert (await send(browser, "GET", url + "?limit=0")).status_code == 422
            sql(db, "UPDATE rfq SET status='published' WHERE id=%s RETURNING 1", (rfq.id,))
            late = await send(browser, "GET", url)
            assert late.status_code == 409 and late.json()["checks"]["problem"] == "not_draft"

    asyncio.run(scenario())
    assert ok and bad


def test_rfq_content_routes_conceal_foreign_tenants(lifecycle_db):
    db, _ = lifecycle_db
    mine = new_rfq(db)
    foreign_unit = unit(db, "FBU", org=db[2])
    foreign_rfq = uuid4()
    owner = db[0].execute("SELECT id FROM identity WHERE email='second@example.test'").fetchone()[0]
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign_rfq, db[2], foreign_unit.id, owner),
        )
    other_org_vendor = vendors(db, db[2]).create(
        VendorCreate(trading_name="Alien", legal_name="Alien", availability="global")
    )
    now = {"If-Match": "1"}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, suffix, data in [
                ("PATCH", f"/rfqs/{foreign_rfq}", {"terms": "x"}),
                ("PUT", f"/rfqs/{foreign_rfq}/requirements", {"requirements": ["a"]}),
                (
                    "POST",
                    f"/rfqs/{foreign_rfq}/requirements/from-template",
                    {"template_id": str(uuid4())},
                ),
                ("POST", f"/rfqs/{foreign_rfq}/requirements/{uuid4()}/waive", {"reason": "x"}),
                (
                    "PUT",
                    f"/rfqs/{foreign_rfq}/attachments/{uuid4()}/visibility",
                    {"vendor_visible": True},
                ),
                ("GET", f"/rfqs/{foreign_rfq}/invitee-candidates", None),
                ("POST", f"/rfqs/{foreign_rfq}/invitees", {"vendor_id": str(other_org_vendor.id)}),
                ("DELETE", f"/rfqs/{foreign_rfq}/invitees/{other_org_vendor.id}", None),
                (
                    "PATCH",
                    f"/rfqs/{foreign_rfq}/invitees/{other_org_vendor.id}",
                    {"contact_ids": [str(uuid4())]},
                ),
                # my RFQ, another tenant's vendor / attachment / template
                ("POST", f"/rfqs/{mine.id}/invitees", {"vendor_id": str(other_org_vendor.id)}),
                ("DELETE", f"/rfqs/{mine.id}/invitees/{other_org_vendor.id}", None),
                (
                    "PUT",
                    f"/rfqs/{mine.id}/attachments/{uuid4()}/visibility",
                    {"vendor_visible": True},
                ),
                (
                    "POST",
                    f"/rfqs/{mine.id}/requirements/from-template",
                    {"template_id": str(uuid4())},
                ),
            ]:
                response = await send(browser, method, suffix, data, now)
                assert response.status_code == 404, (method, suffix, response.text)

    asyncio.run(scenario())
    assert service(db).get(mine.id).invitees == []
    with pytest.raises(Exception):  # noqa: B017, PT011 - the foreign RFQ stays unreadable here
        service(db).get(foreign_rfq)


def test_attachments_change_only_while_draft_and_a_bad_zone_is_422(lifecycle_db):
    from flo.api.collaboration import get_collaboration_storage
    from tests.collaboration.test_attachments import upload
    from tests.imports.test_upload import MemoryStorage

    db, _ = lifecycle_db
    rfq = new_rfq(db)
    app.dependency_overrides[get_collaboration_storage] = lambda: MemoryStorage()
    url = f"/api/v1/documents/rfq/{rfq.id}/attachments"

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            bad = await send(
                browser,
                "PATCH",
                f"/rfqs/{rfq.id}",
                {"deadline_tz": "Mars/Olympus"},
                {"If-Match": "1"},
            )
            assert bad.status_code == 422, bad.text
            first = await upload(browser, url)
            assert first.status_code == 201, first.text
            attachment = first.json()["id"]
            sql(db, "UPDATE rfq SET status='published' WHERE id=%s RETURNING 1", (rfq.id,))
            refused_writes = [
                await upload(browser, url),
                await upload(browser, f"/api/v1/attachments/{attachment}/versions"),
                await browser.delete(
                    f"/api/v1/attachments/{attachment}", headers=headers(browser, uuid4().hex)
                ),
            ]
            for response in refused_writes:
                assert response.status_code == 409, response.text
                assert response.json()["checks"]["problem"] == "not_draft"
            assert (await browser.get(url)).status_code == 200  # reading stays open

    try:
        asyncio.run(scenario())
    finally:
        app.dependency_overrides.pop(get_collaboration_storage, None)
    assert len(sql(db, "SELECT 1 FROM attachment WHERE document_id=%s", (rfq.id,))) == 1
