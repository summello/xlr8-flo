"""Decision evidence and document effects share the locked instance transaction."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter
from flo.kernel.audit import Outcome as AuditOutcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.authz import PermissionHoldersResolver
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.outbox.store import OutboxConnection, OutboxStore
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals import delegation, engine
from flo.modules.approvals.models import WorkflowRepository
from flo.modules.approvals.schemas import (
    DecisionRead,
    DecisionResult,
    DecisionWrite,
    InstanceRead,
    Outcome,
    OutcomeContext,
    decision_invalid,
)
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    IdentityAuthorizationConnection,
    emails_by_id,
)

DECISION_FIELDS = (
    "id",
    "instance_id",
    "task_id",
    "actor_id",
    "on_behalf_of",
    "authority",
    "outcome",
    "reject_mode",
    "comment",
    "document_version",
    "members_at_decision",
    "decided_at",
)


def task_instance(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, task_id: UUID
) -> UUID:
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = (
            WorkflowRepository(conn, scope)
            .execute(
                "SELECT instance_id FROM approval_task WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": task_id},
            )
            .fetchone()
        )
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return cast(UUID, row[0])


def _decision_reads(
    conn: psycopg.Connection[tuple[object, ...]], rows: list[tuple[object, ...]]
) -> list[DecisionRead]:
    emails = emails_by_id(
        cast(IdentityAuthorizationConnection, conn),
        [cast(UUID, id) for row in rows for id in (row[3], row[4]) if id is not None],
    )
    result = []
    for row in rows:
        data = dict(zip((*DECISION_FIELDS, "node_id", "step_label"), row, strict=True))
        data["actor_email"] = emails.get(cast(UUID, data.pop("actor_id")), "(removed user)")
        behalf = cast(UUID | None, data.pop("on_behalf_of"))
        data["on_behalf_of_email"] = emails.get(behalf, "(removed user)") if behalf else None
        result.append(DecisionRead.model_validate(data))
    return result


_DECISION_SELECT = (
    "SELECT "
    + ",".join("d." + f for f in DECISION_FIELDS)
    + ",t.node_id,t.name FROM approval_decision d LEFT JOIN approval_task t "
    "ON t.org_id=d.org_id AND t.id=d.task_id "
)


def decisions_for(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, instance_id: UUID
) -> list[DecisionRead]:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        engine._instance(repo, instance_id)
        rows = repo.execute(
            _DECISION_SELECT + "WHERE d.org_id=%(org_id)s AND d.instance_id=%(id)s "
            "ORDER BY d.decided_at,d.id",
            {"id": instance_id},
        ).fetchall()
        return _decision_reads(conn, rows)


def decided_on_behalf(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, owner_id: UUID
) -> list[DecisionRead]:
    """Decisions a delegate recorded for `owner_id`, newest first (APR-017).

    ponytail: the latest 50; add a cursor if an owner is covered more often than that.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        rows = (
            WorkflowRepository(conn, scope)
            .execute(
                _DECISION_SELECT + "WHERE d.org_id=%(org_id)s AND d.on_behalf_of=%(me)s "
                "ORDER BY d.decided_at DESC,d.id DESC LIMIT 50",
                {"me": owner_id},
            )
            .fetchall()
        )
        return _decision_reads(conn, rows)


def _insert(
    repo: WorkflowRepository,
    instance: dict[str, object],
    *,
    task_id: UUID | None,
    actor_id: UUID,
    authority: str,
    outcome: str,
    comment: str | None,
    version: int,
    members: list[str],
    reject_mode: str | None = None,
    on_behalf_of: UUID | None = None,
) -> UUID:
    id = uuid4()
    repo.execute(
        "INSERT INTO approval_decision(id,org_id,instance_id,task_id,actor_id,on_behalf_of,"
        "authority,outcome,reject_mode,comment,document_version,members_at_decision) VALUES "
        "(%(id)s,%(org_id)s,%(instance)s,%(task)s,%(actor)s,%(behalf)s,%(authority)s,"
        "%(outcome)s,%(reject_mode)s,%(comment)s,%(version)s,%(members)s)",
        {
            "id": id,
            "instance": instance["id"],
            "task": task_id,
            "actor": actor_id,
            "behalf": on_behalf_of,
            "authority": authority,
            "outcome": outcome,
            "reject_mode": reject_mode,
            "comment": comment,
            "version": version,
            "members": Jsonb(members),
        },
    )
    return id


def _audit(
    repo: WorkflowRepository, instance: dict[str, object], actor: UUID, id: UUID, before_status: str
) -> None:
    AuditWriter(cast(AuditConnection, repo.connection), repo.scope).write(
        actor=AuditActor(ActorKind.USER, actor),
        action="approval.decision",
        target_type="approval_instance",
        target_id=cast(UUID, instance["id"]),
        outcome=AuditOutcome.SUCCESS,
        before_source={"status": before_status},
        before_fields=("status",),
        after_source={"decision_id": id, "status": instance["status"]},
        after_fields=("decision_id", "status"),
    )
    if instance["status"] in {"approved", "rejected", "returned"}:
        adapter = engine.adapter_for(str(instance["document_type"]))
        summary = adapter.summaries(
            repo.connection, repo.scope, [cast(UUID, instance["document_id"])]
        )[cast(UUID, instance["document_id"])]
        emails = emails_by_id(
            cast(IdentityAuthorizationConnection, repo.connection),
            [cast(UUID, instance["requester_id"])],
        )
        recipient = emails.get(cast(UUID, instance["requester_id"]))
        if recipient is not None:
            OutboxStore(cast(OutboxConnection, repo.connection), repo.scope).add_email(
                to=recipient,
                template="approval-outcome-v1",
                context={
                    "document_type": instance["document_type"],
                    "number": summary.number,
                    "outcome": instance["status"],
                    "link": summary.href,
                },
                idempotency_key=f"approval:{instance['id']}:{instance['status']}",
            )


def _end(
    repo: WorkflowRepository, instance: dict[str, object], outcome: Outcome, context: OutcomeContext
) -> None:
    repo.execute(
        "UPDATE approval_task SET status='cancelled',completed_at=now() "
        "WHERE org_id=%(org_id)s AND instance_id=%(id)s AND status='open'",
        {"id": instance["id"]},
    )
    for entry in cast(list[dict[str, object]], instance["route"]):
        if entry.get("node_id") == instance["current_node_id"]:
            entry["state"] = "completed"
            if outcome in {Outcome.REJECTED, Outcome.RETURNED}:
                entry["decided_by"] = sorted(
                    set(cast(list[str], entry.get("decided_by", []))) | {str(context.actor_id)}
                )
    instance.update(status=outcome.value, current_node_id=None, finished_at=datetime.now(UTC))
    engine._save(repo, instance)
    engine.adapter_for(str(instance["document_type"])).on_outcome(
        repo.connection,
        repo.scope,
        cast(UUID, instance["document_id"]),
        outcome,
        context,
    )


def decide(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    task_id: UUID,
    actor_id: UUID,
    body: DecisionWrite,
) -> DecisionResult:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        instance_id = task_instance(conn, scope, task_id)
        instance = engine._instance(repo, instance_id, lock=True)
        task = next(t for t in engine._tasks(repo, instance_id) if t["id"] == task_id)
        if instance["status"] != "running" or task["status"] != "open":
            raise engine.conflict(
                "task_closed", "This approval task is closed. Reload the approval route."
            )
        adapter = engine.adapter_for(str(instance["document_type"]))
        snapshot = adapter.snapshot(conn, scope, cast(UUID, instance["document_id"]))
        if body.document_version != snapshot.version:
            raise engine.conflict(
                "stale_version",
                "This document changed after you opened it. "
                "Reload it and review the current version.",
            )
        resolver = AuthorizationResolver(cast(AuthorizationConnection, conn), scope)
        version = engine._version(repo, cast(UUID, instance["workflow_version_id"]))
        node = next(n for n in version.graph.nodes if n.id == task["node_id"])
        effective = delegation.effective_members(
            conn, scope, instance, task, node, snapshot, resolver
        )
        members = effective.base
        acting_for = None if actor_id in members else effective.delegates.get(actor_id)
        if actor_id not in members and acting_for is None:
            raise ProblemError(ErrorCode.FORBIDDEN, detail="You are not an approver for this step.")
        principal = acting_for or actor_id
        if body.assign_project_id is not None:
            next_node = engine._walk(version.graph, node.id, snapshot, [])
            if not adapter.accepts_project_assignment or next_node.kind != "end":
                raise decision_invalid(
                    "assign_project_id",
                    "Assign a project only on the final approval step of a supported document.",
                )
        if (
            body.outcome != "comment"
            and repo.execute(
                "SELECT id FROM approval_decision WHERE org_id=%(org_id)s AND task_id=%(task)s "
                "AND (actor_id IN (%(actor)s,%(principal)s) OR on_behalf_of=%(principal)s) "
                "AND outcome IN ('approve','reject','return')",
                {"task": task_id, "actor": actor_id, "principal": principal},
            ).fetchone()
            is not None
        ):
            raise engine.conflict("already_decided", "You have already decided on this task.")
        before_status = str(instance["status"])
        listed = [*members, *effective.delegates]
        emails = emails_by_id(cast(IdentityAuthorizationConnection, conn), listed)
        id = _insert(
            repo,
            instance,
            task_id=task_id,
            actor_id=actor_id,
            on_behalf_of=acting_for,
            authority="delegate" if acting_for else "member",
            outcome=body.outcome,
            comment=body.comment,
            version=snapshot.version,
            members=sorted(emails.get(m, "(removed user)") for m in listed),
            reject_mode=body.reject_mode,
        )
        prepare = getattr(adapter, "prepare_approval", None)
        if body.outcome == "approve" and prepare is not None:
            # Optional adapter hook: the document may refuse an approval or take a correction
            # (expired master codes, D-M2-25); a raise rolls the decision back with it.
            prepare(
                conn,
                scope,
                cast(UUID, instance["document_id"]),
                actor_id,
                body.substitutions.model_dump(exclude_none=True) if body.substitutions else {},
            )
        elif body.substitutions is not None:
            raise decision_invalid("substitutions", "Substitute codes only when approving.")
        context = OutcomeContext(
            actor_id,
            id,
            body.assign_project_id,
            lambda permission, target: (
                resolver.check(IdentityId(actor_id), permission, target).allowed
            ),
            body.reject_mode,
        )
        if body.outcome == "approve":
            evidence = [
                dict(
                    zip(
                        ("id", "task_id", "user_id", "outcome", "decided_at", "actor_id"),
                        r,
                        strict=True,
                    )
                )
                for r in repo.execute(
                    "SELECT id,task_id,COALESCE(on_behalf_of,actor_id),outcome,decided_at,actor_id "
                    "FROM approval_decision "
                    "WHERE org_id=%(org_id)s AND instance_id=%(id)s",
                    {"id": instance_id},
                ).fetchall()
            ]
            if engine.step_complete(task, evidence, members) is True:
                engine.advance(
                    conn,
                    scope,
                    instance_id=instance_id,
                    task_id=task_id,
                    context=context,
                    resolver=cast(PermissionHoldersResolver, resolver),
                    decisions=evidence,
                )
                instance = engine._instance(repo, instance_id)
        elif body.outcome in {"reject", "return"}:
            terminal = Outcome.REJECTED if body.outcome == "reject" else Outcome.RETURNED
            repo.execute(
                "UPDATE approval_task SET status='completed',outcome=%(outcome)s,"
                "completed_at=now() "
                "WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": task_id, "outcome": terminal.value},
            )
            _end(repo, instance, terminal, context)
        _audit(repo, instance, actor_id, id, before_status)
        return DecisionResult(
            decision=next(d for d in decisions_for(conn, scope, instance_id) if d.id == id),
            instance=engine.get_instance(conn, scope, instance_id),
        )


def withdraw(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    document_type: str,
    document_id: UUID,
    actor_id: UUID,
    reason: str,
) -> InstanceRead:
    """Caller authorizes first; document modules can withdraw on an admin's behalf."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        row = repo.execute(
            "SELECT id FROM approval_instance WHERE org_id=%(org_id)s AND document_type=%(type)s "
            "AND document_id=%(doc)s AND status='running' FOR UPDATE",
            {"type": document_type, "doc": document_id},
        ).fetchone()
        if row is None:
            raise engine.conflict("task_closed", "This approval instance is closed.")
        instance = engine._instance(repo, cast(UUID, row[0]))
        before_status = str(instance["status"])
        adapter = engine.adapter_for(document_type)
        snapshot = adapter.snapshot(conn, scope, document_id)
        id = _insert(
            repo,
            instance,
            task_id=None,
            actor_id=actor_id,
            authority="requester" if actor_id == instance["requester_id"] else "admin",
            outcome="withdraw",
            comment=reason.strip() or None,
            version=snapshot.version,
            members=[],
        )
        resolver = AuthorizationResolver(cast(AuthorizationConnection, conn), scope)
        _end(
            repo,
            instance,
            Outcome.WITHDRAWN,
            OutcomeContext(
                actor_id,
                id,
                None,
                lambda permission, target: (
                    resolver.check(IdentityId(actor_id), permission, target).allowed
                ),
            ),
        )
        _audit(repo, instance, actor_id, id, before_status)
        return engine.get_instance(conn, scope, cast(UUID, instance["id"]))


def withdraw_instance(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    instance_id: UUID,
    actor_id: UUID,
    comment: str | None,
) -> InstanceRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        instance = engine._instance(WorkflowRepository(conn, scope), instance_id, lock=True)
        if instance["requester_id"] != actor_id:
            raise ProblemError(
                ErrorCode.FORBIDDEN, detail="Only the requester may withdraw this approval."
            )
        if instance["status"] != "running":
            raise engine.conflict("task_closed", "This approval instance is closed.")
        return withdraw(
            conn,
            scope,
            document_type=str(instance["document_type"]),
            document_id=cast(UUID, instance["document_id"]),
            actor_id=actor_id,
            reason=comment or "",
        )
