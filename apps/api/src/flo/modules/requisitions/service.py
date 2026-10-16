"""Transactional requisition header creation, paging and draft updates."""

import base64
import json
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.org.service import (
    MasterCodeUnusable,
    NumberingService,
    OrgService,
    assert_usable,
    codes_read,
    today,
)
from flo.modules.projects.service import ProjectService, is_requisition_generated
from flo.modules.requisitions.approval import ProjectRequestAdapter as ProjectRequestAdapter
from flo.modules.requisitions.approval import RequisitionAdapter as RequisitionAdapter
from flo.modules.requisitions.approval import open_document_count as open_document_count
from flo.modules.requisitions.catalogue import record_event, record_requested
from flo.modules.requisitions.lifecycle import SYSTEM_ACTOR_ID
from flo.modules.requisitions.lifecycle import assert_completable as assert_completable
from flo.modules.requisitions.lifecycle import available as available
from flo.modules.requisitions.lifecycle import events as events
from flo.modules.requisitions.lifecycle import expire_org as expire_org
from flo.modules.requisitions.lifecycle import (
    register_downstream_counter as register_downstream_counter,
)
from flo.modules.requisitions.lifecycle import (
    register_sourcing_probe as register_sourcing_probe,
)
from flo.modules.requisitions.lifecycle import routing_check as routing_check
from flo.modules.requisitions.lifecycle import transition as transition
from flo.modules.requisitions.links import assert_current_upstream as assert_current_upstream
from flo.modules.requisitions.links import links as links
from flo.modules.requisitions.links import register_link_provider as register_link_provider
from flo.modules.requisitions.models import READ_FIELDS, READ_SQL, RequisitionRepository
from flo.modules.requisitions.revisions import latest_instance
from flo.modules.requisitions.schemas import (
    AmountTotal,
    Direction,
    RequisitionCreate,
    RequisitionPage,
    RequisitionPatch,
    RequisitionRead,
    RequisitionSort,
    RequisitionStatus,
    RequisitionTransitionCreate,
    invalid,
)
from flo.modules.requisitions.schemas import SubmissionInvalid as SubmissionInvalid
from flo.modules.requisitions.sourcing import source_lines as source_lines
from flo.modules.requisitions.sourcing import sourceable_lines as sourceable_lines
from flo.modules.requisitions.sourcing import sourcing_basis as sourcing_basis
from flo.modules.requisitions.validation import check_submission as check_submission

__all__ = [
    "ProjectRequestAdapter",
    "RequisitionAdapter",
    "open_document_count",
    "transition",
    "available",
    "events",
    "routing_check",
    "assert_completable",
    "assert_current_upstream",
    "source_lines",
    "sourceable_lines",
    "sourcing_basis",
    "links",
    "register_link_provider",
    "register_downstream_counter",
    "register_sourcing_probe",
    "expire_org",
    "RequisitionService",
    "SubmissionInvalid",
    "check_submission",
    "record_event",
    "record_requested",
]


SORT_COLUMNS = {
    "number": "r.number",
    "title": "r.title",
    "estimate": "r.estimate",
    "needed_by": "r.needed_by",
    "updated_at": "r.updated_at",
}
ORDER_SQL = {"asc": "ASC", "desc": "DESC"}


class RequisitionService:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        actor_id: UUID | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.connection, self.scope, self.actor_id, self.settings = (
            connection,
            scope,
            actor_id,
            settings,
        )
        self.repo = RequisitionRepository(connection, scope)

    def _get(self, id: UUID, *, lock: bool = False) -> RequisitionRead:
        row = self.repo.execute(
            READ_SQL
            + " WHERE r.org_id=%(org_id)s AND r.id=%(id)s"
            + (" FOR UPDATE OF r" if lock else ""),
            {"id": id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return self._with_codes(
            RequisitionRead.model_validate(dict(zip(READ_FIELDS, row, strict=True)))
        )

    def _with_codes(self, read: RequisitionRead) -> RequisitionRead:
        read.codes = codes_read(
            self.connection, self.scope, read.department_code, read.ledger_account_code
        )
        read.codes_expired = read.codes.expired
        return read

    def _sponsors(self, id: UUID) -> list[UUID]:
        return [
            cast(UUID, r[0])
            for r in self.repo.execute(
                "SELECT user_id FROM requisition_sponsor WHERE org_id=%(org_id)s AND "
                "requisition_id=%(id)s ORDER BY user_id",
                {"id": id},
            ).fetchall()
        ]

    def _set_sponsors(self, id: UUID, sponsor_ids: list[UUID]) -> None:
        found = self.repo.execute(
            "SELECT i.id FROM identity i WHERE i.id=ANY(%(ids)s) AND i.status='active' AND EXISTS ("
            "SELECT 1 FROM user_role r WHERE r.org_id=%(org_id)s AND r.user_id=i.id)",
            {"ids": sponsor_ids},
        ).fetchall()
        for user_id in sponsor_ids:
            if user_id not in {r[0] for r in found}:
                raise invalid(
                    "sponsor_ids", f"{user_id} is not an active user of this organization."
                )
        self.repo.execute(
            "DELETE FROM requisition_sponsor WHERE org_id=%(org_id)s AND requisition_id=%(id)s",
            {"id": id},
        )
        for user_id in sponsor_ids:
            self.repo.execute(
                "INSERT INTO requisition_sponsor(org_id,requisition_id,user_id) "
                "VALUES (%(org_id)s,%(id)s,%(user)s)",
                {"id": id, "user": user_id},
            )

    def detail(
        self,
        id: UUID,
        can_view_budget: Callable[[UUID], bool],
        can_edit: Callable[[], bool] | None = None,
    ) -> RequisitionRead:
        from flo.modules.budget.service import get_balance, reservation_remaining
        from flo.modules.requisitions.lifecycle import read_row
        from flo.modules.requisitions.revisions import open_amendment
        from flo.modules.requisitions.schemas import AmendmentRef, ApprovalRead, ReservationRead

        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            result = self._get(id)
            result.sponsor_ids = self._sponsors(id)
            row = read_row(self.repo, id)
            if row["reservation_entry_id"] is not None:
                held = reservation_remaining(
                    self.connection, self.scope, cast(int, row["reservation_entry_id"])
                )
                result.reservation = ReservationRead(
                    entry_id=cast(int, row["reservation_entry_id"]),
                    reserved=format(held.amount, ".4f"),
                    released=format(held.amount - held.remaining, ".4f"),
                    remaining=format(held.remaining, ".4f"),
                    currency=held.currency,
                    funding_round=cast(int, row["funding_round"]),
                    project_available=format(
                        get_balance(self.connection, self.scope, held.project_id).available, ".4f"
                    )
                    if can_view_budget(held.project_id)
                    else None,
                )
            instance = latest_instance(self.connection, self.scope, id)
            if instance:
                result.approval = ApprovalRead(instance_id=instance[0], status=instance[1])
            amendment = open_amendment(self.repo, id)
            if amendment and row["status"] == "sourcing_in_progress":
                result.amendment = AmendmentRef(
                    revision_no=cast(int, amendment["revision_no"]),
                    status=cast(Any, amendment["status"]),
                    can_continue=amendment["status"] == "draft"
                    and can_edit is not None
                    and can_edit(),
                )
            return result

    def transition(
        self,
        id: UUID,
        body: RequisitionTransitionCreate,
        version: str | None,
        permitted: Callable[[str], bool],
        can_view_budget: Callable[[UUID], bool],
        can_edit: Callable[[], bool] | None = None,
    ) -> RequisitionRead:
        # Build the reply while the command's instance, funding and document locks are held.
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            transition(
                self.connection, self.scope, id, body, version, cast(UUID, self.actor_id), permitted
            )
            return self.detail(id, can_view_budget, can_edit)

    def get(self, id: UUID) -> RequisitionRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return self._get(id)

    def _project(
        self, id: UUID, permitted: Callable[[UUID], bool] | None = None
    ) -> dict[str, object]:
        project = ProjectService(self.connection, self.scope).get(id)
        if permitted is not None and not permitted(id):
            raise ProblemError(ErrorCode.FORBIDDEN)
        if is_requisition_generated(self.connection, self.scope, id):
            source = self._get(cast(UUID, project.generated_by_requisition_id))
            raise invalid(
                "project_id",
                f"{source.number} created this project. A requisition cannot be raised "
                "against it; choose its parent project.",
                "requisition_generated_project",
            )
        return {
            "project_id": id,
            "bu_id": project.bu_id,
            "currency": project.currency,
            "department_code": project.department_code,
            "ledger_account_code": project.ledger_account_code,
        }

    def _validate(self, values: dict[str, object]) -> None:
        org = OrgService(self.connection, self.scope)
        for field, kind in [
            ("department_code", "department"),
            ("ledger_account_code", "ledger_account"),
        ]:
            code = cast(str | None, values.get(field))
            if code is not None:
                try:
                    values[field] = assert_usable(
                        self.connection, self.scope, kind, code, today()
                    ).code
                except MasterCodeUnusable as exc:
                    raise invalid(
                        field, f"Code {exc.code} is {exc.reason}. Choose a usable code.", exc.reason
                    ) from exc
        address_id = values.get("delivery_address_id")
        if address_id is not None:
            addresses = [
                address
                for unit in org.unit_chain(cast(UUID, values["bu_id"]))
                for address in org.list_addresses(unit, "ship_to", today())
            ]
            if not any(address.id == address_id for address in addresses):
                raise invalid(
                    "delivery_address_id",
                    "Choose an effective ship-to address of this unit or an ancestor.",
                )

    def _audit(
        self, action: str, id: UUID, before: dict[str, object] | None, after: dict[str, object]
    ) -> None:
        audit_event(
            cast(AuditConnection, self.connection),
            self.scope,
            actor=AuditActor(ActorKind.USER, self.actor_id)
            if self.actor_id != SYSTEM_ACTOR_ID
            else AuditActor(ActorKind.JOB, None),
            action=action,
            target_type="requisition",
            target_id=id,
            outcome=Outcome.SUCCESS,
            before=before,
            after=after,
        )

    @staticmethod
    def _require_sponsors(body: RequisitionCreate) -> None:
        """A Project Request names one to ten sponsors when it is created (D-M2-20)."""
        if body.project_id is None and not body.sponsor_ids:
            raise invalid("sponsor_ids", "Name one to ten sponsors for a Project Request.")

    def create(self, body: RequisitionCreate) -> RequisitionRead:
        self._require_sponsors(body)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            values = body.model_dump()
            sponsors = cast(list[UUID] | None, values.pop("sponsor_ids"))
            if body.project_id is not None:
                derived = self._project(body.project_id)
                for field in ("department_code", "ledger_account_code"):
                    if field in body.model_fields_set:
                        derived.pop(field)
                values.update(derived)
            else:
                OrgService(self.connection, self.scope).get_unit(cast(UUID, body.bu_id))
                row = self.repo.execute(
                    "SELECT base_currency FROM organization WHERE id=%(org_id)s"
                ).fetchone()
                if row is None or row[0] is None:
                    raise ProblemError(
                        ErrorCode.CONFLICT,
                        detail="The organization has no base currency. Ask an organization "
                        "administrator to set it before creating a standalone requisition.",
                        checks={"problem": "base_currency_unset"},
                    )
                values["currency"] = row[0]
            self._validate(values)
            values.update(
                id=uuid4(),
                standalone=body.project_id is None,
                requester_id=self.actor_id,
                created_by=self.actor_id,
            )
            values["number"] = NumberingService(self.settings or Settings(), self.scope).allocate(
                cast(UUID, values["bu_id"]), "requisition", today()
            )
            columns = tuple(values)
            inserted = self.repo.execute(
                "INSERT INTO requisition (org_id,"
                + ",".join(columns)
                + ") VALUES (%(org_id)s,"
                + ",".join("%(" + col + ")s" for col in columns)
                + ") ON CONFLICT (org_id,bu_id,number) DO NOTHING RETURNING id",
                values,
            ).fetchone()
            if inserted is None:
                raise self._duplicate_number()
            if sponsors:
                self._set_sponsors(cast(UUID, values["id"]), sponsors)
            result = self._get(cast(UUID, values["id"]))
            result.sponsor_ids = sponsors or []
            self._audit("requisition.create", result.id, None, result.model_dump())
            return result

    def update(
        self,
        id: UUID,
        body: RequisitionPatch,
        version: str | None,
        can_create: Callable[[UUID], bool],
        *,
        can_view_budget: Callable[[UUID], bool] | None = None,
        can_edit: Callable[[], bool] | None = None,
    ) -> RequisitionRead:
        if version is None:
            raise ProblemError(
                ErrorCode.BAD_REQUEST,
                detail="Supply If-Match with the requisition version.",
                checks={"problem": "if_match_required"},
            )
        try:
            expected = int(version.strip('"'))
        except ValueError as exc:
            raise ProblemError(
                ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
            ) from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            before = self._get(id, lock=True)
            if before.version != expected:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="The requisition changed. Reload it and retry your update.",
                    checks={"problem": "stale_version"},
                )
            if before.status != "draft":
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="Only drafts can be edited. Open an editable draft.",
                    checks={"problem": "not_editable"},
                )
            changes = body.model_dump(exclude_unset=True)
            sponsors = cast(list[UUID] | None, changes.pop("sponsor_ids", None))
            old_sponsors: list[UUID] = []
            if sponsors is not None:
                if not before.standalone:
                    raise invalid("sponsor_ids", "Only a Project Request can name sponsors.")
                old_sponsors = self._sponsors(id)
                self._set_sponsors(id, sponsors)
            if "project_id" in changes:
                if before.standalone or body.project_id is None:
                    raise invalid(
                        "project_id", "The requisition mode is immutable.", "immutable_field"
                    )
                derived = self._project(
                    body.project_id,
                    can_create if body.project_id != before.project_id else None,
                )
                if body.project_id != before.project_id:
                    # SEQ-001: the number belongs to the unit scope; a cross-unit move would
                    # collide or need renumbering, so it is refused (E09-S01 amendment 6).
                    if derived["bu_id"] != before.bu_id:
                        target = OrgService(self.connection, self.scope).get_unit(
                            cast(UUID, derived["bu_id"])
                        )
                        raise invalid(
                            "project_id",
                            f"This request belongs to {before.bu_name}. "
                            f"Create a new request in {target.name} instead.",
                            "unit_change_blocked",
                        )
                    if (
                        derived["currency"] != before.currency
                        and self.repo.execute(
                            "SELECT 1 FROM requisition_line WHERE org_id=%(org_id)s "
                            "AND requisition_id=%(id)s LIMIT 1",
                            {"id": id},
                        ).fetchone()
                        is not None
                    ):
                        raise invalid(
                            "project_id",
                            f"This request has lines priced in {before.currency}. Remove the lines "
                            f"or choose a project in {before.currency}.",
                            "currency_change_blocked",
                        )
                    changes.update({field: derived[field] for field in ("bu_id", "currency")})
            if "title" in changes and body.title is None:
                raise invalid("title", "Supply a title.")
            values = before.model_dump() | changes
            self._validate(values)
            for field in ("department_code", "ledger_account_code"):
                if field in changes:
                    changes[field] = values[field]
            assignments = ",".join(field + "=%(" + field + ")s" for field in changes)
            try:
                with self.connection.transaction():
                    self.repo.execute(
                        "UPDATE requisition SET "
                        + (assignments + "," if assignments else "")
                        + "version=version+1,updated_at=now() "
                        "WHERE org_id=%(org_id)s AND id=%(id)s",
                        changes | {"id": id},
                    )
            except psycopg.errors.UniqueViolation as exc:
                if exc.diag.constraint_name != "requisition_org_id_bu_id_number_key":
                    raise
                raise self._duplicate_number() from exc
            after = self._get(id)
            changed_fields = {
                field for field in changes if getattr(before, field) != getattr(after, field)
            }
            audit_before = {field: getattr(before, field) for field in changed_fields}
            audit_after = {field: getattr(after, field) for field in changed_fields}
            if sponsors is not None and sorted(sponsors) != sorted(old_sponsors):
                audit_before["sponsor_ids"] = [str(u) for u in old_sponsors]
                audit_after["sponsor_ids"] = [str(u) for u in sorted(sponsors)]
            self._audit("requisition.update", id, audit_before, audit_after)
            return (
                self.detail(id, can_view_budget, can_edit) if can_view_budget is not None else after
            )

    @staticmethod
    def _duplicate_number() -> ProblemError:
        return ProblemError(
            ErrorCode.CONFLICT,
            detail="This requisition number already exists in the target unit. "
            "Choose another project or ask an administrator to review numbering.",
            checks={"problem": "duplicate_number"},
        )

    def list(
        self,
        *,
        status: RequisitionStatus | None = None,
        project_id: UUID | None = None,
        bu_id: UUID | None = None,
        kind: Literal["project_request", "requisition"] | None = None,
        requester: str | None = None,
        q: str = "",
        sort: RequisitionSort = "number",
        direction: Direction = "asc",
        cursor: str | None = None,
        page_size: int = 50,
    ) -> RequisitionPage:
        if not 1 <= page_size <= 50:
            raise invalid("page_size", "Choose a page size from 1 to 50.")
        # Only these constant strings ever reach the SQL text; the request values pick one.
        column = SORT_COLUMNS.get(sort)
        order = ORDER_SQL.get(direction)
        if column is None or order is None:
            raise invalid("sort", "Choose a supported sort and direction.")
        if requester not in (None, "me"):
            raise invalid("requester", "Choose requester=me or omit this filter.")
        params: dict[str, object] = {
            "status": status,
            "project_id": project_id,
            "bu_id": bu_id,
            "standalone": None if kind is None else kind == "project_request",
            "requester_id": self.actor_id if requester == "me" else None,
        }
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.update(prefix=escaped + "%", substring="%" + escaped + "%")
        filters = """r.org_id=%(org_id)s AND (%(status)s::text IS NULL OR r.status=%(status)s)
          AND (%(project_id)s::uuid IS NULL OR r.project_id=%(project_id)s)
          AND (%(bu_id)s::uuid IS NULL OR r.bu_id=%(bu_id)s)
          AND (%(standalone)s::boolean IS NULL OR r.standalone=%(standalone)s)
          AND (%(requester_id)s::uuid IS NULL OR r.requester_id=%(requester_id)s)
          AND (r.number ILIKE %(prefix)s OR r.title ILIKE %(substring)s)"""
        boundary = ""
        if cursor is not None:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
                if decoded["sort"] != sort or decoded["direction"] != direction:
                    raise ValueError("cursor order")
                key = decoded["key"]
                if key is not None:
                    if not isinstance(key, str):
                        raise ValueError("cursor key")
                    key = (
                        Decimal(key)
                        if sort == "estimate"
                        else date.fromisoformat(key)
                        if sort == "needed_by"
                        else datetime.fromisoformat(key)
                        if sort == "updated_at"
                        else key
                    )
                params.update(key=key, cursor_id=UUID(decoded["id"]))
            except (ValueError, KeyError, TypeError, UnicodeError, ArithmeticError) as exc:
                raise invalid("cursor", "Invalid cursor. Restart paging.") from exc
            comparator = ">" if direction == "asc" else "<"
            # Nulls last in either direction, with a stable UUID tie-breaker.
            boundary = (
                f" AND ({column} IS NULL AND r.id {comparator} %(cursor_id)s"
                if key is None
                else (
                    f" AND ({column} IS NULL OR ({column},r.id) {comparator} "
                    "(%(key)s,%(cursor_id)s)"
                )
            ) + ")"
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            count = self.repo.execute(
                "SELECT count(*) FROM requisition r WHERE " + filters, params
            ).fetchone()
            assert count is not None
            sums = self.repo.execute(
                "SELECT r.currency, sum(r.estimate) FROM requisition r WHERE "
                + filters
                + " GROUP BY r.currency ORDER BY r.currency",
                params,
            ).fetchall()
            rows = self.repo.execute(
                READ_SQL
                + " WHERE "
                + filters
                + boundary
                + f" ORDER BY {column} {order} NULLS LAST,r.id {order}"
                + " LIMIT %(limit)s",
                {**params, "limit": page_size + 1},
            ).fetchall()
            records = [
                self._with_codes(
                    RequisitionRead.model_validate(dict(zip(READ_FIELDS, row, strict=True)))
                )
                for row in rows[:page_size]
            ]
            next_cursor = None
            if len(rows) > page_size:
                last = records[-1]
                key = getattr(last, sort)
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "sort": sort,
                            "direction": direction,
                            "key": None if key is None else str(key),
                            "id": str(last.id),
                        }
                    ).encode()
                ).decode()
            return RequisitionPage(
                rows=records,
                total=int(str(count[0])),
                next_cursor=next_cursor,
                totals=[
                    AmountTotal(currency=str(c), amount=format(cast(Decimal, a), ".4f"))
                    for c, a in sums
                ],
            )
