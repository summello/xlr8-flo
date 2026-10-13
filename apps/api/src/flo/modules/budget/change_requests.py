"""Validated budget requests; posting remains in the decision transaction."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.db.repo import ScopedRepo
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.service import resolve_version, start
from flo.modules.budget.schemas import (
    AdjustmentCreate,
    AllocationResult,
    BudgetChangeRequestRead,
    TransferCreate,
    TransferRead,
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
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, id: UUID
) -> BudgetChangeRequestRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = (
            ChangeRepository(conn, scope)
            .execute(
                f"SELECT {','.join(FIELDS)} FROM budget_change_request "
                "WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": id},
            )
            .fetchone()
        )
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return BudgetChangeRequestRead.model_validate(dict(zip(FIELDS, row, strict=True)))


def request_change(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
    body: AdjustmentCreate | TransferCreate,
    actor_id: UUID,
    idempotency_key: str,
) -> AllocationResult | TransferRead | BudgetChangeRequestRead:
    # Local import keeps the public module boundary free of a service import cycle.
    from flo.modules.budget.service import adjust, transfer

    with tenant_transaction(cast(RlsSession, conn), scope):
        project = ProjectService(conn, scope).get(project_id)
        if resolve_version(conn, scope, "budget_change", project.bu_id) is None:
            return (
                transfer(conn, scope, body, actor_id, idempotency_key)
                if isinstance(body, TransferCreate)
                else adjust(conn, scope, project_id, body, actor_id, idempotency_key)
            )
        repo = ChangeRepository(conn, scope)
        kind = "transfer" if isinstance(body, TransferCreate) else "adjustment"
        payload = body.model_dump(mode="json")
        prior = repo.execute(
            "SELECT id,payload,project_id,requested_by FROM budget_change_request "
            "WHERE org_id=%(org_id)s AND idempotency_key=%(key)s",
            {"key": idempotency_key},
        ).fetchone()
        if prior:
            if prior[1] != payload or prior[2] != project_id or prior[3] != actor_id:
                raise ProblemError(
                    ErrorCode.IDEMPOTENCY_KEY_REUSED,
                    detail="Use a new key for changed budget content.",
                )
            return get_request(conn, scope, cast(UUID, prior[0]))
        # Run the exact immediate-posting checks, including period, evidence, hierarchy,
        # funding policy and availability. All tentative writes roll back to this savepoint.
        with conn.transaction(force_rollback=True):
            if isinstance(body, TransferCreate):
                transfer(conn, scope, body, actor_id, idempotency_key)
            else:
                adjust(conn, scope, project_id, body, actor_id, idempotency_key)
        id = uuid4()
        repo.execute(
            "INSERT INTO budget_change_request(id,org_id,kind,project_id,payload,amount,currency,"
            "requested_by,idempotency_key,status) VALUES(%(id)s,%(org_id)s,%(kind)s,%(project)s,"
            "%(payload)s,%(amount)s,%(currency)s,%(actor)s,%(key)s,'pending')",
            {
                "id": id,
                "kind": kind,
                "project": project_id,
                "payload": Jsonb(payload),
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
