"""Email transmission of an issued PO through the outbox (E13-S09)."""

import asyncio
import base64
import hashlib
from uuid import uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode
from flo.kernel.logging import correlation_context
from flo.kernel.outbox.dispatcher import OutboxDispatcher, email_handler
from flo.kernel.ports.email import PermanentEmailError, TransientEmailError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.purchasing import issue as issue_module
from flo.modules.purchasing import transmission
from flo.modules.purchasing.pdf import pdf_for
from tests.isolation.test_real_sessions import client, login
from tests.purchasing.test_changes import decide_change, line, open_change, publish_change, submit
from tests.purchasing.test_issue import approve, ledger, run
from tests.purchasing.test_purchase_orders import _race, create, pos, ready, send
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import refused, sql
from tests.vendors.test_vendors import regrant


class Sender:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def send(self, to, template, context, idempotency_key, attachments=()):
        self.calls.append((to, idempotency_key, tuple(attachments)))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome:
            raise outcome
        return f"msg-{len(self.calls)}"


def dispatch(db, sender):
    dispatcher = OutboxDispatcher(
        db[0], {"email": email_handler(sender)}, random_fraction=lambda: 0
    )
    return dispatcher.run()


def make_due(db):
    sql(db, "UPDATE outbox SET run_after = now() WHERE state = 'failed' RETURNING 1")


def new_po(db, second=False):
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "4")
    approve(db, po)
    dispatch(db, Sender())  # the RFQ invitations are not under test
    return po


def emails(db):
    return sql(
        db, "SELECT count(*) FROM outbox WHERE left(idempotency_key, 16) = 'po_transmission:'"
    )


def issued_po(db):
    po = new_po(db)
    run(db, po)
    return po


def vendor_of(db):
    return sql(db, "SELECT vendor_id FROM purchase_order LIMIT 1")[0][0]


def contact(db, name, email):
    """A document contact; billing and service_delivery are one per vendor, so this is `other`."""
    with correlation_context("contact"):
        id = uuid4()
        sql(
            db,
            "INSERT INTO vendor_contact (id, org_id, vendor_id, type, name, email, "
            "receives_documents) VALUES (%s, %s, %s, %s, %s, %s, true) RETURNING 1",
            (id, db[1], vendor_of(db), f"other{uuid4().hex[:6]}", name, email),
        )
        return type("C", (), {"id": id, "email": email})


def rows(db, po):
    return sql(
        db,
        "SELECT attempt, recipient_email, outbox_id, result FROM po_transmission "
        "WHERE po_id = %s ORDER BY attempt, recipient_email",
        (po.id,),
    )


def states(db, po):
    return [
        (r.attempt, r.result)
        for r in transmission.list_transmissions(db[0], Scope(db[1]), po.id)[::-1]
    ]


def resend(db, po, contact_ids=None, connection=None):
    with correlation_context("resend"):
        return transmission.resend(connection or db[0], Scope(db[1]), db[3], po.id, contact_ids)


def kill(db):
    """Dead outbox rows: the provider refuses permanently."""
    dispatch(db, Sender(*[PermanentEmailError("no")] * 10))


def test_issue_enqueues_one_email_and_one_row_per_recipient_with_the_pdf(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    [(attempt, email, outbox_id, result)] = rows(db, po)
    assert (attempt, result) == (1, None) and outbox_id is not None
    [(payload,)] = sql(db, "SELECT payload FROM outbox WHERE id = %s", (outbox_id,))
    [attachment] = payload["attachments"]
    data = base64.b64decode(attachment["content_b64"])
    assert attachment["content_type"] == "application/pdf" and data == pdf_for(
        db[0], Scope(db[1]), po.id
    )
    [(sha,)] = sql(db, "SELECT pdf_sha256 FROM po_version")
    assert hashlib.sha256(data).hexdigest() == sha
    assert payload["to"] == email and payload["template"] == "po-issued-v1"
    assert states(db, po) == [(1, "queued")]
    retry = run(db, po, "1")  # the issue retry adds nothing
    assert retry.status == "issued" and len(rows(db, po)) == 1
    assert emails(db) == [(1,)]


def test_a_failing_requester_notice_rolls_back_the_issue_and_the_send(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    po = new_po(db)

    def boom(*_):
        raise RuntimeError("notice failed")

    monkeypatch.setattr(issue_module, "notify_requester_issued", boom)
    with pytest.raises(RuntimeError):
        run(db, po)
    assert emails(db) == [(0,)]
    assert sql(db, "SELECT count(*) FROM po_transmission") == [(0,)]
    assert sql(db, "SELECT status FROM purchase_order") == [("approved",)]


def test_issue_refuses_a_po_with_no_recipient_until_a_contact_receives_documents(lifecycle_db):
    db, _ = lifecycle_db
    po = new_po(db)
    sql(db, "UPDATE purchase_order SET recipients = '[]' RETURNING 1")
    sql(db, "UPDATE vendor_contact SET receives_documents = false RETURNING 1")
    before = ledger(db)
    error = refused(lambda: run(db, po))
    assert error.checks["problem"] == "no_recipients"
    assert ledger(db) == before and sql(db, "SELECT status, version FROM purchase_order") == [
        ("approved", 1)
    ]
    assert emails(db) == [(0,)]
    assert sql(db, "SELECT count(*) FROM po_transmission") == [(0,)]
    added = contact(db, "Dock", "dock@example.com")
    assert run(db, po).status == "issued"  # plant: no refresh of the empty list -> 409 again
    assert sql(db, "SELECT recipients FROM purchase_order") == [
        ([{"name": "Dock", "email": "dock@example.com"}],)
    ]
    assert [r[1] for r in rows(db, po)] == [added.email]


def test_a_po_with_its_own_list_issues_to_it_whatever_the_vendor_contacts_become(lifecycle_db):
    db, _ = lifecycle_db
    po = new_po(db)
    [(recipients,)] = sql(db, "SELECT recipients FROM purchase_order")
    contact(db, "Late", "late@example.com")
    sql(db, "UPDATE vendor_contact SET email = 'moved@example.com' RETURNING 1")
    run(db, po)
    assert [r[1] for r in rows(db, po)] == [recipients[0]["email"]]


def test_enqueue_twice_is_a_unique_violation_and_empty_recipients_refuse(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(db[0], Scope(db[1])):
        transmission.enqueue(db[0], Scope(db[1]), po.id, 2)  # plant: no unique index -> no raise


def test_enqueue_with_no_recipient_refuses(lifecycle_db):
    db, _ = lifecycle_db
    po = new_po(db)
    sql(db, "UPDATE purchase_order SET recipients = '[]' RETURNING 1")
    with tenant_transaction(db[0], Scope(db[1])):
        error = refused(lambda: transmission.enqueue(db[0], Scope(db[1]), po.id, 2))
    assert error.checks["problem"] == "no_recipients"


def test_dispatcher_retry_keeps_the_key_and_the_attachment_and_reads_sent(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    sender = Sender(TransientEmailError("later"))
    assert dispatch(db, sender).retried == 1
    assert states(db, po) == [(1, "queued")]
    make_due(db)
    assert dispatch(db, sender).sent == 1
    assert len(sender.calls) == 2 and sender.calls[0][1] == sender.calls[1][1]
    assert [len(c[2]) for c in sender.calls] == [1, 1]
    assert emails(db) == [(1,)]
    [t] = transmission.list_transmissions(db[0], Scope(db[1]), po.id)
    assert t.result == "sent" and t.provider_message_id == "msg-2" and t.result_at is not None


def test_read_states_follow_the_outbox_and_a_provider_result_wins(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    assert states(db, po) == [(1, "queued")]
    kill(db)
    assert states(db, po) == [(1, "failed")]  # plant: dead -> sent fails here
    sql(db, "UPDATE outbox SET state='sent', provider_message_id='m', sent_at=now() RETURNING 1")
    assert states(db, po) == [(1, "sent")]
    sql(db, "UPDATE po_transmission SET result='bounced', result_at=now() RETURNING 1")
    assert states(db, po) == [(1, "bounced")]


def test_a_suppressed_address_writes_a_row_and_no_email(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    monkeypatch.setattr(transmission, "_suppressed", lambda *_: True)
    po = issued_po(db)
    [(_, _, outbox_id, result)] = rows(db, po)
    assert (outbox_id, result) == (None, "address_suppressed")
    assert emails(db) == [(0,)]


def test_resend_after_a_dead_send_repeats_the_email_and_changes_nothing_else(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    kill(db)
    ledger_before = ledger(db)
    po_before = sql(db, "SELECT to_jsonb(p) FROM purchase_order p")
    result = resend(db, po)
    assert [(t.attempt, t.version, t.result) for t in result] == [
        (2, 2, "queued"),
        (1, 2, "failed"),
    ]
    assert emails(db) == [(2,)]
    assert ledger(db) == ledger_before
    assert sql(db, "SELECT to_jsonb(p) FROM purchase_order p") == po_before
    audit = "SELECT count(*) FROM audit_log WHERE action='purchase_order.transmission.resend'"
    assert sql(db, audit) == [(1,)]
    assert refused(lambda: resend(db, po)).checks["problem"] == "transmission_queued"


def test_resend_with_contacts_and_its_refusals(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    kill(db)
    mine = contact(db, "Dock", "dock@example.com")
    assert [t.recipient_email for t in resend(db, po, [mine.id])][0] == "dock@example.com"
    kill(db)
    foreign = refused(lambda: resend(db, po, [uuid4()]), ErrorCode.VALIDATION_FAILED)
    assert foreign.checks["problem"] == "unknown_contact"
    sql(db, "DELETE FROM po_transmission RETURNING 1")
    assert refused(lambda: resend(db, po)).checks["problem"] == "nothing_to_resend"
    sql(db, "UPDATE purchase_order SET status = 'closed', version = version + 1 RETURNING 1")
    assert refused(lambda: resend(db, po)).checks["problem"] == "not_resendable"


def test_two_contacts_sharing_an_email_get_one_send(lifecycle_db):
    db, _ = lifecycle_db
    po = new_po(db)
    sql(db, "UPDATE purchase_order SET recipients = '[]' RETURNING 1")
    sql(db, "UPDATE vendor_contact SET receives_documents = false RETURNING 1")
    first = contact(db, "One", "same@example.com")
    second = contact(db, "Two", "SAME@example.com")
    run(db, po)
    assert len(rows(db, po)) == 1  # plant: no dedupe -> UniqueViolation
    kill(db)
    resend(db, po, [first.id, second.id])
    assert len(rows(db, po)) == 2 and emails(db) == [(2,)]


def test_two_simultaneous_resends_make_one_set_of_rows(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    kill(db)

    def work(conn, _):
        resend(db, po, connection=conn)
        return "ok"

    assert _race(db, work) == ["ok", "transmission_queued"]  # plant: no FOR UPDATE -> two ok
    assert sql(db, "SELECT count(*) FROM po_transmission WHERE attempt = 2") == [(1,)]


def test_a_revision_goes_to_the_latest_recipients_and_the_log_lists_both(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    kill(db)
    chosen = contact(db, "Dock", "dock@example.com")
    resend(db, po, [chosen.id])
    publish_change(db)
    change = submit(db, open_change(db, pos(db).get(po.id), lines=[line(unit_price="77.5")]))
    decide_change(db, change)
    [(new_id, version)] = sql(db, "SELECT id, version FROM purchase_order WHERE revision = 1")
    assert version == 2
    new = sql(
        db,
        "SELECT attempt, version, recipient_email FROM po_transmission WHERE po_id = %s",
        (new_id,),
    )
    assert new == [(1, 2, "dock@example.com")]
    log = transmission.list_transmissions(db[0], Scope(db[1]), po.id)
    assert [(t.revision, t.attempt) for t in log] == [(1, 1), (0, 2), (0, 1)]
    assert {t.template_version for t in log} == {"po-issued-v1"}


def test_attention_counts_cover_the_window_only(lifecycle_db):
    db, _ = lifecycle_db
    issued_po(db)
    kill(db)

    def clone(attempt, email, result, outbox, age):
        sql(
            db,
            "INSERT INTO po_transmission (org_id, id, po_id, version, attempt, recipient_name, "
            "recipient_email, template_version, outbox_id, result, created_by, created_at) "
            "SELECT org_id, gen_random_uuid(), po_id, version, %s, 'n', %s, 't', "
            "CASE WHEN %s THEN outbox_id END, %s, created_by, now() - %s * interval '1 day' "
            "FROM po_transmission WHERE attempt = 1 RETURNING 1",
            (attempt, email, outbox, result, age),
        )

    clone(2, "b@example.com", "bounced", True, 0)
    clone(3, "s@example.com", "address_suppressed", False, 0)
    clone(4, "old@example.com", "bounced", True, 8)
    assert transmission.attention_counts(db[0], Scope(db[1])) == {
        "failed": 1,
        "bounced": 1,
        "address_suppressed": 1,
    }


def test_no_email_leaves_a_request_handler(lifecycle_db, monkeypatch):
    from flo.kernel.adapters import resend as resend_adapter
    from flo.kernel.adapters import smtp

    called = []
    for cls in (resend_adapter.ResendEmailSender, smtp.SmtpEmailSender):
        monkeypatch.setattr(cls, "send", lambda *a, **k: called.append(1))
    db, _ = lifecycle_db
    po = issued_po(db)
    kill(db)
    key = {"Idempotency-Key": "resend-once"}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["purchase_order.view", "purchase_order.issue"])
            first = await send(browser, "POST", f"/{po.id}/transmissions/resend", {}, key)
            assert first.status_code == 200 and first.json()[0]["result"] == "queued"
            replay = await send(browser, "POST", f"/{po.id}/transmissions/resend", {}, key)
            assert replay.json() == first.json()
            empty = await send(
                browser, "POST", f"/{po.id}/transmissions/resend", {"contact_ids": []},
                {"Idempotency-Key": "k2"},
            )  # fmt: skip
            assert empty.status_code == 422

    asyncio.run(scenario())
    assert called == [] and emails(db) == [(2,)]


def foreign_po(db):
    from tests.requisitions.test_header import unit

    vendor, requisition = sql(db, "SELECT vendor_id, requisition_id FROM purchase_order")[0]
    fu, rfq, award, po = unit(db, "FBU", org=db[2]), uuid4(), uuid4(), uuid4()
    with tenant_transaction(db[0], Scope(db[2])):  # real rows, owned by the other tenant
        db[0].execute(
            "INSERT INTO rfq (id,org_id,number,title,unit_id,currency,owner_id) "
            "VALUES (%s,%s,'RFQ-X','Foreign',%s,'USD',%s)",
            (rfq, db[2], fu.id, db[3]),
        )
        db[0].execute(
            "INSERT INTO award (id,org_id,number,rfq_id,basis,status,rates,created_by) "
            "VALUES (%s,%s,'AWD-X',%s,'manual','approved','{}',%s)",
            (award, db[2], rfq, db[3]),
        )
        db[0].execute(
            "INSERT INTO purchase_order (id,org_id,number,unit_id,vendor_id,requisition_id,"
            "vendor_snapshot,award_id,currency,rate,bill_to,recipients,requester_id,created_by,"
            "requisition_version) "
            "VALUES (%s,%s,'PO-X',%s,%s,%s,'{}',%s,'USD','{}','{}','[]',%s,%s,1)",
            (po, db[2], fu.id, vendor, requisition, award, db[3], db[3]),
        )
    return po


def test_transmission_routes_conceal_foreign_tenants(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    other = foreign_po(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["rfq.view"])
            assert (await send(browser, "GET", f"/{po.id}/transmissions")).status_code == 403
            body = {"Idempotency-Key": "a"}
            assert (
                await send(browser, "POST", f"/{po.id}/transmissions/resend", {}, body)
            ).status_code == 403
            regrant(db, ["purchase_order.view", "purchase_order.issue"])
            got = await send(browser, "GET", f"/{po.id}/transmissions")
            assert got.status_code == 200 and got.json()[0]["result"] == "queued"
            for n, missing in enumerate((other, uuid4())):
                assert (await send(browser, "GET", f"/{missing}/transmissions")).status_code == 404
                gone = await send(
                    browser, "POST", f"/{missing}/transmissions/resend", {},
                    {"Idempotency-Key": f"b{n}"},
                )  # fmt: skip
                assert gone.status_code == 404

    asyncio.run(scenario())
