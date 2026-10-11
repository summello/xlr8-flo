"""Composition root for document access; collaboration never imports document modules."""

from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.tenancy.context import Scope
from flo.modules.collaboration.service import DocumentAccess, register_document_access
from flo.modules.identity.service import AuthorizationTarget, ScopeType
from flo.modules.projects.service import ProjectService
from flo.modules.requisitions.service import RequisitionService


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


def register_document_types() -> None:
    register_document_access(
        DocumentAccess("requisition", "requisition.view", "requisition.edit", requisition_access)
    )
    register_document_access(
        DocumentAccess("project", "project.read", "project.update", project_access)
    )
