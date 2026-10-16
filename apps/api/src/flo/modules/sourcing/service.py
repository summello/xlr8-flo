"""RFQ drafts from approved requisition lines, under the open-quantity guard (SRC-001, 003)."""

import base64
import json
from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError, conflict
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.service import NumberingService, today
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
    NotSelectable,
    RfqCreate,
    RfqLineIn,
    RfqLineRead,
    RfqLinesPatch,
    RfqRead,
    SourceableLine,
    SourceablePage,
    invalid,
)

type Conn = psycopg.Connection[tuple[object, ...]]


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
                "r.version, r.edit_revision, r.created_at, r.updated_at FROM rfq r "
                "JOIN identity i ON i.id = r.owner_id "
                "WHERE r.org_id = %(org_id)s AND r.id = %(id)s",
                {"id": id},
            ).fetchone()
            if head is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            rows = self.repo.execute(
                "SELECT id, line_no, requisition_id, requisition_line_id, requisition_version, "
                "quantity::text, description, uom_code, needed_by, currency FROM rfq_line "
                "WHERE org_id = %(org_id)s AND rfq_id = %(id)s ORDER BY line_no",
                {"id": id},
            ).fetchall()
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
            }
        )

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
            current = {
                cast(UUID, r[0]): r
                for r in self.repo.execute(
                    "SELECT requisition_line_id, id, requisition_id, requisition_version, "
                    "quantity, line_no FROM rfq_line WHERE org_id = %(org_id)s AND rfq_id = %(id)s",
                    {"id": id},
                ).fetchall()
            }
            basis = self._basis(body.lines)
            self._same_unit_and_currency(body.lines, basis, cast(UUID, head[1]), cast(str, head[2]))
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
