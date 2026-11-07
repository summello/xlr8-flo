"""Vendor approvals, duplicate identifiers and the status state machine (E11-S06)."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.engine import adapter_for
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import active_instance, decide
from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService
from flo.modules.org.schemas import MasterCreate
from flo.modules.vendors.schemas import (
    AnswersPut,
    AvailabilityPayload,
    ChangeCreate,
    InactivateCreate,
    SensitivePut,
    SubmitCreate,
    VendorCreate,
    VendorPatch,
)
from flo.modules.vendors.service import VendorService
from tests.approvals.test_engine import graph
from tests.approvals.test_workflow_definitions import definition, draft
from tests.approvals.test_workflow_definitions import svc as workflows
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import admin_id, settings
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_header import org_svc, unit
from tests.vendors.test_questionnaire import new_identity, published, question
from tests.vendors.test_vendors import regrant

TAX = {"tax_id": "12-345.678 9", "tax_id_country": "US"}


def sql(db, query, args=(), org=None):
    with tenant_transaction(db[0], Scope(org or db[1])):
        cursor = db[0].execute(query, args)
        return cursor.fetchall() if cursor.description else []


def service(db, actor=None, org=None):
    return VendorService(db[0], Scope(org or db[1]), actor or db[3])


def problem(error):
    return error.value.code, (error.value.checks or {}).get("problem")


def make_actor(db, permissions):
    """An identity holding exactly `permissions` across the organization."""
    identity = new_identity(db)
    db[0].execute(
        "INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)", (identity, db[1])
    )
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = auth.create_role(f"r{uuid4().hex[:8]}", "Test role")
    for permission in permissions:
        auth.grant_permission(role.id, permission)
    auth.grant_role(identity, role.id, AuthorizationTarget.organization(db[1]))
    return identity


@pytest.fixture
def vdb(header_db):
    """A published vendor workflow with one approver who holds vendor.approve."""
    db = header_db
    reviewer = make_actor(db, ["vendor.approve", "vendor.view"])
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = auth.seed_baseline_roles()["approver"]
    auth.grant_role(reviewer, role.id, AuthorizationTarget.organization(db[1]))
    workflow = definition(db, document_type="vendor")
    draft(db, workflow, graph())
    workflows(db).publish(workflow.id, 1, True)
    return db, reviewer


def ready(db, name="Acme", tax=None, actor=None, identifiers=None):
    """A draft vendor with both required contacts (and a tax id when `tax` is given)."""
    s = service(db, actor)
    vendor = s.create(
        VendorCreate(
            trading_name=name,
            legal_name=name,
            availability="global",
            identifiers=identifiers or {},
        )
    )
    for type in ("billing", "service_delivery"):
        s.create_contact(vendor.id, _contact(type))
    if tax:
        service(db).put_sensitive(vendor.id, SensitivePut(**tax), '"0"')
    return vendor


def _contact(type):
    from flo.modules.vendors.schemas import ContactCreate

    return ContactCreate(type=type, name="C", email="c@example.test")


def set_status(db, id, status):
    sql(db, "UPDATE vendor SET status=%s WHERE id=%s", (status, id))


def status_of(db, id):
    return sql(db, "SELECT status FROM vendor WHERE id=%s", (id,))[0][0]


def decide_change(db, reviewer, change_id, outcome="approve"):
    instance = active_instance(db[0], Scope(db[1]), "vendor", change_id)
    extra = {"reject_mode": "permanent", "comment": "No."} if outcome == "reject" else {}
    decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[-1].id,
        actor_id=reviewer,
        body=DecisionWrite(outcome=outcome, document_version=1, **extra),
    )


# -- state machine ------------------------------------------------------------------------------

STATUSES = ("draft", "pending_approval", "active", "suspended", "inactive", "rejected")
ACTIONS = {
    "submit": {"draft"},
    "revise": {"rejected"},
    "availability": {"active"},
    "suspend": {"active"},
    "reactivate": {"suspended"},
    "inactivate": {"active", "suspended"},
}


def act(s, vendor, action):
    if action == "submit":
        return s.submit(vendor.id, SubmitCreate())
    if action == "revise":
        return s.revise(vendor.id)
    if action == "inactivate":
        return s.inactivate(vendor.id, InactivateCreate(reason="Done"))
    payload = AvailabilityPayload(availability="global") if action == "availability" else None
    return s.request_change(vendor.id, ChangeCreate(kind=action, payload=payload, reason="Why"))


@pytest.mark.parametrize(
    ("action", "status"), [(a, s) for a in ACTIONS for s in STATUSES], ids=lambda v: v
)
def test_state_machine_every_cell(vdb, action, status):
    db, _ = vdb
    vendor = ready(db)
    set_status(db, vendor.id, status)
    s = service(db)
    if status in ACTIONS[action]:
        act(s, vendor, action)
        after = {
            "submit": "pending_approval",
            "revise": "draft",
            "availability": status,
            "suspend": status,
            "reactivate": status,
            "inactivate": "inactive",
        }[action]
        assert status_of(db, vendor.id) == after
    else:
        with pytest.raises(ProblemError) as error:
            act(s, vendor, action)
        assert problem(error) == (ErrorCode.CONFLICT, "invalid_transition")
        assert status_of(db, vendor.id) == status


def test_approval_applies_each_change_only_on_the_decision(vdb):
    db, reviewer = vdb
    s = service(db)
    vendor = ready(db)
    create = s.submit(vendor.id, SubmitCreate())
    assert status_of(db, vendor.id) == "pending_approval"
    decide_change(db, reviewer, create.id)
    assert status_of(db, vendor.id) == "active"

    for kind, during, after in [
        ("suspend", "active", "suspended"),
        ("reactivate", "suspended", "active"),
    ]:
        change = s.request_change(vendor.id, ChangeCreate(kind=kind, reason="Why"))
        assert status_of(db, vendor.id) == during  # plant 1: applied on request, not approval
        decide_change(db, reviewer, change.id)
        assert status_of(db, vendor.id) == after
    rejected = s.request_change(vendor.id, ChangeCreate(kind="suspend", reason="Why"))
    decide_change(db, reviewer, rejected.id, "reject")
    assert status_of(db, vendor.id) == "active"
    assert [c.status for c in s.list_changes(vendor.id)] == [
        "rejected",
        "approved",
        "approved",
        "approved",
    ]

    bad = ready(db, "Bad")
    decide_change(db, reviewer, s.submit(bad.id, SubmitCreate()).id, "reject")
    assert status_of(db, bad.id) == "rejected"
    s.revise(bad.id)
    assert status_of(db, bad.id) == "draft"
    assert sql(db, "SELECT count(*) FROM audit_log WHERE action='vendor.status_change'")[0][0] >= 6


def test_availability_change_is_visible_only_after_approval(vdb):
    db, reviewer = vdb
    s = service(db)
    bu = unit(db, "BU")
    vendor = ready(db)
    decide_change(db, reviewer, s.submit(vendor.id, SubmitCreate()).id)
    change = s.request_change(
        vendor.id,
        ChangeCreate(
            kind="availability",
            payload=AvailabilityPayload(availability="restricted", unit_ids=[bu.id]),
            reason="Only here",
        ),
    )
    assert s.available_in(vendor.id, bu.id) is True  # global: the old availability
    shown = s.evidence(change.id)
    assert shown["current_availability"] == "global" and shown["current_units"] == []
    assert len(shown["new_units"]) == 1 and shown["payload"]["availability"] == "restricted"
    other = unit(db, "OTHER")
    assert s.available_in(vendor.id, other.id) is True
    decide_change(db, reviewer, change.id)
    assert s.available_in(vendor.id, bu.id) is True
    assert s.available_in(vendor.id, other.id) is False

    again = s.request_change(
        vendor.id,
        ChangeCreate(
            kind="availability", payload=AvailabilityPayload(availability="global"), reason="Back"
        ),
    )
    decide_change(db, reviewer, again.id, "reject")
    assert s.available_in(vendor.id, other.id) is False  # rejected: unchanged


def test_a_second_pending_change_is_refused_and_a_requester_can_withdraw(vdb):
    db, reviewer = vdb
    s = service(db)
    vendor = ready(db)
    decide_change(db, reviewer, s.submit(vendor.id, SubmitCreate()).id)
    first = s.request_change(vendor.id, ChangeCreate(kind="suspend", reason="Why"))
    with pytest.raises(ProblemError) as error:
        s.request_change(vendor.id, ChangeCreate(kind="suspend", reason="Again"))
    assert problem(error) == (ErrorCode.CONFLICT, "change_pending")
    with pytest.raises(ProblemError) as error:
        s.inactivate(vendor.id, InactivateCreate(reason="Now"))
    assert problem(error) == (ErrorCode.CONFLICT, "change_pending")
    with pytest.raises(ProblemError) as error:  # someone else's request
        service(db, reviewer).withdraw_change(vendor.id, first.id)
    assert error.value.code == ErrorCode.FORBIDDEN
    assert s.withdraw_change(vendor.id, first.id).status == "withdrawn"
    assert status_of(db, vendor.id) == "active"
    with pytest.raises(ProblemError) as error:
        s.withdraw_change(vendor.id, first.id)
    assert problem(error) == (ErrorCode.CONFLICT, "invalid_transition")
    s.request_change(vendor.id, ChangeCreate(kind="suspend", reason="Again"))  # free again


def test_withdrawing_a_create_returns_the_vendor_to_draft(vdb):
    db, _ = vdb
    s = service(db)
    vendor = ready(db)
    create = s.submit(vendor.id, SubmitCreate())
    s.withdraw_change(vendor.id, create.id)
    assert status_of(db, vendor.id) == "draft"


def test_inactivate_is_an_audited_act_with_a_reason(vdb):
    db, _ = vdb
    vendor = ready(db)
    set_status(db, vendor.id, "suspended")
    service(db).inactivate(vendor.id, InactivateCreate(reason="Fraud found"))
    assert status_of(db, vendor.id) == "inactive"
    assert (
        sql(db, "SELECT reason FROM audit_log WHERE action='vendor.status_change'")[-1][0]
        == "Fraud found"
    )


# -- workflow and submit checks -----------------------------------------------------------------


def test_no_published_workflow_refuses_and_writes_nothing(header_db):
    db = header_db
    s = service(db)
    vendor = ready(db)
    with pytest.raises(ProblemError) as error:
        s.submit(vendor.id, SubmitCreate())  # plant 3: skipping require_workflow_for passes this
    assert problem(error) == (ErrorCode.CONFLICT, "workflow_not_set_up")
    assert status_of(db, vendor.id) == "draft"
    set_status(db, vendor.id, "active")
    with pytest.raises(ProblemError) as error:
        s.request_change(vendor.id, ChangeCreate(kind="suspend", reason="Why"))
    assert problem(error) == (ErrorCode.CONFLICT, "workflow_not_set_up")
    assert sql(db, "SELECT count(*) FROM vendor_change")[0][0] == 0


def test_submit_needs_contacts_and_required_answers_but_not_a_passing_score(vdb):
    db, _ = vdb
    s = service(db)
    bare = s.create(VendorCreate(trading_name="B", legal_name="B", availability="global"))
    with pytest.raises(ProblemError) as error:
        s.submit(bare.id, SubmitCreate())
    assert error.value.code == ErrorCode.VALIDATION_FAILED
    assert {e.field for e in error.value.errors} == {
        "contacts.billing",
        "contacts.service_delivery",
    }

    published(
        db, question("name", weight=50, required=True), question("note", weight=50), threshold=90
    )
    onboarding = s.create_onboarding(
        VendorCreate(trading_name="O", legal_name="O", availability="global")
    )
    for type in ("billing", "service_delivery"):
        s.create_contact(onboarding.vendor_id, _contact(type))
    with pytest.raises(ProblemError) as error:
        s.submit(onboarding.vendor_id, SubmitCreate())
    assert [e.field for e in error.value.errors] == ["answers.name"]
    s.put_answers(onboarding.vendor_id, AnswersPut(answers={"name": "x"}), '"1"')
    change = s.submit(onboarding.vendor_id, SubmitCreate())  # 50 < 90 does not block
    assert change.payload["score"] == 50 and change.payload["threshold"] == 90


# -- duplicates -----------------------------------------------------------------------------------


def test_duplicate_tax_id_blocks_unless_a_vendor_manager_overrides(vdb):
    db, reviewer = vdb
    first = ready(db, "First", TAX)
    service(db).submit(first.id, SubmitCreate())
    spaced = ready(db, "Second", {"tax_id": "123456789", "tax_id_country": "US"})
    with pytest.raises(ProblemError) as error:  # formatting does not hide a match
        service(db).submit(spaced.id, SubmitCreate())
    assert problem(error) == (ErrorCode.CONFLICT, "duplicate_identifier")
    assert [e.field for e in error.value.errors] == ["tax_id"]

    requester = make_actor(db, ["vendor.request", "vendor.edit", "vendor.view"])
    theirs = ready(db, "Third", {"tax_id": "123-456-789", "tax_id_country": "US"}, actor=requester)
    with pytest.raises(ProblemError) as error:  # a reason alone is not authority
        service(db, requester).submit(theirs.id, SubmitCreate(override_reason="Same firm"))
    assert problem(error) == (ErrorCode.CONFLICT, "duplicate_identifier")
    assert status_of(db, theirs.id) == "draft"

    change = service(db).submit(spaced.id, SubmitCreate(override_reason="Same firm, new site"))
    assert change.payload["override_reason"] == "Same firm, new site"
    assert change.payload["duplicates"] == ["tax_id"]
    rows = sql(
        db, "SELECT target_id, reason FROM audit_log WHERE action='vendor.duplicate_override'"
    )
    assert rows == [(spaced.id, "Same firm, new site")]


def test_drafts_hold_the_identifier_but_other_countries_and_rejected_vendors_do_not(vdb):
    db, reviewer = vdb
    first = ready(db, "First", TAX)  # a draft peer created first
    other = ready(db, "Other", TAX)
    with pytest.raises(ProblemError) as error:  # every status but rejected holds the id
        service(db).submit(other.id, SubmitCreate())
    assert problem(error) == (ErrorCode.CONFLICT, "duplicate_identifier")
    live = service(db).duplicate_check("123456789", "US", {}).matches
    assert {m.vendor_id for m in live} == {first.id, other.id}  # a draft shows in the live check
    set_status(db, first.id, "rejected")
    service(db).submit(other.id, SubmitCreate())  # the rejected peer is out of the way
    mine = ready(db, "Mine", {"tax_id": "123456789", "tax_id_country": "GB"})
    service(db).submit(mine.id, SubmitCreate())  # same digits, other country
    third = ready(db, "Third", TAX)
    decide_change(
        db,
        reviewer,
        active_instance(
            db[0],
            Scope(db[1]),
            "vendor",
            sql(db, "SELECT id FROM vendor_change WHERE vendor_id=%s", (other.id,))[0][0],
        ).document_id,
        "reject",
    )
    service(db).submit(third.id, SubmitCreate())  # the match was rejected: free again


def test_configured_unique_identifiers_match_and_plain_ones_do_not(vdb):
    db, _ = vdb
    for code, unique in [("DUNS", True), ("NOTE", False)]:
        org_svc(db).create_master(
            "vendor_identifier_type",
            MasterCreate(
                code=code, name=code, effective_from="2000-01-01", attributes={"unique": unique}
            ),
        )
    ids = {"duns": "ab-123", "NOTE": "AB123"}
    first, second = ready(db, "First", identifiers=ids), ready(db, "Second", identifiers=ids)
    assert service(db).get(first.id).identifiers == {"DUNS": "AB123", "NOTE": "AB123"}
    with pytest.raises(ProblemError) as error:  # an undefined type is refused, nothing stored
        service(db).create(
            VendorCreate(
                trading_name="X", legal_name="X", availability="global", identifiers={"NOPE": "1"}
            )
        )
    assert [e.field for e in error.value.errors] == ["identifiers.NOPE"]
    version = f'"{service(db).get(second.id).version}"'
    patched = service(db).update(second.id, VendorPatch(identifiers={"DUNS": "ab 123"}), version)
    assert patched.identifiers == {"DUNS": "AB123"}
    set_status(db, first.id, "active")
    with pytest.raises(ProblemError) as error:
        service(db).submit(second.id, SubmitCreate())
    assert [e.field for e in error.value.errors] == ["identifiers.DUNS"]  # NOTE is not unique
    live = service(db).duplicate_check(None, None, {"duns": "ab 123", "NOTE": "AB123"})
    with pytest.raises(ProblemError) as undefined:  # an undefined type is refused, as on create
        service(db).duplicate_check(None, None, {"NOPE": "x"})
    assert [e.field for e in undefined.value.errors] == ["identifiers.NOPE"]
    assert {(m.field, m.vendor_id) for m in live.matches} == {  # the draft peer shows too
        ("identifiers.DUNS", first.id),
        ("identifiers.DUNS", second.id),
    }


def test_live_check_names_the_vendor_only_to_a_caller_who_may_view_it(vdb):
    db, _ = vdb
    first = ready(db, "Visible Ltd", TAX)
    set_status(db, first.id, "active")
    seen = service(db).duplicate_check("12 345 6789", "us", {}).matches
    assert [(m.field, m.vendor_id, m.trading_name) for m in seen] == [
        ("tax_id", first.id, "Visible Ltd")
    ]
    blind = make_actor(db, ["vendor.request"])
    hidden = service(db, blind).duplicate_check("123456789", "US", {}).matches
    assert [m.model_dump() for m in hidden] == [
        {
            "field": "tax_id",
            "match": True,
            "vendor_id": None,
            "vendor_number": None,
            "trading_name": None,
        }
    ]
    assert service(db).duplicate_check("123456789", "GB", {}).matches == []
    assert service(db).duplicate_check("123456789", None, {}).matches == []


def test_a_foreign_tenants_vendor_never_matches(vdb):
    db, _ = vdb
    theirs = ready_foreign(db)
    assert theirs
    assert service(db).duplicate_check("123456789", "US", {}).matches == []


def ready_foreign(db):
    s = service(db, admin_id(db[0], "second@example.test"), org=db[2])
    vendor = s.create(VendorCreate(trading_name="F", legal_name="F", availability="global"))
    s.put_sensitive(vendor.id, SensitivePut(**TAX), '"0"')
    sql(db, "UPDATE vendor SET status='active' WHERE id=%s", (vendor.id,), org=db[2])
    return vendor


def test_two_concurrent_submits_with_one_tax_id_leave_exactly_one_409(vdb):
    db, _ = vdb
    vendors = [ready(db, f"V{i}", TAX) for i in range(2)]
    barrier = Barrier(2)
    url = settings(db[0]).database_url.get_secret_value()

    def submit(vendor):
        with psycopg.connect(url, autocommit=True) as conn, correlation_context("dup-race"):
            barrier.wait()
            try:
                VendorService(conn, Scope(db[1]), db[3]).submit(vendor.id, SubmitCreate())
            except ProblemError as error:
                return problem_of(error)
            return "ok"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(submit, vendors))
    assert results == ["duplicate_identifier", "ok"]  # plant 2: without the lock, ["ok", "ok"]


def problem_of(error):
    return (error.checks or {}).get("problem")


def test_evidence_for_the_decision_panel(vdb):
    db, reviewer = vdb
    s = service(db)
    vendor = ready(db)
    create = s.submit(vendor.id, SubmitCreate())
    evidence = s.evidence(create.id)
    assert evidence["kind"] == "create" and len(evidence["contacts"]) == 2
    assert "document_state" in evidence
    # the approver gets it through the registered adapter's snapshot, not a side helper
    material = adapter_for("vendor").snapshot(db[0], Scope(db[1]), create.id).material
    assert json.loads(material["evidence"]) == json.loads(json.dumps(evidence, default=str))
    decide_change(db, reviewer, create.id)
    suspend = s.request_change(vendor.id, ChangeCreate(kind="suspend", reason="Audit"))
    assert s.evidence(suspend.id) == {
        "kind": "suspend",
        "reason": "Audit",
        "payload": {},
        "open_rfqs": 0,
        "issued_purchase_orders": 0,
    }


def test_evidence_masks_sensitive_payload_fields_and_names_the_duplicate(vdb):
    db, _ = vdb
    s = service(db)
    mine = ready(db, "Mine", TAX)
    first = s.submit(mine.id, SubmitCreate())
    decide_change(db, vdb[1], first.id)
    twin = ready(db, "Twin", TAX)
    change = s.submit(twin.id, SubmitCreate(override_reason="Same group"))
    # a raw sensitive value in a payload must not leave the module (plant: drop the masking)
    sql(
        db,
        'UPDATE vendor_change SET payload = payload || \'{"bank_account": "98765432109"}\' '
        "WHERE id = %s",
        (change.id,),
    )
    evidence = s.evidence(change.id)
    assert evidence["payload"]["bank_account"] == "•••• 2109"
    assert "98765432109" not in json.dumps(evidence) and "12-345" not in json.dumps(evidence)
    (match,) = evidence["duplicate_matches"]
    assert match["vendor_id"] == str(mine.id) and match["masked_value"] == "•••• 78 9"
    assert evidence["payload"]["override_reason"] == "Same group"


def test_instance_route_carries_the_vendor_evidence_under_the_read_guard(vdb):
    db, _ = vdb
    vendor = ready(db, "Mine", TAX)
    change = service(db).submit(vendor.id, SubmitCreate())
    instance = active_instance(db[0], Scope(db[1]), "vendor", change.id)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            path = f"/api/v1/approvals/instances/{instance.id}"
            seen = await browser.get(path)
            assert seen.status_code == 200, seen.text
            assert seen.json()["evidence"]["kind"] == "create"
            assert "12-345" not in seen.text
            regrant(db, ["vendor.edit"])  # no vendor.view: the guard is unchanged
            assert (await browser.get(path)).status_code == 403  # plant: drop the guard, 200
        async with client(app) as browser:
            await login(browser, "second@example.test")
            assert (await browser.get(path)).status_code == 404  # foreign tenant

    asyncio.run(scenario())


# -- routes ---------------------------------------------------------------------------------------


async def send(browser, method, suffix="", data=None):
    return await browser.request(
        method, "/api/v1/vendors" + suffix, json=data, headers=headers(browser, uuid4().hex)
    )


def test_vendor_change_routes_conceal_foreign_tenants(vdb):
    db, _ = vdb
    theirs = ready_foreign(db)
    mine = ready(db, "Mine")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, suffix, data in [
                ("POST", f"/{theirs.id}/submit", {}),
                ("POST", f"/{theirs.id}/revise", None),
                ("POST", f"/{theirs.id}/changes", {"kind": "suspend", "reason": "x"}),
                ("GET", f"/{theirs.id}/changes", None),
                ("POST", f"/{theirs.id}/changes/{uuid4()}/withdraw", None),
                ("POST", f"/{mine.id}/changes/{uuid4()}/withdraw", None),
                ("POST", f"/{theirs.id}/inactivate", {"reason": "x"}),
            ]:
                response = await send(browser, method, suffix, data)
                assert response.status_code == 404, (method, suffix, response.text)
            # the live check never sees the other tenant's vendor
            seen = await send(browser, "GET", "/duplicate-check?tax_id=123456789&tax_id_country=US")
            assert seen.status_code == 200 and seen.json()["matches"] == []

    asyncio.run(scenario())


def test_vendor_change_routes_end_to_end_and_per_role(vdb):
    db, _ = vdb
    vendor = ready(db, "Mine", {"tax_id": "99887766", "tax_id_country": "US"})
    twin = ready(db, "Twin")
    other = ready(
        db, "Other", actor=make_actor(db, ["vendor.view", "vendor.request"])
    )  # not the caller's draft
    service(db).put_sensitive(twin.id, SensitivePut(tax_id="9988-7766", tax_id_country="US"), '"0"')

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            check = "/duplicate-check?tax_id=99-88-7766&tax_id_country=US"
            assert len((await send(browser, "GET", check)).json()["matches"]) == 2  # both drafts
            submitted = await send(browser, "POST", f"/{vendor.id}/submit", {})
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["status"] == "pending"
            hit = (await send(browser, "GET", check)).json()["matches"]
            assert {m["field"] for m in hit} == {"tax_id"}
            assert str(vendor.id) in {m["vendor_id"] for m in hit}
            blocked = await send(browser, "POST", f"/{twin.id}/submit", {})
            assert blocked.status_code == 409
            assert blocked.json()["errors"][0]["field"] == "tax_id"
            listed = await send(browser, "GET", f"/{vendor.id}/changes")
            assert [c["kind"] for c in listed.json()] == ["create"]

            for permissions, route, expected in [
                (["vendor.view"], ("POST", f"/{other.id}/submit", {}), 403),
                (["vendor.edit"], ("POST", f"/{twin.id}/submit", {}), 409),  # past authz, a dup
                (
                    ["vendor.request"],
                    ("POST", f"/{twin.id}/changes", {"kind": "suspend", "reason": "x"}),
                    403,
                ),
                (["vendor.edit"], ("POST", f"/{twin.id}/inactivate", {"reason": "x"}), 403),
                (["vendor.edit"], ("POST", f"/{twin.id}/revise", None), 409),
                (["vendor.request"], ("GET", check, None), 200),
                (["vendor.view"], ("GET", check, None), 403),
                (["vendor.request"], ("GET", f"/{twin.id}/changes", None), 403),
                (["vendor.view"], ("GET", f"/{twin.id}/changes", None), 200),
                (["vendor.approve"], ("POST", f"/{twin.id}/inactivate", {"reason": "x"}), 409),
                (["vendor.edit"], ("POST", f"/{other.id}/submit", {}), 200),  # edit alone submits
            ]:
                regrant(db, permissions)
                method, suffix, data = route
                response = await send(browser, method, suffix, data)
                assert response.status_code == expected, (permissions, route, response.text)

    asyncio.run(scenario())
