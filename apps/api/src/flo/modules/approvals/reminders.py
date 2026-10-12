"""Daily approval digests and escalations; one email per person per day, no amounts."""

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter
from flo.kernel.audit import Outcome as AuditOutcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.authz import PermissionHoldersResolver
from flo.kernel.jobs import Job, JobQueue
from flo.kernel.jobs.queue import JobConnection
from flo.kernel.logging import correlation_context
from flo.kernel.outbox.store import OutboxConnection, OutboxStore
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals import delegation, engine
from flo.modules.approvals.models import WorkflowRepository
from flo.modules.approvals.schemas import ReminderReport
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    IdentityAuthorizationConnection,
    emails_by_id,
)

Connection = psycopg.Connection[tuple[object, ...]]
DIGEST_WINDOW = timedelta(hours=24)


def run_org(conn: Connection, scope: Scope, *, now: datetime | None = None) -> ReminderReport:
    """Escalate overdue-by-rule tasks, then send one digest per recipient for today."""
    now = now or datetime.now(UTC)
    today: date = now.date()
    escalated = 0
    digests = 0
    with (
        correlation_context("approval-reminders"),
        tenant_transaction(cast(RlsSession, conn), scope),
    ):
        repo = WorkflowRepository(conn, scope)
        # Serialize daily recipient checks with other reminder runs for this organization.
        repo.execute("SELECT pg_advisory_xact_lock(hashtextextended(%(org_id)s::text, 8))")
        resolver = AuthorizationResolver(cast(AuthorizationConnection, conn), scope)
        open_tasks = repo.execute(
            "SELECT t.id,t.instance_id FROM approval_task t JOIN approval_instance i "
            "ON i.org_id=t.org_id AND i.id=t.instance_id WHERE t.org_id=%(org_id)s "
            "AND t.status='open' AND i.status='running' ORDER BY t.created_at,t.id"
        ).fetchall()
        per_user: dict[UUID, list[tuple[str, str, datetime]]] = defaultdict(list)
        reminded: list[UUID] = []
        for task_id, instance_id in open_tasks:
            instance = engine._instance(repo, cast(UUID, instance_id))
            task = next(
                t for t in engine._tasks(repo, cast(UUID, instance_id)) if t["id"] == task_id
            )
            rule = cast(dict[str, object] | None, task.get("escalation_rule"))
            if (
                rule
                and task.get("escalated_at") is None
                and now
                > cast(datetime, task["created_at"]) + timedelta(days=int(str(rule["after_days"])))
            ):
                repo.execute(
                    "UPDATE approval_task SET escalated_at=%(now)s WHERE org_id=%(org_id)s "
                    "AND id=%(id)s AND escalated_at IS NULL",
                    {"id": task_id, "now": now},
                )
                task["escalated_at"] = now
                escalated += 1
                AuditWriter(cast(AuditConnection, conn), scope).write(
                    actor=AuditActor(ActorKind.JOB, None),
                    action="approval.escalated",
                    target_type="approval_task",
                    target_id=cast(UUID, task_id),
                    outcome=AuditOutcome.SUCCESS,
                    before_source={"escalated": False},
                    before_fields=("escalated",),
                    after_source={"escalated": True},
                    after_fields=("escalated",),
                )
            due = cast(datetime | None, task["due_at"])
            if due is None or due > now + DIGEST_WINDOW or task.get("reminded_on") == today:
                continue
            adapter = engine.adapter_for(str(instance["document_type"]))
            snapshot = adapter.snapshot(conn, scope, cast(UUID, instance["document_id"]))
            version = engine._version(repo, cast(UUID, instance["workflow_version_id"]))
            node = next(n for n in version.graph.nodes if n.id == task["node_id"])
            members = delegation.effective_members(
                conn,
                scope,
                instance,
                task,
                node,
                snapshot,
                cast(PermissionHoldersResolver, resolver),
                now=now,
            )
            covered = set(members.delegates.values())
            recipients = [u for u in members.base if u not in covered] + list(members.delegates)
            summary = adapter.summaries(conn, scope, [cast(UUID, instance["document_id"])])[
                cast(UUID, instance["document_id"])
            ]
            for user in recipients:
                per_user[user].append((str(instance["document_type"]), summary.number, due))
            reminded.append(cast(UUID, task_id))
        emails = emails_by_id(cast(IdentityAuthorizationConnection, conn), list(per_user))
        outbox = OutboxStore(cast(OutboxConnection, conn), scope)
        for user, items in per_user.items():
            recipient = emails.get(user)
            if recipient is None:
                continue
            key = f"approval-digest:{user}:{today.isoformat()}"
            if repo.execute(
                "SELECT 1 FROM outbox WHERE org_id=%(org_id)s AND idempotency_key=%(key)s",
                {"key": key},
            ).fetchone():
                continue
            outbox.add_email(
                to=recipient,
                template="approval-digest-v1",
                context={
                    "items": [
                        {
                            "document_type": t,
                            "number": n,
                            "due": d.date().isoformat(),
                            "overdue": d < now,
                        }
                        for t, n, d in sorted(items, key=lambda x: (x[2], x[1]))
                    ]
                },
                idempotency_key=key,
            )
            digests += 1
        if reminded:
            repo.execute(
                "UPDATE approval_task SET reminded_on=%(today)s WHERE org_id=%(org_id)s "
                "AND id=ANY(%(ids)s)",
                {"today": today, "ids": reminded},
            )
    return ReminderReport(organizations=1, digests=digests, escalated=escalated)


def run_all(conn: Connection) -> ReminderReport:
    """Enumerate tenant ids and enqueue independent, retryable organization jobs."""
    with conn.transaction():
        orgs = conn.execute(
            "SELECT DISTINCT org_id FROM identity_membership ORDER BY org_id"
        ).fetchall()
    for row in orgs:
        scope = Scope(cast(UUID, row[0]))
        with tenant_transaction(cast(RlsSession, conn), scope):
            JobQueue(cast(JobConnection, conn), scope).enqueue("approval.reminders", {})
    return ReminderReport(organizations=len(orgs), digests=0, escalated=0)


def job_handler(connect: Callable[[], Connection]) -> Callable[[Job], None]:
    """Run on an independent connection while the runner owns its queue transaction."""

    def handle(job: Job) -> None:
        with connect() as conn:
            run_org(conn, Scope(job.org_id))

    return handle
