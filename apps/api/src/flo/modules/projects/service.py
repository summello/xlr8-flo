"""Transactional root-project creation and optimistic detail updates."""

import base64
import json
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.config import Settings
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.identity.service import (
    IdentityAuthorizationConnection,
    register_project_scope,
    user_exists,
)
from flo.modules.org.service import (
    MasterCodeUnusable,
    NumberingService,
    OrgService,
    assert_usable,
    list_currencies,
)
from flo.modules.projects.models import COLUMNS, FIELDS, ProjectRepository
from flo.modules.projects.schemas import (
    Direction,
    ProjectCreate,
    ProjectPage,
    ProjectPatch,
    ProjectRead,
    ProjectSort,
    ProjectStatus,
)


def invalid(field: str, message: str, label: str = "invalid_project") -> ProblemError:
    return ProblemError(
        ErrorCode.VALIDATION_FAILED,
        detail=message,
        errors=(ProblemFieldError(field=field, message=message),),
        checks={"problem": label},
    )


class ProjectService:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        actor_id: UUID | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.connection = connection
        self.scope = scope
        self.actor_id = actor_id
        self.settings = settings
        self.repo = ProjectRepository(connection, scope)

    def _get(self, project_id: UUID, *, lock: bool = False) -> dict[str, object]:
        row = self.repo.get(project_id, lock=lock)
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return row

    def get(self, project_id: UUID) -> ProjectRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return ProjectRead.model_validate(self._get(project_id))

    def get_status(self, project_id: UUID) -> str:
        return self.get(project_id).status

    def _audit(
        self,
        action: str,
        project_id: UUID,
        before: dict[str, object] | None,
        after: dict[str, object],
    ) -> None:
        AuditWriter(cast(AuditConnection, self.connection), self.scope).write(
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type="project",
            target_id=project_id,
            outcome=Outcome.SUCCESS,
            before_source=before,
            before_fields=tuple(before or ()),
            after_source=after,
            after_fields=tuple(after),
        )

    def _user(self, field: str, user_id: UUID | None) -> None:
        if user_id is not None and not user_exists(
            cast(IdentityAuthorizationConnection, self.connection), self.scope, user_id
        ):
            raise invalid(field, f"{user_id} is not a user of this organization. Choose a member.")

    def _dates(self, start: date | None, end: date | None) -> None:
        if start is not None and end is not None and end < start:
            raise invalid(
                "planned_end", "Planned end precedes start. Choose an end on or after start."
            )

    def create(self, body: ProjectCreate) -> ProjectRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            OrgService(self.connection, self.scope).get_unit(body.bu_id)
            if body.parent_id is not None:
                raise invalid(
                    "parent_id",
                    "Parenting is not supported yet. Create a root project.",
                    "parenting_not_supported",
                )
            on_date = datetime.now(UTC).date()
            codes: dict[str, str] = {}
            for field, kind in [
                ("department_code", "department"),
                ("ledger_account_code", "ledger_account"),
            ]:
                code = cast(str, getattr(body, field))
                try:
                    codes[field] = assert_usable(
                        self.connection, self.scope, kind, code, on_date
                    ).code
                except MasterCodeUnusable as exc:
                    raise invalid(
                        field, f"Code {exc.code} is {exc.reason}. Choose a usable code.", exc.reason
                    ) from exc
            if body.currency not in {currency.code for currency in list_currencies()}:
                raise invalid(
                    "currency", "Unknown ISO 4217 currency. Choose a valid currency code."
                )
            owner = body.owner_id or self.actor_id
            if owner is None:
                raise RuntimeError("project creation requires an actor")
            self._user("owner_id", owner)
            self._user("sponsor_id", body.sponsor_id)
            self._dates(body.planned_start, body.planned_end)
            number = NumberingService(self.settings or Settings(), self.scope).allocate(
                body.bu_id, "project", on_date
            )
            project_id = uuid4()
            values = (
                body.model_dump()
                | codes
                | {
                    "id": project_id,
                    "number": number,
                    "owner_id": owner,
                    "created_by": self.actor_id,
                }
            )
            inserted = self.repo.execute(
                """INSERT INTO project(id, org_id, bu_id, parent_id, number, name, description,
                owner_id, sponsor_id, department_code, ledger_account_code, currency,
                planned_start, planned_end, created_by)
                VALUES (%(id)s, %(org_id)s, %(bu_id)s, %(parent_id)s, %(number)s, %(name)s,
                %(description)s, %(owner_id)s, %(sponsor_id)s, %(department_code)s,
                %(ledger_account_code)s, %(currency)s, %(planned_start)s, %(planned_end)s,
                %(created_by)s) ON CONFLICT (org_id, bu_id, number) DO NOTHING RETURNING id""",
                values,
            ).fetchone()
            if inserted is None:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=(
                        "The numbering format produced an existing number. "
                        "Choose a distinct format."
                    ),
                    checks={"problem": "duplicate_number"},
                )
            register_project_scope(
                cast(IdentityAuthorizationConnection, self.connection),
                self.scope,
                project_id,
                parent_kind="bu",
                parent_id=body.bu_id,
            )
            row = self._get(project_id)
            self._audit("project.create", project_id, None, row)
            return ProjectRead.model_validate(row)

    def update(self, project_id: UUID, body: ProjectPatch, version: str | None) -> ProjectRead:
        if version is None:
            raise ProblemError(
                ErrorCode.BAD_REQUEST,
                detail="Supply If-Match with the project version.",
                checks={"problem": "if_match_required"},
            )
        try:
            expected = int(version)
        except ValueError as exc:
            raise ProblemError(
                ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
            ) from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            before = self._get(project_id, lock=True)
            if before["version"] != expected:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="The project changed. Reload it and retry your update.",
                    checks={"problem": "stale_version"},
                )
            changes = body.model_dump(exclude_unset=True)
            for field in ("name", "owner_id"):
                if field in changes and changes[field] is None:
                    raise invalid(field, f"{field} cannot be null. Supply a value.")
            for field in ("owner_id", "sponsor_id"):
                if field in changes:
                    self._user(field, cast(UUID | None, changes[field]))
            after = before | changes
            self._dates(
                cast(date | None, after["planned_start"]), cast(date | None, after["planned_end"])
            )
            self.repo.execute(
                """UPDATE project SET name = %(name)s, description = %(description)s,
                owner_id = %(owner_id)s, sponsor_id = %(sponsor_id)s,
                planned_start = %(planned_start)s, planned_end = %(planned_end)s,
                version = version + 1 WHERE org_id = %(org_id)s AND id = %(id)s""",
                after,
            )
            after = self._get(project_id)
            self._audit("project.update", project_id, before, after)
            return ProjectRead.model_validate(after)

    def list(
        self,
        *,
        q: str = "",
        status: ProjectStatus | None = None,
        bu_id: UUID | None = None,
        sort: ProjectSort = "number",
        direction: Direction = "asc",
        cursor: str | None = None,
        page_size: int = 50,
    ) -> ProjectPage:
        # The router requires org-level project.read; that grant covers every project.
        if sort not in ("number", "name", "status", "created_at") or direction not in (
            "asc",
            "desc",
        ):
            raise invalid("sort", "Choose a supported sort and direction.")
        if not 1 <= page_size <= 50:
            raise invalid("page_size", "Page size must be from 1 to 50.")
        key: object = None
        cursor_id: UUID | None = None
        if cursor is not None:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
                if decoded["sort"] != sort or decoded["direction"] != direction:
                    raise ValueError("cursor ordering mismatch")
                key = decoded["key"]
                if not isinstance(key, str):
                    raise ValueError("invalid cursor key")
                if sort == "created_at":
                    key = datetime.fromisoformat(key)
                cursor_id = UUID(decoded["id"])
            except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                raise invalid(
                    "cursor", "Invalid cursor. Restart paging with the selected order."
                ) from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            prefix = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params = {
                "q": prefix,
                "status": status,
                "bu_id": bu_id,
                "key": key,
                "cursor_id": cursor_id,
                "limit": page_size + 1,
            }
            comparator = ">" if direction == "asc" else "<"
            boundary = (
                f" AND ({sort}, id) {comparator} (%(key)s, %(cursor_id)s)"
                if cursor_id is not None
                else ""
            )
            rows = self.repo.execute(
                f"""SELECT {COLUMNS} FROM project WHERE org_id = %(org_id)s
                AND (number ILIKE %(q)s OR name ILIKE %(q)s)
                AND (%(status)s::text IS NULL OR status = %(status)s)
                AND (%(bu_id)s::uuid IS NULL OR bu_id = %(bu_id)s)"""
                + boundary
                + f" ORDER BY {sort} {direction}, id {direction} LIMIT %(limit)s",
                params,
            ).fetchall()
            records = [
                ProjectRead.model_validate(dict(zip(FIELDS, row, strict=True)))
                for row in rows[:page_size]
            ]
            next_cursor = None
            if len(rows) > page_size:
                last = records[-1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "sort": sort,
                            "direction": direction,
                            "id": str(last.id),
                            "key": str(getattr(last, sort)),
                        }
                    ).encode()
                ).decode()
            return ProjectPage(rows=records, next_cursor=next_cursor)


def get_status(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, project_id: UUID
) -> str:
    """Read-only entry point for funding checks."""
    return ProjectService(connection, scope).get_status(project_id)
