"""Authorized approver picker for workflow configuration."""

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from flo.api.admin_users import organization_target
from flo.api.authz_factory import production_authorization_resolver
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import (
    HoldersResolverFactory,
    permits,
    require,
    require_member,
    require_resolved,
)
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import current_scope
from flo.modules.approvals.schemas import (
    DecisionRead,
    DecisionResult,
    DecisionWrite,
    DelegationCreate,
    DelegationPage,
    DelegationRead,
    DocumentType,
    InstanceRead,
    ReassignWrite,
    TaskRead,
    WithdrawWrite,
)
from flo.modules.approvals.service import (
    Explanation,
    InboxPage,
    PickerPage,
    adapter_for,
    approver_picker,
    create_delegation,
    decide,
    decided_on_behalf,
    decisions_for,
    eligible_delegates,
    explain,
    explanation_document,
    get_instance,
    inbox,
    list_delegations,
    reassign_task,
    revoke_delegation,
    task_instance,
    withdraw_instance,
)
from flo.modules.identity.models import AuthorizationContext
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


def picker_target(
    scope_type: Literal["org", "bu"],
    scope_id: UUID,
    connection: Connection,
) -> TargetProtocol:
    scope = current_scope()
    if scope_type == "org":
        if scope_id != scope.org_id:
            raise ProblemError(ErrorCode.NOT_FOUND)
    else:
        OrgService(connection, scope).get_unit(scope_id)
    return cast(TargetProtocol, AuthorizationTarget(ScopeType(scope_type), scope_id))


PickerContext = Annotated[AuthorizationContext, Depends(require("workflow.manage", picker_target))]


@router.get("/eligible-approvers", response_model=PickerPage)
def eligible_picker(
    document_type: DocumentType,
    scope_type: Literal["org", "bu"],
    scope_id: UUID,
    context: PickerContext,
    connection: Connection,
    q: str | None = None,
) -> PickerPage:
    factory: HoldersResolverFactory = production_authorization_resolver
    with factory(current_scope()) as resolver:
        return approver_picker(
            connection,
            current_scope(),
            document_type,
            AuthorizationTarget(ScopeType(scope_type), scope_id),
            q,
            resolver,
        )


def instance_permission(id: UUID, connection: Connection) -> tuple[str, TargetProtocol]:
    scope = current_scope()
    instance = get_instance(connection, scope, id)
    adapter = adapter_for(instance.document_type)
    return adapter.read_permission, adapter.snapshot(connection, scope, instance.document_id).target


InstanceContext = Annotated[AuthorizationContext, Depends(require_resolved(instance_permission))]


def explanation_permission(
    id: UUID, connection: Connection, request: Request
) -> tuple[str, TargetProtocol]:
    scope = current_scope()
    document_type, document_id = explanation_document(connection, scope, id)
    organization = cast(TargetProtocol, AuthorizationTarget.organization(scope.org_id))
    if permits(request, "approval.admin", organization):
        return "approval.admin", organization
    adapter = adapter_for(document_type)
    return adapter.read_permission, adapter.snapshot(connection, scope, document_id).target


ExplanationContext = Annotated[
    AuthorizationContext, Depends(require_resolved(explanation_permission))
]


@router.get("/instances/{id}", response_model=InstanceRead)
def read_instance(id: UUID, context: InstanceContext, connection: Connection) -> InstanceRead:
    return get_instance(connection, current_scope(), id)


@router.get("/instances/{id}/explanation", response_model=Explanation)
def read_explanation(id: UUID, context: ExplanationContext, connection: Connection) -> Explanation:
    return explain(connection, current_scope(), id)


def task_permission(task_id: UUID, connection: Connection) -> tuple[str, TargetProtocol]:
    scope = current_scope()
    instance = get_instance(connection, scope, task_instance(connection, scope, task_id))
    adapter = adapter_for(instance.document_type)
    return adapter.approve_permission, adapter.snapshot(
        connection, scope, instance.document_id
    ).target


DecisionContext = Annotated[AuthorizationContext, Depends(require_resolved(task_permission))]


@router.post("/tasks/{task_id}/decisions", response_model=DecisionResult)
def record_decision(
    task_id: UUID, body: DecisionWrite, context: DecisionContext, connection: Connection
) -> DecisionResult:
    return decide(connection, current_scope(), task_id=task_id, actor_id=context.user_id, body=body)


@router.post("/instances/{id}/withdraw", response_model=InstanceRead)
def withdraw_approval(
    id: UUID, body: WithdrawWrite, context: InstanceContext, connection: Connection
) -> InstanceRead:
    return withdraw_instance(
        connection, current_scope(), instance_id=id, actor_id=context.user_id, comment=body.comment
    )


@router.get("/instances/{id}/decisions", response_model=list[DecisionRead])
def decision_trail(
    id: UUID, context: InstanceContext, connection: Connection
) -> list[DecisionRead]:
    return decisions_for(connection, current_scope(), id)


DelegateContext = Annotated[
    AuthorizationContext, Depends(require("approval.delegate", organization_target))
]
AdminContext = Annotated[
    AuthorizationContext, Depends(require("approval.admin", organization_target))
]
MemberUser = Annotated[IdentityId, Depends(require_member())]


def _is_admin(request: Request) -> bool:
    return permits(
        request,
        "approval.admin",
        cast(TargetProtocol, AuthorizationTarget.organization(current_scope().org_id)),
    )


@router.post("/delegations", status_code=201, response_model=DelegationRead)
def create_delegation_route(
    body: DelegationCreate, context: DelegateContext, connection: Connection
) -> DelegationRead:
    factory: HoldersResolverFactory = production_authorization_resolver
    with factory(current_scope()) as resolver:
        return create_delegation(
            connection, current_scope(), actor_id=context.user_id, body=body, resolver=resolver
        )


# Registered before the id routes so "eligible-delegates" is never read as a delegation id.
@router.get("/delegations/eligible-delegates", response_model=PickerPage)
def eligible_delegates_route(
    context: DelegateContext,
    connection: Connection,
    document_type: DocumentType | None = None,
    document_types: Annotated[list[DocumentType] | None, Query()] = None,
) -> PickerPage:
    # A person must hold every selected type; none selected means every registered type.
    chosen: list[str] = [*(document_types or []), *([document_type] if document_type else [])]
    factory: HoldersResolverFactory = production_authorization_resolver
    with factory(current_scope()) as resolver:
        return eligible_delegates(
            connection,
            current_scope(),
            context.user_id,
            chosen or None,
            resolver,
        )


@router.get("/delegations/decided-on-my-behalf", response_model=list[DecisionRead])
def decided_on_my_behalf_route(user: MemberUser, connection: Connection) -> list[DecisionRead]:
    # Self-scoped (convention P): only decisions recorded for the caller as the delegator.
    return decided_on_behalf(connection, current_scope(), user)


@router.get("/delegations", response_model=DelegationPage)
def list_delegations_route(
    user: MemberUser,
    connection: Connection,
    request: Request,
    view: Literal["mine", "covering", "organization"] = "mine",
    cursor: str | None = None,
) -> DelegationPage:
    if view == "organization" and not _is_admin(request):
        raise ProblemError(ErrorCode.FORBIDDEN)
    return list_delegations(connection, current_scope(), actor_id=user, view=view, cursor=cursor)


@router.post("/delegations/{id}/revoke", response_model=DelegationRead)
def revoke_delegation_route(
    id: UUID, user: MemberUser, connection: Connection, request: Request
) -> DelegationRead:
    # Authority-reducing: the service checks owner-or-approval.admin in the same transaction.
    return revoke_delegation(
        connection,
        current_scope(),
        actor_id=user,
        delegation_id=id,
        is_admin=lambda: _is_admin(request),
    )


@router.post("/tasks/{task_id}/reassign", response_model=TaskRead)
def reassign_route(
    task_id: UUID, body: ReassignWrite, context: AdminContext, connection: Connection
) -> TaskRead:
    factory: HoldersResolverFactory = production_authorization_resolver
    with factory(current_scope()) as resolver:
        instance_id = reassign_task(
            connection,
            current_scope(),
            actor_id=context.user_id,
            task_id=task_id,
            body=body,
            resolver=resolver,
        )
    instance = get_instance(connection, current_scope(), instance_id)
    return next(t for t in instance.tasks if t.id == task_id)


@router.get("/tasks", response_model=InboxPage)
def inbox_route(
    user: MemberUser,
    connection: Connection,
    view: Literal["pending", "overdue", "completed", "delegated"] = "pending",
    type: DocumentType | None = None,
    q: str = "",
    sort: Literal["due", "amount", "decided"] | None = None,
    direction: Literal["asc", "desc"] | None = None,
    cursor: str | None = None,
) -> InboxPage:
    # Self-scoped list (convention P): membership is evaluated live for the caller only.
    return inbox(
        connection,
        current_scope(),
        user_id=user,
        view=view,
        document_type=type,
        q=q,
        sort=sort,
        direction=direction,
        cursor=cursor,
    )
