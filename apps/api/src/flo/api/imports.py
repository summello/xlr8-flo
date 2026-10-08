"""Authorized import templates, streaming upload, and mapping routes."""

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from flo.api.admin_users import organization_target
from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.config import Settings
from flo.kernel.ports.storage import Storage
from flo.kernel.storage import create_storage
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.imports.multipart import read_upload
from flo.modules.imports.schemas import ImportBatchRead, MappingPut, TemplateRead
from flo.modules.imports.service import ImportService, list_templates, template_file

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])


def get_import_storage(settings: Annotated[Settings, Depends(get_auth_settings)]) -> Storage:
    return create_storage(settings)


StoragePort = Annotated[Storage, Depends(get_import_storage)]
ReadContext = Annotated[AuthorizationContext, Depends(require("import.read", organization_target))]
RunContext = Annotated[AuthorizationContext, Depends(require("import.run", organization_target))]


def record_target(id: UUID, connection: Connection) -> TargetProtocol:
    batch = ImportService(connection, current_scope()).get(id)
    return cast(
        TargetProtocol,
        AuthorizationTarget(ScopeType.ORG, current_scope().org_id, "import_batch", batch.id),
    )


BatchReadContext = Annotated[AuthorizationContext, Depends(require("import.read", record_target))]
BatchRunContext = Annotated[AuthorizationContext, Depends(require("import.run", record_target))]


@router.get("/templates", response_model=list[TemplateRead])
def templates(context: ReadContext) -> list[TemplateRead]:
    return list_templates()


@router.get("/templates/{name}/file")
def download(name: str, context: ReadContext, format: Literal["csv", "xlsx"] = "csv") -> Response:
    data, media_type, version = template_file(name, format)
    return Response(data, media_type=media_type, headers={"X-Template-Version": str(version)})


@router.post(
    "",
    status_code=201,
    response_model=ImportBatchRead,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["template", "file"],
                        "properties": {
                            "template": {"type": "string"},
                            "file": {"type": "string", "format": "binary"},
                        },
                    }
                }
            },
        }
    },
)
async def upload(
    request: Request, context: RunContext, connection: Connection, storage: StoragePort
) -> ImportBatchRead:
    """Every upload creates a new batch; Idempotency-Key is accepted and ignored (D-M1-19)."""
    payload = await read_upload(request)
    return ImportService(connection, current_scope(), context.user_id, storage).upload(payload)


@router.get("/{id}", response_model=ImportBatchRead)
def get(id: UUID, context: BatchReadContext, connection: Connection) -> ImportBatchRead:
    return ImportService(connection, current_scope()).get(id)


@router.put("/{id}/mapping", response_model=ImportBatchRead)
def mapping(
    id: UUID,
    body: MappingPut,
    context: BatchRunContext,
    connection: Connection,
    storage: StoragePort,
) -> ImportBatchRead:
    return ImportService(connection, current_scope(), context.user_id, storage).save_mapping(
        id, body.mapping
    )
