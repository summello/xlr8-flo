"""Transactional workflow versioning; published graphs are immutable in Postgres."""

import base64
import json
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.models import DEFINITION_FIELDS, VERSION_FIELDS, WorkflowRepository
from flo.modules.approvals.schemas import (
    DocumentType,
    WorkflowCreate,
    WorkflowPage,
    WorkflowRead,
    WorkflowVersionRead,
    WorkflowVersionSummary,
    WorkflowVersionWrite,
    invalid,
    parse_graph,
)
from flo.modules.org.service import OrgService

__all__ = ["WorkflowService", "resolve_version"]


def conflict(problem: str) -> ProblemError:
    return ProblemError(
        ErrorCode.CONFLICT,
        detail="The workflow cannot accept this change. Reload its versions and retry.",
        checks={"problem": problem},
    )


class WorkflowService:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        actor_id: UUID | None = None,
    ) -> None:
        self.connection = connection
        self.scope = scope
        self.actor_id = actor_id
        self.repo = WorkflowRepository(connection, scope)

    def _definition(self, id: UUID, *, lock: bool = False) -> dict[str, object]:
        row = self.repo.definition(id, lock=lock)
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return row

    def _version(self, id: UUID, version: int) -> dict[str, object]:
        row = self.repo.version(id, version)
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return row

    def _audit(
        self, action: str, id: UUID, before: dict[str, object] | None, after: dict[str, object]
    ) -> None:
        AuditWriter(cast(AuditConnection, self.connection), self.scope).write(
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type="workflow",
            target_id=id,
            outcome=Outcome.SUCCESS,
            before_source=before,
            before_fields=tuple(before or ()),
            after_source=after,
            after_fields=tuple(after),
        )

    def get(self, id: UUID) -> WorkflowRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self._definition(id)
            summaries = self.repo.execute(
                "SELECT version,status,created_at,published_at FROM workflow_version "
                "WHERE org_id=%(org_id)s AND definition_id=%(id)s ORDER BY version",
                {"id": id},
            ).fetchall()
            return WorkflowRead.model_validate(
                row
                | {
                    "versions": [
                        WorkflowVersionSummary.model_validate(
                            dict(
                                zip(
                                    ("version", "status", "created_at", "published_at"),
                                    v,
                                    strict=True,
                                )
                            )
                        )
                        for v in summaries
                    ]
                }
            )

    def create(self, body: WorkflowCreate) -> WorkflowRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            scope_id = self.scope.org_id
            if body.scope_type == "bu":
                assert body.scope_id is not None
                scope_id = OrgService(self.connection, self.scope).get_unit(body.scope_id).id
            id = uuid4()
            try:
                with self.connection.transaction():
                    self.repo.execute(
                        "INSERT INTO workflow_definition(id,org_id,document_type,name,scope_type,"
                        "scope_id,created_by) VALUES(%(id)s,%(org_id)s,%(document_type)s,%(name)s,"
                        "%(scope_type)s,%(scope_id)s,%(actor)s)",
                        body.model_dump()
                        | {"id": id, "scope_id": scope_id, "actor": self.actor_id},
                    )
            except psycopg.errors.UniqueViolation as exc:
                raise conflict("workflow_scope_taken") from exc
            self._audit("workflow.create", id, None, self._definition(id))
            return self.get(id)

    def get_version(self, id: UUID, version: int) -> WorkflowVersionRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._definition(id)
            return WorkflowVersionRead.model_validate(self._version(id, version))

    def create_version(self, id: UUID, body: WorkflowVersionWrite) -> WorkflowVersionRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._definition(id, lock=True)
            existing = self.repo.execute(
                "SELECT version,status FROM workflow_version WHERE org_id=%(org_id)s "
                "AND definition_id=%(id)s ORDER BY version",
                {"id": id},
            ).fetchall()
            if any(row[1] == "draft" for row in existing):
                raise conflict("draft_exists")
            version = max((cast(int, row[0]) for row in existing), default=0) + 1
            self.repo.execute(
                "INSERT INTO workflow_version(id,org_id,definition_id,version,status,graph,"
                "created_by) "
                "VALUES(%(vid)s,%(org_id)s,%(id)s,%(version)s,'draft',%(graph)s,%(actor)s)",
                {
                    "vid": uuid4(),
                    "id": id,
                    "version": version,
                    "actor": self.actor_id,
                    "graph": Jsonb(
                        body.graph.model_dump(mode="json", by_alias=True, exclude_none=True)
                    ),
                },
            )
            after = self._version(id, version)
            self._audit("workflow.version.create", id, None, after)
            return WorkflowVersionRead.model_validate(after)

    def update_version(
        self, id: UUID, version: int, body: WorkflowVersionWrite, if_match: str | None
    ) -> WorkflowVersionRead:
        if if_match is None:
            raise ProblemError(
                ErrorCode.BAD_REQUEST, detail="Supply If-Match with the workflow version."
            )
        try:
            expected = int(if_match)
        except ValueError as exc:
            raise ProblemError(
                ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
            ) from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._definition(id, lock=True)
            before = self._version(id, version)
            if before["status"] != "draft":
                raise conflict("version_immutable")
            if before["row_version"] != expected:
                raise conflict("stale_version")
            self.repo.execute(
                "UPDATE workflow_version SET graph=%(graph)s,row_version=row_version+1 "
                "WHERE org_id=%(org_id)s AND definition_id=%(id)s AND version=%(version)s",
                {
                    "id": id,
                    "version": version,
                    "graph": Jsonb(
                        body.graph.model_dump(mode="json", by_alias=True, exclude_none=True)
                    ),
                },
            )
            after = self._version(id, version)
            self._audit("workflow.version.update", id, before, after)
            return WorkflowVersionRead.model_validate(after)

    def publish(self, id: UUID, version: int) -> WorkflowVersionRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._definition(id, lock=True)
            before = self._version(id, version)
            if before["status"] != "draft":
                raise conflict("version_immutable")
            parse_graph(before["graph"])
            superseded = self.repo.execute(
                "UPDATE workflow_version SET status='superseded' WHERE org_id=%(org_id)s "
                "AND definition_id=%(id)s AND status='published' RETURNING id",
                {"id": id},
            ).fetchone()
            self.repo.execute(
                "UPDATE workflow_version SET status='published',published_at=now(),"
                "published_by=%(actor)s,row_version=row_version+1 WHERE org_id=%(org_id)s "
                "AND definition_id=%(id)s AND version=%(version)s",
                {"id": id, "version": version, "actor": self.actor_id},
            )
            after = self._version(id, version)
            self._audit(
                "workflow.publish",
                id,
                before,
                after | {"superseded_version_id": superseded[0] if superseded else None},
            )
            return WorkflowVersionRead.model_validate(after)

    def retire(self, id: UUID) -> WorkflowRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            before = self._definition(id, lock=True)
            if before["retired_at"] is None:
                self.repo.execute(
                    "UPDATE workflow_definition SET retired_at=now() "
                    "WHERE org_id=%(org_id)s AND id=%(id)s",
                    {"id": id},
                )
                self._audit("workflow.retire", id, before, self._definition(id))
            return self.get(id)

    def list(self, document_type: DocumentType | None, cursor: str | None) -> WorkflowPage:
        key = None
        cursor_id = None
        if cursor is not None:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
                key = datetime.fromisoformat(decoded["created_at"])
                cursor_id = UUID(decoded["id"])
                if decoded["document_type"] != document_type:
                    raise ValueError("filter mismatch")
            except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                raise invalid("/cursor", "Invalid cursor. Restart paging.") from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            records = self.repo.execute(
                f"SELECT {', '.join(DEFINITION_FIELDS)} FROM workflow_definition "
                "WHERE org_id=%(org_id)s AND (%(type)s::text IS NULL OR document_type=%(type)s) "
                "AND (%(key)s::timestamptz IS NULL OR (created_at,id) > "
                "(%(key)s,%(cursor_id)s::uuid)) ORDER BY created_at,id LIMIT 51",
                {"type": document_type, "key": key, "cursor_id": cursor_id},
            ).fetchall()
            rows = [
                WorkflowRead.model_validate(dict(zip(DEFINITION_FIELDS, row, strict=True)))
                for row in records[:50]
            ]
            next_cursor = None
            if len(records) > 50:
                last = rows[-1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "created_at": last.created_at.isoformat(),
                            "id": str(last.id),
                            "document_type": document_type,
                        }
                    ).encode()
                ).decode()
            return WorkflowPage(rows=rows, next_cursor=next_cursor)


def resolve_version(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    bu_id: UUID | None,
) -> WorkflowVersionRead | None:
    """Nearest published live definition; retirement does not affect stored versions."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = (
            WorkflowRepository(conn, scope)
            .execute(
                f"SELECT {', '.join('v.' + field for field in VERSION_FIELDS)} "
                "FROM workflow_definition d JOIN workflow_version v "
                "ON v.org_id=d.org_id AND v.definition_id=d.id "
                "WHERE d.org_id=%(org_id)s AND d.document_type=%(type)s AND d.retired_at IS NULL "
                "AND v.status='published' AND ((d.scope_type='bu' AND d.scope_id=%(bu)s) "
                "OR (d.scope_type='org' AND d.scope_id=%(org_id)s)) "
                "ORDER BY (d.scope_type='bu') DESC LIMIT 1",
                {"type": document_type, "bu": bu_id},
            )
            .fetchone()
        )
        return (
            WorkflowVersionRead.model_validate(dict(zip(VERSION_FIELDS, row, strict=True)))
            if row
            else None
        )
