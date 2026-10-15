"""Catalogue suggestions for requisition line entry (REQ-007)."""

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends

from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.service import OrgService
from flo.modules.requisitions.catalogue import CatalogueService
from flo.modules.requisitions.schemas import CatalogueRows

router = APIRouter(prefix="/api/v1/catalogue", tags=["catalogue"])


def unit_target(bu_id: UUID, connection: Connection) -> TargetProtocol:
    unit = OrgService(connection, current_scope()).get_unit(bu_id)  # 404 when foreign
    return cast(TargetProtocol, AuthorizationTarget(ScopeType.BU, unit.id))


CreateContext = Annotated[AuthorizationContext, Depends(require("requisition.create", unit_target))]


@router.get("", response_model=CatalogueRows)
def search_catalogue(
    bu_id: UUID,
    q: str,
    context: CreateContext,
    connection: Connection,
    line_type: Literal["item", "service"] | None = None,
    limit: int = 4,
) -> CatalogueRows:
    return CatalogueService(connection, current_scope()).search(bu_id, q, line_type, limit)
