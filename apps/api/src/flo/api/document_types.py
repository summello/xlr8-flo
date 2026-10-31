"""Composition root for document access; collaboration never imports document modules."""

from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.approvals.service import register_document_type
from flo.modules.budget.approval import BudgetChangeAdapter
from flo.modules.collaboration.service import DocumentAccess, register_document_access
from flo.modules.identity.service import (
    AuthorizationConnection,
    AuthorizationResolver,
    AuthorizationTarget,
    ScopeType,
)
from flo.modules.projects.approval import ProjectAdapter
from flo.modules.projects.service import ProjectService, register_open_document_counter
from flo.modules.purchasing.approval import PoAdapter, PoChangeAdapter
from flo.modules.purchasing.service import PurchaseOrderService
from flo.modules.requisitions.service import (
    ProjectRequestAdapter,
    RequisitionAdapter,
    RequisitionService,
    open_document_count,
)
from flo.modules.sourcing.approval import AwardAdapter
from flo.modules.sourcing.service import register_access as register_rfq_access
from flo.modules.vendors.documents import register_access as register_vendor_access


def requisition_access(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, id: UUID
) -> TargetProtocol:
    row = RequisitionService(connection, scope).get(id)
    return cast(
        TargetProtocol,
        AuthorizationTarget(
            ScopeType.BU if row.standalone else ScopeType.PROJECT,
            row.bu_id if row.standalone else cast(UUID, row.project_id),
            "requisition",
            row.id,
        ),
    )


def project_access(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, id: UUID
) -> TargetProtocol:
    row = ProjectService(connection, scope).get(id)
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.PROJECT, row.id, "project", row.id))


def po_access(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, id: UUID
) -> TargetProtocol:
    unit_id = PurchaseOrderService(connection, scope).unit_of(id)
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit_id, "org_unit", unit_id))


def po_write(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, id: UUID, actor: UUID | None
) -> None:
    """`purchase_order.create` while draft, `purchase_order.amend` in every other status."""
    # ponytail: the status is read without a PO lock; a transition committing between this read and
    # the write is judged by the earlier status. Every seeded role holding one of create/amend holds
    # both.
    with tenant_transaction(cast(RlsSession, connection), scope):
        status = PurchaseOrderService(connection, scope).get(id).status
        permission = "purchase_order.create" if status == "draft" else "purchase_order.amend"
        if (
            actor is None
            or not AuthorizationResolver(cast(AuthorizationConnection, connection), scope)
            .check(IdentityId(actor), permission, po_access(connection, scope, id))
            .allowed
        ):
            raise ProblemError(ErrorCode.FORBIDDEN)


def register_document_types() -> None:
    register_document_type(ProjectAdapter())
    register_document_type(BudgetChangeAdapter())
    register_document_type(RequisitionAdapter())
    register_document_type(ProjectRequestAdapter())
    register_document_type(AwardAdapter())
    register_document_type(PoAdapter())
    register_document_type(PoChangeAdapter())
    register_open_document_counter(open_document_count)
    register_document_access(
        DocumentAccess("requisition", "requisition.view", "requisition.edit", requisition_access)
    )
    register_document_access(
        DocumentAccess("project", "project.read", "project.update", project_access)
    )
    register_document_access(
        DocumentAccess(
            "purchase_order",
            "purchase_order.view",
            "purchase_order.create",
            po_access,
            write_authorize=po_write,
            share_authorize=po_write,
        )
    )
    register_vendor_access()
    register_rfq_access()
