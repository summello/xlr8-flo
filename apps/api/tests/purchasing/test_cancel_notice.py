"""The vendor's cancellation email goes through the outbox (E13-S24, PO-013, PO-015, ARCH-006)."""

import asyncio
import json

import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.outbox.store import OutboxStore
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.purchasing import transmission
from flo.modules.purchasing.cancel import cancel
from flo.modules.purchasing.schemas import PoCancelCreate
from tests.isolation.test_real_sessions import client, login
from tests.purchasing.test_cancel import cancel_po
from tests.purchasing.test_purchase_orders import _race, create, ready, send
from tests.purchasing.test_transmission import (
    Sender,
    contact,
    dispatch,
    issued_po,
    kill,
    new_po,
    resend,
    rows,
)
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import sql
from tests.vendors.test_vendors import regrant

NOTICE = "po-cancelled-v1"


def notice_rows(db, po):
    return sql(
        db,
        "SELECT attempt, version, recipient_email, created_by, outbox_id FROM po_transmission "
        "WHERE po_id = %s AND template_version = %s ORDER BY recipient_email",
        (po.id, NOTICE),
    )


def cancel_audit(db, po):
    return sql(
        db,
        "SELECT after FROM audit_log WHERE action = 'purchase_order.cancel' AND target_id = %s",
        (po.id,),
    )


def counts(db):
    return sql(db, "SELECT (SELECT count(*) FROM po_transmission), (SELECT count(*) FROM outbox)")


def cancelled_with_dead_notice(db):
    po = issued_po(db)
    cancel_po(db, po)
    kill(db)
    return po


def test_cancel_notifies_every_recipient_in_the_cancel_transaction(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    [(_, issue_email, _, _)] = rows(db, po)
    other = contact(db, "Second", "second@example.com")
    dup = contact(db, "Dup", issue_email.upper())
    kill(db)
    resend(db, po, [other.id, dup.id])  # attempt 2 carries both, and one is the issue row's email
    before = sql(db, "SELECT count(*) FROM po_transmission WHERE po_id = %s", (po.id,))
    cancel_po(db, po)
    version = sql(db, "SELECT version FROM purchase_order WHERE id = %s", (po.id,))[0][0]
    got = notice_rows(db, po)
    assert [(r[0], r[1], r[2].lower(), r[3]) for r in got] == [
        (3, version, issue_email.lower(), db[3]),
        (3, version, "second@example.com", db[3]),
    ]
    assert all(r[4] for r in got) and len(got) == 2  # one row per distinct email, with an outbox
    assert sql(db, "SELECT count(*) FROM po_transmission WHERE po_id = %s", (po.id,))[0][0] == (
        before[0][0] + 2
    )
    assert sql(db, "SELECT count(*) FROM outbox WHERE payload->>'template' = %s", (NOTICE,)) == [
        (2,)
    ]
    assert cancel_audit(db, po) == [({"status": "cancelled"},)]


def test_the_notice_carries_no_reason_and_no_pdf(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    po = issued_po(db)
    monkeypatch.setattr(transmission, "pdf_for", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    body = PoCancelCreate(reason="REASONLEAK4", cancel_requisition_lines=False, notify_vendor=True)
    version = sql(db, "SELECT version FROM purchase_order WHERE id = %s", (po.id,))[0][0]
    with correlation_context("cancel"):
        cancel(db[0], Scope(db[1]), po.id, db[3], f'"{version}"', body)
    [(payload,)] = sql(db, "SELECT payload FROM outbox WHERE payload->>'template' = %s", (NOTICE,))
    assert set(payload["context"]) == {"po_number", "revision", "vendor_name"}
    assert "REASONLEAK4" not in json.dumps(payload) and "attachments" not in payload


@pytest.mark.parametrize("step", ["flag", "draft", "approved"])
def test_no_notice_unless_an_issued_po_is_cancelled_with_notify(lifecycle_db, step):
    db, _ = lifecycle_db
    if step == "flag":
        po = issued_po(db)
        before = counts(db)
        cancel_po(db, po, notify=False)
    else:
        if step == "approved":
            po = new_po(db)
        else:
            rfq, _, a, _, award_id, _ = ready(db)
            po = create(db, rfq, a, award_id, "4")
        before = counts(db)
        cancel_po(db, po)
    assert counts(db) == before and cancel_audit(db, po) == [({"status": "cancelled"},)]


def test_a_cancel_with_nobody_to_tell_still_releases_once(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    po = issued_po(db)
    before = counts(db)
    monkeypatch.setattr(transmission, "cancellation_recipients", lambda *a: [])
    cancel_po(db, po)
    assert counts(db) == before
    assert sql(db, "SELECT status FROM purchase_order WHERE id = %s", (po.id,)) == [("cancelled",)]
    assert sql(
        db,
        "SELECT count(*) FROM ledger_entry WHERE entry_type = 'release' "
        "AND idempotency_key LIKE %s",
        (f"po:{po.id}:cancel:%",),
    ) == [(1,)]
    assert cancel_audit(db, po) == [({"status": "cancelled", "vendor_notified": False},)]
    monkeypatch.undo()
    with pytest.raises(ProblemError) as error:
        resend(db, po)
    assert error.value.checks["problem"] == "nothing_to_resend"


def test_a_retry_adds_no_second_notice(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    first = cancel_po(db, po)
    n = len(notice_rows(db, po))
    version = sql(db, "SELECT version FROM purchase_order WHERE id = %s", (po.id,))[0][0]
    cancel_po(db, po, version=version)  # the new If-Match of the cancelled row
    cancel_po(db, po, version=version - 1)  # the old one
    assert first.status == "cancelled" and len(notice_rows(db, po)) == n == 1


def test_the_notice_goes_to_the_contacts_chosen_at_resend(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    chosen = contact(db, "X", "x@example.com")
    kill(db)
    resend(db, po, [chosen.id])
    cancel_po(db, po)
    assert [r[2] for r in notice_rows(db, po)] == ["x@example.com"]


def test_suppressed_addresses_get_a_row_and_no_email(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    po = issued_po(db)
    monkeypatch.setattr(transmission, "_suppressed", lambda *a: True)
    cancel_po(db, po)
    assert [(r[4]) for r in notice_rows(db, po)] == [None]
    assert sql(db, "SELECT result FROM po_transmission WHERE template_version = %s", (NOTICE,)) == [
        ("address_suppressed",)
    ]


def test_an_outbox_failure_rolls_the_whole_cancel_back(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    po = issued_po(db)
    before = (
        counts(db),
        sql(db, "SELECT status, version FROM purchase_order WHERE id = %s", (po.id,)),
    )
    ledger = sql(db, "SELECT count(*) FROM ledger_entry")

    def boom(*a, **k):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(OutboxStore, "add_email", boom)
    with pytest.raises(RuntimeError):
        cancel_po(db, po)
    monkeypatch.undo()
    assert (
        counts(db),
        sql(db, "SELECT status, version FROM purchase_order WHERE id = %s", (po.id,)),
    ) == before
    assert sql(db, "SELECT count(*) FROM ledger_entry") == ledger and cancel_audit(db, po) == []


def test_the_dispatcher_sends_the_notice_not_the_handler(lifecycle_db, monkeypatch):
    from flo.kernel.adapters import resend as resend_adapter
    from flo.kernel.adapters import smtp

    called = []
    for cls in (resend_adapter.ResendEmailSender, smtp.SmtpEmailSender):
        monkeypatch.setattr(cls, "send", lambda *a, **k: called.append(1))
    db, _ = lifecycle_db
    po = issued_po(db)
    dispatch(db, Sender())  # the issue email

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["purchase_order.view", "purchase_order.amend"])
            version = sql(db, "SELECT version FROM purchase_order WHERE id = %s", (po.id,))[0][0]
            r = await send(
                browser, "POST", f"/{po.id}/cancel", {"reason": "x", "notify_vendor": True},
                {"Idempotency-Key": "c1", "If-Match": f'"{version}"'},
            )  # fmt: skip
            assert r.status_code == 200

    asyncio.run(scenario())
    assert called == []
    seen = []

    class Recorder(Sender):
        def send(self, to, template, context, idempotency_key, attachments=()):
            seen.append((template, idempotency_key))
            return super().send(to, template, context, idempotency_key, attachments)

    dispatch(db, Recorder())
    [(row_id,)] = sql(db, "SELECT id FROM po_transmission WHERE template_version = %s", (NOTICE,))
    assert seen == [(NOTICE, f"po_transmission:{row_id}")]
    assert transmission.list_transmissions(db[0], Scope(db[1]), po.id)[0].result == "sent"


def test_resend_of_a_dead_notice_sends_another_without_the_pdf(lifecycle_db):
    db, _ = lifecycle_db
    po = cancelled_with_dead_notice(db)
    state = sql(db, "SELECT status, version FROM purchase_order WHERE id = %s", (po.id,))
    ledger = sql(db, "SELECT count(*) FROM ledger_entry")
    resend(db, po)
    got = notice_rows(db, po)
    assert [(r[0], r[1]) for r in got][-1] == (3, state[0][1])
    [(payload,)] = sql(db, "SELECT payload FROM outbox WHERE id = %s", (got[-1][4],))
    assert payload["template"] == NOTICE and "attachments" not in payload
    assert sql(db, "SELECT status, version FROM purchase_order WHERE id = %s", (po.id,)) == state
    assert sql(db, "SELECT count(*) FROM ledger_entry") == ledger
    assert sql(
        db,
        "SELECT after->>'template' FROM audit_log "
        "WHERE action = 'purchase_order.transmission.resend' AND target_id = %s",
        (po.id,),
    ) == [(NOTICE,)]
    assert transmission.list_transmissions(db[0], Scope(db[1]), po.id)[0].template_version == NOTICE


def test_resend_of_a_cancelled_po_looks_at_notice_rows_only(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)  # the issue email stays queued
    cancel_po(db, po)
    kill(db)
    # the issue row is queued again, the notice row is dead: the resend goes through
    sql(
        db,
        "UPDATE outbox SET state = 'pending' "
        "WHERE payload->>'template' = 'po-issued-v1' RETURNING 1",
    )
    resend(db, po)
    # now the notice is queued: refused
    with pytest.raises(ProblemError) as error:
        resend(db, po)
    assert error.value.checks["problem"] == "transmission_queued"


def test_resend_refuses_what_has_no_notice_to_repeat(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    cancel_po(db, po, notify=False)
    with pytest.raises(ProblemError) as error:
        resend(db, po)
    assert error.value.checks["problem"] == "nothing_to_resend"
    with tenant_transaction(db[0], Scope(db[1])):  # a terminal status is frozen (E13-S26): force it
        db[0].execute("SET LOCAL session_replication_role = replica")
        db[0].execute("UPDATE purchase_order SET status = 'superseded' WHERE id = %s", (po.id,))
    with pytest.raises(ProblemError) as error:
        resend(db, po)
    assert error.value.checks["problem"] == "not_resendable"


def test_two_resends_of_a_cancelled_po_send_once(lifecycle_db):
    db, _ = lifecycle_db
    po = cancelled_with_dead_notice(db)
    outcome = _race(db, lambda conn, n: len(resend(db, po, connection=conn)) and "ok")
    assert outcome == ["ok", "transmission_queued"]
    assert len(notice_rows(db, po)) == 2


def test_cancelled_resend_is_gated_and_concealed(lifecycle_db):
    db, _ = lifecycle_db
    po = cancelled_with_dead_notice(db)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["purchase_order.view"])
            no = await send(
                browser, "POST", f"/{po.id}/transmissions/resend", {}, {"Idempotency-Key": "n"}
            )
            assert no.status_code == 403
            regrant(db, ["purchase_order.view", "purchase_order.issue"])
            got = await send(browser, "GET", f"/{po.id}/transmissions")
            assert [r["template_version"] for r in got.json()][0] == NOTICE

    asyncio.run(scenario())
