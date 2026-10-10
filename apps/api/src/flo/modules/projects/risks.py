"""Scoped risk writes, optimistic versions and keyset paging."""

import base64
import json
from datetime import date
from typing import cast
from uuid import UUID, uuid4

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.identity.service import IdentityAuthorizationConnection, user_exists
from flo.modules.projects.models import ProjectRepository
from flo.modules.projects.schemas import RiskCreate, RiskPage, RiskPatch, RiskRead, RiskStatus

FIELDS = (
    "id",
    "project_id",
    "title",
    "description",
    "likelihood",
    "impact",
    "score",
    "owner_id",
    "mitigation",
    "due_date",
    "status",
    "closed_reason",
    "created_by",
    "created_at",
    "updated_at",
    "version",
)
WRITABLE = (
    "title",
    "description",
    "likelihood",
    "impact",
    "owner_id",
    "mitigation",
    "due_date",
    "status",
    "closed_reason",
)


def today() -> date:
    return date.today()


class Risks:
    def __init__(self, repo: ProjectRepository, actor_id: UUID | None) -> None:
        self.repo = repo
        self.actor_id = actor_id

    def _project(self, project_id: UUID, write: bool = False) -> None:
        # Avoid serializing unrelated risk updates on the project lock. A share lock
        # protects the status check against a concurrent project transition.
        row = self.repo.execute(
            "SELECT status FROM project WHERE org_id=%(org_id)s AND id=%(id)s"
            + (" FOR SHARE" if write else ""),
            {"id": project_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        if write and row[0] in ("completed", "abandoned"):
            raise ProblemError(
                ErrorCode.CONFLICT,
                detail="The project is closed.",
                checks={"problem": "project_closed"},
            )

    def _row(self, project_id: UUID, risk_id: UUID, lock: bool = False) -> dict[str, object]:
        row = self.repo.execute(
            f"SELECT {', '.join(FIELDS)} FROM project_risk "
            "WHERE org_id=%(org_id)s AND project_id=%(project_id)s AND id=%(id)s"
            + (" FOR UPDATE" if lock else ""),
            {"project_id": project_id, "id": risk_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return dict(zip(FIELDS, row, strict=True))

    def _read(self, row: dict[str, object]) -> RiskRead:
        due = cast(date | None, row["due_date"])
        return RiskRead.model_validate(
            row
            | {
                "overdue": due is not None
                and due < today()
                and row["status"] in ("open", "mitigating")
            }
        )

    def write(
        self,
        project_id: UUID,
        body: RiskCreate | RiskPatch,
        risk_id: UUID | None = None,
        version: str | None = None,
    ) -> RiskRead:
        from flo.modules.projects.service import invalid

        expected = None
        if risk_id is not None:
            if version is None:
                raise ProblemError(
                    ErrorCode.BAD_REQUEST,
                    detail="Supply If-Match with the risk version.",
                    checks={"problem": "if_match_required"},
                )
            try:
                expected = int(version)
            except ValueError as exc:
                raise ProblemError(
                    ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
                ) from exc
        with tenant_transaction(cast(RlsSession, self.repo.connection), self.repo.scope):
            self._project(project_id, True)
            before = self._row(project_id, risk_id, True) if risk_id is not None else None
            if before is not None and before["version"] != expected:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="The risk changed. Reload and retry.",
                    checks={"problem": "stale_version"},
                )
            row = (
                before or {"id": uuid4(), "project_id": project_id, "created_by": self.actor_id}
            ) | body.model_dump(exclude_unset=before is not None)
            for field in ("title", "likelihood", "impact", "owner_id", "status"):
                if row[field] is None:
                    raise invalid(field, f"{field} cannot be null. Supply a value.")
            if not user_exists(
                cast(IdentityAuthorizationConnection, self.repo.connection),
                self.repo.scope,
                cast(UUID, row["owner_id"]),
            ):
                raise invalid("owner_id", "Choose a user in this organization.")
            if row["status"] == "closed":
                if not str(row["closed_reason"] or "").strip():
                    raise invalid("closed_reason", "Supply a reason for closing the risk.")
            else:
                row["closed_reason"] = None
            if before is None:
                columns = ("id", "org_id", "project_id", "created_by", *WRITABLE)
                self.repo.execute(
                    f"INSERT INTO project_risk ({', '.join(columns)}) VALUES "
                    f"({', '.join('%(' + f + ')s' for f in columns)})",
                    row,
                )
            else:
                self.repo.execute(
                    "UPDATE project_risk SET "
                    + ", ".join(f + "=%(" + f + ")s" for f in WRITABLE)
                    + ", version=version+1, updated_at=now() "
                    "WHERE org_id=%(org_id)s AND project_id=%(project_id)s AND id=%(id)s",
                    row,
                )
            after = self._row(project_id, cast(UUID, row["id"]))
            AuditWriter(cast(AuditConnection, self.repo.connection), self.repo.scope).write(
                actor=AuditActor(ActorKind.USER, self.actor_id),
                action="project.risk.update" if before is not None else "project.risk.create",
                target_type="project_risk",
                target_id=cast(UUID, row["id"]),
                outcome=Outcome.SUCCESS,
                before_source=before,
                before_fields=FIELDS if before is not None else (),
                after_source=after,
                after_fields=FIELDS,
            )
            return self._read(after)

    def list(
        self,
        project_id: UUID,
        status: RiskStatus | None = None,
        min_score: int | None = None,
        cursor: str | None = None,
        page_size: int = 50,
    ) -> RiskPage:
        from flo.modules.projects.service import invalid

        if not 1 <= page_size <= 50:
            raise invalid("page_size", "Page size must be from 1 to 50.")
        if min_score is not None and not 1 <= min_score <= 25:
            raise invalid("min_score", "Minimum score must be from 1 to 25.")
        if status is not None and status not in ("open", "mitigating", "closed", "accepted"):
            raise invalid("status", "Choose a supported risk status.")
        boundary = ""
        params: dict[str, object] = {
            "project_id": project_id,
            "status": status,
            "min_score": min_score,
            "limit": page_size + 1,
        }
        if cursor is not None:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
                if not isinstance(decoded, dict) or not isinstance(decoded["id"], str):
                    raise ValueError("invalid cursor structure")
                score = decoded["score"]
                if type(score) is not int or not 1 <= score <= 25:
                    raise ValueError("invalid score")
                due = decoded["due_date"]
                if due is not None and not isinstance(due, str):
                    raise ValueError("invalid cursor due date")
                params |= {
                    "s": score,
                    "d": date.fromisoformat(due) if due is not None else None,
                    "i": UUID(decoded["id"]),
                }
            except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                raise invalid("cursor", "Invalid cursor. Restart paging.") from exc
            dd = "COALESCE(due_date, 'infinity'::date)"
            d = "COALESCE(%(d)s::date, 'infinity'::date)"
            boundary = (
                f" AND (score < %(s)s OR (score = %(s)s AND {dd} > {d}) OR "
                f"(score = %(s)s AND {dd} = {d} AND id > %(i)s))"
            )
        with tenant_transaction(cast(RlsSession, self.repo.connection), self.repo.scope):
            self._project(project_id)
            rows = self.repo.execute(
                f"SELECT {', '.join(FIELDS)} FROM project_risk "
                "WHERE org_id=%(org_id)s AND project_id=%(project_id)s "
                "AND (%(status)s::text IS NULL OR status=%(status)s) "
                "AND (%(min_score)s::int IS NULL OR score >= %(min_score)s)"
                + boundary
                + " ORDER BY score DESC, due_date ASC NULLS LAST, id ASC LIMIT %(limit)s",
                params,
            ).fetchall()
            records = [self._read(dict(zip(FIELDS, r, strict=True))) for r in rows[:page_size]]
            next_cursor = None
            if len(rows) > page_size:
                last = records[-1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "score": last.score,
                            "due_date": last.due_date.isoformat() if last.due_date else None,
                            "id": str(last.id),
                        }
                    ).encode()
                ).decode()
            return RiskPage(rows=records, next_cursor=next_cursor)
