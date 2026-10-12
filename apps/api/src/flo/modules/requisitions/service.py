"""Transactional requisition header creation, paging and draft updates."""

import base64
import json
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
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
    today,
)
from flo.modules.projects.service import ProjectService, is_requisition_generated
from flo.modules.requisitions.catalogue import record_event, record_requested
from flo.modules.requisitions.models import READ_FIELDS, READ_SQL, RequisitionRepository
from flo.modules.requisitions.schemas import (
    Direction,
    RequisitionCreate,
    RequisitionPage,
    RequisitionPatch,
    RequisitionRead,
    RequisitionSort,
    RequisitionStatus,
    invalid,
)
from flo.modules.requisitions.schemas import SubmissionInvalid as SubmissionInvalid
from flo.modules.requisitions.validation import check_submission as check_submission

__all__ = [
    "RequisitionService",
    "SubmissionInvalid",
    "check_submission",
    "record_event",
    "record_requested",
]


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
        return RequisitionRead.model_validate(dict(zip(READ_FIELDS, row, strict=True)))

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
        AuditWriter(cast(AuditConnection, self.connection), self.scope).write(
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type="requisition",
            target_id=id,
            outcome=Outcome.SUCCESS,
            before_source=before,
            before_fields=tuple(before or ()),
            after_source=after,
            after_fields=tuple(after),
        )

    def create(self, body: RequisitionCreate) -> RequisitionRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            values = body.model_dump()
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
            result = self._get(cast(UUID, values["id"]))
            self._audit("requisition.create", result.id, None, result.model_dump())
            return result

    def update(
        self,
        id: UUID,
        body: RequisitionPatch,
        version: str | None,
        can_create: Callable[[UUID], bool],
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
            self._audit(
                "requisition.update",
                id,
                {field: getattr(before, field) for field in changed_fields},
                {field: getattr(after, field) for field in changed_fields},
            )
            return after

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
        requester: str | None = None,
        q: str = "",
        sort: RequisitionSort = "number",
        direction: Direction = "asc",
        cursor: str | None = None,
    ) -> RequisitionPage:
        if sort not in (
            "number",
            "title",
            "estimate",
            "needed_by",
            "updated_at",
        ) or direction not in ("asc", "desc"):
            raise invalid("sort", "Choose a supported sort and direction.")
        if requester not in (None, "me"):
            raise invalid("requester", "Choose requester=me or omit this filter.")
        params: dict[str, object] = {
            "status": status,
            "project_id": project_id,
            "bu_id": bu_id,
            "requester_id": self.actor_id if requester == "me" else None,
        }
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.update(prefix=escaped + "%", substring="%" + escaped + "%")
        filters = """r.org_id=%(org_id)s AND (%(status)s::text IS NULL OR r.status=%(status)s)
          AND (%(project_id)s::uuid IS NULL OR r.project_id=%(project_id)s)
          AND (%(bu_id)s::uuid IS NULL OR r.bu_id=%(bu_id)s)
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
                f" AND (r.{sort} IS NULL AND r.id {comparator} %(cursor_id)s"
                if key is None
                else (
                    f" AND (r.{sort} IS NULL OR (r.{sort},r.id) {comparator} "
                    "(%(key)s,%(cursor_id)s)"
                )
            ) + ")"
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            count = self.repo.execute(
                "SELECT count(*) FROM requisition r WHERE " + filters, params
            ).fetchone()
            assert count is not None
            rows = self.repo.execute(
                READ_SQL
                + " WHERE "
                + filters
                + boundary
                + f" ORDER BY r.{sort} {direction} NULLS LAST,r.id {direction} LIMIT 51",
                params,
            ).fetchall()
            records = [
                RequisitionRead.model_validate(dict(zip(READ_FIELDS, row, strict=True)))
                for row in rows[:50]
            ]
            next_cursor = None
            if len(rows) > 50:
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
            return RequisitionPage(rows=records, total=int(str(count[0])), next_cursor=next_cursor)
