"""Pinned, transactional execution; task membership is always resolved live."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from flo.kernel.authz import AuthorizationTarget, PermissionHoldersResolver
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals import conditions
from flo.modules.approvals.eligibility import EligibleSet, eligible_approvers
from flo.modules.approvals.models import VERSION_FIELDS, WorkflowRepository
from flo.modules.approvals.preview import walk
from flo.modules.approvals.schemas import (
    Approvers,
    DocumentAdapter,
    DocumentSnapshot,
    ExcludedApprover,
    InstanceRead,
    Node,
    Outcome,
    OutcomeContext,
    Quorum,
    RoleApprovers,
    RoutePreview,
    RouteProblem,
    RouteStep,
    RouteStepState,
    TaskRead,
    WorkflowGraph,
    WorkflowVersionRead,
)
from flo.modules.identity.service import AuthorizationConnection, AuthorizationResolver
from flo.modules.identity.service import AuthorizationTarget as IdentityTarget

INSTANCE_FIELDS = (
    "id",
    "document_type",
    "document_id",
    "workflow_version_id",
    "document_version",
    "material_digest",
    "approved_material_digest",
    "approved_version",
    "requester_id",
    "status",
    "current_node_id",
    "route",
    "started_at",
    "finished_at",
)
TASK_FIELDS = (
    "id",
    "instance_id",
    "node_id",
    "name",
    "approver_rule",
    "mode",
    "allow_repeat",
    "status",
    "outcome",
    "created_at",
    "due_at",
    "completed_at",
    "escalated_at",
    "escalation_rule",
    "reminded_on",
)


_RULE: TypeAdapter[Any] = TypeAdapter(Approvers)


@dataclass(frozen=True)
class VersionResolution:
    version: WorkflowVersionRead
    definition_id: UUID
    definition_name: str
    scope_type: Literal["org", "bu"]
    scope_id: UUID
    scope_name: str
    scope_kind: Literal["org", "bu", "ou"]

    @property
    def bound_scope(self) -> str:
        return f"{self.scope_type}:{self.scope_id}"

    @property
    def reason(self) -> str:
        if self.scope_type == "org":
            return (
                f"Published version {self.version.version} bound to {self.scope_name} "
                "(organization level)."
            )
        kind = "business unit" if self.scope_kind == "bu" else "organizational unit"
        return (
            f"Published version {self.version.version} bound to {self.scope_name} ({kind}) "
            "— nearer than the organization-level workflow."
        )


def adapter_for(document_type: str) -> DocumentAdapter:
    conditions.approve_permission(document_type)  # actionable registry error
    return cast(DocumentAdapter, conditions._adapters[document_type])


def material_digest(snapshot: DocumentSnapshot) -> str:
    return hashlib.sha256(
        json.dumps(
            snapshot.material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def conflict(problem: str, detail: str) -> ProblemError:
    return ProblemError(ErrorCode.CONFLICT, checks={"problem": problem}, detail=detail)


def _instance(repo: WorkflowRepository, id: UUID, *, lock: bool = False) -> dict[str, object]:
    row = repo.execute(
        f"SELECT {','.join(INSTANCE_FIELDS)} FROM approval_instance "
        "WHERE org_id=%(org_id)s AND id=%(id)s" + (" FOR UPDATE" if lock else ""),
        {"id": id},
    ).fetchone()
    if row is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return dict(zip(INSTANCE_FIELDS, row, strict=True))


def resolve_version(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    bu_id: UUID | None,
) -> WorkflowVersionRead | None:
    """Nearest published live definition; retirement does not affect stored versions."""
    resolved = resolve_version_reason(conn, scope, document_type, bu_id)
    return resolved.version if resolved else None


def resolve_version_reason(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    bu_id: UUID | None,
    *,
    pinned_version_id: UUID | None = None,
) -> VersionResolution | None:
    """Resolve once and retain the binding facts that explain the choice.

    ``pinned_version_id`` reads the same facts for an already-recorded instance, so
    explainability survives a later publish or retirement without re-routing history.
    """
    with tenant_transaction(cast(RlsSession, conn), scope):
        live_filter = (
            "d.document_type=%(type)s AND d.retired_at IS NULL AND v.status='published' "
            "AND ((d.scope_type='bu' AND d.scope_id=%(bu)s) OR "
            "(d.scope_type='org' AND d.scope_id=%(org_id)s))"
        )
        pinned_filter = "v.id=%(pinned)s AND d.document_type=%(type)s"
        row = (
            WorkflowRepository(conn, scope)
            .execute(
                f"SELECT {', '.join('v.' + field for field in VERSION_FIELDS)} "
                ",d.id,d.name,d.scope_type,d.scope_id,"
                "CASE WHEN d.scope_type='org' THEN o.name ELSE u.name END,"
                "CASE WHEN d.scope_type='org' THEN 'org' ELSE u.kind END "
                "FROM workflow_definition d JOIN workflow_version v "
                "ON v.org_id=d.org_id AND v.definition_id=d.id "
                "JOIN organization o ON o.id=d.org_id "
                "LEFT JOIN org_unit u ON u.org_id=d.org_id AND u.id=d.scope_id "
                "WHERE d.org_id=%(org_id)s AND "
                + (pinned_filter if pinned_version_id is not None else live_filter)
                + " "
                "ORDER BY (d.scope_type='bu') DESC LIMIT 1",
                {"type": document_type, "bu": bu_id, "pinned": pinned_version_id},
            )
            .fetchone()
        )
        if row is None:
            return None
        width = len(VERSION_FIELDS)
        return VersionResolution(
            version=WorkflowVersionRead.model_validate(
                dict(zip(VERSION_FIELDS, row[:width], strict=True))
            ),
            definition_id=cast(UUID, row[width]),
            definition_name=str(row[width + 1]),
            scope_type=cast(Literal["org", "bu"], row[width + 2]),
            scope_id=cast(UUID, row[width + 3]),
            scope_name=str(row[width + 4]),
            scope_kind=cast(Literal["org", "bu", "ou"], row[width + 5]),
        )


def _version(repo: WorkflowRepository, id: UUID) -> WorkflowVersionRead:
    row = repo.execute(
        f"SELECT {','.join(VERSION_FIELDS)} FROM workflow_version "
        "WHERE org_id=%(org_id)s AND id=%(id)s",
        {"id": id},
    ).fetchone()
    if row is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return WorkflowVersionRead.model_validate(dict(zip(VERSION_FIELDS, row, strict=True)))


def _tasks(repo: WorkflowRepository, id: UUID) -> list[dict[str, object]]:
    return [
        dict(zip(TASK_FIELDS, row, strict=True))
        for row in repo.execute(
            f"SELECT {','.join(TASK_FIELDS)} FROM approval_task WHERE org_id=%(org_id)s "
            "AND instance_id=%(id)s ORDER BY created_at,id",
            {"id": id},
        ).fetchall()
    ]


def step_complete(
    task: Mapping[str, object],
    decisions: Sequence[Mapping[str, object]],
    current_members: Sequence[UUID],
) -> bool | Literal["rejected", "returned"]:
    """Total re-evaluation of persisted evidence against current membership."""
    members = set(current_members)
    evidence = [
        d
        for d in decisions
        if d["user_id"] in members and d.get("task_id", task.get("id")) == task.get("id")
    ]
    terminal = sorted(
        (d for d in evidence if d["outcome"] in {"reject", "return"}),
        key=lambda d: (cast(datetime, d["decided_at"]), cast(UUID, d["id"])),
    )
    if terminal:
        return "rejected" if terminal[0]["outcome"] == "reject" else "returned"
    approved = {cast(UUID, d["user_id"]) for d in evidence if d["outcome"] == "approve"}
    mode = task["mode"]
    if mode == "any":
        return bool(approved)
    if mode == "all":
        return bool(members) and members <= approved
    return len(approved) >= cast(int, cast(Mapping[str, object], mode)["quorum"])


def current_members(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    instance: Mapping[str, object],
    node: Node,
    snapshot: DocumentSnapshot,
    resolver: PermissionHoldersResolver,
) -> EligibleSet:
    assert node.approvers is not None
    result = eligible_approvers(
        conn,
        scope,
        str(instance["document_type"]),
        node.approvers,
        cast(IdentityTarget, snapshot.target),
        cast(UUID | None, instance["requester_id"]),
        resolver,
    )
    if not node.allow_repeat:
        earlier = {
            UUID(str(user))
            for entry in cast(list[dict[str, object]], instance["route"])
            if entry.get("node_id") != node.id
            for user in cast(list[str], entry.get("decided_by", []))
        }
        from flo.modules.approvals.eligibility import Excluded

        result.excluded.extend(
            Excluded(user_id=m.user_id, reason="already_decided")
            for m in result.members
            if m.user_id in earlier
        )
        result.members = [m for m in result.members if m.user_id not in earlier]
    return result


def _emails(repo: WorkflowRepository, ids: Sequence[UUID]) -> dict[UUID, str]:
    return {
        cast(UUID, row[0]): str(row[1])
        for row in repo.execute(
            "SELECT i.id,i.email FROM identity i WHERE i.id=ANY(%(ids)s) AND EXISTS "
            "(SELECT 1 FROM identity_membership m WHERE m.org_id=%(org_id)s "
            "AND m.identity_id=i.id)",
            {"ids": list(ids)},
        ).fetchall()
    }


def _step(
    repo: WorkflowRepository,
    instance: Mapping[str, object],
    node: Node,
    snapshot: DocumentSnapshot,
    resolver: PermissionHoldersResolver,
    state: RouteStepState,
    due_at: datetime | None,
) -> RouteStep:
    assert node.mode is not None
    recorded = cast(list[dict[str, object]], instance["route"])
    prefix = recorded[
        : next((i for i, e in enumerate(recorded) if e.get("node_id") == node.id), len(recorded))
    ]
    eligible = current_members(repo.connection, repo.scope, instance, node, snapshot, resolver)
    emails = _emails(
        repo, [m.user_id for m in eligible.members] + [e.user_id for e in eligible.excluded]
    )
    rule = node.approvers
    words = (
        f"role {rule.role_code}"
        if isinstance(rule, RoleApprovers)
        else (
            "listed by name"
            if getattr(rule, "type", None) == "users"
            else f"permission {adapter_for(str(instance['document_type'])).approve_permission}"
        )
    )
    return RouteStep(
        node_id=node.id,
        label=node.name or node.id,
        mode="quorum" if isinstance(node.mode, Quorum) else node.mode,
        quorum=node.mode.quorum if isinstance(node.mode, Quorum) else None,
        rule=words,
        approver_emails=sorted(emails[m.user_id] for m in eligible.members),
        excluded=sorted(
            [
                ExcludedApprover(email=emails[e.user_id], reason=e.reason)
                for e in eligible.excluded
                if e.user_id in emails
            ],
            key=lambda e: e.email,
        ),
        state=state,
        due_at=due_at,
        reached_via=next(
            (cast(str | None, e["reached_via"]) for e in reversed(prefix) if "reached_via" in e),
            None,
        ),
    )


def _walk(
    graph: WorkflowGraph, from_node: str, snapshot: DocumentSnapshot, route: list[dict[str, object]]
) -> Node:
    return walk(graph, snapshot.attributes, from_node, route).node


def _open(
    repo: WorkflowRepository,
    instance: dict[str, object],
    node: Node,
    snapshot: DocumentSnapshot,
    resolver: PermissionHoldersResolver,
) -> None:
    due = datetime.now(UTC) + timedelta(days=node.due_days) if node.due_days else None
    step = _step(repo, instance, node, snapshot, resolver, RouteStepState.OPEN, due)
    if not step.approver_emails:
        raise conflict(
            "no_eligible_approver",
            f"No eligible approver for {step.label}. Ask a workflow administrator.",
        )
    assert node.approvers is not None
    repo.execute(
        "INSERT INTO approval_task(id,org_id,instance_id,node_id,name,approver_rule,mode,"
        "allow_repeat,status,due_at,escalation_rule) VALUES(%(id)s,%(org_id)s,%(instance)s,"
        "%(node)s,%(name)s,%(rule)s,%(mode)s,%(repeat)s,'open',%(due)s,%(escalation)s)",
        {
            "id": uuid4(),
            "instance": instance["id"],
            "node": node.id,
            "name": step.label,
            "escalation": Jsonb(
                {
                    "after_days": node.escalate_after_days,
                    "approvers": node.escalate_to.model_dump(mode="json"),
                }
            )
            if node.escalate_after_days and node.escalate_to
            else None,
            "rule": Jsonb(node.approvers.model_dump(mode="json")),
            "mode": Jsonb(node.mode.model_dump() if isinstance(node.mode, Quorum) else node.mode),
            "repeat": node.allow_repeat,
            "due": due,
        },
    )
    cast(list[dict[str, object]], instance["route"]).append({"node_id": node.id, "state": "open"})
    instance["current_node_id"] = node.id


def _save(repo: WorkflowRepository, instance: Mapping[str, object]) -> None:
    repo.execute(
        "UPDATE approval_instance SET route=%(route)s,current_node_id=%(node)s,"
        "status=%(status)s,finished_at=%(finished)s,"
        "approved_material_digest=%(approved_digest)s,approved_version=%(approved_version)s "
        "WHERE org_id=%(org_id)s AND id=%(id)s",
        {
            "id": instance["id"],
            "route": Jsonb(instance["route"]),
            "node": instance["current_node_id"],
            "status": instance["status"],
            "approved_version": instance.get("approved_version"),
            "finished": instance["finished_at"],
            "approved_digest": instance["approved_material_digest"],
        },
    )


def _outcome(
    repo: WorkflowRepository, instance: dict[str, object], context: OutcomeContext
) -> None:
    adapter = adapter_for(str(instance["document_type"]))
    if context.assign_project_id is not None and not adapter.accepts_project_assignment:
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED, detail="This document does not accept project assignment."
        )
    snapshot = adapter.snapshot(repo.connection, repo.scope, cast(UUID, instance["document_id"]))
    instance.update(
        status="approved",
        current_node_id=None,
        finished_at=datetime.now(UTC),
        approved_version=snapshot.version,
        approved_material_digest=material_digest(snapshot),
    )
    _save(repo, instance)
    adapter.on_outcome(
        repo.connection, repo.scope, cast(UUID, instance["document_id"]), Outcome.APPROVED, context
    )


def preview_route(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    document_type: str,
    document_id: UUID,
    resolver: PermissionHoldersResolver,
    requester_id: UUID | None = None,
    version: WorkflowVersionRead | None = None,
    synthetic_snapshot: DocumentSnapshot | None = None,
) -> RoutePreview:
    with tenant_transaction(cast(RlsSession, conn), scope):
        snapshot = synthetic_snapshot or adapter_for(document_type).snapshot(
            conn, scope, document_id
        )
        bu: UUID | None = None
        scope_type = getattr(snapshot.target.scope_type, "value", "")
        if scope_type == "bu":
            bu = snapshot.target.scope_id
        elif scope_type == "project":
            bu = _business_unit(WorkflowRepository(conn, scope), snapshot.target)
        version = version or resolve_version(conn, scope, document_type, bu)
        result = RoutePreview(
            separation_of_duty_applied=synthetic_snapshot is None,
            workflow_version_id=version.id if version else None,
            workflow_version=version.version if version else None,
            steps=[],
            problems=[],
        )
        if version is None:
            result.problems.append(
                RouteProblem(
                    code="no_workflow",
                    step_label=None,
                    message=f"No approval workflow is published for {document_type}s in this "
                    f"business unit. Ask a workflow administrator.",
                )
            )
            return result
        route: list[dict[str, object]] = []
        instance = {
            "document_type": document_type,
            "requester_id": requester_id
            if synthetic_snapshot is not None
            else snapshot.requester_id,
            "route": route,
        }
        node = next(n for n in version.graph.nodes if n.kind == "start")
        while node.kind != "end":
            try:
                walked = walk(version.graph, snapshot.attributes, node.id, route)
                node = walked.node
                result.path_edge_indices.extend(walked.path_edge_indices)
            except ProblemError as exc:
                result.problems.append(
                    RouteProblem(
                        code="route_blocked",
                        step_label=node.name or node.id,
                        message=exc.detail or "Routing is blocked.",
                    )
                )
                break
            if node.kind == "approval":
                step = _step(
                    WorkflowRepository(conn, scope),
                    instance,
                    node,
                    snapshot,
                    resolver,
                    RouteStepState.OPEN if not result.steps else RouteStepState.UPCOMING,
                    None,
                )
                result.steps.append(step)
                if not step.approver_emails:
                    result.problems.append(
                        RouteProblem(
                            code="no_eligible_approver",
                            step_label=step.label,
                            message=f"No eligible approver for {step.label}. Ask a workflow "
                            f"administrator.",
                        )
                    )
                    break
        return result


def _business_unit(repo: WorkflowRepository, target: AuthorizationTarget) -> UUID | None:
    row = repo.execute(
        "WITH RECURSIVE ancestors AS (SELECT scope_type,scope_id,parent_scope_type,parent_scope_id "
        "FROM authorization_scope WHERE org_id=%(org_id)s AND "
        "scope_type='project' AND scope_id=%(id)s "
        "UNION ALL SELECT "
        "s.scope_type,s.scope_id,s.parent_scope_type,s.parent_scope_id FROM authorization_scope s "
        "JOIN ancestors a ON s.scope_type=a.parent_scope_type AND s.scope_id=a.parent_scope_id "
        "WHERE s.org_id=%(org_id)s) SELECT scope_id FROM ancestors WHERE scope_type='bu' LIMIT 1",
        {"id": target.scope_id},
    ).fetchone()
    return cast(UUID, row[0]) if row else None


def document_bu(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    document_id: UUID,
) -> UUID | None:
    """The business unit a new approval of this document would resolve its workflow in."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        target = adapter_for(document_type).snapshot(conn, scope, document_id).target
        kind = getattr(target.scope_type, "value", "")
        if kind == "bu":
            return target.scope_id
        return (
            _business_unit(WorkflowRepository(conn, scope), target) if kind == "project" else None
        )


def start(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    document_type: str,
    document_id: UUID,
    actor_id: UUID,
    resolver: PermissionHoldersResolver,
    resume_from: UUID | None = None,
) -> InstanceRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        # Lock any existing instance before the adapter touches funding/document rows (R).
        existing = repo.execute(
            f"SELECT {','.join(INSTANCE_FIELDS)} FROM approval_instance "
            "WHERE org_id=%(org_id)s AND document_type=%(type)s AND document_id=%(doc)s "
            "ORDER BY started_at DESC,id DESC LIMIT 1 FOR UPDATE",
            {"type": document_type, "doc": document_id},
        ).fetchone()
        prior = dict(zip(INSTANCE_FIELDS, existing, strict=True)) if existing else None
        if prior and prior["status"] == "running":
            raise conflict(
                "approval_in_progress",
                "Approval is already in progress. Open the current approval instance.",
            )
        snapshot = adapter_for(document_type).snapshot(conn, scope, document_id)
        route: list[dict[str, object]] = []
        if resume_from is not None:
            if prior is None or prior["id"] != resume_from or prior["status"] != "returned":
                raise conflict(
                    "cannot_resume",
                    "Only the latest returned approval can resume. Submit a new approval instead.",
                )
            if prior["material_digest"] != material_digest(snapshot):
                raise conflict(
                    "cannot_resume",
                    "This change is material; the request restarts from the first step.",
                )
            version = _version(repo, cast(UUID, prior["workflow_version_id"]))
            returned_tasks = [t for t in _tasks(repo, resume_from) if t["outcome"] == "returned"]
            if not returned_tasks:
                raise conflict(
                    "cannot_resume",
                    "The returned step is unavailable. Ask a workflow administrator.",
                )
            node = next(n for n in version.graph.nodes if n.id == returned_tasks[-1]["node_id"])
            for entry in cast(list[dict[str, object]], prior["route"]):
                if entry.get("node_id") == node.id:
                    break
                route.append(
                    entry
                    | (
                        {
                            "carried": True,
                            "source_instance_id": str(resume_from),
                            "state": "carried",
                        }
                        if entry.get("node_id")
                        else {}
                    )
                )
        else:
            kind = getattr(snapshot.target.scope_type, "value", "")
            bu = (
                snapshot.target.scope_id
                if kind == "bu"
                else (_business_unit(repo, snapshot.target) if kind == "project" else None)
            )
            resolved_version = resolve_version(conn, scope, document_type, bu)
            if resolved_version is None:
                raise conflict(
                    "no_workflow",
                    f"No approval workflow is published for {document_type}s in this "
                    f"business unit. Ask a workflow administrator.",
                )
            version = resolved_version
            node = _walk(
                version.graph,
                next(n.id for n in version.graph.nodes if n.kind == "start"),
                snapshot,
                route,
            )
        id = uuid4()
        try:
            with conn.transaction():
                repo.execute(
                    "INSERT INTO "
                    "approval_instance(id,org_id,document_type,document_id,workflow_version_id,"
                    "document_version,material_digest,requester_id,status) "
                    "VALUES(%(id)s,%(org_id)s,%(type)s,"
                    "%(doc)s,%(version)s,%(dv)s,%(digest)s,%(requester)s,'running')",
                    {
                        "id": id,
                        "type": document_type,
                        "doc": document_id,
                        "version": version.id,
                        "dv": snapshot.version,
                        "digest": material_digest(snapshot),
                        "requester": snapshot.requester_id,
                    },
                )
        except psycopg.errors.UniqueViolation as exc:
            raise conflict(
                "approval_in_progress",
                "Approval is already in progress. Open the current approval instance.",
            ) from exc
        instance = _instance(repo, id)
        instance["route"] = route
        if node.kind == "end":
            _outcome(
                repo,
                instance,
                OutcomeContext(
                    snapshot.requester_id,
                    None,
                    None,
                    lambda permission, target: (
                        resolver.check(
                            IdentityId(snapshot.requester_id), permission, target
                        ).allowed
                    ),
                ),
            )
        else:
            _open(repo, instance, node, snapshot, resolver)
            _save(repo, instance)
        return get_instance(conn, scope, id)


def advance(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    *,
    instance_id: UUID,
    task_id: UUID,
    context: OutcomeContext,
    resolver: PermissionHoldersResolver,
    decisions: Sequence[Mapping[str, object]],
) -> InstanceRead:
    """E10-S06 calls after threshold validation in the same locked transaction."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        instance = _instance(repo, instance_id, lock=True)
        task = next((t for t in _tasks(repo, instance_id) if t["id"] == task_id), None)
        if task is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        if instance["status"] != "running" or task["status"] != "open":
            raise conflict(
                "task_closed", "This approval task is closed. Reload the approval route."
            )
        repo.execute(
            "UPDATE approval_task SET status='completed',outcome='approved',completed_at=now() "
            "WHERE org_id=%(org_id)s AND id=%(id)s",
            {"id": task_id},
        )
        for entry in cast(list[dict[str, object]], instance["route"]):
            if entry.get("node_id") == task["node_id"]:
                entry.update(
                    state="completed",
                    decided_by=sorted(
                        {str(context.actor_id)}
                        | {
                            str(user)
                            for d in decisions
                            if d.get("task_id") == task_id
                            and d["outcome"] in {"approve", "reject", "return"}
                            for user in (d["user_id"], d.get("actor_id", d["user_id"]))
                        }
                    ),
                )
        snapshot = adapter_for(str(instance["document_type"])).snapshot(
            conn, scope, cast(UUID, instance["document_id"])
        )
        version = _version(repo, cast(UUID, instance["workflow_version_id"]))
        node = _walk(
            version.graph,
            str(task["node_id"]),
            snapshot,
            cast(list[dict[str, object]], instance["route"]),
        )
        if node.kind == "end":
            _outcome(repo, instance, context)
        else:
            _open(repo, instance, node, snapshot, resolver)
            _save(repo, instance)
        return get_instance(conn, scope, instance_id)


def get_instance(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, instance_id: UUID
) -> InstanceRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        instance = _instance(repo, instance_id)
        adapter = adapter_for(str(instance["document_type"]))
        snapshot = adapter.snapshot(conn, scope, cast(UUID, instance["document_id"]))
        resolver = AuthorizationResolver(cast(AuthorizationConnection, conn), scope)
        version = _version(repo, cast(UUID, instance["workflow_version_id"]))
        nodes = {n.id: n for n in version.graph.nodes}
        tasks = {str(t["node_id"]): t for t in _tasks(repo, instance_id)}
        route = []
        for entry in cast(list[dict[str, object]], instance["route"]):
            if "node_id" not in entry:
                continue
            node = nodes[str(entry["node_id"])]
            task = tasks.get(node.id)
            if task is not None:
                # An admin reassignment replaces the rule for this task only (E10-S08).
                node = node.model_copy(
                    update={"approvers": _RULE.validate_python(task["approver_rule"])}
                )
            route.append(
                _step(
                    repo,
                    instance,
                    node,
                    snapshot,
                    resolver,
                    RouteStepState(str(entry["state"])),
                    cast(datetime | None, task["due_at"]) if task else None,
                )
            )
        read_tasks = []
        for step in route:
            if step.node_id in tasks:
                t = tasks[step.node_id]
                read_tasks.append(
                    TaskRead.model_validate(
                        {
                            "id": t["id"],
                            "node_id": step.node_id,
                            "label": step.label,
                            "mode": step.mode,
                            "quorum": step.quorum,
                            "status": t["status"],
                            "outcome": t["outcome"],
                            "approver_emails": step.approver_emails,
                            "due_at": t["due_at"],
                            "completed_at": t["completed_at"],
                        }
                    )
                )
        return InstanceRead.model_validate(
            {
                k: v
                for k, v in instance.items()
                if k
                not in {
                    "requester_id",
                    "route",
                    "material_digest",
                    "approved_material_digest",
                    "approved_version",
                }
            }
            | {
                "workflow_version": version.version,
                "requester_email": _emails(repo, [cast(UUID, instance["requester_id"])])[
                    cast(UUID, instance["requester_id"])
                ],
                "route": route,
                "tasks": read_tasks,
            }
        )


def instances_for(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    document_id: UUID,
) -> list[InstanceRead]:
    with tenant_transaction(cast(RlsSession, conn), scope):
        rows = (
            WorkflowRepository(conn, scope)
            .execute(
                "SELECT id FROM approval_instance WHERE org_id=%(org_id)s "
                "AND document_type=%(type)s AND document_id=%(doc)s ORDER BY started_at,id",
                {"type": document_type, "doc": document_id},
            )
            .fetchall()
        )
        return [get_instance(conn, scope, cast(UUID, r[0])) for r in rows]


def active_instance(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    document_id: UUID,
) -> InstanceRead | None:
    return next(
        (
            i
            for i in instances_for(conn, scope, document_type, document_id)
            if i.status == "running"
        ),
        None,
    )
