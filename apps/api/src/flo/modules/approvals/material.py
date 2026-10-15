"""Approval in force and material-change policy for document callers."""

from collections.abc import Callable
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter
from flo.kernel.audit import Outcome as AuditOutcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals import engine
from flo.modules.approvals.models import WorkflowRepository
from flo.modules.approvals.schemas import ApprovalState


def _in_force(
    repo: WorkflowRepository, document_type: str, document_id: UUID, *, lock: bool = False
) -> dict[str, object] | None:
    rows = repo.execute(
        f"SELECT {','.join(engine.INSTANCE_FIELDS)} FROM approval_instance "
        "WHERE org_id=%(org_id)s AND document_type=%(type)s AND document_id=%(doc)s "
        "ORDER BY started_at DESC,id DESC" + (" FOR UPDATE" if lock else ""),
        {"type": document_type, "doc": document_id},
    ).fetchall()
    instances = [dict(zip(engine.INSTANCE_FIELDS, row, strict=True)) for row in rows]
    return next(
        (i for i in instances if i["status"] in {"running", "approved", "superseded"}),
        instances[0] if instances else None,
    )


def approval_state(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    document_id: UUID,
) -> ApprovalState:
    with tenant_transaction(cast(RlsSession, conn), scope):
        instance = _in_force(WorkflowRepository(conn, scope), document_type, document_id)
        if instance is None:
            return ApprovalState(status="none")
        return ApprovalState.model_validate(
            {
                "status": instance["status"],
                "instance_id": instance["id"],
                "approved_version": instance["approved_version"]
                if instance["status"] == "approved"
                else None,
                "approved_material_digest": instance["approved_material_digest"],
                "running_instance_id": instance["id"] if instance["status"] == "running" else None,
            }
        )


def requires_resubmission(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    document_id: UUID,
) -> bool:
    with tenant_transaction(cast(RlsSession, conn), scope):
        state = approval_state(conn, scope, document_type, document_id)
        return (
            state.status == "approved"
            and state.approved_material_digest
            != engine.material_digest(
                engine.adapter_for(document_type).snapshot(conn, scope, document_id)
            )
        )


def assert_current_approval(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    document_id: UUID,
    document_version: int,
) -> None:
    with tenant_transaction(cast(RlsSession, conn), scope):
        state = approval_state(conn, scope, document_type, document_id)
        snapshot = engine.adapter_for(document_type).snapshot(conn, scope, document_id)
        if (
            state.status != "approved"
            or document_version != snapshot.version
            or state.approved_material_digest != engine.material_digest(snapshot)
        ):
            raise engine.conflict(
                "stale_version",
                "This document needs current approval. Reload it and resubmit material changes.",
            )


def supersede(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    document_id: UUID,
    actor_id: UUID,
    reason: str,
) -> None:
    """Caller authorizes the edit; instance lock precedes any document mutation."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        instance = _in_force(repo, document_type, document_id, lock=True)
        if instance is None or instance["status"] != "approved":
            raise engine.conflict(
                "stale_version",
                "Only a current approved document can be superseded. Reload its approval state.",
            )
        instance["status"] = "superseded"
        engine._save(repo, instance)
        AuditWriter(cast(AuditConnection, conn), scope).write(
            actor=AuditActor(ActorKind.USER, actor_id),
            action="approval.supersede",
            target_type="approval_instance",
            target_id=cast(UUID, instance["id"]),
            outcome=AuditOutcome.SUCCESS,
            before_source={"status": "approved"},
            before_fields=("status",),
            after_source={"status": "superseded", "reason": reason},
            after_fields=("status", "reason"),
        )


def rebase_digest(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    document_id: UUID,
    mutate: Callable[[], None],
) -> None:
    """Run `mutate` (a non-material correction the caller authorized) under the instance lock,
    then re-stamp the approved digest so the approval stays in force. Lock order: instance first."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        instance = _in_force(repo, document_type, document_id, lock=True)
        mutate()
        if instance is not None and instance["status"] == "approved":
            instance["approved_material_digest"] = engine.material_digest(
                engine.adapter_for(document_type).snapshot(conn, scope, document_id)
            )
            engine._save(repo, instance)
