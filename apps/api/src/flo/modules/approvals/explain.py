"""Recorded approval explanations; only the open step uses live document attributes.

M2 exposes only the requisition amount to conditions, and every authorized document
reader may already read it. Future condition fields must pass through the document's
same read authorization before their values may appear here.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

import psycopg
from pydantic import TypeAdapter

from flo.kernel.authz import PermissionHoldersResolver
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals import delegation, engine
from flo.modules.approvals.eligibility import EligibleSet, Excluded
from flo.modules.approvals.models import WorkflowRepository
from flo.modules.approvals.schemas import (
    Approvers,
    Contract,
    Node,
    RoleApprovers,
    UserApprovers,
)
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    IdentityAuthorizationConnection,
    emails_by_id,
    names_by_id,
)

Connection = psycopg.Connection[tuple[object, ...]]
_RULE: TypeAdapter[Approvers] = TypeAdapter(Approvers)


class ExplanationWorkflow(Contract):
    definition_id: UUID
    name: str
    version: int
    bound_scope: str


class ExplanationLeaf(Contract):
    field: str
    op: str
    expected: str
    actual: str
    result: bool


class ExplanationEvaluation(Contract):
    edge_to: str
    result: bool
    leaves: list[ExplanationLeaf]


class ConditionExplanation(Contract):
    node_id: str
    kind: Literal["condition"] = "condition"
    name: str
    evaluated: list[ExplanationEvaluation]
    taken: str


class ExplanationMember(Contract):
    user_id: UUID | None
    email: str
    name: str | None
    basis: str


class ExplanationExclusion(Contract):
    user_id: UUID
    email: str
    name: str | None
    reason: str
    detail: str


class ExplanationDecision(Contract):
    actor: str
    actor_name: str | None
    on_behalf_of: str | None
    on_behalf_of_name: str | None
    outcome: str
    decided_at: datetime


class ApprovalExplanation(Contract):
    node_id: str
    kind: Literal["approval"] = "approval"
    name: str
    mode: Literal["any", "all", "quorum"]
    rule: str
    status: Literal["open", "completed", "carried"]
    members_as_of: Literal["as_of_now", "at_decision"]
    members: list[ExplanationMember]
    excluded: list[ExplanationExclusion]
    decisions: list[ExplanationDecision]


class Explanation(Contract):
    instance_id: UUID
    workflow: ExplanationWorkflow
    why_this_workflow: str
    steps: list[ConditionExplanation | ApprovalExplanation]


def explanation_document(conn: Connection, scope: Scope, instance_id: UUID) -> tuple[str, UUID]:
    """Return the tenant-scoped document reference without rendering live route membership."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        instance = engine._instance(WorkflowRepository(conn, scope), instance_id)
        return str(instance["document_type"]), cast(UUID, instance["document_id"])


def _rule_words(rule: Approvers, permission: str) -> str:
    if isinstance(rule, RoleApprovers):
        return f"role {rule.role_code}"
    if isinstance(rule, UserApprovers):
        return "listed by name"
    return f"permission {permission}"


def _basis(rule: Approvers, permission: str, scope_name: str) -> str:
    if isinstance(rule, RoleApprovers):
        return f"role {rule.role_code} at {scope_name}; holds {permission} there"
    if isinstance(rule, UserApprovers):
        return f"listed by name; holds {permission} at {scope_name}"
    return f"holds {permission} at {scope_name}"


def _identity_ids(repo: WorkflowRepository, emails: Sequence[str]) -> dict[str, UUID]:
    if not emails:
        return {}
    return {
        str(row[1]): cast(UUID, row[0])
        for row in repo.connection.execute(
            "SELECT id,email FROM identity WHERE email=ANY(%s)", (list(emails),)
        ).fetchall()
    }


def _decisions(repo: WorkflowRepository, instance_ids: Sequence[UUID]) -> list[dict[str, object]]:
    if not instance_ids:
        return []
    fields = (
        "id",
        "instance_id",
        "task_id",
        "actor_id",
        "on_behalf_of",
        "authority",
        "outcome",
        "members_at_decision",
        "decided_at",
    )
    rows = repo.execute(
        "SELECT " + ",".join(fields) + " FROM approval_decision "
        "WHERE org_id=%(org_id)s AND instance_id=ANY(%(ids)s) ORDER BY decided_at,id",
        {"ids": list(instance_ids)},
    ).fetchall()
    emails = emails_by_id(
        cast(IdentityAuthorizationConnection, repo.connection),
        [cast(UUID, value) for row in rows for value in (row[3], row[4]) if value is not None],
    )
    names = names_by_id(cast(IdentityAuthorizationConnection, repo.connection), list(emails))
    result: list[dict[str, object]] = []
    for row in rows:
        item = dict(zip(fields, row, strict=True))
        item["actor_email"] = emails.get(cast(UUID, item["actor_id"]), "(removed user)")
        item["actor_name"] = names.get(cast(UUID, item["actor_id"]))
        owner = cast(UUID | None, item["on_behalf_of"])
        item["on_behalf_of_email"] = emails.get(owner, "(removed user)") if owner else None
        item["on_behalf_of_name"] = names.get(owner) if owner else None
        result.append(item)
    return result


def _delegation_basis(
    repo: WorkflowRepository,
    actor_id: UUID,
    owner_id: UUID,
    at: datetime,
    owner_email: str,
) -> str | None:
    row = repo.execute(
        "SELECT ends_at FROM approval_delegation WHERE org_id=%(org_id)s "
        "AND delegator_id=%(owner)s AND delegate_id=%(actor)s AND starts_at<=%(at)s "
        "AND ends_at>%(at)s AND created_at<=%(at)s ORDER BY created_at DESC,id DESC LIMIT 1",
        {"owner": owner_id, "actor": actor_id, "at": at},
    ).fetchone()
    if row is None:
        return None
    return f"delegate for {owner_email} until {cast(datetime, row[0]).date().isoformat()}"


def _exclusions(
    repo: WorkflowRepository,
    rows: Sequence[Excluded],
    permission: str,
    scope_name: str,
) -> list[ExplanationExclusion]:
    emails = emails_by_id(
        cast(IdentityAuthorizationConnection, repo.connection), [row.user_id for row in rows]
    )
    names = names_by_id(cast(IdentityAuthorizationConnection, repo.connection), list(emails))
    details = {
        "separation_of_duty": "Requested this document",
        "already_decided": "Already decided an earlier step",
        "not_eligible_in_scope": f"No {permission} in {scope_name}",
    }
    unique = {(row.user_id, row.reason): row for row in rows}
    return [
        ExplanationExclusion(
            user_id=row.user_id,
            email=emails.get(row.user_id, "(removed user)"),
            name=names.get(row.user_id),
            reason=row.reason,
            detail=details.get(row.reason, row.reason.replace("_", " ").capitalize()),
        )
        for row in sorted(
            unique.values(), key=lambda item: (emails.get(item.user_id, ""), item.reason)
        )
    ]


def _live_members(
    repo: WorkflowRepository,
    instance: Mapping[str, object],
    task: Mapping[str, object],
    node: Node,
    scope_name: str,
    resolver: PermissionHoldersResolver,
) -> tuple[list[ExplanationMember], list[ExplanationExclusion]]:
    adapter = engine.adapter_for(str(instance["document_type"]))
    snapshot = adapter.snapshot(repo.connection, repo.scope, cast(UUID, instance["document_id"]))
    rule = _RULE.validate_python(task["approver_rule"])
    task_node = node.model_copy(update={"approvers": rule, "allow_repeat": task["allow_repeat"]})
    base = engine.current_members(
        repo.connection, repo.scope, instance, task_node, snapshot, resolver
    )
    escalated = EligibleSet(members=[], excluded=[])
    escalation = cast(Mapping[str, object] | None, task.get("escalation_rule"))
    if escalation and task.get("escalated_at") is not None:
        escalated = engine.current_members(
            repo.connection,
            repo.scope,
            instance,
            task_node.model_copy(
                update={"approvers": _RULE.validate_python(escalation["approvers"])}
            ),
            snapshot,
            resolver,
        )
    effective = delegation.effective_members(
        repo.connection, repo.scope, instance, task, node, snapshot, resolver
    )
    ids = [*effective.base, *effective.delegates]
    emails = emails_by_id(cast(IdentityAuthorizationConnection, repo.connection), ids)
    names = names_by_id(cast(IdentityAuthorizationConnection, repo.connection), ids)
    escalation_ids = {member.user_id for member in escalated.members}
    base_ids = {member.user_id for member in base.members}
    members = []
    for user_id in effective.base:
        basis = (
            f"added by escalation on {cast(datetime, task['escalated_at']).date().isoformat()}"
            if user_id in escalation_ids and user_id not in base_ids
            else _basis(rule, adapter.approve_permission, scope_name)
        )
        members.append(
            ExplanationMember(
                user_id=user_id,
                email=emails.get(user_id, "(removed user)"),
                name=names.get(user_id),
                basis=basis,
            )
        )
    now = datetime.now(UTC)
    for delegate_id, owner_id in effective.delegates.items():
        owner_email = emails_by_id(
            cast(IdentityAuthorizationConnection, repo.connection), [owner_id]
        ).get(owner_id, "(removed user)")
        delegate_basis = _delegation_basis(repo, delegate_id, owner_id, now, owner_email)
        members.append(
            ExplanationMember(
                user_id=delegate_id,
                email=emails.get(delegate_id, "(removed user)"),
                name=names.get(delegate_id),
                basis=delegate_basis or f"delegate for {owner_email}",
            )
        )
    return (
        sorted(members, key=lambda item: (item.email, str(item.user_id))),
        _exclusions(
            repo, [*base.excluded, *escalated.excluded], adapter.approve_permission, scope_name
        ),
    )


def _historical_members(
    repo: WorkflowRepository,
    task: Mapping[str, object],
    decisions: Sequence[Mapping[str, object]],
    permission: str,
    scope_name: str,
) -> list[ExplanationMember]:
    evidence = [row for row in decisions if row["outcome"] != "comment"]
    if not evidence:
        return []
    snapshot = cast(list[str], evidence[-1]["members_at_decision"])
    ids = _identity_ids(repo, snapshot)
    rule = _RULE.validate_python(task["approver_rule"])
    escalation = cast(Mapping[str, object] | None, task.get("escalation_rule"))
    escalated_ids: set[UUID] = set()
    if escalation and task.get("escalated_at") is not None:
        escalation_rule = _RULE.validate_python(escalation["approvers"])
        if isinstance(escalation_rule, UserApprovers):
            escalated_ids = set(escalation_rule.user_ids)
    delegate_basis: dict[str, str] = {}
    for row in evidence:
        owner = cast(UUID | None, row["on_behalf_of"])
        if owner is None:
            continue
        owner_email = cast(str | None, row["on_behalf_of_email"]) or "(removed user)"
        basis = _delegation_basis(
            repo,
            cast(UUID, row["actor_id"]),
            owner,
            cast(datetime, row["decided_at"]),
            owner_email,
        )
        if basis:
            delegate_basis[cast(str, row["actor_email"])] = basis
    names = names_by_id(cast(IdentityAuthorizationConnection, repo.connection), list(ids.values()))
    members = []
    for email in snapshot:
        user_id = ids.get(email)
        if email in delegate_basis:
            basis = delegate_basis[email]
        elif user_id in escalated_ids:
            basis = (
                f"added by escalation on {cast(datetime, task['escalated_at']).date().isoformat()}"
            )
        else:
            basis = _basis(rule, permission, scope_name)
        members.append(
            ExplanationMember(
                user_id=user_id,
                email=email,
                name=names.get(user_id) if user_id else None,
                basis=basis,
            )
        )
    return members


def explain(
    conn: Connection,
    scope: Scope,
    instance_id: UUID,
) -> Explanation:
    """Describe only recorded history plus live membership for the currently open step."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        instance = engine._instance(repo, instance_id)
        resolution = engine.resolve_version_reason(
            conn,
            scope,
            str(instance["document_type"]),
            None,
            pinned_version_id=cast(UUID, instance["workflow_version_id"]),
        )
        if resolution is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        version = resolution.version
        nodes = {node.id: node for node in version.graph.nodes}
        current_tasks = {str(task["node_id"]): task for task in engine._tasks(repo, instance_id)}
        source_ids = [
            UUID(str(entry["source_instance_id"]))
            for entry in cast(list[dict[str, object]], instance["route"])
            if entry.get("source_instance_id") is not None
        ]
        source_tasks: dict[tuple[UUID, str], dict[str, object]] = {}
        for source_id in source_ids:
            for task in engine._tasks(repo, source_id):
                source_tasks[(source_id, str(task["node_id"]))] = task
        all_decisions = _decisions(repo, [instance_id, *source_ids])
        resolver = AuthorizationResolver(cast(AuthorizationConnection, conn), scope)
        permission = engine.adapter_for(str(instance["document_type"])).approve_permission
        steps: list[ConditionExplanation | ApprovalExplanation] = []
        condition_steps: dict[str, ConditionExplanation] = {}
        for entry in cast(list[dict[str, object]], instance["route"]):
            condition_id = entry.get("condition_node_id")
            if condition_id is not None:
                node = nodes[str(condition_id)]
                condition = condition_steps.get(node.id)
                if condition is None:
                    condition = ConditionExplanation(
                        node_id=node.id,
                        name=node.name or node.id,
                        evaluated=[],
                        taken="",
                    )
                    condition_steps[node.id] = condition
                    steps.append(condition)
                evaluation = ExplanationEvaluation(
                    edge_to=str(entry["to"]),
                    result=bool(entry["result"]),
                    leaves=[
                        ExplanationLeaf.model_validate(leaf)
                        for leaf in cast(list[object], entry["trace"])
                    ],
                )
                condition.evaluated.append(evaluation)
                if evaluation.result:
                    condition.taken = evaluation.edge_to
                continue
            if entry.get("node_id") is None:
                continue
            node = nodes[str(entry["node_id"])]
            source_id = (
                UUID(str(entry["source_instance_id"]))
                if entry.get("source_instance_id") is not None
                else instance_id
            )
            maybe_task = (
                source_tasks.get((source_id, node.id))
                if source_id != instance_id
                else current_tasks.get(node.id)
            )
            if maybe_task is None:
                continue
            task = maybe_task
            task_decisions = [row for row in all_decisions if row["task_id"] == task["id"]]
            state = str(entry["state"])
            if state == "open":
                members, excluded = _live_members(
                    repo, instance, task, node, resolution.scope_name, resolver
                )
                members_as_of: Literal["as_of_now", "at_decision"] = "as_of_now"
            else:
                members = _historical_members(
                    repo, task, task_decisions, permission, resolution.scope_name
                )
                excluded = []
                members_as_of = "at_decision"
            task_rule = _RULE.validate_python(task["approver_rule"])
            mode = task["mode"]
            steps.append(
                ApprovalExplanation(
                    node_id=node.id,
                    name=str(task["name"]),
                    mode=(
                        "quorum" if isinstance(mode, Mapping) else cast(Literal["any", "all"], mode)
                    ),
                    rule=_rule_words(task_rule, permission),
                    status=cast(Literal["open", "completed", "carried"], state),
                    members_as_of=members_as_of,
                    members=members,
                    excluded=excluded,
                    decisions=[
                        ExplanationDecision(
                            actor=cast(str, row["actor_email"]),
                            actor_name=cast(str | None, row["actor_name"]),
                            on_behalf_of=cast(str | None, row["on_behalf_of_email"]),
                            on_behalf_of_name=cast(str | None, row["on_behalf_of_name"]),
                            outcome=str(row["outcome"]),
                            decided_at=cast(datetime, row["decided_at"]),
                        )
                        for row in task_decisions
                    ],
                )
            )
        for condition in condition_steps.values():
            if condition.taken:
                continue
            default = next(
                edge
                for edge in version.graph.edges
                if edge.from_ == condition.node_id and edge.default
            )
            condition.taken = default.to
        return Explanation(
            instance_id=instance_id,
            workflow=ExplanationWorkflow(
                definition_id=resolution.definition_id,
                name=resolution.definition_name,
                version=version.version,
                bound_scope=resolution.bound_scope,
            ),
            why_this_workflow=resolution.reason,
            steps=steps,
        )
