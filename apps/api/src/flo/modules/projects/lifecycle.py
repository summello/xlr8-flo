"""Project state machine; transitions and postings share balance-first serialization."""

from collections.abc import Callable
from decimal import Decimal
from typing import cast, get_args
from uuid import UUID

from psycopg.types.json import Jsonb

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.modules.projects.models import ProjectRepository
from flo.modules.projects.schemas import ProjectStatus, TransitionAvailable, TransitionCreate

TRANSITIONS = {
    ("draft", "approval_pending"): ("project.submit", False),
    ("approval_pending", "active"): ("project.approve", False),
    ("approval_pending", "draft"): ("project.approve", True),
    ("draft", "abandoned"): ("project.update", False),
    ("active", "deferred"): ("project.update", True),
    ("deferred", "active"): ("project.update", False),
    ("active", "completed"): ("project.complete", False),
    ("active", "abandoned"): ("project.complete", True),
    ("deferred", "abandoned"): ("project.complete", True),
}


def conflict(label: str, detail: str) -> ProblemError:
    return ProblemError(ErrorCode.CONFLICT, detail=detail, checks={"problem": label})


def open_documents(project_id: UUID) -> int:
    """M1 hook; requisitions and POs supply open-document counts in M2/M3."""
    return 0


class Lifecycle:
    def __init__(self, repo: ProjectRepository, actor_id: UUID | None) -> None:
        self.repo = repo
        self.actor_id = actor_id

    def snapshot(
        self, project_id: UUID, *, for_update: bool
    ) -> tuple[dict[str, object], dict[str, object]]:
        if for_update:
            self.repo.execute(
                "INSERT INTO project_balance(project_id, org_id, bu_id, currency) "
                "SELECT id, org_id, bu_id, currency FROM project "
                "WHERE org_id = %(org_id)s AND id = %(id)s ON CONFLICT (project_id) DO NOTHING",
                {"id": project_id},
            )
        balance = self.repo.execute(
            "SELECT reserved, committed FROM project_balance "
            "WHERE org_id = %(org_id)s AND project_id = %(id)s"
            + (" FOR UPDATE" if for_update else ""),
            {"id": project_id},
        ).fetchone()
        if balance is None and for_update:
            raise ProblemError(ErrorCode.NOT_FOUND)
        project = self.repo.get(project_id, lock=for_update)
        if project is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        blocked = {
            "reserved": str(balance[0]) if balance is not None else "0",
            "committed": str(balance[1]) if balance is not None else "0",
            "open_documents": open_documents(project_id),
            "currency": project["currency"],
        }
        return project, blocked

    def evaluate(
        self,
        project: dict[str, object],
        blocked: dict[str, object],
        body: TransitionCreate,
        permitted: Callable[[str], bool],
    ) -> list[ProblemError]:
        rule = TRANSITIONS.get((str(project["status"]), body.to))
        if rule is None:
            return [
                conflict(
                    "invalid_transition",
                    "This status transition is invalid. Choose an available action.",
                )
            ]
        permission, needs_reason = rule
        problems: list[ProblemError] = []
        if not permitted(permission):
            problems.append(
                ProblemError(
                    ErrorCode.FORBIDDEN,
                    detail=(
                        f"You need {permission} at this project. Ask an administrator for access."
                    ),
                )
            )
        reason = (body.reason or "").strip()
        if body.override and not permitted("project.close.override"):
            problems.append(
                ProblemError(
                    ErrorCode.FORBIDDEN,
                    detail=(
                        "You need project.close.override to override closure. "
                        "Ask an authorized closer."
                    ),
                )
            )
        if needs_reason and not reason:
            problems.append(
                ProblemError(
                    ErrorCode.VALIDATION_FAILED,
                    detail="A reason is required. Explain this transition.",
                    errors=(ProblemFieldError(field="reason", message="Supply a reason."),),
                )
            )
        if body.to == "approval_pending":
            missing = [
                f
                for f in ("name", "bu_id", "department_code", "ledger_account_code", "currency")
                if not project[f] or (isinstance(project[f], str) and not str(project[f]).strip())
            ]
            if missing:
                problems.append(
                    ProblemError(
                        ErrorCode.VALIDATION_FAILED,
                        detail="Complete required project fields before submitting: "
                        + ", ".join(missing),
                        errors=tuple(
                            ProblemFieldError(
                                field=f, message="Complete this field before submitting."
                            )
                            for f in missing
                        ),
                    )
                )
        if project["status"] == "approval_pending" and body.to == "active":
            submitter = self.repo.execute(
                "SELECT actor_id FROM project_status_event WHERE org_id = %(org_id)s "
                "AND project_id = %(id)s AND to_status = 'approval_pending' "
                "ORDER BY id DESC LIMIT 1",
                {"id": project["id"]},
            ).fetchone()
            if submitter and submitter[0] == self.actor_id:
                problems.append(
                    ProblemError(
                        ErrorCode.FORBIDDEN,
                        detail=(
                            "The submitter cannot approve this project. Ask a different approver."
                        ),
                    )
                )
        if body.to in {"completed", "abandoned"} and (
            Decimal(str(blocked["reserved"])) > 0
            or Decimal(str(blocked["committed"])) > 0
            or cast(int, blocked["open_documents"]) > 0
        ):
            if body.override and permitted("project.close.override"):
                if len(reason) < 20:
                    problems.append(
                        ProblemError(
                            ErrorCode.VALIDATION_FAILED,
                            detail="Explain the closure override in at least 20 characters.",
                            errors=(
                                ProblemFieldError(
                                    field="reason", message="Use at least 20 characters."
                                ),
                            ),
                        )
                    )
            else:
                problems.append(
                    conflict(
                        "closing_blocked",
                        (
                            f"Reserved {blocked['reserved']} {blocked['currency']}, "
                            f"committed {blocked['committed']} {blocked['currency']}, "
                            f"and {blocked['open_documents']} open documents block closure. "
                            "Release or resolve them, or use an authorized override."
                        ),
                    )
                )
        return problems

    def available(
        self,
        project: dict[str, object],
        blocked: dict[str, object],
        permitted: Callable[[str], bool],
    ) -> list[TransitionAvailable]:
        result = []
        for target in get_args(ProjectStatus.__value__):
            rule = TRANSITIONS.get((str(project["status"]), target))
            needs_reason = rule is not None and rule[1]
            problems = self.evaluate(
                project,
                blocked,
                TransitionCreate(to=target, reason="x" * 20 if needs_reason else None),
                permitted,
            )
            override_available = len(problems) == 1 and problems[0].checks == {
                "problem": "closing_blocked"
            }
            result.append(
                TransitionAvailable(
                    to=target,
                    reachable=rule is not None,
                    reason_required=needs_reason or override_available,
                    override_available=override_available,
                    allowed=not problems,
                    blocked_reasons=[p.detail or p.code.value for p in problems],
                )
            )
        return result

    def write(
        self, project: dict[str, object], blocked: dict[str, object], body: TransitionCreate
    ) -> None:
        self.repo.execute(
            "UPDATE project SET status = %(to)s, version = version + 1 "
            "WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": project["id"], "to": body.to},
        )
        self.repo.execute(
            "INSERT INTO project_status_event(org_id, project_id, from_status, to_status, "
            "actor_id, reason, override, blocked_by) "
            "VALUES (%(org_id)s, %(id)s, %(from)s, %(to)s, %(actor)s, "
            "%(reason)s, %(override)s, %(blocked)s)",
            {
                "id": project["id"],
                "from": project["status"],
                "to": body.to,
                "actor": self.actor_id,
                "reason": body.reason,
                "override": body.override,
                "blocked": Jsonb(blocked),
            },
        )
