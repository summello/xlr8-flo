"""RFQ drafts from approved requisition lines, under the open-quantity guard (SRC-001, 003)."""

import base64
import json
from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError, conflict
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.collaboration.service import DocumentAccess, register_document_access
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.org.service import NumberingService, OrgService, today
from flo.modules.requisitions.schemas import (
    LineSourcing,
    LinkAccess,
    LinkedNode,
    SourcingBasis,
    Tone,
)
from flo.modules.requisitions.service import (
    register_link_provider,
    source_lines,
    sourceable_lines,
    sourcing_basis,
)
from flo.modules.sourcing.models import RfqRepository
from flo.modules.sourcing.schemas import (
    DeliveryRead,
    InviteeCreate,
    InviteePatch,
    InviteeRead,
    NotSelectable,
    RequirementRead,
    RequirementsFromTemplate,
    RequirementsPut,
    RequirementWaive,
    RfqAttachmentRead,
    RfqContentPatch,
    RfqCreate,
    RfqLineIn,
    RfqLineRead,
    RfqLinesPatch,
    RfqRead,
    SourceableLine,
    SourceablePage,
    VisibilityPut,
    invalid,
)
from flo.modules.vendors.service import VendorService

type Conn = psycopg.Connection[tuple[object, ...]]

MAX_INVITEES = 5  # decision 106
CONTENT = "specifications, delivery_location, delivery_terms, terms, deadline_at, deadline_tz"


def _decode(cursor: str) -> tuple[str, int, UUID]:
    try:
        number, line_no, line_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return str(number), int(line_no), UUID(line_id)
    except (ValueError, TypeError):
        raise invalid("cursor", "The page cursor is not valid.", "invalid_cursor") from None


def _encode(row: SourcingBasis) -> str:
    return base64.urlsafe_b64encode(
        json.dumps([row.requisition_number, row.line_no, str(row.line_id)]).encode()
    ).decode()


class RfqService:
    def __init__(
        self,
        connection: Conn,
        scope: Scope,
        actor_id: UUID | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.connection, self.scope = connection, scope
        self.actor_id, self.settings = actor_id, settings
        self.repo = RfqRepository(connection, scope)

    # -- reads -----------------------------------------------------------------------------

    def unit_of(self, id: UUID) -> UUID:
        """The unit an RFQ belongs to, 404 for an unknown or foreign id."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                "SELECT unit_id FROM rfq WHERE org_id = %(org_id)s AND id = %(id)s", {"id": id}
            ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return cast(UUID, row[0])

    def unit_of_line(self, requisition_line_id: UUID) -> UUID:
        """The unit a requisition line is sourced for, 404 for an unknown or foreign id."""
        found = sourcing_basis(self.connection, self.scope, [requisition_line_id])
        if not found:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return found[0].bu_id

    def get(self, id: UUID) -> RfqRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            head = self.repo.execute(
                "SELECT r.id, r.number, r.title, r.status, r.unit_id, r.currency, i.email, "
                "r.version, r.edit_revision, r.created_at, r.updated_at, r.specifications, "
                "r.delivery_location, r.delivery_terms, r.terms, r.deadline_at, r.deadline_tz, "
                "r.published_at FROM rfq r JOIN identity i ON i.id = r.owner_id "
                "WHERE r.org_id = %(org_id)s AND r.id = %(id)s",
                {"id": id},
            ).fetchone()
            if head is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            rows = self.repo.execute(
                "SELECT id, line_no, requisition_id, requisition_line_id, requisition_version, "
                "quantity::text, description, uom_code, needed_by, currency, specification "
                "FROM rfq_line "
                "WHERE org_id = %(org_id)s AND rfq_id = %(id)s ORDER BY line_no",
                {"id": id},
            ).fetchall()
            extra = self._extra(id, cast(int, head[7]))
        live = {
            b.line_id: b
            for b in sourcing_basis(self.connection, self.scope, [cast(UUID, r[3]) for r in rows])
        }
        lines = [
            RfqLineRead(
                id=cast(UUID, r[0]),
                line_no=cast(int, r[1]),
                requisition_id=cast(UUID, r[2]),
                requisition_number=live[cast(UUID, r[3])].requisition_number,
                requisition_line_id=cast(UUID, r[3]),
                requisition_line_no=live[cast(UUID, r[3])].line_no,
                requisition_version=cast(int, r[4]),
                quantity=cast(str, r[5]),
                description=cast(str, r[6]),
                uom_code=cast(str, r[7]),
                needed_by=cast(date | None, r[8]),
                currency=cast(str, r[9]),
                specification=cast(str | None, r[10]),
                open_qty=live[cast(UUID, r[3])].open_qty,
            )
            for r in rows
        ]
        keys = ("id", "number", "title", "status", "unit_id", "currency", "owner_email")
        return RfqRead.model_validate(
            {
                **dict(zip(keys, head[:7], strict=True)),
                "version": head[7],
                "edit_revision": head[8],
                "created_at": head[9],
                "updated_at": head[10],
                "lines": lines,
                "published_at": head[17],
                **dict(zip(CONTENT.split(", "), head[11:17], strict=True)),
                **extra,
            }
        )

    def _extra(self, id: UUID, version: int) -> dict[str, list[object]]:
        """Requirements, attachments with visibility, invitees with live eligibility."""
        requirements = [
            RequirementRead(
                id=cast(UUID, r[0]),
                position=cast(int, r[1]),
                text=cast(str, r[2]),
                waived=cast(bool, r[3]),
                waived_by_email=cast(str | None, r[4]),
                waived_reason=cast(str | None, r[5]),
            )
            for r in self.repo.execute(
                "SELECT q.id, q.position, q.text, q.waived, i.email, q.waived_reason "
                "FROM rfq_requirement q LEFT JOIN identity i ON i.id = q.waived_by "
                "WHERE q.org_id = %(org_id)s AND q.rfq_id = %(id)s ORDER BY q.position",
                {"id": id},
            ).fetchall()
        ]
        attachments = [
            RfqAttachmentRead(
                id=cast(UUID, r[0]),
                display_name=cast(str, r[1]),
                media_type=cast(str, r[2]),
                size_bytes=cast(int, r[3]),
                scan_status=cast(str, r[4]),
                vendor_visible=cast(bool, r[5]),
            )
            for r in self.repo.execute(
                "SELECT a.id, v.display_name, v.media_type, v.size_bytes, v.scan_status, "
                "COALESCE(x.vendor_visible, false) FROM attachment a "
                "JOIN attachment_version v ON v.org_id = a.org_id AND v.attachment_id = a.id "
                "AND v.version = (SELECT max(m.version) FROM attachment_version m "
                "WHERE m.org_id = a.org_id AND m.attachment_id = a.id) "
                "LEFT JOIN rfq_attachment_visibility x ON x.org_id = a.org_id "
                "AND x.attachment_id = a.id AND x.rfq_id = a.document_id "
                "WHERE a.org_id = %(org_id)s AND a.document_type = 'rfq' "
                "AND a.document_id = %(id)s AND a.deleted_at IS NULL ORDER BY a.created_at, a.id",
                {"id": id},
            ).fetchall()
        ]
        invited = self.repo.execute(
            "SELECT n.vendor_id, n.contact_ids, n.vendor_snapshot, n.override_reason, i.email, "
            "n.invited_at, r.unit_id FROM rfq_invitee n JOIN rfq r ON r.id = n.rfq_id "
            "JOIN identity i ON i.id = n.invited_by "
            "WHERE n.org_id = %(org_id)s AND n.rfq_id = %(id)s ORDER BY n.invited_at, n.vendor_id",
            {"id": id},
        ).fetchall()
        live = (
            VendorService(self.connection, self.scope).eligibility(
                [cast(UUID, r[0]) for r in invited], cast(UUID, invited[0][6]), today()
            )
            if invited
            else {}
        )
        told: dict[UUID, list[DeliveryRead]] = {}
        for r in self.repo.execute(
            "SELECT t.vendor_id, t.contact_id, t.version, "
            "CASE o.state WHEN 'pending' THEN 'queued' WHEN 'sent' THEN 'sent' "
            "WHEN 'dead' THEN 'bounced' ELSE 'failed' END "
            "FROM rfq_invitee_version t "
            "JOIN outbox o ON o.id = t.outbox_id "
            "WHERE t.org_id = %(org_id)s AND t.rfq_id = %(id)s ORDER BY t.created_at, t.contact_id",
            {"id": id},
        ).fetchall():
            told.setdefault(cast(UUID, r[0]), []).append(
                DeliveryRead.model_validate({"contact_id": r[1], "version": r[2], "state": r[3]})
            )

        def sent(vendor: UUID) -> set[int]:
            return {d.version for d in told.get(vendor, []) if d.state == "sent"}

        invitees = [
            InviteeRead(
                vendor_id=cast(UUID, r[0]),
                contact_ids=cast(list[UUID], r[1]),
                vendor_snapshot=cast(dict[str, object], r[2]),
                override_reason=cast(str | None, r[3]),
                invited_by_email=cast(str, r[4]),
                invited_at=cast(datetime, r[5]),
                eligible=live[cast(UUID, r[0])].eligible if r[0] in live else None,
                reasons=[x.text for x in live[cast(UUID, r[0])].reasons] if r[0] in live else [],
                deliveries=told.get(cast(UUID, r[0]), []),
                latest_sent_version=max(sent(cast(UUID, r[0])), default=None),
                not_told_of=[v for v in range(1, version + 1) if v not in sent(cast(UUID, r[0]))],
            )
            for r in invited
        ]
        return {
            "requirements": list(requirements),
            "attachments": list(attachments),
            "invitees": list(invitees),
        }

    def sourceable(
        self,
        *,
        unit_id: UUID | None,
        currency: str | None,
        q: str,
        cursor: str | None,
        limit: int,
        with_rfq: UUID | None,
    ) -> SourceablePage:
        if not 1 <= limit <= 100:
            raise invalid("limit", "Choose a limit from 1 to 100.")
        rfq_unit = rfq_currency = None
        on_rfq: set[UUID] = set()
        if with_rfq is not None:
            rfq = self.get(with_rfq)  # 404 if foreign
            rfq_unit, rfq_currency = rfq.unit_id, rfq.currency
            on_rfq = {line.requisition_line_id for line in rfq.lines}
        rows = sourceable_lines(
            self.connection,
            self.scope,
            unit_id=unit_id,
            currency=currency,
            q=q,
            after=_decode(cursor) if cursor else None,
            limit=limit + 1,
        )
        page = rows[:limit]

        def reason(row: SourcingBasis) -> NotSelectable | None:
            if rfq_unit is not None and row.bu_id != rfq_unit:
                return "another_unit"
            if rfq_currency is not None and row.currency != rfq_currency:
                return "different_currency"
            if row.amendment_pending:
                return "amendment_pending"
            return "on_rfq" if row.line_id in on_rfq else None

        return SourceablePage(
            rows=[
                SourceableLine(
                    line_id=r.line_id,
                    requisition_id=r.requisition_id,
                    requisition_number=r.requisition_number,
                    requisition_version=r.requisition_version,
                    line_no=r.line_no,
                    unit_id=r.bu_id,
                    currency=r.currency,
                    description=r.description,
                    uom_code=r.uom_code,
                    needed_by=r.needed_by,
                    requested_qty=r.requested_qty,
                    sourced_qty=r.sourced_qty,
                    awarded_qty=r.awarded_qty,
                    ordered_qty=r.ordered_qty,
                    cancelled_qty=r.cancelled_qty,
                    open_qty=r.open_qty,
                    selectable=reason(r) is None,
                    reason=reason(r),
                )
                for r in page
            ],
            next_cursor=_encode(page[-1]) if len(rows) > limit else None,
        )

    # -- writes ----------------------------------------------------------------------------

    def _audit(
        self, action: str, id: UUID, before: dict[str, object] | None, after: dict[str, object]
    ) -> None:
        audit_event(
            cast(AuditConnection, self.connection),
            self.scope,
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type="rfq",
            target_id=id,
            outcome=Outcome.SUCCESS,
            before=before,
            after=after,
        )

    def _basis(self, lines: list[RfqLineIn]) -> dict[UUID, SourcingBasis]:
        found = {
            b.line_id: b
            for b in sourcing_basis(
                self.connection, self.scope, [line.requisition_line_id for line in lines]
            )
        }
        if len(found) != len(lines):  # a foreign tenant's line is indistinguishable from none
            raise ProblemError(ErrorCode.NOT_FOUND)
        return found

    @staticmethod
    def _same_unit_and_currency(
        lines: list[RfqLineIn], basis: dict[UUID, SourcingBasis], unit_id: UUID, currency: str
    ) -> None:
        """Decision 144: one RFQ, one unit, one currency; every offending line is named."""
        errors, problem = [], None
        for index, line in enumerate(lines):
            row = basis[line.requisition_line_id]
            reason = (
                "another_unit"
                if row.bu_id != unit_id
                else "different_currency"
                if row.currency != currency
                else None
            )
            if reason is not None:
                problem = problem or reason
                message = (
                    f"{row.requisition_number} line {row.line_no} belongs to another unit "
                    "than the other lines. Start a separate RFQ for it."
                    if reason == "another_unit"
                    else f"{row.requisition_number} line {row.line_no} is in {row.currency}, "
                    f"not {currency}. Start a separate RFQ for it."
                )
                errors.append(
                    ProblemFieldError(field=f"lines/{index}/requisition_line_id", message=message)
                )
        if errors:
            raise ProblemError(
                ErrorCode.VALIDATION_FAILED,
                detail=errors[0].message,
                errors=tuple(errors),
                checks={"problem": problem or "invalid_rfq"},
            )

    def _insert_line(self, rfq_id: UUID, line_no: int, line: RfqLineIn, row: SourcingBasis) -> None:
        self.repo.execute(
            "INSERT INTO rfq_line (id, org_id, rfq_id, line_no, requisition_id, "
            "requisition_line_id, requisition_version, quantity, description, uom_code, "
            "needed_by, currency) VALUES (%(id)s, %(org_id)s, %(rfq_id)s, %(line_no)s, "
            "%(requisition_id)s, %(line_id)s, %(version)s, %(quantity)s, %(description)s, "
            "%(uom)s, %(needed_by)s, %(currency)s)",
            {
                "id": uuid4(),
                "rfq_id": rfq_id,
                "line_no": line_no,
                "requisition_id": row.requisition_id,
                "line_id": line.requisition_line_id,
                "version": line.requisition_version,
                "quantity": Decimal(line.quantity),
                "description": row.description,
                "uom": row.uom_code,
                "needed_by": row.needed_by,
                "currency": row.currency,
            },
        )

    def create(self, body: RfqCreate) -> RfqRead:
        id = uuid4()
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            basis = self._basis(body.lines)
            first = basis[body.lines[0].requisition_line_id]
            self._same_unit_and_currency(body.lines, basis, first.bu_id, first.currency)
            source_lines(
                self.connection,
                self.scope,
                [
                    LineSourcing(
                        basis[line.requisition_line_id].requisition_id,
                        line.requisition_line_id,
                        line.requisition_version,
                        Decimal(line.quantity),
                    )
                    for line in body.lines
                ],
                self.actor_id,
            )
            number = NumberingService(self.settings or Settings(), self.scope).allocate(
                self.scope.org_id, "rfq", today()
            )
            self.repo.execute(
                "INSERT INTO rfq (id, org_id, number, title, unit_id, currency, owner_id) "
                "VALUES (%(id)s, %(org_id)s, %(number)s, %(title)s, %(unit_id)s, %(currency)s, "
                "%(owner)s)",
                {
                    "id": id,
                    "number": number,
                    "title": body.title,
                    "unit_id": first.bu_id,
                    "currency": first.currency,
                    "owner": self.actor_id,
                },
            )
            for line_no, line in enumerate(body.lines, start=1):
                self._insert_line(id, line_no, line, basis[line.requisition_line_id])
            self._audit(
                "rfq.create",
                id,
                None,
                {
                    "number": number,
                    "title": body.title,
                    "lines": {str(line.requisition_line_id): line.quantity for line in body.lines},
                },
            )
        return self.get(id)

    @staticmethod
    def _check_edit_revision(current: int, if_match: str | None) -> None:
        """PATCH is not replayed by the idempotency middleware; If-Match stops a stale retry."""
        if if_match is None:
            raise ProblemError(
                ErrorCode.BAD_REQUEST,
                detail="Supply If-Match with the RFQ edit revision.",
                checks={"problem": "if_match_required"},
            )
        try:
            expected = int(if_match.strip('"'))
        except ValueError:
            raise ProblemError(
                ErrorCode.BAD_REQUEST,
                detail="If-Match must contain an integer edit revision.",
                checks={"problem": "if_match_invalid"},
            ) from None
        if expected != current:
            raise conflict("stale_version", "The RFQ changed. Reload it and retry your update.")

    def _apply_lines(
        self, id: UUID, unit_id: UUID, currency: str, body: RfqLinesPatch
    ) -> tuple[dict[UUID, tuple[object, ...]], dict[UUID, RfqLineIn]]:
        """Move the RFQ onto `body`'s lines through the open-quantity guard. Call in a tx."""
        current = {
            cast(UUID, r[0]): r
            for r in self.repo.execute(
                "SELECT requisition_line_id, id, requisition_id, requisition_version, "
                "quantity, line_no FROM rfq_line WHERE org_id = %(org_id)s AND rfq_id = %(id)s",
                {"id": id},
            ).fetchall()
        }
        basis = self._basis(body.lines)
        self._same_unit_and_currency(body.lines, basis, unit_id, currency)
        wanted = {line.requisition_line_id: line for line in body.lines}
        changes: list[LineSourcing] = []
        for line_id, row in current.items():
            if line_id not in wanted:
                changes.append(
                    LineSourcing(
                        cast(UUID, row[2]), line_id, cast(int, row[3]), -cast(Decimal, row[4])
                    )
                )
        for line in body.lines:
            old = current.get(line.requisition_line_id)
            delta = Decimal(line.quantity) - (cast(Decimal, old[4]) if old else Decimal(0))
            if delta != 0:
                changes.append(
                    LineSourcing(
                        basis[line.requisition_line_id].requisition_id,
                        line.requisition_line_id,
                        line.requisition_version,
                        delta,
                    )
                )
        source_lines(self.connection, self.scope, changes, self.actor_id)
        next_no = max((cast(int, r[5]) for r in current.values()), default=0) + 1
        for line_id, row in current.items():
            if line_id not in wanted:
                self.repo.execute(
                    "DELETE FROM rfq_line WHERE org_id = %(org_id)s AND id = %(id)s",
                    {"id": row[1]},
                )
        for line in body.lines:
            old = current.get(line.requisition_line_id)
            if old is None:
                self._insert_line(id, next_no, line, basis[line.requisition_line_id])
                next_no += 1
            elif Decimal(line.quantity) != cast(Decimal, old[4]):
                # Only an increase was checked against the requisition's version.
                grew = Decimal(line.quantity) > cast(Decimal, old[4])
                self.repo.execute(
                    "UPDATE rfq_line SET quantity = %(quantity)s, "
                    "requisition_version = CASE WHEN %(grew)s THEN %(version)s "
                    "ELSE requisition_version END "
                    "WHERE org_id = %(org_id)s AND id = %(id)s",
                    {
                        "id": old[1],
                        "quantity": Decimal(line.quantity),
                        "version": line.requisition_version,
                        "grew": grew,
                    },
                )
        return current, wanted

    def update_lines(self, id: UUID, body: RfqLinesPatch, if_match: str | None) -> RfqRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            head = self.repo.execute(
                "SELECT status, unit_id, currency, edit_revision FROM rfq "
                "WHERE org_id = %(org_id)s AND id = %(id)s FOR UPDATE",
                {"id": id},
            ).fetchone()
            if head is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            self._check_edit_revision(cast(int, head[3]), if_match)
            if head[0] != "draft":
                raise conflict(
                    "not_draft", "Only a draft RFQ can change its lines. Reload it and retry."
                )
            current, wanted = self._apply_lines(id, cast(UUID, head[1]), cast(str, head[2]), body)
            self.repo.execute(
                "UPDATE rfq SET updated_at = now(), edit_revision = edit_revision + 1 "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": id},
            )
            self._audit(
                "rfq.lines.update",
                id,
                {str(k): format(cast(Decimal, v[4]), "f") for k, v in current.items()},
                {str(k): v.quantity for k, v in wanted.items()},
            )
        return self.get(id)

    # -- content, requirements, attachments, invitees (E12-S02) ----------------------------

    def _draft(
        self,
        id: UUID,
        if_match: str | None = None,
        *,
        versioned: bool = True,
        states: tuple[str, ...] = ("draft",),
    ) -> UUID:
        """Lock the RFQ row; 404 if foreign, 409 `not_draft`; returns its unit. Call in a tx."""
        head = self.repo.execute(
            "SELECT status, unit_id, edit_revision FROM rfq "
            "WHERE org_id = %(org_id)s AND id = %(id)s FOR UPDATE",
            {"id": id},
        ).fetchone()
        if head is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        if versioned:
            self._check_edit_revision(cast(int, head[2]), if_match)
        if head[0] not in states:
            raise conflict("not_draft", "Only a draft RFQ can change. Reload it and retry.")
        return cast(UUID, head[1])

    def _bump(self, id: UUID) -> None:
        self.repo.execute(
            "UPDATE rfq SET updated_at = now(), edit_revision = edit_revision + 1 "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": id},
        )

    def _apply_content(
        self, id: UUID, body: RfqContentPatch
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Write the fields sent and line specifications; returns (before, sent). Call in a tx."""
        sent = body.model_dump(exclude_unset=True, exclude={"line_specifications"})
        # the deadline and its zone cannot be cleared; null means "unchanged"
        sent = {
            k: v
            for k, v in sent.items()
            if v is not None or k not in ("deadline_at", "deadline_tz")
        }
        sent.pop("reason", None)  # an amendment's reason and lines are not RFQ columns
        sent.pop("lines", None)
        before = dict(
            zip(
                CONTENT.split(", "),
                self.repo.execute(
                    f"SELECT {CONTENT} FROM rfq WHERE org_id = %(org_id)s AND id = %(id)s",
                    {"id": id},
                ).fetchone()
                or (),
                strict=True,
            )
        )
        if sent:
            self.repo.execute(
                "UPDATE rfq SET " + ", ".join(f"{k} = %({k})s" for k in sent) + " "
                "WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": id, **sent},
            )
        for line_id, text in (body.line_specifications or {}).items():
            done = self.repo.execute(
                "UPDATE rfq_line SET specification = %(text)s "
                "WHERE org_id = %(org_id)s AND rfq_id = %(id)s AND id = %(line)s",
                {"id": id, "line": line_id, "text": text},
            )
            if done.rowcount == 0:
                raise ProblemError(ErrorCode.NOT_FOUND)
        return before, sent

    def update_content(self, id: UUID, body: RfqContentPatch, if_match: str | None) -> RfqRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(id, if_match)
            before, sent = self._apply_content(id, body)
            self._bump(id)
            self._audit(
                "rfq.update",
                id,
                {k: before[k] for k in sent},
                {**sent, "line_specifications": len(body.line_specifications or {})},
            )
        return self.get(id)

    def _add_requirements(self, id: UUID, texts: list[str]) -> None:
        position = cast(
            int,
            self.repo.execute(
                "SELECT COALESCE(max(position), 0) FROM rfq_requirement "
                "WHERE org_id = %(org_id)s AND rfq_id = %(id)s",
                {"id": id},
            ).fetchone()[0],  # type: ignore[index]
        )
        for offset, text in enumerate(texts, start=1):
            self.repo.execute(
                "INSERT INTO rfq_requirement (id, org_id, rfq_id, position, text) "
                "VALUES (%(rid)s, %(org_id)s, %(id)s, %(position)s, %(text)s)",
                {"rid": uuid4(), "id": id, "position": position + offset, "text": text},
            )

    def put_requirements(self, id: UUID, body: RequirementsPut, if_match: str | None) -> RfqRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(id, if_match)
            old = {
                cast(str, r[0]): r
                for r in self.repo.execute(
                    "SELECT text, waived, waived_by, waived_reason FROM rfq_requirement "
                    "WHERE org_id = %(org_id)s AND rfq_id = %(id)s",
                    {"id": id},
                ).fetchall()
            }
            self.repo.execute(
                "DELETE FROM rfq_requirement WHERE org_id = %(org_id)s AND rfq_id = %(id)s",
                {"id": id},
            )
            self._add_requirements(id, list(dict.fromkeys(body.requirements)))
            # a requirement kept word for word keeps its waiver
            for text, (_, waived, by, reason) in old.items():
                if waived and text in body.requirements:
                    self.repo.execute(
                        "UPDATE rfq_requirement SET waived = true, waived_by = %(by)s, "
                        "waived_reason = %(reason)s WHERE org_id = %(org_id)s "
                        "AND rfq_id = %(id)s AND text = %(text)s",
                        {"id": id, "text": text, "by": by, "reason": reason},
                    )
            self._bump(id)
            self._audit(
                "rfq.requirement.replace",
                id,
                {"count": len(old)},
                {"count": len(body.requirements)},
            )
        return self.get(id)

    def requirements_from_template(self, id: UUID, body: RequirementsFromTemplate) -> RfqRead:
        template = OrgService(self.connection, self.scope).get_master(
            "rfq_requirement_template", body.template_id
        )
        if not template.active:
            raise ProblemError(ErrorCode.NOT_FOUND)
        wanted = cast(list[str], template.attributes["requirements"])
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(id, versioned=False)
            have = {
                cast(str, r[0])
                for r in self.repo.execute(
                    "SELECT text FROM rfq_requirement WHERE org_id = %(org_id)s "
                    "AND rfq_id = %(id)s",
                    {"id": id},
                ).fetchall()
            }
            fresh = [t for t in dict.fromkeys(wanted) if t not in have]
            self._add_requirements(id, fresh)
            self._bump(id)
            self._audit("rfq.requirement.from_template", id, None, {"added": len(fresh)})
        return self.get(id)

    def waive_requirement(self, id: UUID, requirement_id: UUID, body: RequirementWaive) -> RfqRead:
        """The caller's `rfq.approve` on the RFQ's unit is checked by the route."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(id, versioned=False)
            done = self.repo.execute(
                "UPDATE rfq_requirement SET waived = true, waived_by = %(by)s, "
                "waived_reason = %(reason)s WHERE org_id = %(org_id)s AND rfq_id = %(id)s "
                "AND id = %(rid)s AND NOT waived",
                {"id": id, "rid": requirement_id, "by": self.actor_id, "reason": body.reason},
            )
            if done.rowcount == 0:
                exists = self.repo.execute(
                    "SELECT 1 FROM rfq_requirement WHERE org_id = %(org_id)s "
                    "AND rfq_id = %(id)s AND id = %(rid)s",
                    {"id": id, "rid": requirement_id},
                ).fetchone()
                if exists is None:
                    raise ProblemError(ErrorCode.NOT_FOUND)
                raise conflict("already_waived", "This requirement is already waived.")
            self._bump(id)
            self._audit(
                "rfq.requirement.waive",
                id,
                None,
                {"requirement_id": str(requirement_id), "reason": body.reason},
            )
        return self.get(id)

    def set_visibility(
        self, id: UUID, attachment_id: UUID, body: VisibilityPut, if_match: str | None
    ) -> RfqRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(id, if_match)
            found = self.repo.execute(
                "SELECT 1 FROM attachment WHERE org_id = %(org_id)s AND id = %(aid)s "
                "AND document_type = 'rfq' AND document_id = %(id)s AND deleted_at IS NULL",
                {"id": id, "aid": attachment_id},
            ).fetchone()
            if found is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            self.repo.execute(
                "INSERT INTO rfq_attachment_visibility (org_id, rfq_id, attachment_id, "
                "vendor_visible) VALUES (%(org_id)s, %(id)s, %(aid)s, %(visible)s) "
                "ON CONFLICT (rfq_id, attachment_id) DO UPDATE SET vendor_visible = %(visible)s",
                {"id": id, "aid": attachment_id, "visible": body.vendor_visible},
            )
            self._bump(id)
            self._audit(
                "rfq.attachment.visibility",
                id,
                None,
                {"attachment_id": str(attachment_id), "vendor_visible": body.vendor_visible},
            )
        return self.get(id)

    def _contacts(
        self, vendors: VendorService, vendor_id: UUID, wanted: list[UUID] | None
    ) -> list[UUID]:
        """The vendor's document recipients, or `wanted` if every one is a contact of the vendor."""
        contacts = vendors.list_contacts(vendor_id)
        recipients = sorted(
            (c for c in contacts if c.receives_documents),
            key=lambda c: c.type != "service_delivery",
        )
        if not recipients:
            raise conflict(
                "no_recipient",
                "This vendor has no contact who receives RFQs. "
                "Add a Service and Delivery contact who receives RFQs.",
            )
        if wanted is None:
            return [c.id for c in recipients]
        if not set(wanted) <= {c.id for c in contacts}:
            raise invalid("contact_ids", "Choose contacts of this vendor.", "invalid_contacts")
        return list(dict.fromkeys(wanted))

    def add_invitee(self, id: UUID, body: InviteeCreate, can_override: bool) -> RfqRead:
        """`can_override`: the caller holds `rfq.approve` on the RFQ's unit (decision 146)."""
        vendors = VendorService(self.connection, self.scope, self.actor_id)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            unit = self._draft(id, versioned=False)  # the row lock serialises the count below
            vendor = vendors.get(body.vendor_id)  # 404 for a foreign or unknown vendor
            have = self.repo.execute(
                "SELECT vendor_id FROM rfq_invitee WHERE org_id = %(org_id)s AND rfq_id = %(id)s",
                {"id": id},
            ).fetchall()
            if any(r[0] == body.vendor_id for r in have):
                raise conflict("already_invited", f"{vendor.trading_name} is already invited.")
            if len(have) >= MAX_INVITEES:  # even with an override
                raise conflict(
                    "invitee_limit",
                    f"Up to {MAX_INVITEES} vendors per RFQ. "
                    f"Remove one to invite {vendor.trading_name}.",
                )
            result = vendors.eligibility([body.vendor_id], unit, today())[body.vendor_id]
            overriding = not result.eligible
            if overriding and not (body.override_reason and can_override):
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=f"{vendor.trading_name} is not eligible: "
                    + "; ".join(r.text for r in result.reasons),
                    checks={
                        "problem": "vendor_not_eligible",
                        "reasons": "; ".join(r.text for r in result.reasons),
                    },
                )
            contact_ids = self._contacts(vendors, body.vendor_id, body.contact_ids)
            snapshot = {
                "trading_name": vendor.trading_name,
                "legal_name": vendor.legal_name,
                "number": vendor.number,
                "status": vendor.status,
                "eligibility": {
                    "eligible": result.eligible,
                    "reasons": [r.text for r in result.reasons],
                },
            }
            self.repo.execute(
                "INSERT INTO rfq_invitee (org_id, rfq_id, vendor_id, contact_ids, vendor_snapshot, "
                "override_reason, overridden_by, invited_by) VALUES (%(org_id)s, %(id)s, "
                "%(vendor)s, %(contacts)s, %(snapshot)s, %(reason)s, %(by)s, %(actor)s)",
                {
                    "id": id,
                    "vendor": body.vendor_id,
                    "contacts": contact_ids,
                    "snapshot": Jsonb(snapshot),
                    "reason": body.override_reason if overriding else None,
                    "by": self.actor_id if overriding else None,
                    "actor": self.actor_id,
                },
            )
            self._bump(id)
            after: dict[str, object] = {"vendor_id": str(body.vendor_id), **snapshot}
            self._audit("rfq.invitee.add", id, None, after)
            if overriding:
                self._audit(
                    "rfq.invitee.override",
                    id,
                    None,
                    {"vendor_id": str(body.vendor_id), "reason": body.override_reason},
                )
        return self.get(id)

    def remove_invitee(self, id: UUID, vendor_id: UUID, if_match: str | None) -> RfqRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(id, if_match)
            done = self.repo.execute(
                "DELETE FROM rfq_invitee WHERE org_id = %(org_id)s AND rfq_id = %(id)s "
                "AND vendor_id = %(vendor)s",
                {"id": id, "vendor": vendor_id},
            )
            if done.rowcount == 0:
                raise ProblemError(ErrorCode.NOT_FOUND)
            self._bump(id)
            self._audit("rfq.invitee.remove", id, {"vendor_id": str(vendor_id)}, {})
        return self.get(id)

    def update_invitee(
        self, id: UUID, vendor_id: UUID, body: InviteePatch, if_match: str | None
    ) -> RfqRead:
        vendors = VendorService(self.connection, self.scope, self.actor_id)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._draft(id, if_match, states=("draft", "published"))
            old = self.repo.execute(
                "SELECT contact_ids FROM rfq_invitee WHERE org_id = %(org_id)s "
                "AND rfq_id = %(id)s AND vendor_id = %(vendor)s",
                {"id": id, "vendor": vendor_id},
            ).fetchone()
            if old is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            contact_ids = self._contacts(vendors, vendor_id, body.contact_ids)
            self.repo.execute(
                "UPDATE rfq_invitee SET contact_ids = %(contacts)s WHERE org_id = %(org_id)s "
                "AND rfq_id = %(id)s AND vendor_id = %(vendor)s",
                {"id": id, "vendor": vendor_id, "contacts": contact_ids},
            )
            self._bump(id)
            self._audit(
                "rfq.invitee.update",
                id,
                {
                    "vendor_id": str(vendor_id),
                    "contacts": [str(c) for c in cast(list[UUID], old[0])],
                },
                {"vendor_id": str(vendor_id), "contacts": [str(c) for c in contact_ids]},
            )
        return self.get(id)


_TONES: dict[str, Tone] = {
    "draft": "neutral",
    "published": "info",
    "closed": "neutral",
    "under_evaluation": "info",
    "awarded": "success",
    "cancelled": "danger",
}


def _rfq_links(
    conn: Conn, scope: Scope, requisition_id: UUID, read_check: object
) -> list[LinkedNode]:
    """SRC-015: the requisition's Linked Records list every RFQ that sources one of its lines."""
    rows = (
        RfqRepository(conn, scope)
        .execute(
            "SELECT r.id, r.number, r.title, r.status, r.unit_id, r.created_at FROM rfq r "
            "WHERE r.org_id = %(org_id)s AND EXISTS (SELECT 1 FROM rfq_line l "
            "WHERE l.org_id = r.org_id AND l.rfq_id = r.id "
            "AND l.requisition_id = %(requisition_id)s)",
            {"requisition_id": requisition_id},
        )
        .fetchall()
    )
    return [
        LinkedNode(
            key=f"rfq:{r[0]}",
            kind="rfq",
            id=str(r[0]),
            label=f"RFQ {r[1]} · {r[2]}",
            state=str(r[3]).replace("_", " ").capitalize(),
            state_tone=_TONES[str(r[3])],
            href=f"/sourcing/rfqs/{r[0]}",
            at=cast(datetime, r[5]),
            relation="sourced_by",
            access=LinkAccess(permission="rfq.view", scope_type="bu", target_id=cast(UUID, r[4])),
        )
        for r in rows
    ]


register_link_provider("rfq", _rfq_links)


def rfq_access(connection: Conn, scope: Scope, id: UUID) -> TargetProtocol:
    """An RFQ is scoped to its unit; a foreign or unknown id is a 404."""
    row = connection.execute(
        "SELECT unit_id FROM rfq WHERE org_id = %(org_id)s AND id = %(id)s",
        {"org_id": scope.org_id, "id": id},
    ).fetchone()
    if row is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, cast(UUID, row[0]), "rfq", id))


def rfq_attachments_open(connection: Conn, scope: Scope, id: UUID) -> None:
    """Attachments change only while the RFQ is a draft; reading stays open."""
    row = connection.execute(
        "SELECT status FROM rfq WHERE org_id = %(org_id)s AND id = %(id)s",
        {"org_id": scope.org_id, "id": id},
    ).fetchone()
    if row is not None and row[0] != "draft":
        raise conflict("not_draft", "Only a draft RFQ can change. Reload it and retry.")


def register_access() -> None:
    """Attachments on an RFQ use the collaboration routes: read rfq.view, write rfq.edit."""
    register_document_access(
        DocumentAccess("rfq", "rfq.view", "rfq.edit", rfq_access, rfq_attachments_open)
    )
