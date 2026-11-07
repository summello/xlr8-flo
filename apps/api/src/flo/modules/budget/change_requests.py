"""Validated budget requests; posting remains in the decision transaction."""

from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.errors import ErrorCode, ProblemError, conflict
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.service import require_workflow, start
from flo.modules.budget import funding_policy
from flo.modules.budget.schemas import (
    AdjustmentCreate,
    AllocationCreate,
    BudgetChangeRequestRead,
    TransferCreate,
)
from flo.modules.identity.service import AuthorizationConnection, AuthorizationResolver
from flo.modules.projects.service import ProjectService

FIELDS = (
    "id",
    "kind",
    "project_id",
    "payload",
    "amount",
    "currency",
    "requested_by",
    "idempotency_key",
    "status",
    "created_at",
    "decided_at",
    "result",
)


class ChangeRepository(ScopedRepo[object]):
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(connection, scope)
        self.connection = connection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> psycopg.Cursor[tuple[object, ...]]:
        return self.connection.execute(query, self.scoped_params(params))


def get_request(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    id: UUID,
    *,
    include_deleted: bool = False,
) -> BudgetChangeRequestRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = (
            ChangeRepository(conn, scope)
            .execute(
                f"SELECT {','.join(FIELDS)} FROM budget_change_request "
                "WHERE org_id=%(org_id)s AND id=%(id)s"
                + ("" if include_deleted else " AND status<>'deleted'"),
                {"id": id},
            )
            .fetchone()
        )
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return BudgetChangeRequestRead.model_validate(dict(zip(FIELDS, row, strict=True)))


def delete_request(conn: psycopg.Connection[tuple[object, ...]], scope: Scope, id: UUID) -> None:
    """E22-S02: a budget change request has no draft status, so it is never deletable.

    The route exists so the answer is a clear 409; it changes and audits nothing. Soft delete
    needs a draft status first (operator decision).
    """
    get_request(conn, scope, id)  # 404 for an unknown or foreign request
    raise conflict("not_draft", "A budget change request cannot be deleted. Withdraw it instead.")


PLANNED = "planned_parent_id"


def planned_parent(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, project_id: UUID, amount: Decimal
) -> str | None:
    """The parent an allocation would draw from under the funding mode in force now."""
    plan = funding_policy.plan_allocation(
        ProjectService(conn, scope).get(project_id),
        amount,
        funding_policy.mode_for(conn, scope, ProjectService(conn, scope).get(project_id)),
    )
    return str(plan.parent_id) if isinstance(plan, funding_policy.FromParent) else None


def request_by_key(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, key: str
) -> BudgetChangeRequestRead | None:
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = (
            ChangeRepository(conn, scope)
            .execute(
                "SELECT id FROM budget_change_request WHERE org_id=%(org_id)s "
                "AND idempotency_key=%(key)s",
                {"key": key},
            )
            .fetchone()
        )
        return get_request(conn, scope, cast(UUID, row[0])) if row else None


def request_change(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
    body: AdjustmentCreate | AllocationCreate | TransferCreate,
    actor_id: UUID,
    idempotency_key: str,
    *,
    permitted_at: Callable[[UUID], bool] | None = None,
) -> BudgetChangeRequestRead:
    """D-M2-21: every governed budget change is a pending request; none posts at once."""
    # Local import keeps the public module boundary free of a service import cycle.
    from flo.modules.budget.service import adjust, allocate, transfer

    with tenant_transaction(cast(RlsSession, conn), scope):
        project = ProjectService(conn, scope).get(project_id)
        require_workflow(conn, scope, "budget_change", project.bu_id, "Budget Changes")
        repo = ChangeRepository(conn, scope)
        # Serialize against a funding-mode change: it takes this lock, then sees our request.
        repo.execute("SELECT id FROM organization WHERE id=%(org_id)s FOR UPDATE")
        kind = (
            "transfer"
            if isinstance(body, TransferCreate)
            else "allocation"
            if isinstance(body, AllocationCreate)
            else "adjustment"
        )
        payload = body.model_dump(mode="json")
        prior = repo.execute(
            "SELECT id,payload,project_id,requested_by,kind FROM budget_change_request "
            "WHERE org_id=%(org_id)s AND idempotency_key=%(key)s",
            {"key": idempotency_key},
        ).fetchone()
        if prior:
            recorded = {k: v for k, v in cast(dict[str, object], prior[1]).items() if k != PLANNED}
            if (
                recorded != payload
                or prior[2] != project_id
                or prior[3] != actor_id
                or prior[4] != kind
            ):
                raise ProblemError(
                    ErrorCode.IDEMPOTENCY_KEY_REUSED,
                    detail="Use a new key for changed budget content.",
                )
            return get_request(conn, scope, cast(UUID, prior[0]), include_deleted=True)
        # Run the exact immediate-posting checks, including period, evidence, hierarchy,
        # funding policy and availability. All tentative writes roll back to this savepoint.
        with conn.transaction(force_rollback=True):
            if isinstance(body, TransferCreate):
                transfer(conn, scope, body, actor_id, idempotency_key)
            elif isinstance(body, AllocationCreate):
                allocate(
                    conn,
                    scope,
                    project_id,
                    body,
                    actor_id,
                    idempotency_key,
                    permitted_at=permitted_at,
                )
            else:
                adjust(conn, scope, project_id, body, actor_id, idempotency_key)
        stored = payload
        if kind == "allocation":
            # The plan the requester was authorized against; approval must still plan the same.
            stored = payload | {PLANNED: planned_parent(conn, scope, project_id, body.amount)}
        id = uuid4()
        repo.execute(
            "INSERT INTO budget_change_request(id,org_id,kind,project_id,payload,amount,currency,"
            "requested_by,idempotency_key,status) VALUES(%(id)s,%(org_id)s,%(kind)s,%(project)s,"
            "%(payload)s,%(amount)s,%(currency)s,%(actor)s,%(key)s,'pending')",
            {
                "id": id,
                "kind": kind,
                "project": project_id,
                "payload": Jsonb(stored),
                "amount": abs(body.amount),
                "currency": body.currency,
                "actor": actor_id,
                "key": idempotency_key,
            },
        )
        start(
            conn,
            scope,
            document_type="budget_change",
            document_id=id,
            actor_id=actor_id,
            resolver=AuthorizationResolver(cast(AuthorizationConnection, conn), scope),
        )
        return get_request(conn, scope, id)
