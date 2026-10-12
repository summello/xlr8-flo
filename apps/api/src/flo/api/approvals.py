"""Authorized approver picker for workflow configuration."""

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends

from flo.api.authz_factory import production_authorization_resolver
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import HoldersResolverFactory, require, require_resolved
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import current_scope
from flo.modules.approvals.schemas import DocumentType, InstanceRead
from flo.modules.approvals.service import PickerPage, adapter_for, approver_picker, get_instance
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


@router.get("/instances/{id}", response_model=InstanceRead)
def read_instance(id: UUID, context: InstanceContext, connection: Connection) -> InstanceRead:
    return get_instance(connection, current_scope(), id)
