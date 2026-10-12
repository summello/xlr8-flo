"""Delegation, out-of-office cover and admin reassignment over live membership (APR-017)."""

import base64
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter
from flo.kernel.audit import Outcome as AuditOutcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import PermissionHoldersResolver
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals import conditions, engine
from flo.modules.approvals.conditions import approve_permission
from flo.modules.approvals.eligibility import PickerPage, PickerRow, eligible_approvers
from flo.modules.approvals.models import WorkflowRepository
from flo.modules.approvals.schemas import (
    Approvers,
    DelegationCreate,
    DelegationPage,
    DelegationRead,
    DocumentSnapshot,
    Node,
    PermissionApprovers,
    ReassignWrite,
    RoleApprovers,
    UserApprovers,
    decision_invalid,
)
from flo.modules.identity.service import (
    AuthorizationTarget,
    IdentityAuthorizationConnection,
    emails_by_id,
)

Connection = psycopg.Connection[tuple[object, ...]]
_RULE: TypeAdapter[RoleApprovers | UserApprovers | PermissionApprovers] = TypeAdapter(Approvers)
DELEGATION_FIELDS = (
    "id",
    "delegator_id",
    "delegate_id",
    "starts_at",
    "ends_at",
    "document_types",
    "reason",
    "created_at",
    "revoked_at",
    "revoked_by",
)


def _conflict(problem: str, detail: str) -> ProblemError:
    return ProblemError(ErrorCode.CONFLICT, detail=detail, checks={"problem": problem})


@dataclass
class Members:
    """`base` decide as themselves; `delegates` maps an acting delegate to the delegator."""

    base: list[UUID]
    delegates: dict[UUID, UUID] = field(default_factory=dict)


def effective_members(
    conn: Connection,
    scope: Scope,
    instance: Mapping[str, object],
    task: Mapping[str, object],
    node: Node,
    snapshot: object,
    resolver: PermissionHoldersResolver,
    *,
    now: datetime | None = None,
) -> Members:
    """The task's current deciders: its rule (as reassigned), escalation, and active cover."""
    snap = cast(DocumentSnapshot, snapshot)
    rule = _RULE.validate_python(task["approver_rule"])
    task_node = node.model_copy(update={"approvers": rule, "allow_repeat": task["allow_repeat"]})
    members = engine.current_members(conn, scope, instance, task_node, snap, resolver).members
    base = [m.user_id for m in members]
    escalation = cast(Mapping[str, object] | None, task.get("escalation_rule"))
    if escalation and task.get("escalated_at") is not None:
        extra = engine.current_members(
            conn,
            scope,
            instance,
            task_node.model_copy(
                update={"approvers": _RULE.validate_python(escalation["approvers"])}
            ),
            snap,
            resolver,
        ).members
        base += [m.user_id for m in extra if m.user_id not in base]
    delegates = _active_delegates(
        conn, scope, instance, task, snap, resolver, base, now or datetime.now(UTC)
    )
    return Members(base=base, delegates=delegates)


def _active_delegates(
    conn: Connection,
    scope: Scope,
    instance: Mapping[str, object],
    task: Mapping[str, object],
    snapshot: object,
    resolver: PermissionHoldersResolver,
    base: Sequence[UUID],
    now: datetime,
) -> dict[UUID, UUID]:
    if not base:
        return {}
    snap = cast(DocumentSnapshot, snapshot)
    repo = WorkflowRepository(conn, scope)
    rows = repo.execute(
        "SELECT delegator_id,delegate_id FROM approval_delegation WHERE org_id=%(org_id)s "
        "AND revoked_at IS NULL AND starts_at <= %(now)s AND ends_at > %(now)s "
        "AND delegator_id = ANY(%(base)s) AND delegate_id <> ALL(%(base)s) "
        "AND (document_types IS NULL OR %(type)s = ANY(document_types)) "
        "ORDER BY created_at,id",
        {"now": now, "base": list(base), "type": instance["document_type"]},
    ).fetchall()
    if not rows:
        return {}
    # The delegate's own access is re-checked now, with separation of duty applied to them.
    able = {
        m.user_id
        for m in eligible_approvers(
            conn,
            scope,
            str(instance["document_type"]),
            PermissionApprovers(type="permission"),
            cast(AuthorizationTarget, snap.target),
            cast(UUID, instance["requester_id"]),
            resolver,
        ).members
    }
    earlier: set[UUID] = set()
    if not task.get("allow_repeat"):
        earlier = {
            UUID(str(user))
            for entry in cast(list[dict[str, object]], instance["route"])
            if entry.get("node_id") != task["node_id"]
            for user in cast(list[str], entry.get("decided_by", []))
        }
    result: dict[UUID, UUID] = {}
    for delegator, delegate in rows:
        delegate_id = cast(UUID, delegate)
        if delegate_id in able and delegate_id not in earlier and delegate_id not in result:
            result[delegate_id] = cast(UUID, delegator)
    return result


def _read(
    conn: Connection, rows: Sequence[tuple[object, ...]], now: datetime
) -> list[DelegationRead]:
    ids = [v for row in rows for v in (row[1], row[2], row[9]) if isinstance(v, UUID)]
    emails = emails_by_id(cast(IdentityAuthorizationConnection, conn), ids)
    out = []
    for row in rows:
        data = dict(zip(DELEGATION_FIELDS, row, strict=True))
        revoked_at = cast(datetime | None, data["revoked_at"])
        if revoked_at is not None:
            state: Literal["scheduled", "active", "ended", "revoked"] = "revoked"
        elif cast(datetime, data["ends_at"]) <= now:
            state = "ended"
        elif cast(datetime, data["starts_at"]) > now:
            state = "scheduled"
        else:
            state = "active"
        out.append(
            DelegationRead(
                id=cast(UUID, data["id"]),
                delegator_email=emails.get(cast(UUID, data["delegator_id"]), "(removed user)"),
                delegate_email=emails.get(cast(UUID, data["delegate_id"]), "(removed user)"),
                starts_at=cast(datetime, data["starts_at"]),
                ends_at=cast(datetime, data["ends_at"]),
                document_types=cast(list[str] | None, data["document_types"]),
                reason=str(data["reason"]),
                created_at=cast(datetime, data["created_at"]),
                revoked_at=revoked_at,
                state=state,
            )
        )
    return out


SELECT = "SELECT " + ",".join(DELEGATION_FIELDS) + " FROM approval_delegation "


def _types(body_types: Sequence[str] | None) -> list[str]:
    registered = sorted(conditions._adapters)
    wanted = list(body_types) if body_types is not None else registered
    unknown = [t for t in wanted if t not in registered]
    if unknown or not wanted:
        raise decision_invalid(
            "document_types", "Choose document types that approvals are available for."
        )
    return wanted


def _delegate_holds_everything(
    conn: Connection,
    scope: Scope,
    resolver: PermissionHoldersResolver,
    delegate_id: UUID,
    types: Sequence[str],
) -> bool:
    target = AuthorizationTarget.organization(scope.org_id)
    return all(
        delegate_id in set(resolver.holders(approve_permission(t), cast(TargetProtocol, target)))
        for t in types
    )


def eligible_delegates(
    conn: Connection,
    scope: Scope,
    actor_id: UUID,
    document_types: list[str] | None,
    resolver: PermissionHoldersResolver,
) -> PickerPage:
    types = _types(document_types)
    with tenant_transaction(cast(RlsSession, conn), scope):
        target = cast(TargetProtocol, AuthorizationTarget.organization(scope.org_id))
        holder_sets = [set(resolver.holders(approve_permission(t), target)) for t in types]
        ids = set.intersection(*holder_sets) - {actor_id}
        rows = (
            WorkflowRepository(conn, scope)
            .execute(
                "SELECT i.id,i.email FROM identity i WHERE i.status='active' AND i.id=ANY(%(ids)s) "
                "AND EXISTS (SELECT 1 FROM user_role u WHERE u.org_id=%(org_id)s "
                "AND u.user_id=i.id) ORDER BY i.email,i.id LIMIT 50",
                {"ids": sorted(ids)},
            )
            .fetchall()
        )
        return PickerPage(rows=[PickerRow(user_id=cast(UUID, r[0]), email=str(r[1])) for r in rows])


def _audit(
    repo: WorkflowRepository,
    actor_id: UUID,
    action: str,
    target_id: UUID,
    before: dict[str, object] | None,
    after: dict[str, object],
) -> None:
    AuditWriter(cast(AuditConnection, repo.connection), repo.scope).write(
        actor=AuditActor(ActorKind.USER, actor_id),
        action=action,
        target_type="approval_delegation"
        if action.startswith("approval.delegat")
        else "approval_task",
        target_id=target_id,
        outcome=AuditOutcome.SUCCESS,
        before_source=before,
        before_fields=tuple(before or ()),
        after_source=after,
        after_fields=tuple(after),
    )


def create_delegation(
    conn: Connection,
    scope: Scope,
    *,
    actor_id: UUID,
    body: DelegationCreate,
    resolver: PermissionHoldersResolver,
    now: datetime | None = None,
) -> DelegationRead:
    now = now or datetime.now(UTC)
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        if body.delegate_id == actor_id:
            raise decision_invalid("delegate_id", "Choose someone other than yourself.")
        types = _types(body.document_types)
        member = repo.execute(
            "SELECT 1 FROM identity_membership m JOIN identity i ON i.id=m.identity_id "
            "WHERE m.org_id=%(org_id)s AND m.identity_id=%(id)s AND i.status='active'",
            {"id": body.delegate_id},
        ).fetchone()
        if member is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        if not _delegate_holds_everything(conn, scope, resolver, body.delegate_id, types):
            raise decision_invalid(
                "delegate_id", "Choose someone who may approve every document type you cover."
            )
        covering = repo.execute(
            "SELECT 1 FROM approval_delegation WHERE org_id=%(org_id)s AND delegate_id=%(me)s "
            "AND revoked_at IS NULL AND ends_at > %(start)s AND starts_at < %(end)s LIMIT 1",
            {"me": actor_id, "start": body.starts_at, "end": body.ends_at},
        ).fetchone()
        if covering is not None:
            raise _conflict(
                "no_redelegation",
                "You are covering for someone in this period, so you cannot pass that cover on.",
            )
        id = uuid4()
        try:
            with conn.transaction():
                repo.execute(
                    "INSERT INTO approval_delegation(id,org_id,delegator_id,delegate_id,starts_at,"
                    "ends_at,document_types,reason) VALUES (%(id)s,%(org_id)s,%(me)s,%(delegate)s,"
                    "%(start)s,%(end)s,%(types)s,%(reason)s)",
                    {
                        "id": id,
                        "me": actor_id,
                        "delegate": body.delegate_id,
                        "start": body.starts_at,
                        "end": body.ends_at,
                        "types": body.document_types,
                        "reason": body.reason,
                    },
                )
        except psycopg.errors.ExclusionViolation as exc:
            raise _conflict(
                "delegation_overlaps",
                "You already have a delegation in this period. Revoke it or choose other dates.",
            ) from exc
        _audit(
            repo,
            actor_id,
            "approval.delegation.create",
            id,
            None,
            {"delegate_id": body.delegate_id, "starts_at": body.starts_at, "ends_at": body.ends_at},
        )
        row = repo.execute(SELECT + "WHERE org_id=%(org_id)s AND id=%(id)s", {"id": id}).fetchone()
        assert row is not None
        return _read(conn, [row], now)[0]


def revoke_delegation(
    conn: Connection,
    scope: Scope,
    *,
    actor_id: UUID,
    delegation_id: UUID,
    is_admin: Callable[[], bool],
    now: datetime | None = None,
) -> DelegationRead:
    """Authority-reducing: the owner can always revoke, else `approval.admin` (convention P)."""
    now = now or datetime.now(UTC)
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        row = repo.execute(
            SELECT + "WHERE org_id=%(org_id)s AND id=%(id)s FOR UPDATE", {"id": delegation_id}
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        if row[1] != actor_id and not is_admin():
            raise ProblemError(
                ErrorCode.FORBIDDEN, detail="Only the person who delegated can revoke this."
            )
        if row[8] is None:
            repo.execute(
                "UPDATE approval_delegation SET revoked_at=%(now)s,revoked_by=%(me)s "
                "WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": delegation_id, "now": now, "me": actor_id},
            )
            _audit(
                repo,
                actor_id,
                "approval.delegation.revoke",
                delegation_id,
                {"revoked_at": None},
                {"revoked_at": now},
            )
            row = repo.execute(
                SELECT + "WHERE org_id=%(org_id)s AND id=%(id)s", {"id": delegation_id}
            ).fetchone()
        assert row is not None
        return _read(conn, [row], now)[0]


def list_delegations(
    conn: Connection,
    scope: Scope,
    *,
    actor_id: UUID,
    view: Literal["mine", "covering", "organization"],
    cursor: str | None = None,
    now: datetime | None = None,
) -> DelegationPage:
    now = now or datetime.now(UTC)
    key: datetime | None = None
    cursor_id: UUID | None = None
    if cursor is not None:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
            key = datetime.fromisoformat(decoded["created_at"])
            cursor_id = UUID(decoded["id"])
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise decision_invalid("cursor", "Invalid cursor. Restart paging.") from exc
    column = {"mine": "delegator_id", "covering": "delegate_id", "organization": None}[view]
    with tenant_transaction(cast(RlsSession, conn), scope):
        rows = (
            WorkflowRepository(conn, scope)
            .execute(
                SELECT
                + "WHERE org_id=%(org_id)s "
                + (f"AND {column}=%(me)s " if column else "")
                + "AND (%(key)s::timestamptz IS NULL OR (created_at,id) < (%(key)s,%(cid)s::uuid)) "
                "ORDER BY created_at DESC,id DESC LIMIT 51",
                {"me": actor_id, "key": key, "cid": cursor_id},
            )
            .fetchall()
        )
        page = _read(conn, rows[:50], now)
        next_cursor = None
        if len(rows) > 50:
            last = rows[49]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    {"created_at": cast(datetime, last[7]).isoformat(), "id": str(last[0])}
                ).encode()
            ).decode()
        return DelegationPage(rows=page, next_cursor=next_cursor)


def reassign_task(
    conn: Connection,
    scope: Scope,
    *,
    actor_id: UUID,
    task_id: UUID,
    body: ReassignWrite,
    resolver: PermissionHoldersResolver,
) -> UUID:
    """Replace an open task's approver rule with named, eligible users; returns the instance id."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        instance_id_row = repo.execute(
            "SELECT instance_id FROM approval_task WHERE org_id=%(org_id)s AND id=%(id)s",
            {"id": task_id},
        ).fetchone()
        if instance_id_row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        instance_id = cast(UUID, instance_id_row[0])
        instance = engine._instance(repo, instance_id, lock=True)
        task = next(t for t in engine._tasks(repo, instance_id) if t["id"] == task_id)
        if instance["status"] != "running" or task["status"] != "open":
            raise engine.conflict(
                "task_closed", "This approval task is closed. Reload the approval route."
            )
        adapter = engine.adapter_for(str(instance["document_type"]))
        snapshot = adapter.snapshot(conn, scope, cast(UUID, instance["document_id"]))
        eligible = {
            m.user_id
            for m in eligible_approvers(
                conn,
                scope,
                str(instance["document_type"]),
                UserApprovers(type="users", user_ids=body.user_ids),
                cast(AuthorizationTarget, snapshot.target),
                cast(UUID, instance["requester_id"]),
                resolver,
            ).members
        }
        missing = [u for u in body.user_ids if u not in eligible]
        if missing:
            raise decision_invalid(
                "user_ids", "Choose people who may approve this document and are not its requester."
            )
        before = cast(dict[str, object], task["approver_rule"])
        after = UserApprovers(type="users", user_ids=body.user_ids).model_dump(mode="json")
        repo.execute(
            "UPDATE approval_task SET approver_rule=%(rule)s WHERE org_id=%(org_id)s AND id=%(id)s",
            {"id": task_id, "rule": Jsonb(after)},
        )
        _audit(
            repo,
            actor_id,
            "approval.task.reassign",
            task_id,
            {"approver_rule": before},
            {"approver_rule": after, "reason": body.reason},
        )
        return instance_id
