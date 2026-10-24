"""RFQ publication, amendment, deadline and close; every version is told to every invitee
(SRC-004, SRC-005; decisions 206, 207)."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError, conflict
from flo.kernel.jobs import Job, JobQueue
from flo.kernel.jobs.queue import JobConnection
from flo.kernel.logging import correlation_context
from flo.kernel.outbox.store import OutboxConnection, OutboxStore
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.requisitions.service import mark_sourced
from flo.modules.sourcing.models import RfqRepository
from flo.modules.sourcing.schemas import (
    ExtendDeadline,
    RfqAmend,
    RfqLinesPatch,
    RfqRead,
    RfqVersionRead,
    invalid,
)
from flo.modules.sourcing.service import RfqService
from flo.modules.sourcing.versions import diff, snapshot
from flo.modules.vendors.schemas import ContactRead
from flo.modules.vendors.service import VendorService

type Conn = psycopg.Connection[tuple[object, ...]]

JOB_KIND = "rfq-close"
TEMPLATE = "rfq-version-v1"
TRANSITIONS = {
    ("draft", "publish"): "published",
    ("published", "amend"): "published",
    ("published", "extend"): "published",
    ("published", "close"): "closed",
    ("closed", "start_evaluation"): "under_evaluation",
}


class RfqLifecycle(RfqService):
    def _lock(self, id: UUID, action: str) -> tuple[object, ...]:
        """Lock the RFQ and refuse an action its state does not allow. Call in a tx."""
        head = self.repo.execute(
            "SELECT status, deadline_at, version, deadline_at <= now(), number, title FROM rfq "
            "WHERE org_id = %(org_id)s AND id = %(id)s FOR UPDATE",
            {"id": id},
        ).fetchone()
        if head is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        if (head[0], action) not in TRANSITIONS:
            raise conflict(
                "invalid_transition",
                f"A {str(head[0]).replace('_', ' ')} RFQ cannot {action.replace('_', ' ')}. "
                "Reload it and retry.",
            )
        return head

    def _recipients(self, vendor_id: UUID, contact_ids: list[UUID]) -> list[ContactRead]:
        contacts = VendorService(self.connection, self.scope).list_contacts(vendor_id)
        return [c for c in contacts if c.id in contact_ids and c.email]

    def _invitees(self, id: UUID) -> list[tuple[UUID, list[UUID], str]]:
        return [
            (cast(UUID, r[0]), cast(list[UUID], r[1]), cast(str, r[2]["trading_name"]))  # type: ignore[index]
            for r in self.repo.execute(
                "SELECT vendor_id, contact_ids, vendor_snapshot FROM rfq_invitee "
                "WHERE org_id = %(org_id)s AND rfq_id = %(id)s ORDER BY invited_at, vendor_id",
                {"id": id},
            ).fetchall()
        ]

    # -- publish ---------------------------------------------------------------------------

    def publish(self, id: UUID) -> RfqRead:
        """Decision 207: publishing needs no approval, even when an `rfq` workflow exists."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._lock(id, "publish")
            self._check_publishable(id)
            self.repo.execute(
                "UPDATE rfq SET status = 'published', version = 1, published_at = now(), "
                "updated_at = now(), edit_revision = edit_revision + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": id},
            )
            self._record(id, 1, None)
            requisitions = self.repo.execute(
                "SELECT DISTINCT requisition_id FROM rfq_line "
                "WHERE org_id = %(org_id)s AND rfq_id = %(id)s ORDER BY requisition_id",
                {"id": id},
            ).fetchall()
            for (requisition_id,) in requisitions:
                mark_sourced(self.connection, self.scope, cast(UUID, requisition_id), self._actor())
            self._audit(
                "rfq.publish", id, {"status": "draft"}, {"status": "published", "version": 1}
            )
        return self.get(id)

    def _actor(self) -> UUID:
        assert self.actor_id is not None
        return self.actor_id

    def _check_publishable(self, id: UUID) -> None:
        """Every problem at once, as field errors."""
        problems: list[ProblemFieldError] = []
        lines = self.repo.execute(
            "SELECT count(*) FROM rfq_line WHERE org_id = %(org_id)s AND rfq_id = %(id)s",
            {"id": id},
        ).fetchone()
        if not (lines and lines[0]):
            problems.append(ProblemFieldError(field="lines", message="Add at least one line."))
        soon = self.repo.execute(
            "SELECT deadline_at < now() + interval '1 hour' FROM rfq "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": id},
        ).fetchone()
        if soon and soon[0]:
            problems.append(
                ProblemFieldError(
                    field="deadline_at", message="Set a deadline at least one hour from now."
                )
            )
        invitees = self._invitees(id)
        if not invitees:
            problems.append(
                ProblemFieldError(field="invitees", message="Invite at least one vendor.")
            )
        for vendor_id, contact_ids, name in invitees:
            if not self._recipients(vendor_id, contact_ids):
                problems.append(
                    ProblemFieldError(
                        field=f"invitees/{vendor_id}",
                        message=f"{name} has no contact with an email address to receive the RFQ.",
                    )
                )
        if problems:
            raise ProblemError(
                ErrorCode.VALIDATION_FAILED,
                detail=problems[0].message,
                errors=tuple(problems),
                checks={"problem": "cannot_publish"},
            )

    # -- versions and telling invitees -------------------------------------------------------

    def _record(self, id: UUID, version: int, reason: str | None) -> None:
        """Store the version's snapshot and diff, then queue one email per invitee contact."""
        now = snapshot(self.repo, id)
        previous = self.repo.execute(
            "SELECT snapshot FROM rfq_version WHERE org_id = %(org_id)s AND rfq_id = %(id)s "
            "AND version = %(version)s",
            {"id": id, "version": version - 1},
        ).fetchone()
        changes = diff(cast(dict[str, object], previous[0]), now) if previous else {}
        self.repo.execute(
            "INSERT INTO rfq_version (org_id, rfq_id, version, snapshot, reason, diff, "
            "published_by) VALUES (%(org_id)s, %(id)s, %(version)s, %(snapshot)s, %(reason)s, "
            "%(diff)s, %(by)s)",
            {
                "id": id,
                "version": version,
                "snapshot": Jsonb(now),
                "reason": reason,
                "diff": Jsonb(changes),
                "by": self._actor(),
            },
        )
        self._tell(id, version)

    def _tell(self, id: UUID, version: int, only: dict[UUID, list[UUID]] | None = None) -> int:
        """One outbox email per contact of each vendor, never sent from the request (SRC-005).

        `only` narrows a resend to those contacts; its emails get a fresh idempotency key.
        """
        row = self.repo.execute(
            "SELECT r.number, r.title, v.snapshot, v.reason, v.diff FROM rfq r "
            "JOIN rfq_version v ON v.org_id = r.org_id AND v.rfq_id = r.id "
            "AND v.version = %(version)s "
            "WHERE r.org_id = %(org_id)s AND r.id = %(id)s",
            {"id": id, "version": version},
        ).fetchone()
        assert row is not None
        context = {
            "rfq_number": row[0],
            "title": row[1],
            "version": version,
            "content": row[2],
            "reason": row[3],
            "changes": row[4],
        }
        outbox = OutboxStore(cast(OutboxConnection, self.connection), self.scope)
        queued = 0
        for vendor_id, contact_ids, _ in self._invitees(id):
            if only is not None:
                if vendor_id not in only:
                    continue
                contact_ids = only[vendor_id]
            for contact in self._recipients(vendor_id, contact_ids):
                key = f"rfq:{id}:v{version}:{contact.id}"
                if only is not None:
                    tries = self.repo.execute(
                        "SELECT count(*) FROM rfq_invitee_version WHERE org_id = %(org_id)s "
                        "AND rfq_id = %(id)s AND contact_id = %(contact)s "
                        "AND version = %(version)s",
                        {"id": id, "contact": contact.id, "version": version},
                    ).fetchone()
                    key += f":resend{tries[0] if tries else 0}"
                record = outbox.add_email(
                    to=contact.email,
                    template=TEMPLATE,
                    context=context,
                    idempotency_key=key,
                )
                self.repo.execute(
                    "INSERT INTO rfq_invitee_version (org_id, rfq_id, vendor_id, contact_id, "
                    "version, outbox_id) VALUES (%(org_id)s, %(id)s, %(vendor)s, %(contact)s, "
                    "%(version)s, %(outbox)s)",
                    {
                        "id": id,
                        "vendor": vendor_id,
                        "contact": contact.id,
                        "version": version,
                        "outbox": record.id,
                    },
                )
                queued += 1
        return queued

    def resend(self, id: UUID, vendor_id: UUID) -> RfqRead:
        """Send the current version again to this vendor's contacts that never received it."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            head = self.repo.execute(
                "SELECT status, version FROM rfq WHERE org_id = %(org_id)s AND id = %(id)s "
                "FOR UPDATE",
                {"id": id},
            ).fetchone()
            if head is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            if head[0] != "published":
                raise conflict("not_published", "Only a published RFQ can be sent again.")
            vendor = next((v for v in self._invitees(id) if v[0] == vendor_id), None)
            if vendor is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            version = cast(int, head[1])
            # a contact still queued or already sent is not sent a second copy
            held = {
                cast(UUID, r[0])
                for r in self.repo.execute(
                    "SELECT t.contact_id FROM rfq_invitee_version t "
                    "JOIN outbox o ON o.id = t.outbox_id WHERE t.org_id = %(org_id)s "
                    "AND t.rfq_id = %(id)s AND t.version = %(version)s "
                    "AND o.state IN ('pending', 'sent', 'failed')",
                    {"id": id, "version": version},
                ).fetchall()
            }
            wanted = [c for c in vendor[1] if c not in held]
            if not wanted:
                raise conflict(
                    "nothing_to_resend", f"{vendor[2]} already has version {version} or is queued."
                )
            sent = self._tell(id, version, {vendor_id: wanted})
            self._audit("rfq.resend", id, None, {"vendor_id": str(vendor_id), "emails": sent})
        return self.get(id)

    # -- amend, extend, evaluate ---------------------------------------------------------------

    def amend(self, id: UUID, body: RfqAmend) -> RfqRead:
        return self._amend(id, "amend", body)

    def extend(self, id: UUID, body: ExtendDeadline) -> RfqRead:
        return self._amend(
            id,
            "extend",
            RfqAmend(
                reason=body.reason or "Deadline extended.",
                deadline_at=body.deadline_at,
                deadline_tz=body.deadline_tz,
            ),
        )

    def _amend(self, id: UUID, action: str, body: RfqAmend) -> RfqRead:
        """A new version under the row lock; the published row's history is never rewritten."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            head = self._lock(id, action)
            if head[3]:  # the close job has not run yet, but vendors can no longer respond
                raise conflict("deadline_passed", "The deadline has passed. The RFQ is closing.")
            current = cast(datetime, head[1])
            # an extension is later-only by instant, whatever the zone says
            if body.deadline_at is not None and (
                body.deadline_at <= current if action == "extend" else body.deadline_at < current
            ):
                raise invalid(
                    "deadline_at",
                    "Choose a deadline later than the current one.",
                    "deadline_earlier",
                )
            before = snapshot(self.repo, id)
            if body.lines is not None:
                self._quantities_only(id, body)
            self._apply_content(id, body)
            if diff(before, snapshot(self.repo, id)) == {}:
                raise invalid("reason", "Change something to amend the RFQ.", "no_change")
            version = cast(int, head[2]) + 1
            self.repo.execute(
                "UPDATE rfq SET version = %(version)s, updated_at = now(), "
                "edit_revision = edit_revision + 1 WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": id, "version": version},
            )
            self._record(id, version, body.reason)
            self._audit(
                f"rfq.{action}",
                id,
                {"version": version - 1},
                {"version": version, "reason": body.reason},
            )
        return self.get(id)

    def _quantities_only(self, id: UUID, body: RfqAmend) -> None:
        """An amendment keeps the RFQ's lines; their quantities move through the guard."""
        unit = self.repo.execute(
            "SELECT unit_id, currency FROM rfq WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": id},
        ).fetchone()
        assert unit is not None and body.lines is not None
        have = {
            cast(UUID, r[0])
            for r in self.repo.execute(
                "SELECT requisition_line_id FROM rfq_line WHERE org_id = %(org_id)s "
                "AND rfq_id = %(id)s",
                {"id": id},
            ).fetchall()
        }
        if {line.requisition_line_id for line in body.lines} != have:
            raise invalid(
                "lines",
                "An amendment changes quantities only, not which lines are on the RFQ.",
                "lines_fixed",
            )
        # A sourced requisition has no open quantity (decision 206); handing quantity back would
        # leave it sourced with units nobody can source. Refuse until reopening is decided.
        lowered = [
            line.requisition_line_id
            for line in body.lines
            if self.repo.execute(
                "SELECT 1 FROM rfq_line l JOIN requisition r ON r.org_id = l.org_id "
                "AND r.id = l.requisition_id WHERE l.org_id = %(org_id)s AND l.rfq_id = %(id)s "
                "AND l.requisition_line_id = %(line)s AND l.quantity > %(quantity)s "
                "AND r.status = 'sourced'",
                {"id": id, "line": line.requisition_line_id, "quantity": Decimal(line.quantity)},
            ).fetchone()
        ]
        if lowered:
            raise conflict(
                "requisition_sourced",
                "This requisition is fully sourced, so its quantity cannot be reduced here.",
            )
        self._apply_lines(
            id, cast(UUID, unit[0]), cast(str, unit[1]), RfqLinesPatch(lines=body.lines)
        )

    def start_evaluation(self, id: UUID) -> RfqRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._lock(id, "start_evaluation")
            self.repo.execute(
                "UPDATE rfq SET status = 'under_evaluation', updated_at = now(), "
                "edit_revision = edit_revision + 1 WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": id},
            )
            self._audit(
                "rfq.start_evaluation", id, {"status": "closed"}, {"status": "under_evaluation"}
            )
        return self.get(id)

    def versions(self, id: UUID) -> list[RfqVersionRead]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rows = self.repo.execute(
                "SELECT v.version, v.reason, v.diff, v.snapshot, i.email, i.display_name, "
                "v.published_at "
                "FROM rfq_version v JOIN identity i ON i.id = v.published_by "
                "WHERE v.org_id = %(org_id)s AND v.rfq_id = %(id)s ORDER BY v.version",
                {"id": id},
            ).fetchall()
        return [
            RfqVersionRead.model_validate(
                dict(
                    zip(
                        (
                            "version",
                            "reason",
                            "diff",
                            "snapshot",
                            "published_by_email",
                            "published_by_name",
                            "published_at",
                        ),
                        r,
                        strict=True,
                    )
                )
            )
            for r in rows
        ]


# -- the close job -------------------------------------------------------------------------------


def close_due(conn: Conn, scope: Scope) -> int:
    """Close each published RFQ whose deadline has passed; running it twice closes nothing more."""
    with correlation_context(JOB_KIND), tenant_transaction(cast(RlsSession, conn), scope):
        closed = (
            RfqRepository(conn, scope)
            .execute(
                "UPDATE rfq SET status = 'closed', updated_at = now(), "
                "edit_revision = edit_revision + 1 WHERE org_id = %(org_id)s "
                "AND status = 'published' AND deadline_at <= now() RETURNING id, number",
            )
            .fetchall()
        )
        for id, number in closed:
            audit_event(
                cast(AuditConnection, conn),
                scope,
                actor=AuditActor(ActorKind.JOB, None),
                action="rfq.close",
                target_type="rfq",
                target_id=cast(UUID, id),
                outcome=Outcome.SUCCESS,
                before={"status": "published"},
                after={"status": "closed", "number": number},
            )
    return len(closed)


def enqueue_due(conn: Conn) -> int:
    """Queue one close job for each organization that has an RFQ past its deadline."""
    with conn.transaction():
        orgs = conn.execute(
            "SELECT DISTINCT org_id FROM identity_membership ORDER BY org_id"
        ).fetchall()
    queued = 0
    for row in orgs:
        scope = Scope(cast(UUID, row[0]))
        with correlation_context(JOB_KIND), tenant_transaction(cast(RlsSession, conn), scope):
            due = (
                RfqRepository(conn, scope)
                .execute(
                    "SELECT 1 FROM rfq WHERE org_id = %(org_id)s AND status = 'published' "
                    "AND deadline_at <= now() LIMIT 1"
                )
                .fetchone()
            )
            if due:
                JobQueue(cast(JobConnection, conn), scope).enqueue(JOB_KIND, {})
                queued += 1
    return queued


def job_handler(connect: Callable[[], Conn]) -> Callable[[Job], None]:
    """Run on an independent connection while the runner owns its queue transaction."""

    def handle(job: Job) -> None:
        with connect() as conn:
            close_due(conn, Scope(job.org_id))

    return handle
