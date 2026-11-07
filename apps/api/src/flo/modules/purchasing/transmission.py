"""Email transmission of an issued purchase order through the outbox (E13-S09).

`enqueue` runs inside the caller's transaction (issue or change-order apply) and writes only
`po_transmission` and `outbox`; the dispatcher sends later. Resend is the one write here that
starts a transaction of its own, under the PO row lock.
"""

from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.email import DOMAIN_NOT_VERIFIED
from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.outbox.store import OutboxStore
from flo.kernel.ports.email import EmailAttachment
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.purchasing.models import READ_STATE, TRANSMISSION_FROM
from flo.modules.purchasing.pdf import filename, frozen, pdf_for
from flo.modules.purchasing.schemas import PoTransmissionRead
from flo.modules.purchasing.service import PurchaseOrderService, _invalid
from flo.modules.purchasing.vendor_copy import email_summary
from flo.modules.vendors.service import VendorService

type Conn = psycopg.Connection[tuple[object, ...]]

TEMPLATE = "po-issued-v1"
# keep the `po-` prefix: E13-S10's domain gate recognises a PO email by it
CANCEL_TEMPLATE = "po-cancelled-v1"
_FROM = TRANSMISSION_FROM
BOUNCED_REASON = "Hard bounce: the address does not accept mail"
COMPLAINED_REASON = "The recipient marked the email as spam"
FAILED_REASON = "The email provider could not deliver the email"
SUPPRESSED_REASON = "Address is on the suppression list"
NO_RECIPIENTS = (
    "no_recipients",
    "The vendor has no contact that receives documents. Add one, then issue.",
)


def normalise_email(email: str) -> str:
    return email.strip().lower()


def _suppressed(conn: Conn, scope: Scope, email: str) -> bool:
    repo = PurchaseOrderService(conn, scope).repo
    return (
        repo.execute(
            "SELECT 1 FROM email_suppression WHERE org_id = %(org_id)s "
            "AND email_normalised = %(email)s",
            {"email": normalise_email(email)},
        ).fetchone()
        is not None
    )


def _dedupe(recipients: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in recipients:
        if (key := normalise_email(str(r["email"]))) not in seen:
            seen.add(key)
            # old purchase_order.recipients rows carry no contact id
            out.append({"name": r["name"], "email": r["email"], "contact_id": r.get("contact_id")})
    return out


def _send(
    conn: Conn,
    scope: Scope,
    po_id: UUID,
    version: int,
    attempt: int,
    recipients: Sequence[dict[str, Any]],
    created_by: UUID,
    *,
    template: str = TEMPLATE,
    context: Mapping[str, object] | None = None,
    attachments: Sequence[EmailAttachment] | None = None,
) -> None:
    """One `po_transmission` row and one outbox email per distinct recipient.

    `context=None` is the issue email: the frozen PDF, summary and context are built here."""
    recipients = _dedupe(recipients)
    if not recipients:
        raise conflict(*NO_RECIPIENTS)
    if context is None:
        pdf = pdf_for(conn, scope, po_id)
        copy = frozen(conn, scope, po_id).copy
        summary = email_summary(copy)
        if len(summary) > 2000:
            summary = summary[:1999] + "…"
        context = {
            "po_number": copy.number,
            "revision": str(copy.revision),
            "vendor_name": copy.vendor_trading_name,
            "summary": summary,
        }
        attachments = [EmailAttachment(filename(copy), "application/pdf", pdf)]
    store = OutboxStore(cast(Any, conn), scope)
    repo = PurchaseOrderService(conn, scope).repo
    # ponytail: the PDF travels base64 in each recipient's outbox payload until sent (`sent`
    # blanks it); move it to a storage key resolved at dispatch if PDFs pass ~1 MB.
    for r in recipients:
        row_id = uuid4()
        outbox_id: UUID | None = None
        result: str | None = None
        if _suppressed(conn, scope, r["email"]):
            result = "address_suppressed"
        else:
            outbox_id = store.add_email(
                to=r["email"],
                template=template,
                context=context,
                idempotency_key=f"po_transmission:{row_id}",
                attachments=attachments or (),
            ).id
        repo.execute(
            "INSERT INTO po_transmission (org_id, id, po_id, version, attempt, recipient_name, "
            "recipient_email, template_version, outbox_id, result, result_reason, created_by, "
            "vendor_contact_id) VALUES "
            "(%(org_id)s, %(row)s, %(po)s, %(version)s, %(attempt)s, %(name)s, %(email)s, "
            "%(template)s, %(outbox)s, %(result)s, %(reason)s, %(by)s, "
            # a contact deleted since the recipients were stored becomes NULL, not an FK error
            "(SELECT id FROM vendor_contact WHERE org_id = %(org_id)s AND id = %(contact)s::uuid))",
            {
                "row": row_id,
                "po": po_id,
                "version": version,
                "attempt": attempt,
                "name": r["name"],
                "email": r["email"],
                "template": template,
                "outbox": outbox_id,
                "result": result,
                "reason": SUPPRESSED_REASON if result else None,
                "by": created_by,
                "contact": r.get("contact_id"),
            },
        )


def _latest_recipients(repo: Any, po_id: UUID, template: str | None = None) -> list[dict[str, Any]]:
    """The rows of the highest attempt on `po_id`, of one template when given."""
    only = "AND template_version = %(template)s " if template else ""
    rows = repo.execute(
        "SELECT recipient_name, recipient_email, vendor_contact_id FROM po_transmission "
        f"WHERE org_id = %(org_id)s AND po_id = %(po)s {only}AND attempt = "
        "(SELECT max(attempt) FROM po_transmission "
        f"WHERE org_id = %(org_id)s AND po_id = %(po)s {only}) "
        "ORDER BY created_at, lower(recipient_email)",
        {"po": po_id, "template": template},
    ).fetchall()
    return [{"name": r[0], "email": r[1], "contact_id": r[2]} for r in rows]


def enqueue(conn: Conn, scope: Scope, po_id: UUID, version: int) -> None:
    """Attempt 1 of an issued row. Takes no lock: both callers hold the PO row."""
    repo = PurchaseOrderService(conn, scope).repo
    revision, recipients, supersedes_id, issued_by = cast(
        tuple[int, Any, UUID | None, UUID],
        repo.execute(
            "SELECT revision, recipients, supersedes_id, issued_by FROM purchase_order "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": po_id},
        ).fetchone(),
    )
    if revision and supersedes_id is not None:
        # a recipient chosen at resend carries forward to the next revision
        recipients = _latest_recipients(repo, supersedes_id) or recipients
    _send(conn, scope, po_id, version, 1, recipients, issued_by)


def cancellation_recipients(conn: Conn, scope: Scope, po_id: UUID) -> list[dict[str, Any]]:
    """Who hears of a cancel: the latest send's recipients, else the row's own. Reads only."""
    repo = PurchaseOrderService(conn, scope).repo
    found: list[dict[str, Any]] = (
        _latest_recipients(repo, po_id)
        or cast(
            tuple[Any],
            repo.execute(
                "SELECT recipients FROM purchase_order WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": po_id},
            ).fetchone(),
        )[0]
    )
    return _dedupe(found or [])


def _cancel_notice(repo: Any, po_id: UUID) -> tuple[int, dict[str, str]]:
    """The cancelled row's version and the notice context: the number, revision and vendor,
    never the reason (E13-S24, Resolved 2)."""
    number, revision, version, snapshot = cast(
        tuple[str, int, int, dict[str, Any]],
        repo.execute(
            "SELECT number, revision, version, vendor_snapshot FROM purchase_order "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": po_id},
        ).fetchone(),
    )
    return version, {
        "po_number": number,
        "revision": str(revision),
        "vendor_name": snapshot["trading_name"],
    }


def enqueue_cancellation(
    conn: Conn, scope: Scope, po_id: UUID, actor_id: UUID, recipients: Sequence[dict[str, Any]]
) -> None:
    """The vendor's cancellation notice, in the cancel transaction; takes no lock, no PDF."""
    repo = PurchaseOrderService(conn, scope).repo
    version, context = _cancel_notice(repo, po_id)
    [last] = cast(
        tuple[int | None],
        repo.execute(
            "SELECT max(attempt) FROM po_transmission WHERE org_id = %(org_id)s AND po_id = %(po)s",
            {"po": po_id},
        ).fetchone(),
    )
    # attempts are numbered per PO: the unique index has no version, so 1 would hit the issue rows
    _send(
        conn, scope, po_id, version, (last or 0) + 1, recipients, actor_id,
        template=CANCEL_TEMPLATE, context=context, attachments=(),
    )  # fmt: skip


def list_transmissions(conn: Conn, scope: Scope, po_id: UUID) -> list[PoTransmissionRead]:
    service = PurchaseOrderService(conn, scope)
    with tenant_transaction(cast(RlsSession, conn), scope):
        service._row(po_id)  # 404 for a foreign or unknown id
        rows = service.repo.execute(
            "SELECT t.id, t.po_id, p.revision, t.version, t.attempt, t.recipient_name, "
            f"t.recipient_email, t.template_version, o.provider_message_id, {READ_STATE}, "
            "t.created_at, COALESCE(t.result_at, o.sent_at), "
            "COALESCE(t.result_reason, CASE WHEN o.state = 'dead' THEN CASE "
            "WHEN o.last_error = %(gate)s THEN o.last_error ELSE %(failed)s END END) "
            f"{_FROM} JOIN purchase_order p ON p.org_id = t.org_id AND p.id = t.po_id "
            "JOIN purchase_order b ON b.org_id = p.org_id AND b.unit_id = p.unit_id "
            "AND b.number = p.number AND b.id = %(id)s "
            "WHERE t.org_id = %(org_id)s ORDER BY t.created_at DESC, t.attempt DESC, t.id",
            {"id": po_id, "gate": DOMAIN_NOT_VERIFIED, "failed": FAILED_REASON},
        ).fetchall()
    keys = list(PoTransmissionRead.model_fields)
    return [PoTransmissionRead.model_validate(dict(zip(keys, r, strict=True))) for r in rows]


def attention_counts(conn: Conn, scope: Scope, days: int = 7) -> dict[str, int]:
    """OBS-004: sends that need a person, inside the window. E19-S07 surfaces it."""
    repo = PurchaseOrderService(conn, scope).repo
    rows = repo.execute(
        f"SELECT {READ_STATE}, count(*) {_FROM} WHERE t.org_id = %(org_id)s "
        "AND t.created_at > now() - %(days)s * interval '1 day' GROUP BY 1",
        {"days": days},
    ).fetchall()
    counts = {str(r[0]): int(cast(int, r[1])) for r in rows}
    return {k: counts.get(k, 0) for k in ("failed", "bounced", "address_suppressed")}


def resend(
    conn: Conn, scope: Scope, actor_id: UUID, po_id: UUID, contact_ids: list[UUID] | None
) -> list[PoTransmissionRead]:
    service = PurchaseOrderService(conn, scope, actor_id)
    with tenant_transaction(cast(RlsSession, conn), scope):
        h = service._row(po_id, lock=True)
        if h["status"] not in ("issued", "cancelled"):
            raise conflict(
                "not_resendable", "Only an issued or cancelled purchase order can be sent again."
            )
        # a cancelled PO resends its cancellation notice; only those rows count
        template = CANCEL_TEMPLATE if h["status"] == "cancelled" else None
        only = "AND t.template_version = %(template)s " if template else ""
        repo = service.repo
        if repo.execute(
            f"SELECT 1 {_FROM} WHERE t.org_id = %(org_id)s AND t.po_id = %(po)s {only}"
            f"AND {READ_STATE} = 'queued' LIMIT 1",
            {"po": po_id, "template": template},
        ).fetchone():
            raise conflict("transmission_queued", "A send of this version is still queued.")
        if (
            template
            and not repo.execute(
                f"SELECT 1 {_FROM} WHERE t.org_id = %(org_id)s AND t.po_id = %(po)s {only}LIMIT 1",
                {"po": po_id, "template": template},
            ).fetchone()
        ):
            raise conflict("nothing_to_resend", "This purchase order has no send to repeat.")
        [last] = cast(
            tuple[int | None],
            repo.execute(
                "SELECT max(attempt) FROM po_transmission "
                "WHERE org_id = %(org_id)s AND po_id = %(po)s",
                {"po": po_id},
            ).fetchone(),
        )
        if last is None:
            raise conflict("nothing_to_resend", "This purchase order has no send to repeat.")
        if contact_ids is None:
            recipients = _latest_recipients(repo, po_id, template)
        else:
            known = {
                c.id: {"name": c.name, "email": c.email, "contact_id": c.id}
                for c in VendorService(conn, scope, actor_id).list_contacts(
                    cast(UUID, h["vendor_id"])
                )
            }
            if any(c not in known for c in contact_ids):
                raise _invalid("contact_ids", "Choose contacts of this vendor.", "unknown_contact")
            recipients = [known[c] for c in contact_ids]
        if template:
            version, context = _cancel_notice(repo, po_id)
            _send(
                conn, scope, po_id, version, last + 1, recipients, actor_id,
                template=template, context=context, attachments=(),
            )  # fmt: skip
        else:
            _send(conn, scope, po_id, cast(int, h["version"]), last + 1, recipients, actor_id)
        audit_event(
            cast(AuditConnection, conn),
            scope,
            actor=AuditActor(ActorKind.USER, actor_id),
            action="purchase_order.transmission.resend",
            target_type="purchase_order",
            target_id=po_id,
            outcome=Outcome.SUCCESS,
            after={
                "attempt": last + 1,
                "recipients": [r["email"] for r in _dedupe(recipients)],
                "template": template or TEMPLATE,
            },
        )
    return list_transmissions(conn, scope, po_id)


# -- provider events (E13-S10) ------------------------------------------------------------

_EVENT_RESULT = {
    "email.delivered": ("delivered", None),
    "email.bounced": ("bounced", BOUNCED_REASON),
    "email.complained": ("complained", COMPLAINED_REASON),
    "email.failed": ("failed", FAILED_REASON),
    "email.suppressed": ("failed", FAILED_REASON),
}


def apply_email_event(
    conn: Conn, scope: Scope, outbox_id: UUID, *, event_id: str, type: str, hard: bool
) -> None:
    """Store one provider event and move `po_transmission.result` by the transition rule.

    A soft bounce stores the event and changes nothing; a repeated `event_id` is a no-op."""
    if type not in _EVENT_RESULT:
        return  # an event we do not track is stored nowhere
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = PurchaseOrderService(conn, scope).repo
        found = repo.execute(
            "SELECT id, vendor_contact_id, recipient_email FROM po_transmission "
            "WHERE org_id = %(org_id)s AND outbox_id = %(o)s",
            {"o": outbox_id},
        ).fetchone()
        if found is None:
            return
        transmission_id, contact_id, email = found
        event_row = uuid4()
        if not repo.execute(
            "INSERT INTO email_event (id, org_id, provider_event_id, type, transmission_id, "
            "vendor_contact_id) VALUES (%(id)s, %(org_id)s, %(event)s, %(type)s, %(t)s, %(c)s) "
            "ON CONFLICT (provider_event_id) DO NOTHING",
            {
                "id": event_row,
                "event": event_id,
                "type": type,
                "t": transmission_id,
                "c": contact_id,
            },
        ).rowcount:
            return  # a duplicate delivery
        if type == "email.bounced" and not hard:
            return
        result, reason = _EVENT_RESULT[type]
        # delivered is the only result that moves: a bounce or complaint can follow it, and
        # nothing may hide a bounced, complained or failed send (EML-003)
        repo.execute(
            "UPDATE po_transmission SET result = %(new)s, result_at = now(), "
            "result_reason = %(reason)s WHERE org_id = %(org_id)s AND id = %(t)s "
            "AND (result IS NULL OR "
            "(result = 'delivered' AND %(new)s IN ('bounced','complained')))",
            {"new": result, "reason": reason, "t": transmission_id},
        )
        if result in ("bounced", "complained"):
            suppression_id = uuid4()
            if repo.execute(
                "INSERT INTO email_suppression (id, org_id, email_normalised, reason, "
                "source_event_id) VALUES (%(id)s, %(org_id)s, %(email)s, %(reason)s, %(event)s) "
                "ON CONFLICT (org_id, email_normalised) DO NOTHING",
                {
                    "id": suppression_id,
                    "email": normalise_email(str(email)),
                    "reason": result,
                    "event": event_row,
                },
            ).rowcount:
                audit_event(
                    cast(AuditConnection, conn),
                    scope,
                    actor=AuditActor(ActorKind.SYSTEM),
                    action="email.suppression.add",
                    target_type="email_suppression",
                    target_id=suppression_id,
                    outcome=Outcome.SUCCESS,
                    after={"email": normalise_email(str(email)), "reason": result},
                )


def remove_suppression(
    conn: Conn, scope: Scope, actor_id: UUID, suppression_id: UUID, reason: str
) -> None:
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = (
            PurchaseOrderService(conn, scope)
            .repo.execute(
                "DELETE FROM email_suppression WHERE org_id = %(org_id)s AND id = %(id)s "
                "RETURNING email_normalised, reason",
                {"id": suppression_id},
            )
            .fetchone()
        )
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        audit_event(
            cast(AuditConnection, conn),
            scope,
            actor=AuditActor(ActorKind.USER, actor_id),
            action="email.suppression.remove",
            target_type="email_suppression",
            target_id=suppression_id,
            outcome=Outcome.SUCCESS,
            reason=reason,
            before={"email": row[0], "reason": row[1]},
        )
