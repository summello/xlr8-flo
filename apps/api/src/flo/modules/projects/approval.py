"""Project approval adapter; outcomes use the existing lifecycle writer."""

from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.schemas import (
    DocumentSnapshot,
    DocumentSummary,
    FieldType,
    Outcome,
    OutcomeContext,
)
from flo.modules.approvals.service import decisions_for
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.projects.models import ProjectRepository
from flo.modules.projects.service import ProjectService


class ProjectAdapter:
    document_type = "project"
    approve_permission = "project.approve"
    read_permission = "project.read"
    accepts_project_assignment = False

    def fields(self) -> Mapping[str, FieldType]:
        return {}

    def snapshot(
        self, conn: psycopg.Connection[tuple[object, ...]], scope: Scope, document_id: UUID
    ) -> DocumentSnapshot:
        with tenant_transaction(cast(RlsSession, conn), scope):
            project = ProjectService(conn, scope).get(document_id)
            event = (
                ProjectRepository(conn, scope)
                .execute(
                    "SELECT actor_id FROM project_status_event WHERE org_id=%(org_id)s "
                    "AND project_id=%(id)s AND to_status='approval_pending' "
                    "ORDER BY id DESC LIMIT 1",
                    {"id": document_id},
                )
                .fetchone()
            )
            if event is None or event[0] is None:
                raise ProblemError(
                    ErrorCode.CONFLICT, detail="Submit this project before approval."
                )
            return DocumentSnapshot(
                project.version,
                cast(UUID, event[0]),
                cast(
                    TargetProtocol,
                    AuthorizationTarget(ScopeType.PROJECT, project.id, "project", project.id),
                ),
                {},
                {
                    name: str(getattr(project, name))
                    for name in (
                        "bu_id",
                        "parent_id",
                        "currency",
                        "department_code",
                        "ledger_account_code",
                    )
                },
            )

    def summaries(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_ids: Sequence[UUID],
    ) -> Mapping[UUID, DocumentSummary]:
        result = {}
        for id in document_ids:
            project = ProjectService(conn, scope).get(id)
            snapshot = self.snapshot(conn, scope, id)
            result[id] = DocumentSummary(
                project.number, project.name, None, snapshot.requester_id, f"/projects/{id}", None
            )
        return result

    def on_outcome(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        document_id: UUID,
        outcome: Outcome,
        context: OutcomeContext,
    ) -> None:
        reason = None
        if context.decision_id is not None:
            # The decision is visible on this connection before the callback commits.
            from flo.modules.approvals.service import instances_for

            for instance in instances_for(conn, scope, self.document_type, document_id):
                for decision in decisions_for(conn, scope, instance.id):
                    if decision.id == context.decision_id:
                        reason = decision.comment
        ProjectService(conn, scope, context.actor_id).approval_outcome(
            document_id, "active" if outcome == Outcome.APPROVED else "draft", reason
        )
