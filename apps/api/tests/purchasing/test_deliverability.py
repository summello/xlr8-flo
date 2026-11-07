"""Deliverability: domain check and gate, webhook, transitions, suppression (E13-S10)."""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import time
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql as sql_
from pydantic import SecretStr

from flo.api import internal
from flo.api.health import app
from flo.api.internal import get_internal_settings
from flo.api.origin_auth import ORIGIN_SECRET_HEADER, get_origin_settings
from flo.kernel.adapters.resend import ResendDomainChecker, verify_svix
from flo.kernel.config import Settings
from flo.kernel.email import DOMAIN_NOT_VERIFIED, latest_domain_status, po_domain_gate
from flo.kernel.logging import correlation_context
from flo.kernel.outbox.dispatcher import OutboxDispatcher, email_handler
from flo.kernel.outbox.store import WorkerOutboxStore, email_route
from flo.kernel.ports.email import DomainStatus, PermanentEmailError, TransientEmailError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.purchasing import transmission
from flo.modules.purchasing.transmission import (
    BOUNCED_REASON,
    COMPLAINED_REASON,
    FAILED_REASON,
    SUPPRESSED_REASON,
    apply_email_event,
)
from flo.modules.vendors.service import VendorService
from tests.isolation.test_real_sessions import client, headers, login
from tests.purchasing.test_transmission import (
    Sender,
    contact,
    dispatch,
    issued_po,
    kill,
    resend,
)
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lifecycle import sql
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import sql_org
from tests.vendors.test_vendors import regrant

SECRET = "whsec_" + base64.b64encode(b"A" * 32).decode()
ORIGIN = "o" * 40
PASS = DomainStatus("pass", "pass", "")


def sign(body: bytes, svix_id: str, ts: int | None = None, secret: str = SECRET) -> dict[str, str]:
    stamp = str(int(time.time()) if ts is None else ts)
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{svix_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    return {
        "svix-id": svix_id,
        "svix-timestamp": stamp,
        "svix-signature": "v1," + base64.b64encode(mac).decode(),
    }


def event(type_: str, message_id: str, bounce: str | None = None, **extra) -> bytes:
    data: dict[str, object] = {"email_id": message_id, **extra}
    if bounce:
        data["bounce"] = {"type": bounce}
    return json.dumps({"type": type_, "created_at": "2026-11-07T00:00:00Z", "data": data}).encode()


@pytest.fixture
def stack(lifecycle_db):
    """The production dependency stack with the webhook secret and database configured."""
    import os

    settings = Settings(
        origin_shared_secret=ORIGIN,
        resend_webhook_secret=SecretStr(SECRET),
        database_url=SecretStr(os.environ["DATABASE_URL"]),
    )
    app.dependency_overrides[get_origin_settings] = lambda: settings
    app.dependency_overrides[get_internal_settings] = lambda: settings
    yield lifecycle_db[0], settings
    app.dependency_overrides.pop(get_origin_settings, None)
    app.dependency_overrides.pop(get_internal_settings, None)


def post_webhook(body: bytes, svix: dict[str, str] | None, extra: dict[str, str] | None = None):
    """Exactly the request shape the proxy builds: CSRF pair, svix-id as the idempotency key."""
    pair = "webhook-csrf-pair-0123456789abcdef"
    request_headers = {
        ORIGIN_SECRET_HEADER: ORIGIN,
        "Cookie": f"flo_csrf={pair}",
        "X-CSRF-Token": pair,
        "Content-Type": "application/json",
        **(svix or {}),
    }
    if svix and "svix-id" in svix:
        request_headers["Idempotency-Key"] = svix["svix-id"]

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as browser:
            return await browser.post(
                "/api/v1/webhooks/email", content=body, headers=request_headers | (extra or {})
            )

    return asyncio.run(go())


def sent_po(db, message_id="msg-1"):
    """An issued PO whose one email the provider accepted as `message_id`."""
    po = issued_po(db)
    dispatch(db, Sender())
    [(provider_id,)] = sql(db, "SELECT provider_message_id FROM email_message_route")
    return po, provider_id


def result(db, po):
    return [
        (r.attempt, r.result, r.result_reason)
        for r in transmission.list_transmissions(db[0], Scope(db[1]), po.id)
    ]


def rows_of(db, as_org, table, owner):
    """`owner`'s rows of `table`, read in `as_org`'s session."""
    return sql_org(db, as_org, f"SELECT count(*) FROM {table} WHERE org_id = '{owner}'")


def count(db, table):
    return sql(db, f"SELECT count(*) FROM {table} WHERE org_id = %s", (db[1],))[0][0]


# -- signature ------------------------------------------------------------------------------


def test_svix_known_answer_and_failures():
    body = b'{"test": 2432232314}'
    good = "v1,Ge2zcRRL0hU6e/IYNSixVToPdEBic8PvRUnHRboqNJo="
    now = 1614265330
    ok = verify_svix(SECRET, "msg_p4", "1614265330", good, body, now)
    assert ok is True
    assert verify_svix(SECRET, "msg_p4", "1614265330", "v2," + good[3:], body, now) is False
    assert verify_svix(SECRET, "msg_p4", "1614265330", good, body + b" ", now) is False
    assert verify_svix(SECRET, "msg_p4", "1614265330", good, body, now + 301) is False
    assert verify_svix(SECRET, "msg_p4", "1614265330", "v1,bad " + good, body, now) is True
    assert verify_svix(SECRET, None, "1614265330", good, body, now) is False
    assert verify_svix(SECRET, "msg_p4", "soon", good, body, now) is False


# -- webhook --------------------------------------------------------------------------------


def test_webhook_applies_a_signed_event_and_only_that(stack):
    db, _ = stack
    po, message_id = sent_po(db)
    body = event("email.delivered", message_id)
    assert post_webhook(body, sign(body, "evt_1")).status_code == 200
    assert result(db, po) == [(1, "delivered", None)]
    assert sql(db, "SELECT type, vendor_contact_id IS NOT NULL FROM email_event") == [
        ("email.delivered", True)
    ]


def test_webhook_rejects_bad_signature_old_timestamp_and_missing_headers(stack):
    db, _ = stack
    po, message_id = sent_po(db)
    body = event("email.bounced", message_id, "Permanent")
    forged = sign(body, "evt_1", secret="whsec_" + base64.b64encode(b"B" * 32).decode())
    assert post_webhook(body, forged).status_code == 401  # plant: skip verification -> 200
    assert post_webhook(body, sign(body, "evt_2", int(time.time()) - 301)).status_code == 401
    assert post_webhook(body, {"svix-id": "evt_3"}).status_code == 401
    assert result(db, po) == [(1, "sent", None)]
    assert count(db, "email_event") == 0 and count(db, "email_suppression") == 0


def test_webhook_fails_closed_without_a_secret_and_never_logs_it(stack, caplog):
    db, settings = stack
    _, message_id = sent_po(db)
    body = event("email.delivered", message_id)
    caplog.set_level(logging.DEBUG)
    assert post_webhook(body, sign(body, "evt_1")).status_code == 200
    assert SECRET not in caplog.text and SECRET.removeprefix("whsec_") not in caplog.text
    app.dependency_overrides[get_internal_settings] = lambda: settings.model_copy(
        update={"resend_webhook_secret": None}
    )
    assert post_webhook(body, sign(body, "evt_2")).status_code == 401


def test_a_repeated_svix_id_stores_one_event_and_an_unknown_message_is_ignored(stack):
    db, _ = stack
    po, message_id = sent_po(db)
    body = event("email.delivered", message_id)
    assert post_webhook(body, sign(body, "evt_1")).status_code == 200
    assert post_webhook(body, sign(body, "evt_1")).status_code == 200
    assert count(db, "email_event") == 1
    unknown = event("email.bounced", "never-sent", "Permanent")
    assert post_webhook(unknown, sign(unknown, "evt_9")).status_code == 200
    assert count(db, "email_event") == 1 and count(db, "email_suppression") == 0
    garbage = b"not json"
    assert post_webhook(garbage, sign(garbage, "evt_10")).status_code == 200
    no_id = json.dumps({"type": "email.delivered", "data": {}}).encode()
    assert post_webhook(no_id, sign(no_id, "evt_11")).status_code == 200
    assert result(db, po) == [(1, "delivered", None)]


def test_the_org_comes_only_from_the_route_never_the_payload(stack):
    db, _ = stack
    po, message_id = sent_po(db)
    # plant: take org_id from the payload -> the foreign org is written to, or the event lost
    body = event("email.complained", message_id, org_id=str(db[2]))
    assert post_webhook(body, sign(body, "evt_1")).status_code == 200
    assert result(db, po) == [(1, "complained", COMPLAINED_REASON)]
    assert rows_of(db, db[2], "email_event", db[2]) == [(0,)]
    assert sql_org(
        db, db[2], f"SELECT count(*) FROM email_suppression WHERE org_id = '{db[2]}'"
    ) == [(0,)]
    assert count(db, "email_event") == 1


@pytest.mark.parametrize(
    ("type_", "bounce", "expected", "suppressed"),
    [
        ("email.bounced", "Permanent", ("bounced", BOUNCED_REASON), "bounced"),
        ("email.complained", None, ("complained", COMPLAINED_REASON), "complained"),
        ("email.bounced", "Transient", ("sent", None), None),
        ("email.failed", None, ("failed", FAILED_REASON), None),
        ("email.suppressed", None, ("failed", FAILED_REASON), None),
        ("email.opened", None, ("sent", None), None),
    ],
)
def test_event_types(stack, type_, bounce, expected, suppressed):
    db, _ = stack
    po, message_id = sent_po(db)
    body = event(type_, message_id, bounce)
    assert post_webhook(body, sign(body, "evt_1")).status_code == 200
    assert result(db, po) == [(1, *expected)]
    stored = 0 if type_ == "email.opened" else 1
    assert count(db, "email_event") == stored
    listed = sql(db, "SELECT reason FROM email_suppression")
    assert listed == ([(suppressed,)] if suppressed else [])
    audits = sql(db, "SELECT count(*) FROM audit_log WHERE action = 'email.suppression.add'")
    assert audits == [(1 if suppressed else 0,)]


TRANSITIONS = [
    (["email.bounced", "email.delivered"], "bounced"),
    (["email.delivered", "email.complained"], "complained"),
    (["email.failed", "email.delivered"], "failed"),
    (["email.complained", "email.failed"], "complained"),
    (["email.delivered", "email.delivered"], "delivered"),
    (["email.delivered", "email.bounced"], "bounced"),
]


@pytest.mark.parametrize(("events", "final"), TRANSITIONS)
def test_a_later_event_never_hides_a_bad_result(lifecycle_db, events, final):
    db, _ = lifecycle_db
    po, _ = sent_po(db)
    [(outbox_id,)] = sql(db, "SELECT outbox_id FROM po_transmission")
    for n, kind in enumerate(events):
        with correlation_context("event"):
            apply_email_event(
                db[0], Scope(db[1]), outbox_id,
                event_id=f"e{n}", type=kind, hard=True,
            )  # fmt: skip
    # plant: drop the `result IS NULL OR ...` guard -> late `delivered` overwrites `bounced`
    assert result(db, po)[0][1] == final
    assert count(db, "email_event") == len(events)


# -- suppression ----------------------------------------------------------------------------


def test_a_suppressed_contact_is_marked_and_not_emailed_again(stack):
    db, _ = stack
    po, message_id = sent_po(db)
    body = event("email.bounced", message_id, "Permanent")
    assert post_webhook(body, sign(body, "evt_1")).status_code == 200
    [(address,)] = sql(db, "SELECT recipient_email FROM po_transmission")
    spare = contact(db, "Spare", "spare@example.com")
    vendor_id = sql(db, "SELECT vendor_id FROM purchase_order")[0][0]
    with correlation_context("contacts"), tenant_transaction(db[0], Scope(db[1])):
        flags = {
            c.email: c.suppressed
            for c in VendorService(db[0], Scope(db[1]), db[3]).list_contacts(vendor_id)
        }
    assert flags[address] is True and flags[spare.email] is False
    with correlation_context("resend"):
        again = resend(db, po)
    suppressed = [t for t in again if t.attempt == 2 and t.recipient_email == address]
    assert [(t.result, t.result_reason) for t in suppressed] == [
        ("address_suppressed", SUPPRESSED_REASON)
    ]
    sql(
        db,
        "UPDATE vendor_contact SET email = upper(email) WHERE email = %s RETURNING 1",
        (address,),
    )
    assert sql(
        db, "SELECT lower(trim(email)) FROM vendor_contact WHERE email = upper(%s)", (address,)
    ) == [(address,)]  # case never lets an address slip past the list


def test_removal_requires_a_reason_a_permission_and_the_own_org(stack):
    db, _ = stack
    _, message_id = sent_po(db)
    body = event("email.complained", message_id)
    post_webhook(body, sign(body, "evt_1"))
    [(sid, address)] = sql(db, "SELECT id, email_normalised FROM email_suppression")
    foreign = uuid4()
    with tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO email_suppression (id, org_id, email_normalised, reason) "
            "VALUES (%s, %s, 'x@example.com', 'bounced')", (foreign, db[2]),
        )  # fmt: skip

    async def scenario():
        async with client(app) as browser:
            await login(browser)

            def delete(id, reason="Mailbox fixed"):
                return browser.request(
                    "DELETE", f"/api/v1/admin/email-suppressions/{id}",
                    json={"reason": reason}, headers=headers(browser, uuid4().hex),
                )  # fmt: skip

            regrant(db, ["rfq.view"])
            assert (await delete(sid)).status_code == 403
            regrant(db, ["org.setting.manage"])
            assert (await delete(sid, "   ")).status_code == 422
            assert (await delete(sid, "")).status_code == 422
            assert (await delete(foreign)).status_code == 404
            assert (await delete(uuid4())).status_code == 404
            assert (await delete(sid)).status_code == 204
            assert (await delete(sid)).status_code == 404

    asyncio.run(scenario())
    assert count(db, "email_suppression") == 0
    assert sql_org(
        db, db[2], f"SELECT count(*) FROM email_suppression WHERE org_id = '{db[2]}'"
    ) == [(1,)]
    audit = sql(
        db, "SELECT reason, before FROM audit_log WHERE action = 'email.suppression.remove'"
    )
    assert audit == [("Mailbox fixed", {"email": address, "reason": "complained"})]


def test_suppression_delete_foreign_tenant_is_concealed(stack):
    """Named for test_route_coverage: another org's suppression id is a 404, never a 403."""
    test_removal_requires_a_reason_a_permission_and_the_own_org(stack)


# -- reasons on the transmission ------------------------------------------------------------


def test_the_gate_and_dead_rows_read_fixed_reasons(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    resend_settings = Settings(email_provider="resend")
    sender = Sender()
    for status in (None, DomainStatus("pass", "fail", "x"), DomainStatus("unknown", "pass", "")):
        sql(
            db,
            "UPDATE outbox SET state = 'pending', last_error = NULL, run_after = now() "
            "WHERE state = 'dead' RETURNING 1",
        )
        handler = email_handler(sender, domain_gate=po_domain_gate(resend_settings, status))
        OutboxDispatcher(db[0], {"email": handler}, random_fraction=lambda: 0).run()
        assert sender.calls == []  # no provider call
        assert result(db, po) == [(1, "failed", DOMAIN_NOT_VERIFIED)]
        assert sql(db, "SELECT last_error FROM outbox WHERE state = 'dead'") == [
            (DOMAIN_NOT_VERIFIED,)
        ]
    sql(
        db,
        "UPDATE outbox SET last_error = 'Traceback secret stuff' WHERE state = 'dead' RETURNING 1",
    )
    assert result(db, po) == [(1, "failed", FAILED_REASON)]


def test_only_po_templates_are_gated_and_only_in_production():
    resend_settings, smtp = Settings(email_provider="resend"), Settings(email_provider="smtp")
    closed = po_domain_gate(resend_settings, DomainStatus("fail", "fail", ""))
    assert closed("po-issued-v1") == closed("po-cancelled-v1") == DOMAIN_NOT_VERIFIED
    assert closed("password-reset-v1") is None and closed("invitation-v1") is None
    assert po_domain_gate(resend_settings, PASS)("po-issued-v1") is None
    assert po_domain_gate(smtp, None)("po-issued-v1") is None


def test_a_gated_po_email_sends_when_spf_and_dkim_pass(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    sender = Sender()
    handler = email_handler(
        sender, domain_gate=po_domain_gate(Settings(email_provider="resend"), PASS)
    )
    assert OutboxDispatcher(db[0], {"email": handler}, random_fraction=lambda: 0).run().sent >= 1
    assert result(db, po) == [(1, "sent", None)]


# -- jobs tick: transaction safety and the route row ---------------------------------------


def test_the_tick_reads_the_domain_status_without_losing_what_it_sent(lifecycle_db, monkeypatch):
    import os

    db, _ = lifecycle_db
    po = issued_po(db)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "INSERT INTO email_domain_status (spf, dkim, detail) VALUES ('pass','pass','')"
        )
    sender = Sender()
    monkeypatch.setattr(internal, "create_email_sender", lambda settings: sender)
    settings = Settings(
        email_provider="resend", database_url=SecretStr(os.environ["DATABASE_URL"]),
    )  # fmt: skip
    with correlation_context("tick"):
        internal._run_jobs_tick(settings)
    # plant: read the status on the tick connection outside a transaction -> sent is rolled back
    assert result(db, po) == [(1, "sent", None)]
    assert sql(db, "SELECT count(*) FROM email_message_route") == [(1,)]
    assert len(sender.calls) >= 1


def test_route_row_is_written_with_sent_for_po_emails_only(lifecycle_db):
    db, _ = lifecycle_db
    issued_po(db)
    dispatch(db, Sender())
    [(message_id, outbox_id)] = sql(
        db, "SELECT provider_message_id, outbox_id FROM email_message_route"
    )
    assert sql(db, "SELECT outbox_id FROM po_transmission") == [(outbox_id,)]
    assert email_route_for(db, message_id) == (db[1], outbox_id)
    # an invitation-style email gets no route
    with correlation_context("other"), tenant_transaction(db[0], Scope(db[1])):
        from flo.kernel.outbox.store import OutboxStore

        OutboxStore(db[0], Scope(db[1])).add_email(
            to="x@example.com", template="invitation-v1", context={}, idempotency_key="other-1"
        )
    dispatch(db, Sender())
    assert count(db, "email_message_route") == 1


def email_route_for(db, message_id):
    with correlation_context("route"):
        return email_route(db[0], message_id)


def test_a_failed_sent_leaves_no_route_row(lifecycle_db):
    db, _ = lifecycle_db
    issued_po(db)
    row = None
    with correlation_context("claim"), db[0].transaction():
        row = WorkerOutboxStore(db[0]).claim()
    while row is not None and not str(row.payload.get("template", "")).startswith("po-"):
        with db[0].transaction():
            row = WorkerOutboxStore(db[0]).claim()
    assert row is not None
    with pytest.raises(RuntimeError), tenant_transaction(db[0], Scope(row.org_id)):
        WorkerOutboxStore(db[0]).sent(row, "msg-rollback")
        raise RuntimeError("the rest of the delivery transaction failed")
    # plant: write the route row in its own commit -> a row survives for an unsent email
    assert count(db, "email_message_route") == 0


@contextlib.contextmanager
def app_role(db):
    """A role that is subject to RLS (the test connection is a superuser and bypasses it)."""
    role = "deliverability_rls_" + uuid4().hex
    conn = db[0]
    conn.execute(
        sql_.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql_.Identifier(role))
    )
    try:
        conn.execute(sql_.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql_.Identifier(role)))
        conn.execute(
            sql_.SQL(
                "GRANT SELECT, INSERT, UPDATE ON email_message_route, po_transmission, outbox TO {}"
            ).format(sql_.Identifier(role))
        )
        conn.execute(sql_.SQL("SET ROLE {}").format(sql_.Identifier(role)))
        yield
    finally:
        conn.execute("RESET ROLE")
        conn.execute(sql_.SQL("DROP OWNED BY {}").format(sql_.Identifier(role)))
        conn.execute(sql_.SQL("DROP ROLE {}").format(sql_.Identifier(role)))


def test_route_table_isolation_and_the_webhook_flag_reads_nothing_else(lifecycle_db):
    db, _ = lifecycle_db
    issued_po(db)
    dispatch(db, Sender())
    assert count(db, "email_message_route") == 1
    zero = "SET LOCAL app.org_id = '00000000-0000-0000-0000-000000000000'"
    route = "SELECT count(*) FROM email_message_route"
    with app_role(db):
        with tenant_transaction(db[0], Scope(db[1])):
            assert db[0].execute(route).fetchone() == (1,)
        # plant: drop FORCE or the tenant policy on the route table -> the foreign org sees it
        with tenant_transaction(db[0], Scope(db[2])):
            assert db[0].execute(route).fetchone() == (0,)
        for flag in (None, "jobs"):  # neither the zero org nor the jobs flag reads routes
            with db[0].transaction():
                db[0].execute(zero)
                if flag:
                    db[0].execute(f"SET LOCAL app.worker = '{flag}'")
                assert db[0].execute(route).fetchone() == (0,)
        with db[0].transaction():
            db[0].execute(zero)
            db[0].execute("SET LOCAL app.worker = 'webhook'")
            assert db[0].execute(route).fetchone() == (1,)
            # plant: grant the flag SELECT on po_transmission or outbox -> these become 1
            assert db[0].execute("SELECT count(*) FROM po_transmission").fetchone() == (0,)
            assert db[0].execute("SELECT count(*) FROM outbox").fetchone() == (0,)
        forged = (
            "INSERT INTO email_message_route (provider_message_id, org_id, outbox_id) "
            f"VALUES ('forged', '{db[1]}', gen_random_uuid())"
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db[0].transaction():
            db[0].execute(zero)
            db[0].execute("SET LOCAL app.worker = 'webhook'")
            db[0].execute(forged)
        with db[0].transaction():
            db[0].execute(zero)
            db[0].execute("SET LOCAL app.worker = 'webhook'")
            assert db[0].execute("UPDATE email_message_route SET org_id = org_id").rowcount == 0


# -- contact attribution --------------------------------------------------------------------


def test_contact_attribution_tolerates_old_and_stale_recipients(lifecycle_db):
    db, _ = lifecycle_db
    po = issued_po(db)
    [(attributed,)] = sql(db, "SELECT vendor_contact_id FROM po_transmission")
    assert attributed is not None
    kill(db)
    # a pre-story recipient list has no contact id; resend reuses the latest rows, which do
    sql(db, "UPDATE po_transmission SET vendor_contact_id = NULL RETURNING 1")
    with correlation_context("resend"):
        resend(db, po)
    assert sql(db, "SELECT vendor_contact_id FROM po_transmission WHERE attempt = 2") == [(None,)]
    # a stored contact id whose contact is gone becomes NULL, never an FK error
    ghost = {"name": "Gone", "email": "gone@example.com", "contact_id": str(uuid4())}
    with correlation_context("send"), tenant_transaction(db[0], Scope(db[1])):
        transmission._send(db[0], Scope(db[1]), po.id, 2, 3, [ghost], db[3])
    assert sql(db, "SELECT vendor_contact_id FROM po_transmission WHERE attempt = 3") == [(None,)]
    # deleting a contact later nulls the attribution but keeps the send
    sql(
        db,
        "UPDATE po_transmission SET vendor_contact_id = (SELECT id FROM vendor_contact LIMIT 1) "
        "WHERE attempt = 1 RETURNING 1",
    )
    sql(db, "DELETE FROM vendor_contact RETURNING 1")
    assert sql(db, "SELECT vendor_contact_id FROM po_transmission WHERE attempt = 1") == [(None,)]


# -- domain check ---------------------------------------------------------------------------


def records(spf="verified", dkim="verified", name="xlr8flo.local"):
    return json.dumps(
        {
            "name": name,
            "status": "verified",
            "records": [
                {"record": "SPF", "type": "MX", "name": "send", "status": spf},
                {"record": "DKIM", "type": "TXT", "name": "k._domainkey", "status": dkim},
                {"record": "Receiving", "type": "MX", "name": "x", "status": "failed"},
            ],
        }
    ).encode()


def checker(status=200, body=b"", error=None, calls=None):
    def requester(url, request_headers, timeout):
        if calls is not None:
            calls.append((url, dict(request_headers)))
        if error:
            raise error
        return status, body

    return ResendDomainChecker("KEY", "dom-1", "noreply@xlr8flo.local", requester=requester)


@pytest.mark.parametrize(
    ("body", "spf", "dkim", "detail"),
    [
        (records(), "pass", "pass", ""),
        (records(dkim="failed"), "pass", "fail", "DKIM TXT k._domainkey: failed"),
        (records(spf="pending"), "unknown", "pass", "SPF MX send: pending"),
        (records(name="other.example"), "fail", "fail", "EMAIL_FROM is not on other.example"),
        (json.dumps({"name": "xlr8flo.local", "records": []}).encode(), "unknown", "unknown", ""),
    ],
)
def test_domain_status_mapping(body, spf, dkim, detail):
    calls: list = []
    status = checker(200, body, calls=calls).domain_status()
    assert (status.spf, status.dkim) == (spf, dkim) and status.detail == detail
    assert calls[0][0].endswith("/domains/dom-1")
    assert calls[0][1]["Authorization"] == "Bearer KEY"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 503}, TransientEmailError),
        ({"status": 429}, TransientEmailError),
        ({"error": TimeoutError()}, TransientEmailError),
        ({"status": 401}, PermanentEmailError),
        ({"status": 200, "body": b"not json"}, PermanentEmailError),
    ],
)
def test_domain_check_errors_are_classified_and_never_echo_the_key(kwargs, error):
    with pytest.raises(error) as raised:
        checker(**kwargs).domain_status()
    assert "KEY" not in str(raised.value)


def post_job():
    pair = "domain-check-csrf-pair-0123456789"

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as browser:
            return await browser.post(
                "/internal/jobs/email-domain-check",
                headers={
                    ORIGIN_SECRET_HEADER: ORIGIN, "Idempotency-Key": uuid4().hex,
                    "Cookie": f"flo_csrf={pair}", "X-CSRF-Token": pair,
                },
            )  # fmt: skip

    return asyncio.run(go())


def test_the_job_stores_a_row_or_leaves_the_last_status_in_force(stack, monkeypatch):
    db, _ = stack
    answers = [
        DomainStatus("pass", "pass", ""),
        TransientEmailError("later"),
        PermanentEmailError("401"),
        DomainStatus("pass", "fail", "DKIM TXT k: failed"),
    ]

    class Fake:
        def domain_status(self):
            outcome = answers.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(internal, "create_domain_checker", lambda settings: Fake())
    first = post_job()
    assert first.status_code == 200 and first.json() == {"spf": "pass", "dkim": "pass"}
    for _ in range(2):
        failed = post_job()
        assert failed.status_code == 503 and "KEY" not in failed.text
    with psycopg.connect(stack[1].database_url.get_secret_value()) as conn:
        # a provider blip stores nothing
        assert conn.execute("SELECT count(*) FROM email_domain_status").fetchone() == (1,)
    assert post_job().json() == {"spf": "pass", "dkim": "fail"}
    with psycopg.connect(stack[1].database_url.get_secret_value()) as conn:
        assert latest_domain_status(conn) == DomainStatus("pass", "fail", "DKIM TXT k: failed")
