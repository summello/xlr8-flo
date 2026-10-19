"""Transactional workflow versioning; published graphs are immutable in Postgres."""

import base64
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.audit import ActorKind, AuditActor, Outcome, audit_event
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import PermissionHoldersResolver
from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.money import Money
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.conditions import (
    AttributeProvider,
    ConditionError,
    Evaluation,
    LeafTrace,
    approve_permission,
    evaluate,
    field_catalog,
    register_attribute_provider,
    register_document_type,
)

# Frozen document-module entry points and the decision route's service boundary.
from flo.modules.approvals.decisions import (
    decide as decide,
)
from flo.modules.approvals.decisions import (
    decided_on_behalf as decided_on_behalf,
)
from flo.modules.approvals.decisions import (
    decisions_for as decisions_for,
)
from flo.modules.approvals.decisions import (
    task_instance as task_instance,
)
from flo.modules.approvals.decisions import (
    withdraw as withdraw,
)
from flo.modules.approvals.decisions import (
    withdraw_instance as withdraw_instance,
)
from flo.modules.approvals.delegation import (
    create_delegation as create_delegation,
)
from flo.modules.approvals.delegation import (
    eligible_delegates as eligible_delegates,
)
from flo.modules.approvals.delegation import (
    list_delegations as list_delegations,
)
from flo.modules.approvals.delegation import (
    reassign_task as reassign_task,
)
from flo.modules.approvals.delegation import (
    revoke_delegation as revoke_delegation,
)
from flo.modules.approvals.eligibility import (
    EligibleSet as EligibleSet,
)
from flo.modules.approvals.eligibility import (
    PickerPage as PickerPage,
)
from flo.modules.approvals.eligibility import (
    approver_picker as approver_picker,
)
from flo.modules.approvals.eligibility import (
    eligible_approvers as eligible_approvers,
)
from flo.modules.approvals.engine import (
    active_instance as active_instance,
)
from flo.modules.approvals.engine import (
    adapter_for as adapter_for,
)
from flo.modules.approvals.engine import (
    advance as advance,
)
from flo.modules.approvals.engine import (
    current_members as current_members,
)
from flo.modules.approvals.engine import (
    get_instance as get_instance,
)
from flo.modules.approvals.engine import (
    instances_for as instances_for,
)
from flo.modules.approvals.engine import material_digest as material_digest
from flo.modules.approvals.engine import (
    preview_route as preview_route,
)
from flo.modules.approvals.engine import (
    resolve_version as resolve_version,
)
from flo.modules.approvals.engine import (
    resolve_version_reason as resolve_version_reason,
)
from flo.modules.approvals.engine import (
    start as start,
)
from flo.modules.approvals.engine import (
    step_complete as step_complete,
)
from flo.modules.approvals.explain import Explanation as Explanation
from flo.modules.approvals.explain import explain as explain
from flo.modules.approvals.explain import explanation_document as explanation_document
from flo.modules.approvals.inbox import InboxPage as InboxPage
from flo.modules.approvals.inbox import inbox as inbox
from flo.modules.approvals.material import (
    approval_state as approval_state,
)
from flo.modules.approvals.material import (
    assert_current_approval as assert_current_approval,
)
from flo.modules.approvals.material import (
    rebase_digest as rebase_digest,
)
from flo.modules.approvals.material import (
    requires_resubmission as requires_resubmission,
)
from flo.modules.approvals.material import (
    supersede as supersede,
)
from flo.modules.approvals.models import DEFINITION_FIELDS, WorkflowRepository
from flo.modules.approvals.schemas import (
    DocumentAdapter as DocumentAdapter,
)
from flo.modules.approvals.schemas import (
    DocumentSnapshot,
    DocumentType,
    RoutePreview,
    WorkflowCreate,
    WorkflowPage,
    WorkflowRead,
    WorkflowVersionRead,
    WorkflowVersionWrite,
    invalid,
    parse_graph,
)
from flo.modules.approvals.validation import ValidationReport, check_graph
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    AuthorizationTarget,
    ScopeType,
)
from flo.modules.org.service import OrgService

__all__ = [
    "InboxPage",
    "inbox",
    "create_delegation",
    "eligible_delegates",
    "list_delegations",
    "reassign_task",
    "revoke_delegation",
    "material_digest",
    "approval_state",
    "requires_resubmission",
    "supersede",
    "rebase_digest",
    "assert_current_approval",
    "decide",
    "decided_on_behalf",
    "decisions_for",
    "task_instance",
    "withdraw",
    "withdraw_instance",
    "WorkflowService",
    "start",
    "advance",
    "preview_route",
    "get_instance",
    "active_instance",
    "instances_for",
    "step_complete",
    "adapter_for",
    "current_members",
    "EligibleSet",
    "PickerPage",
    "approver_picker",
    "eligible_approvers",
    "ValidationReport",
    "validate_graph",
    "require_workflow",
    "require_workflow_for",
    "resolve_version",
    "resolve_version_reason",
    "Explanation",
    "explain",
    "explanation_document",
    "AttributeProvider",
    "ConditionError",
    "DocumentAdapter",
    "Evaluation",
    "LeafTrace",
    "evaluate",
    "field_catalog",
    "register_attribute_provider",
    "register_document_type",
]


def conflict(problem: str) -> ProblemError:
    return ProblemError(
        ErrorCode.CONFLICT,
        detail="The workflow cannot accept this change. Reload its versions and retry.",
        checks={"problem": problem},
    )


def require_workflow(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    bu_id: UUID | None,
    label: str,
) -> None:
    """D-M2-21: a governed action with no published workflow is refused, never auto-approved."""
    if resolve_version(conn, scope, document_type, bu_id) is None:
        raise ProblemError(
            ErrorCode.CONFLICT,
            detail=f"No approval workflow is published for {label}. "
            "Ask an administrator to set one up.",
            checks={"problem": "workflow_not_set_up"},
        )


def require_workflow_for(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    document_id: UUID,
    label: str,
) -> None:
    """require_workflow for an existing document: resolve its business unit as start does."""
    from flo.modules.approvals.engine import document_bu

    require_workflow(
        conn, scope, document_type, document_bu(conn, scope, document_type, document_id), label
    )


class WorkflowService:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        actor_id: UUID | None = None,
        resolver: PermissionHoldersResolver | None = None,
    ) -> None:
        self.connection = connection
        self.scope = scope
        self.resolver = resolver or AuthorizationResolver(
            cast(AuthorizationConnection, connection), scope
        )
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
        audit_event(
            cast(AuditConnection, self.connection),
            self.scope,
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type="workflow",
            target_id=id,
            outcome=Outcome.SUCCESS,
            before=before,
            after=after,
        )

    def get(self, id: UUID) -> WorkflowRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self._definition(id)
            from flo.modules.approvals.versions import enrich

            return enrich(self.repo, WorkflowRead.model_validate(row))

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

    def delete_version(self, id: UUID, version: int) -> None:
        """Soft-delete the draft version; the number is kept so it is never reused."""
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self._definition(id, lock=True)
            before = self._version(id, version)
            if before["status"] != "draft":
                raise conflict("not_draft")
            self.repo.execute(
                "UPDATE workflow_version SET status='deleted',row_version=row_version+1 "
                "WHERE org_id=%(org_id)s AND definition_id=%(id)s AND version=%(version)s",
                {"id": id, "version": version},
            )
            # A workflow that never had a published version frees its scope with its last draft.
            live = self.repo.execute(
                "SELECT 1 FROM workflow_version WHERE org_id=%(org_id)s AND definition_id=%(id)s "
                "AND status<>'deleted' LIMIT 1",
                {"id": id},
            ).fetchone()
            retired = live is None
            if retired:
                self.repo.execute(
                    "UPDATE workflow_definition SET retired_at=now() "
                    "WHERE org_id=%(org_id)s AND id=%(id)s AND retired_at IS NULL",
                    {"id": id},
                )
            self._audit(
                "workflow.version.delete",
                id,
                {"version": version, "status": "draft"},
                {"version": version, "status": "deleted", "definition_retired": retired},
            )

    def validation(self, id: UUID, version: int) -> ValidationReport:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            definition = self._definition(id)
            return validate_graph(
                self.connection,
                self.scope,
                str(definition["document_type"]),
                self._version(id, version)["graph"],
                target=AuthorizationTarget(
                    ScopeType(str(definition["scope_type"])), cast(UUID, definition["scope_id"])
                ),
                resolver=self.resolver,
            )

    def publish(
        self, id: UUID, version: int, acknowledge_warnings: bool = False
    ) -> WorkflowVersionRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            definition = self._definition(id, lock=True)
            before = self._version(id, version)
            if before["status"] != "draft":
                raise conflict("version_immutable")
            report = validate_graph(
                self.connection,
                self.scope,
                str(definition["document_type"]),
                before["graph"],
                resolver=self.resolver,
                target=AuthorizationTarget(
                    ScopeType(str(definition["scope_type"])), cast(UUID, definition["scope_id"])
                ),
            )
            graph = parse_graph(before["graph"])
            pointers = {node.id: f"/nodes/{i}" for i, node in enumerate(graph.nodes)}
            if report.errors:
                raise ProblemError(
                    ErrorCode.VALIDATION_FAILED,
                    detail="Correct workflow issues before publishing.",
                    checks={"problem": "workflow_invalid"},
                    errors=tuple(
                        ProblemFieldError(
                            field=(
                                f"/edges/{issue.edge_index}"
                                if issue.edge_index is not None
                                else pointers[str(issue.node_id)]
                            ),
                            message=issue.detail,
                        )
                        for issue in report.errors
                    ),
                )
            if report.warnings and not acknowledge_warnings:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="Acknowledge repeated approvals before publishing.",
                    checks={"problem": "warnings_unacknowledged"},
                    errors=tuple(
                        ProblemFieldError(field=pointers[str(issue.node_id)], message=issue.detail)
                        for issue in report.warnings
                    ),
                )
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

    def route_preview(
        self, id: UUID, version: int, amount: str, currency: str, bu_id: UUID | None
    ) -> RoutePreview:
        if re.fullmatch(r"[0-9]{1,14}(\.[0-9]{1,4})?", amount) is None:
            raise invalid(
                "/amount", "Enter an estimate with up to 14 digits and four decimal places."
            )
        try:
            money = Money(Decimal(amount), currency)
        except ValueError as exc:
            raise invalid("/currency", "Choose a valid currency code.") from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            definition = self.get(id)
            selected = self.get_version(id, version)
            unit_id = bu_id or (definition.scope_id if definition.scope_type == "bu" else None)
            if unit_id is not None:
                chain = OrgService(self.connection, self.scope).unit_chain(unit_id)
                if definition.scope_type == "bu" and definition.scope_id not in chain:
                    raise ProblemError(ErrorCode.NOT_FOUND)
            target = (
                AuthorizationTarget(ScopeType.BU, unit_id)
                if unit_id
                else AuthorizationTarget.organization(self.scope.org_id)
            )
            report = validate_graph(
                self.connection,
                self.scope,
                definition.document_type,
                selected.graph.model_dump(mode="json", by_alias=True, exclude_none=True),
                target=target,
                resolver=self.resolver,
            )
            structural = [
                issue
                for issue in report.errors
                if issue.code not in {"missing_approvers", "duplicate_approval"}
            ]
            if structural:
                from flo.modules.approvals.schemas import RouteProblem

                return RoutePreview(
                    workflow_version_id=selected.id,
                    workflow_version=selected.version,
                    separation_of_duty_applied=False,
                    steps=[],
                    problems=[
                        RouteProblem(
                            code="route_blocked", step_label=issue.node_id, message=issue.detail
                        )
                        for issue in structural
                    ],
                )
            # The snapshot contract needs an ID; the sentinel never reaches eligibility.
            # Administrative previews pass requester_id=None to the engine.
            snapshot = DocumentSnapshot(
                1, UUID(int=0), cast(TargetProtocol, target), {"amount": money}, {}
            )
            return preview_route(
                self.connection,
                self.scope,
                document_type=definition.document_type,
                document_id=id,
                resolver=self.resolver,
                version=selected,
                synthetic_snapshot=snapshot,
                requester_id=None,
            )

    def list(
        self, document_type: DocumentType | None, cursor: str | None, search: str = ""
    ) -> WorkflowPage:
        key = None
        cursor_id = None
        if cursor is not None:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
                key = datetime.fromisoformat(decoded["created_at"])
                cursor_id = UUID(decoded["id"])
                if decoded["document_type"] != document_type or decoded.get("search", "") != search:
                    raise ValueError("filter mismatch")
            except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                raise invalid("/cursor", "Invalid cursor. Restart paging.") from exc
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            records = self.repo.execute(
                f"SELECT {', '.join(DEFINITION_FIELDS)} FROM workflow_definition "
                "WHERE org_id=%(org_id)s AND (%(type)s::text IS NULL OR document_type=%(type)s) "
                "AND position(lower(%(search)s) in lower(name)) > 0 "
                "AND (%(key)s::timestamptz IS NULL OR (created_at,id) > "
                "(%(key)s,%(cursor_id)s::uuid)) ORDER BY created_at,id LIMIT 51",
                {"type": document_type, "key": key, "cursor_id": cursor_id, "search": search},
            ).fetchall()
            from flo.modules.approvals.versions import enrich

            rows = [
                enrich(
                    self.repo,
                    WorkflowRead.model_validate(dict(zip(DEFINITION_FIELDS, row, strict=True))),
                )
                for row in records[:50]
            ]
            # Lineage belongs to the detail sheet, not every row of the paged list.
            for row in rows:
                row.versions = []
            next_cursor = None
            if len(records) > 50:
                last = rows[-1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "created_at": last.created_at.isoformat(),
                            "id": str(last.id),
                            "document_type": document_type,
                            "search": search,
                        }
                    ).encode()
                ).decode()
            return WorkflowPage(rows=rows, next_cursor=next_cursor)


def validate_graph(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    document_type: str,
    graph: object,
    *,
    target: AuthorizationTarget | None = None,
    resolver: PermissionHoldersResolver,
) -> ValidationReport:
    """Validate named approvers and the organization's current operational limit."""
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = WorkflowRepository(conn, scope)
        roles = {
            str(row[0])
            for row in repo.execute("SELECT code FROM role WHERE org_id=%(org_id)s").fetchall()
        }
        users = {
            cast(UUID, row[0])
            for row in repo.execute(
                "SELECT identity_id FROM identity_membership WHERE org_id=%(org_id)s"
            ).fetchall()
        }
        if target is not None:
            from flo.modules.approvals.schemas import UserApprovers

            parsed = parse_graph(graph)
            if any(
                isinstance(rule, UserApprovers)
                for node in parsed.nodes
                for rule in (node.approvers, node.escalate_to)
            ):
                users &= set(
                    resolver.holders(
                        approve_permission(document_type), cast(TargetProtocol, target)
                    )
                )
        setting = repo.execute(
            "SELECT value FROM org_setting WHERE org_id=%(org_id)s AND unit_id IS NULL "
            "AND key='approval.max_depth'"
        ).fetchone()
        return check_graph(
            parse_graph(graph),
            document_type,
            roles,
            users,
            cast(int, setting[0]) if setting else 10,
        )
