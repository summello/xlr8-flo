"""RFQ publish, versioned amendments told to invitees, deadline, close (E12-S03: SRC-004/005)."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.sourcing import lifecycle
from flo.modules.sourcing.lifecycle import TRANSITIONS, RfqLifecycle
from flo.modules.sourcing.schemas import ExtendDeadline, InviteePatch, RfqAmend, RfqContentPatch
from flo.modules.vendors.schemas import ContactCreate
from tests.isolation.test_real_sessions import client, headers, login
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_header import unit
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import approved, draft_rfq, entry, refused, send, sql
from tests.sourcing.test_rfq_content import audit_actions, invite, vendor, vendors
from tests.vendors.test_vendors import regrant

STATUSES = ("draft", "published", "closed", "under_evaluation", "awarded", "cancelled")
ACTIONS = ("publish", "amend", "extend", "close", "start_evaluation")


def life(db, connection=None):
    return RfqLifecycle(connection or db[0], Scope(db[1]), db[3], Settings())


def ready(db, qty="4", names=("V",)):
    """A draft RFQ for `qty` units with a far deadline and one invitee per name."""
    row, line = approved(db)
    rfq = draft_rfq(db, entry(row, line, qty))
    life(db).update_content(
        rfq.id,
        RfqContentPatch(
            specifications="Pump spec", deadline_at=datetime.now(UTC) + timedelta(days=3)
        ),
        str(rfq.edit_revision),
    )
    for name in names:
        invite(db, rfq, vendor(db, name))
    return life(db).get(rfq.id), row, line


def publish(db, *args, **kwargs):
    rfq, row, line = ready(db, *args, **kwargs)
    with correlation_context("publish"):
        return life(db).publish(rfq.id), row, line


def emails(db, rfq, version=None):
    like = f"rfq:{rfq.id}:v{version}:%" if version else f"rfq:{rfq.id}:%"
    return sql(
        db,
        "SELECT idempotency_key, state, provider_message_id FROM outbox "
        "WHERE topic='email' AND idempotency_key LIKE %s ORDER BY idempotency_key",
        (like,),
    )


def contacts(db, vendor_id):
    return vendors(db).list_contacts(vendor_id)


def test_every_state_and_action_pair(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _ = ready(db)
    calls = {
        "publish": lambda: life(db).publish(rfq.id),
        "amend": lambda: life(db).amend(rfq.id, RfqAmend(reason="r", terms="t")),
        "extend": lambda: life(db).extend(
            rfq.id, ExtendDeadline(deadline_at=datetime.now(UTC) + timedelta(days=9))
        ),
        "start_evaluation": lambda: life(db).start_evaluation(rfq.id),
    }
    for status in STATUSES:
        for action in ACTIONS:
            if (status, action) in TRANSITIONS:
                continue
            sql(db, "UPDATE rfq SET status=%s WHERE id=%s RETURNING 1", (status, rfq.id))
            if action == "close":  # the system action leaves any other state alone
                with correlation_context("close"):
                    sql(db, "UPDATE rfq SET deadline_at=now() WHERE id=%s RETURNING 1", (rfq.id,))
                    assert lifecycle.close_due(db[0], Scope(db[1])) == 0
                continue
            with correlation_context("pair"):
                error = refused(calls[action])
            assert error.checks["problem"] == "invalid_transition", (status, action)
    assert set(TRANSITIONS) == {
        ("draft", "publish"),
        ("published", "amend"),
        ("published", "extend"),
        ("published", "close"),
        ("closed", "start_evaluation"),
    }


def test_publish_lists_every_problem_at_once(lifecycle_db):
    db, _ = lifecycle_db
    row, line = approved(db)
    rfq = draft_rfq(db, entry(row, line, "1"))
    sql(
        db,
        "UPDATE rfq SET deadline_at=now()+interval '10 minutes' WHERE id=%s RETURNING 1",
        (rfq.id,),
    )
    with correlation_context("publish"):
        error = refused(lambda: life(db).publish(rfq.id), ErrorCode.VALIDATION_FAILED)
        assert error.checks["problem"] == "cannot_publish"
        assert {e.field for e in error.errors} == {"deadline_at", "invitees"}
        mute = vendor(db, "Mute")
        invite(db, rfq, mute)
        sql(db, "DELETE FROM vendor_contact WHERE vendor_id=%s RETURNING 1", (mute.id,))
        error = refused(lambda: life(db).publish(rfq.id), ErrorCode.VALIDATION_FAILED)
        assert {e.field for e in error.errors} == {"deadline_at", f"invitees/{mute.id}"}
    assert life(db).get(rfq.id).status == "draft" and emails(db, rfq) == []


def test_publish_needs_no_approval_and_queues_one_email_per_contact_never_sending(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, line = ready(db, names=("A", "B"))
    extra = rfq.invitees[0].vendor_id
    vendors(db).create_contact(
        extra,
        ContactCreate(
            type="billing", receives_documents=True, name="Two", email=f"{uuid4().hex}@x.test"
        ),
    )
    sql(
        db,
        "UPDATE rfq_invitee SET contact_ids=%s WHERE vendor_id=%s RETURNING 1",
        ([c.id for c in contacts(db, extra)], extra),
    )
    with correlation_context("publish"):
        done = life(db).publish(rfq.id)
    assert done.status == "published" and done.version == 1 and done.published_at is not None
    sent = emails(db, rfq, 1)
    assert len(sent) == 3  # three contacts, one outbox row each
    assert all(r[1] == "pending" and r[2] is None for r in sent)  # queued, not sent by the request
    [v1] = life(db).versions(rfq.id)
    assert v1.version == 1 and v1.reason is None and v1.diff == {}
    assert v1.snapshot["specifications"] == "Pump spec"
    assert {"rfq.publish"} <= set(audit_actions(db, rfq))
    told = {i.vendor_id: i for i in done.invitees}
    assert told[extra].latest_sent_version is None and told[extra].not_told_of == [1]
    assert {d.state for d in told[extra].deliveries} == {"queued"}


def test_amend_records_a_diff_keeps_v1_and_tells_each_contact_once(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, line = publish(db)
    with correlation_context("amend"):
        done = life(db).amend(
            rfq.id, RfqAmend(reason="Spec change", terms="Net 30", specifications="Pump spec 2")
        )
    assert done.version == 2 and done.terms == "Net 30"
    v1, v2 = life(db).versions(rfq.id)
    assert v1.snapshot["specifications"] == "Pump spec" and v1.snapshot["terms"] is None
    assert v2.reason == "Spec change"
    assert v2.diff == {
        "specifications": {"was": "Pump spec", "now": "Pump spec 2"},
        "terms": {"was": None, "now": "Net 30"},
    }
    assert len(emails(db, rfq, 2)) == 1 and len(emails(db, rfq, 1)) == 1
    for statement in ("UPDATE rfq_version SET reason='x'", "DELETE FROM rfq_version"):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            sql(db, statement + " RETURNING 1")  # a raw UPDATE of a published version raises
    error = refused(
        lambda: life(db).amend(rfq.id, RfqAmend(reason="again", terms="Net 30")),
        ErrorCode.VALIDATION_FAILED,
    )
    assert error.checks["problem"] == "no_change"
    assert life(db).get(rfq.id).version == 2


def test_amend_changes_quantities_through_the_guard_and_never_the_line_set(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, line = publish(db, qty="4")
    from flo.modules.sourcing.schemas import RfqLineIn
    from tests.sourcing.test_rfq import line_body  # noqa: F401

    def lines(qty):
        return [RfqLineIn(requisition_line_id=line, requisition_version=row.version, quantity=qty)]

    with correlation_context("amend"):
        done = life(db).amend(rfq.id, RfqAmend(reason="More", lines=lines("6")))
        assert done.lines[0].quantity == "6.0000"
        assert sql(db, "SELECT sourced_qty FROM requisition_line WHERE id=%s", (line,))[0][0] == 6
        over = refused(
            lambda: life(db).amend(rfq.id, RfqAmend(reason="Too many", lines=lines("99")))
        )
        assert over.checks["problem"] == "over_open_quantity"
        other = RfqLineIn(requisition_line_id=uuid4(), requisition_version=1, quantity="1")
        fixed = refused(
            lambda: life(db).amend(rfq.id, RfqAmend(reason="Swap", lines=[other])),
            ErrorCode.VALIDATION_FAILED,
        )
        assert fixed.checks["problem"] == "lines_fixed"
    _, v2 = life(db).versions(rfq.id)
    assert next(iter(v2.diff.values())) == {"was": "4.0000", "now": "6.0000"}


def test_extend_only_later_and_tells_invitees(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _ = publish(db)
    with correlation_context("extend"):
        earlier = refused(
            lambda: life(db).extend(
                rfq.id, ExtendDeadline(deadline_at=rfq.deadline_at - timedelta(hours=1))
            ),
            ErrorCode.VALIDATION_FAILED,
        )
        assert earlier.checks["problem"] == "deadline_earlier"
        assert life(db).get(rfq.id).version == 1 and len(emails(db, rfq)) == 1
        later = rfq.deadline_at + timedelta(days=2)
        done = life(db).extend(rfq.id, ExtendDeadline(deadline_at=later))
    assert done.version == 2 and done.deadline_at == later
    assert len(emails(db, rfq, 2)) == 1
    assert "deadline_at" in life(db).versions(rfq.id)[1].diff


def test_close_job_closes_once_then_evaluation_starts(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _ = publish(db)
    assert lifecycle.close_due(db[0], Scope(db[1])) == 0  # deadline in the future
    sql(
        db,
        "UPDATE rfq SET deadline_at=now() - interval '1 minute' WHERE id=%s RETURNING 1",
        (rfq.id,),
    )
    with correlation_context("tick"):
        assert lifecycle.close_due(db[0], Scope(db[1])) == 1
        assert lifecycle.close_due(db[0], Scope(db[1])) == 0  # twice changes nothing more
    closed = life(db).get(rfq.id)
    assert closed.status == "closed" and closed.version == 1
    assert [a for a in audit_actions(db, rfq) if a == "rfq.close"] == ["rfq.close"]
    with correlation_context("evaluate"):
        assert life(db).start_evaluation(rfq.id).status == "under_evaluation"


def test_a_deadline_that_has_passed_refuses_amendment_before_the_job_runs(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _ = publish(db)
    sql(
        db,
        "UPDATE rfq SET deadline_at=now() - interval '1 minute' WHERE id=%s RETURNING 1",
        (rfq.id,),
    )
    with correlation_context("late"):
        error = refused(lambda: life(db).amend(rfq.id, RfqAmend(reason="late", terms="x")))
    assert error.checks["problem"] == "deadline_passed"


def test_the_tick_queues_a_close_job_only_for_an_org_with_a_due_rfq(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _ = publish(db)
    with correlation_context("tick"):
        assert lifecycle.enqueue_due(db[0]) == 0
        sql(
            db,
            "UPDATE rfq SET deadline_at=now() - interval '1 minute' WHERE id=%s RETURNING 1",
            (rfq.id,),
        )
        assert lifecycle.enqueue_due(db[0]) == 1
    assert sql(db, "SELECT kind FROM job") == [("rfq-close",)]


def test_requisition_is_sourced_only_when_every_line_is_on_a_published_rfq(lifecycle_db):
    db, _ = lifecycle_db
    row, line = approved(db, qty="10")
    deadline = datetime.now(UTC) + timedelta(days=3)

    def make(qty):
        rfq = draft_rfq(db, entry(row, line, qty))
        life(db).update_content(
            rfq.id, RfqContentPatch(deadline_at=deadline), str(rfq.edit_revision)
        )
        invite(db, life(db).get(rfq.id), vendor(db, f"V{uuid4().hex[:4]}"))
        return rfq

    first, second = make("4"), make("6")
    status = lambda: sql(db, "SELECT status FROM requisition WHERE id=%s", (row.id,))[0][0]  # noqa: E731
    with correlation_context("publish"):
        life(db).publish(first.id)
        assert status() == "sourcing_in_progress"  # the other 6 are on a draft RFQ
        life(db).publish(second.id)
    assert status() == "sourced"
    assert "requisition.transition" in [
        r[0] for r in sql(db, "SELECT action FROM audit_log WHERE target_id=%s", (row.id,))
    ]


def test_a_requisition_with_open_quantity_stays_in_sourcing(lifecycle_db):
    db, _ = lifecycle_db
    rfq, row, line = publish(db, qty="4")  # 6 of 10 still open
    assert sql(db, "SELECT status FROM requisition WHERE id=%s", (row.id,))[0][0] == (
        "sourcing_in_progress"
    )


def test_a_bounced_contact_is_changed_and_the_same_version_is_sent_again(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _ = publish(db)
    vendor_id = rfq.invitees[0].vendor_id
    [old] = contacts(db, vendor_id)
    [first] = emails(db, rfq, 1)
    sql(db, "UPDATE outbox SET state='dead' WHERE idempotency_key=%s RETURNING 1", (first[0],))
    told = life(db).get(rfq.id).invitees[0]
    assert told.not_told_of == [1] and told.deliveries[0].state == "failed"
    fresh = vendors(db).create_contact(
        vendor_id,
        ContactCreate(
            type="billing", receives_documents=True, name="New", email=f"{uuid4().hex}@x.test"
        ),
    )
    with correlation_context("resend"):
        life(db).update_invitee(
            rfq.id,
            vendor_id,
            InviteePatch(contact_ids=[old.id, fresh.id]),
            str(life(db).get(rfq.id).edit_revision),
        )
        again = life(db).resend(rfq.id, vendor_id)
        assert len(emails(db, rfq, 1)) == 3  # the dead one, a resend to it, one for the new contact
        assert len(again.invitees[0].deliveries) == 3
        error = refused(lambda: life(db).resend(rfq.id, vendor_id))
        assert error.checks["problem"] == "nothing_to_resend"
    sql(
        db,
        "UPDATE outbox SET state='sent', provider_message_id='m', sent_at=now() "
        "WHERE idempotency_key LIKE %s RETURNING 1",
        (f"rfq:{rfq.id}:%",),
    )
    done = life(db).get(rfq.id).invitees[0]
    assert done.not_told_of == [] and done.latest_sent_version == 1


def test_lifecycle_routes_replay_amend_and_publish_through_the_real_middleware(lifecycle_db):
    db, _ = lifecycle_db
    rfq, _, _ = ready(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            published = await send(browser, "POST", f"/rfqs/{rfq.id}/publish")
            assert published.status_code == 200, published.text
            assert published.json()["status"] == "published"
            key = headers(browser, "amend-key")
            body = {"reason": "Spec", "terms": "Net 45"}
            first = await browser.post(
                f"/api/v1/sourcing/rfqs/{rfq.id}/amend", json=body, headers=key
            )
            second = await browser.post(
                f"/api/v1/sourcing/rfqs/{rfq.id}/amend", json=body, headers=key
            )
            assert first.status_code == second.status_code == 200, first.text
            assert first.json()["version"] == second.json()["version"] == 2
            listed = await send(browser, "GET", f"/rfqs/{rfq.id}/versions")
            assert [v["version"] for v in listed.json()] == [1, 2]
            late = await send(
                browser,
                "POST",
                f"/rfqs/{rfq.id}/extend-deadline",
                {"deadline_at": (rfq.deadline_at - timedelta(days=1)).isoformat()},
            )
            assert (
                late.status_code == 422 and late.json()["checks"]["problem"] == "deadline_earlier"
            )
            again = await send(browser, "POST", f"/rfqs/{rfq.id}/publish")
            assert again.status_code == 409
            assert again.json()["checks"]["problem"] == "invalid_transition"

    asyncio.run(scenario())
    assert len(emails(db, rfq, 2)) == 1  # the retried amend made no second version or email


@pytest.mark.parametrize(
    ("permissions", "status"),
    [(["rfq.view"], 403), (["rfq.create", "rfq.edit", "rfq.view"], 200)],
)
def test_only_editors_publish_and_viewers_read_versions(lifecycle_db, permissions, status):
    db, _ = lifecycle_db
    rfq, _, _ = ready(db)
    regrant(db, permissions)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            return (
                await send(browser, "POST", f"/rfqs/{rfq.id}/publish"),
                await send(browser, "GET", f"/rfqs/{rfq.id}/versions"),
            )

    attempt, read = asyncio.run(scenario())
    assert attempt.status_code == status
    assert read.status_code == 200
    assert life(db).get(rfq.id).status == ("published" if status == 200 else "draft")


def test_lifecycle_routes_conceal_foreign_tenants(lifecycle_db):
    db, _ = lifecycle_db
    foreign_unit = unit(db, "FBU", org=db[2])
    foreign_rfq = uuid4()
    owner = db[0].execute("SELECT id FROM identity WHERE email='second@example.test'").fetchone()[0]
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (foreign_rfq, db[2], foreign_unit.id, owner),
        )
    other = vendors(db, db[2])
    mine, _, _ = publish(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for method, suffix, data in [
                ("POST", f"/rfqs/{foreign_rfq}/publish", None),
                ("POST", f"/rfqs/{foreign_rfq}/amend", {"reason": "x", "terms": "y"}),
                (
                    "POST",
                    f"/rfqs/{foreign_rfq}/extend-deadline",
                    {"deadline_at": "2099-01-01T00:00:00Z"},
                ),
                ("POST", f"/rfqs/{foreign_rfq}/start-evaluation", None),
                ("POST", f"/rfqs/{foreign_rfq}/invitees/{uuid4()}/resend", None),
                ("GET", f"/rfqs/{foreign_rfq}/versions", None),
                ("POST", f"/rfqs/{mine.id}/invitees/{uuid4()}/resend", None),
            ]:
                response = await send(browser, method, suffix, data)
                assert response.status_code == 404, (method, suffix, response.text)

    assert other  # a foreign tenant's vendor id is just an unknown id here
    asyncio.run(scenario())
    assert sql(db, "SELECT status FROM rfq WHERE id=%s", (mine.id,))[0][0] == "published"
