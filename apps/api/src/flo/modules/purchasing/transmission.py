"""Email transmission of an issued purchase order through the outbox (E13-S09).

`enqueue` runs inside the caller's transaction (issue or change-order apply) and writes only
`po_transmission` and `outbox`; the dispatcher sends later. Resend is the one write here that
starts a transaction of its own, under the PO row lock.
"""

from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import conflict
from flo.kernel.outbox.store import OutboxStore
from flo.kernel.ports.email import EmailAttachment
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.purchasing.pdf import filename, frozen, pdf_for
from flo.modules.purchasing.schemas import PoTransmissionRead
from flo.modules.purchasing.service import PurchaseOrderService, _invalid
from flo.modules.purchasing.vendor_copy import email_summary
from flo.modules.vendors.service import VendorService

type Conn = psycopg.Connection[tuple[object, ...]]

TEMPLATE = "po-issued-v1"
# The outbox owns the state until a provider event (E13-S10) writes `result`. A dead outbox row is
# `failed`, never `bounced`: a bounce is a provider event.
READ_STATE = (
    "COALESCE(t.result, CASE o.state WHEN 'sent' THEN 'sent' WHEN 'dead' THEN 'failed' "
    "ELSE 'queued' END)"
)
_FROM = "FROM po_transmission t LEFT JOIN outbox o ON o.org_id = t.org_id AND o.id = t.outbox_id"
NO_RECIPIENTS = (
    "no_recipients",
    "The vendor has no contact that receives documents. Add one, then issue.",
)


def _suppressed(conn: Conn, scope: Scope, email: str) -> bool:
    """E13-S10 fills the suppression list in."""
    return False


def _dedupe(recipients: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in recipients:
        if (key := str(r["email"]).lower()) not in seen:
            seen.add(key)
            out.append({"name": r["name"], "email": r["email"]})
    return out


def _send(
    conn: Conn,
    scope: Scope,
    po_id: UUID,
    version: int,
    attempt: int,
    recipients: Sequence[dict[str, Any]],
    created_by: UUID,
) -> None:
    """One `po_transmission` row and one outbox email per distinct recipient."""
    recipients = _dedupe(recipients)
    if not recipients:
        raise conflict(*NO_RECIPIENTS)
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
    attachment = EmailAttachment(filename(copy), "application/pdf", pdf)
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
                template=TEMPLATE,
                context=context,
                idempotency_key=f"po_transmission:{row_id}",
                attachments=[attachment],
            ).id
        repo.execute(
            "INSERT INTO po_transmission (org_id, id, po_id, version, attempt, recipient_name, "
            "recipient_email, template_version, outbox_id, result, created_by) VALUES "
            "(%(org_id)s, %(row)s, %(po)s, %(version)s, %(attempt)s, %(name)s, %(email)s, "
            "%(template)s, %(outbox)s, %(result)s, %(by)s)",
            {
                "row": row_id,
                "po": po_id,
                "version": version,
                "attempt": attempt,
                "name": r["name"],
                "email": r["email"],
                "template": TEMPLATE,
                "outbox": outbox_id,
                "result": result,
                "by": created_by,
            },
        )


def _latest_recipients(repo: Any, po_id: UUID) -> list[dict[str, Any]]:
    rows = repo.execute(
        "SELECT recipient_name, recipient_email FROM po_transmission "
        "WHERE org_id = %(org_id)s AND po_id = %(po)s AND attempt = "
        "(SELECT max(attempt) FROM po_transmission WHERE org_id = %(org_id)s AND po_id = %(po)s) "
        "ORDER BY created_at, lower(recipient_email)",
        {"po": po_id},
    ).fetchall()
    return [{"name": r[0], "email": r[1]} for r in rows]


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


def list_transmissions(conn: Conn, scope: Scope, po_id: UUID) -> list[PoTransmissionRead]:
    service = PurchaseOrderService(conn, scope)
    with tenant_transaction(cast(RlsSession, conn), scope):
        service._row(po_id)  # 404 for a foreign or unknown id
        rows = service.repo.execute(
            "SELECT t.id, t.po_id, p.revision, t.version, t.attempt, t.recipient_name, "
            f"t.recipient_email, t.template_version, o.provider_message_id, {READ_STATE}, "
            "t.created_at, COALESCE(t.result_at, o.sent_at) "
            f"{_FROM} JOIN purchase_order p ON p.org_id = t.org_id AND p.id = t.po_id "
            "JOIN purchase_order b ON b.org_id = p.org_id AND b.unit_id = p.unit_id "
            "AND b.number = p.number AND b.id = %(id)s "
            "WHERE t.org_id = %(org_id)s ORDER BY t.created_at DESC, t.attempt DESC, t.id",
            {"id": po_id},
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
        if h["status"] != "issued":
            raise conflict("not_resendable", "Only an issued purchase order can be sent again.")
        repo = service.repo
        if repo.execute(
            f"SELECT 1 {_FROM} WHERE t.org_id = %(org_id)s AND t.po_id = %(po)s "
            f"AND {READ_STATE} = 'queued' LIMIT 1",
            {"po": po_id},
        ).fetchone():
            raise conflict("transmission_queued", "A send of this version is still queued.")
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
            recipients = _latest_recipients(repo, po_id)
        else:
            known = {
                c.id: {"name": c.name, "email": c.email}
                for c in VendorService(conn, scope, actor_id).list_contacts(
                    cast(UUID, h["vendor_id"])
                )
            }
            if any(c not in known for c in contact_ids):
                raise _invalid("contact_ids", "Choose contacts of this vendor.", "unknown_contact")
            recipients = [known[c] for c in contact_ids]
        _send(conn, scope, po_id, cast(int, h["version"]), last + 1, recipients, actor_id)
        audit_event(
            cast(AuditConnection, conn),
            scope,
            actor=AuditActor(ActorKind.USER, actor_id),
            action="purchase_order.transmission.resend",
            target_type="purchase_order",
            target_id=po_id,
            outcome=Outcome.SUCCESS,
            after={"attempt": last + 1, "recipients": [r["email"] for r in _dedupe(recipients)]},
        )
    return list_transmissions(conn, scope, po_id)
