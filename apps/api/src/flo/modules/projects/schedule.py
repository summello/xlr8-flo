"""Scoped, transactional project schedule writes."""

from datetime import date
from typing import cast
from uuid import UUID, uuid4

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.projects.models import ProjectRepository
from flo.modules.projects.schemas import (
    MilestoneCreate,
    MilestonePatch,
    MilestoneRead,
    PhaseCreate,
    PhasePatch,
    PhaseRead,
)


def today() -> date:
    return date.today()


def with_variance(row: dict[str, object]) -> dict[str, object]:
    end = cast(date | None, row["planned_end"])
    return row | {
        "schedule_variance_days": ((cast(date | None, row["actual_end"]) or today()) - end).days
        if end is not None
        else None
    }


PHASE_FIELDS = (
    "id",
    "project_id",
    "name",
    "sequence",
    "sub_project_id",
    "planned_start",
    "planned_end",
    "actual_start",
    "actual_end",
    "percent_complete",
    "status",
    "created_at",
)
MILESTONE_FIELDS = (
    "id",
    "project_id",
    "name",
    "due_date",
    "phase_id",
    "completed_on",
    "created_at",
)


def problem(label: str, *, conflict: bool = False, field: str | None = None) -> ProblemError:
    messages = {
        "project_closed": "The project is closed. Choose an open project to edit its schedule.",
        "sequence_taken": "That phase sequence is occupied. Choose another sequence.",
        "sub_project_taken": "That child already backs a phase. Choose an unassigned child.",
        "invalid_sub_project": "The project is not a direct child. Choose a child of this project.",
        "invalid_phase": "The phase value is invalid. Supply a value and dates in start/end order.",
        "invalid_milestone": "The milestone value is invalid. Use this project's phase, a due date "
        "inside its planned window, and a completion date no later than today.",
    }
    return ProblemError(
        ErrorCode.CONFLICT if conflict else ErrorCode.VALIDATION_FAILED,
        detail=messages[label],
        checks={"problem": label},
        errors=(ProblemFieldError(field=field, message=messages[label]),) if field else (),
    )


class Schedule:
    def __init__(self, repo: ProjectRepository, actor_id: UUID | None) -> None:
        self.repo = repo
        self.actor_id = actor_id

    def _project(self, project_id: UUID, write: bool) -> dict[str, object]:
        row = self.repo.get(project_id, lock=write)
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        if write and row["status"] in ("completed", "abandoned"):
            raise problem("project_closed", conflict=True)
        return row

    def _row(
        self, kind: str, project_id: UUID, row_id: UUID, lock: bool = False
    ) -> dict[str, object]:
        fields = PHASE_FIELDS if kind == "phase" else MILESTONE_FIELDS
        row = self.repo.execute(
            f"SELECT {', '.join(fields)} FROM project_{kind} "
            "WHERE org_id=%(org_id)s AND project_id=%(project)s AND id=%(id)s"
            + (" FOR UPDATE" if lock else ""),
            {"project": project_id, "id": row_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return dict(zip(fields, row, strict=True))

    def phases(self, project_id: UUID) -> list[PhaseRead]:
        with tenant_transaction(cast(RlsSession, self.repo.connection), self.repo.scope):
            self._project(project_id, False)
            # ponytail: a project has a handful of phases; add cursor paging if that changes
            rows = self.repo.execute(
                f"SELECT {', '.join(PHASE_FIELDS)} FROM project_phase "
                "WHERE org_id=%(org_id)s AND project_id=%(project)s ORDER BY sequence",
                {"project": project_id},
            ).fetchall()
            return [
                PhaseRead.model_validate(with_variance(dict(zip(PHASE_FIELDS, r, strict=True))))
                for r in rows
            ]

    def milestones(self, project_id: UUID) -> list[MilestoneRead]:
        with tenant_transaction(cast(RlsSession, self.repo.connection), self.repo.scope):
            self._project(project_id, False)
            rows = self.repo.execute(
                f"SELECT {', '.join(MILESTONE_FIELDS)} FROM project_milestone "
                "WHERE org_id=%(org_id)s AND project_id=%(project)s ORDER BY due_date, created_at",
                {"project": project_id},
            ).fetchall()
            return [
                MilestoneRead.model_validate(dict(zip(MILESTONE_FIELDS, r, strict=True)))
                for r in rows
            ]

    def _validate_phase(self, row: dict[str, object]) -> None:
        for field in ("name", "sequence", "status", "percent_complete"):
            if row[field] is None:
                raise problem("invalid_phase", field=field)
        for prefix in ("planned", "actual"):
            start, end = row[prefix + "_start"], row[prefix + "_end"]
            if start is not None and end is not None and cast(date, end) < cast(date, start):
                raise problem("invalid_phase", field=prefix + "_end")
        if row["sub_project_id"] is not None:
            child = self.repo.get(cast(UUID, row["sub_project_id"]))
            if child is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            if child["parent_id"] != row["project_id"]:
                raise problem("invalid_sub_project", field="sub_project_id")

    def _validate_milestone(self, row: dict[str, object], project: dict[str, object]) -> None:
        if row["name"] is None or row["due_date"] is None:
            raise problem("invalid_milestone", field="name" if row["name"] is None else "due_date")
        due = cast(date, row["due_date"])
        for field, too_early in (("planned_start", True), ("planned_end", False)):
            bound = cast(date | None, project[field])
            if bound is not None and (due < bound if too_early else due > bound):
                raise problem("invalid_milestone", field="due_date")
        completed = cast(date | None, row["completed_on"])
        if completed is not None and completed > today():
            raise problem("invalid_milestone", field="completed_on")
        if row["phase_id"] is not None:
            phase = self.repo.execute(
                "SELECT project_id FROM project_phase WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": row["phase_id"]},
            ).fetchone()
            if phase is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            if phase[0] != row["project_id"]:
                raise problem("invalid_milestone", field="phase_id")

    def write(
        self,
        project_id: UUID,
        body: PhaseCreate | PhasePatch | MilestoneCreate | MilestonePatch,
        row_id: UUID | None = None,
    ) -> PhaseRead | MilestoneRead:
        kind = "phase" if isinstance(body, (PhaseCreate, PhasePatch)) else "milestone"
        fields = PHASE_FIELDS if kind == "phase" else MILESTONE_FIELDS
        with tenant_transaction(cast(RlsSession, self.repo.connection), self.repo.scope):
            project = self._project(project_id, True)
            before = self._row(kind, project_id, row_id, True) if row_id is not None else None
            changes = body.model_dump(exclude_unset=before is not None)
            row = (before or {"id": uuid4(), "project_id": project_id}) | changes
            if kind == "phase":
                if before is None and row["sequence"] is None:
                    highest = self.repo.execute(
                        "SELECT COALESCE(max(sequence), 0) FROM project_phase "
                        "WHERE org_id=%(org_id)s AND project_id=%(project)s",
                        {"project": project_id},
                    ).fetchone()
                    assert highest is not None
                    row["sequence"] = cast(int, highest[0]) + 1
                if cast(int, row["sequence"] or 0) > 32767:
                    raise problem("invalid_phase", field="sequence")
                self._validate_phase(row)
            else:
                self._validate_milestone(row, project)
            writable = [f for f in fields if f not in ("id", "project_id", "created_at")]
            try:
                with self.repo.connection.transaction():
                    if before is None:
                        columns = ["id", "org_id", "project_id", *writable]
                        self.repo.execute(
                            f"INSERT INTO project_{kind} ({', '.join(columns)}) VALUES "
                            f"({', '.join('%(' + f + ')s' for f in columns)})",
                            row,
                        )
                    else:
                        assignments = ", ".join(f + "=%(" + f + ")s" for f in writable)
                        self.repo.execute(
                            f"UPDATE project_{kind} SET {assignments} "
                            "WHERE org_id=%(org_id)s AND id=%(id)s "
                            "AND project_id=%(project_id)s",
                            row,
                        )
            except psycopg.errors.UniqueViolation as exc:
                label = (
                    "sequence_taken"
                    if exc.diag.constraint_name == "project_phase_project_id_sequence_key"
                    else "sub_project_taken"
                )
                raise problem(label, conflict=True) from exc
            after = self._row(kind, project_id, cast(UUID, row["id"]))
            audit_fields = tuple(
                f
                for f in writable
                if f
                in (
                    "name",
                    "planned_start",
                    "planned_end",
                    "actual_start",
                    "actual_end",
                    "percent_complete",
                    "due_date",
                    "completed_on",
                )
            )
            AuditWriter(cast(AuditConnection, self.repo.connection), self.repo.scope).write(
                actor=AuditActor(ActorKind.USER, self.actor_id),
                action=f"project.{kind}.{'update' if before else 'create'}",
                target_type=f"project_{kind}",
                target_id=cast(UUID, row["id"]),
                outcome=Outcome.SUCCESS,
                before_source=before,
                before_fields=audit_fields if before is not None else (),
                after_source=after,
                after_fields=audit_fields,
            )
            return (
                PhaseRead.model_validate(with_variance(after))
                if kind == "phase"
                else MilestoneRead.model_validate(after)
            )
