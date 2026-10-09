"""Transactional root-project creation and optimistic detail updates."""

import base64
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
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
from flo.modules.projects.hierarchy import Hierarchy
from flo.modules.projects.lifecycle import Lifecycle, conflict
from flo.modules.projects.models import COLUMNS, FIELDS, ProjectRepository
from flo.modules.projects.schedule import Schedule, with_variance
from flo.modules.projects.schemas import (
    ChildrenPage,
    Direction,
    MilestoneCreate,
    MilestonePatch,
    MilestoneRead,
    PathRead,
    PhaseCreate,
    PhasePatch,
    PhaseRead,
    ProjectCreate,
    ProjectPage,
    ProjectPatch,
    ProjectRead,
    ProjectRef,
    ProjectSort,
    ProjectStatus,
    TransitionAvailable,
    TransitionCreate,
    TreeNode,
    TreeRead,
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
            return ProjectRead.model_validate(with_variance(self._get(project_id)))

    def transition(
        self,
        project_id: UUID,
        body: TransitionCreate,
        version: str | None,
        permitted: Callable[[str], bool],
    ) -> ProjectRead:
        if version is None:
            raise ProblemError(
                ErrorCode.BAD_REQUEST, detail="Supply If-Match with the project version."
            )
        try:
            expected = int(version)
        except ValueError as exc:
            raise ProblemError(
                ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
            ) from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            lifecycle = Lifecycle(self.repo, self.actor_id)
            before, blocked = lifecycle.snapshot(project_id, for_update=True)
            if before["version"] != expected:
                raise conflict(
                    "stale_version", "The project changed. Reload it and retry the transition."
                )
            problems = lifecycle.evaluate(before, blocked, body, permitted)
            if problems:
                raise problems[0]
            lifecycle.write(before, blocked, body)
            after = self._get(project_id)
            self._audit(
                "project.transition",
                project_id,
                before,
                after | {"override": body.override, "blocked_by": blocked, "reason": body.reason},
            )
            return ProjectRead.model_validate(with_variance(after))

    def available_transitions(
        self, project_id: UUID, permitted: Callable[[str], bool]
    ) -> list[TransitionAvailable]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            lifecycle = Lifecycle(self.repo, self.actor_id)
            project, blocked = lifecycle.snapshot(project_id, for_update=False)
            return lifecycle.available(project, blocked, permitted)

    def phases(self, project_id: UUID) -> list[PhaseRead]:
        return Schedule(self.repo, self.actor_id).phases(project_id)

    def milestones(self, project_id: UUID) -> list[MilestoneRead]:
        return Schedule(self.repo, self.actor_id).milestones(project_id)

    def write_phase(
        self, project_id: UUID, body: PhaseCreate | PhasePatch, phase_id: UUID | None = None
    ) -> PhaseRead:
        return cast(PhaseRead, Schedule(self.repo, self.actor_id).write(project_id, body, phase_id))

    def write_milestone(
        self, project_id: UUID, body: MilestoneCreate | MilestonePatch, mid: UUID | None = None
    ) -> MilestoneRead:
        return cast(MilestoneRead, Schedule(self.repo, self.actor_id).write(project_id, body, mid))

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
                parent = self.depth_of(body.parent_id)
                if parent >= 5:
                    raise invalid(
                        "parent_id",
                        "The parent is at level five. Choose a shallower parent.",
                        "project_depth_exceeded",
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
            try:
                with self.connection.transaction():
                    inserted = self.repo.execute(
                        """INSERT INTO project(id, org_id, bu_id, parent_id, number, name,
                        description,
                        owner_id, sponsor_id, department_code, ledger_account_code, currency,
                        planned_start, planned_end, created_by)
                        VALUES (%(id)s, %(org_id)s, %(bu_id)s, %(parent_id)s, %(number)s, %(name)s,
                        %(description)s, %(owner_id)s, %(sponsor_id)s, %(department_code)s,
                        %(ledger_account_code)s, %(currency)s, %(planned_start)s, %(planned_end)s,
                        %(created_by)s) ON CONFLICT (org_id, bu_id, number)
                        DO NOTHING RETURNING id""",
                        values,
                    ).fetchone()
            except psycopg.errors.CheckViolation as exc:
                if exc.diag.message_primary != "project depth exceeded":
                    raise
                raise invalid(
                    "parent_id",
                    "The parent is at level five. Choose a shallower parent.",
                    "project_depth_exceeded",
                ) from exc
            if inserted is None:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=(
                        "The numbering format produced an existing number. "
                        "Choose a distinct format."
                    ),
                    checks={"problem": "duplicate_number"},
                )
            self.repo.execute(
                "INSERT INTO project_balance(project_id, org_id, bu_id, currency) "
                "VALUES (%(project_id)s, %(org_id)s, %(bu_id)s, %(currency)s)",
                {"project_id": project_id, "bu_id": body.bu_id, "currency": body.currency},
            )
            register_project_scope(
                cast(IdentityAuthorizationConnection, self.connection),
                self.scope,
                project_id,
                parent_kind="project" if body.parent_id is not None else "bu",
                parent_id=body.parent_id or body.bu_id,
            )
            row = self._get(project_id)
            self._audit("project.create", project_id, None, row)
            return ProjectRead.model_validate(with_variance(row))

    def ancestors(self, project_id: UUID) -> list[ProjectRef]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return Hierarchy(self.repo).ancestors(project_id)

    def descendants(self, project_id: UUID, include_self: bool = True) -> list[ProjectRef]:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return Hierarchy(self.repo).descendants(project_id, include_self)

    def lowest_common_ancestor(self, a: UUID, b: UUID) -> ProjectRef | None:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return Hierarchy(self.repo).common_path(a, b)[0]

    def path_between(self, a: UUID, b: UUID) -> PathRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return Hierarchy(self.repo).common_path(a, b)[1]

    def depth_of(self, project_id: UUID) -> int:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                "SELECT depth FROM project WHERE org_id = %(org_id)s AND id = %(id)s",
                {"id": project_id},
            ).fetchone()
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            return cast(int, row[0])

    def children(
        self, project_id: UUID, cursor: str | None = None, page_size: int = 50
    ) -> ChildrenPage:
        if not 1 <= page_size <= 50:
            raise invalid("page_size", "Choose a page size between 1 and 50.")
        number, cursor_id = "", UUID(int=0)
        if cursor is not None:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
                number, cursor_id = decoded["number"], UUID(decoded["id"])
                if not isinstance(number, str) or decoded["parent"] != str(project_id):
                    raise ValueError("cursor mismatch")
            except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                raise invalid("cursor", "Invalid cursor. Restart paging.") from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._get(project_id)
            rows = self.repo.execute(
                """SELECT id, parent_id, root_id, number, name, status, depth FROM project
                WHERE org_id = %(org_id)s AND parent_id = %(parent)s
                AND (number, id) > (%(number)s, %(cursor_id)s)
                ORDER BY number, id LIMIT %(limit)s""",
                {
                    "parent": project_id,
                    "number": number,
                    "cursor_id": cursor_id,
                    "limit": page_size + 1,
                },
            ).fetchall()
            from flo.modules.projects.hierarchy import FIELDS as REF_FIELDS

            records = [
                ProjectRef.model_validate(dict(zip(REF_FIELDS, row, strict=True)))
                for row in rows[:page_size]
            ]
            next_cursor = None
            if len(rows) > page_size:
                last = records[-1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {"parent": str(project_id), "number": last.number, "id": str(last.id)}
                    ).encode()
                ).decode()
            return ChildrenPage(rows=records, next_cursor=next_cursor)

    def tree(self, project_id: UUID, max_depth: int = 5) -> TreeRead:
        if not 1 <= max_depth <= 5:
            raise invalid("max_depth", "Choose a depth between 1 and 5.")
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rows = self.repo.execute(
                """WITH RECURSIVE walk AS (
                SELECT id, parent_id, number, name, status, depth, 1 AS depth_guard
                FROM project WHERE org_id = %(org_id)s AND id = %(id)s
                UNION
                SELECT p.id, p.parent_id, p.number, p.name, p.status, p.depth, depth_guard + 1
                FROM walk JOIN project p ON p.parent_id = walk.id AND p.org_id = %(org_id)s
                WHERE depth_guard < 6 AND depth_guard < %(max_depth)s
                ) SELECT DISTINCT ON (depth, id) id, parent_id, number, name, status, depth
                FROM walk ORDER BY depth, id LIMIT 501""",
                {"id": project_id, "max_depth": max_depth},
            ).fetchall()
            if not rows:
                raise ProblemError(ErrorCode.NOT_FOUND)
            nodes: dict[UUID, TreeNode] = {}
            for row in rows[:500]:
                node = TreeNode.model_validate(
                    dict(
                        zip(
                            ("id", "number", "name", "status", "depth"),
                            (row[0], *row[2:]),
                            strict=True,
                        )
                    )
                )
                nodes[node.id] = node
                parent_id = cast(UUID | None, row[1])
                if parent_id in nodes and node.id != project_id:
                    nodes[parent_id].children.append(node)
            return TreeRead(tree=nodes[project_id], truncated=len(rows) > 500)

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
            for field in ("name", "owner_id", "health", "percent_complete"):
                if field in changes and changes[field] is None:
                    raise invalid(field, f"{field} cannot be null. Supply a value.")
            for field in ("owner_id", "sponsor_id"):
                if field in changes:
                    self._user(field, cast(UUID | None, changes[field]))
            after = before | changes
            self._dates(
                cast(date | None, after["planned_start"]), cast(date | None, after["planned_end"])
            )
            if (
                after["actual_start"] is not None
                and after["actual_end"] is not None
                and cast(date, after["actual_end"]) < cast(date, after["actual_start"])
            ):
                raise invalid("actual_end", "Actual end precedes start.")
            self.repo.execute(
                """UPDATE project SET name = %(name)s, description = %(description)s,
                owner_id = %(owner_id)s, sponsor_id = %(sponsor_id)s,
                planned_start = %(planned_start)s, planned_end = %(planned_end)s,
                health = %(health)s, percent_complete = %(percent_complete)s,
                actual_start = %(actual_start)s, actual_end = %(actual_end)s,
                version = version + 1 WHERE org_id = %(org_id)s AND id = %(id)s""",
                after,
            )
            after = self._get(project_id)
            self._audit("project.update", project_id, before, after)
            return ProjectRead.model_validate(with_variance(after))

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
                ProjectRead.model_validate(with_variance(dict(zip(FIELDS, row, strict=True))))
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


def ancestors(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, project_id: UUID
) -> list[ProjectRef]:
    return ProjectService(connection, scope).ancestors(project_id)


def descendants(
    connection: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
    include_self: bool = True,
) -> list[ProjectRef]:
    return ProjectService(connection, scope).descendants(project_id, include_self)


def lowest_common_ancestor(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, a: UUID, b: UUID
) -> ProjectRef | None:
    return ProjectService(connection, scope).lowest_common_ancestor(a, b)


def path_between(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, a: UUID, b: UUID
) -> PathRead:
    return ProjectService(connection, scope).path_between(a, b)


def depth_of(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, project_id: UUID
) -> int:
    return ProjectService(connection, scope).depth_of(project_id)


def assert_posting_allowed(
    connection: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
    entry_type: str,
    bucket: str,
    amount: Decimal,
) -> None:
    """Caller holds the balance lock; releases/reversals and outgoing funds remain usable."""
    status = get_status(connection, scope, project_id)
    if entry_type in {"release", "reversal"}:
        return
    allowed = (
        (bucket == "allocated" and (amount < 0 or status in {"draft", "active"}))
        or (bucket in {"reserved", "committed"} and status == "active")
        or (bucket == "actual" and status in {"active", "deferred"})
    )
    if not allowed:
        raise conflict(
            "posting_not_allowed",
            (
                f"A {entry_type} posting to {bucket} is not allowed while the project is {status}. "
                "Use an eligible project or release/reverse an existing entry."
            ),
        )
