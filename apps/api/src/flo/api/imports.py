"""Authorized import templates, streaming upload, and mapping routes."""

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from flo.api.admin_users import organization_target
from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import permits, require
from flo.kernel.config import Settings
from flo.kernel.ports.storage import Storage
from flo.kernel.storage import create_storage
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.imports.commit import CommitService
from flo.modules.imports.multipart import read_upload
from flo.modules.imports.reports import PreviewKind, ReportService
from flo.modules.imports.schemas import (
    BatchStatus,
    CommitBody,
    ImportBatchRead,
    ImportHistory,
    ImportPreview,
    MappingPut,
    TemplateRead,
)
from flo.modules.imports.service import ImportService, list_templates, template_file
from flo.modules.imports.templates.builtin import register_builtin
from flo.modules.imports.validation import ValidationService

register_builtin()

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
    responses={200: {"model": ImportBatchRead, "description": "External key replay"}},
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
                            "external_key": {"type": "string", "minLength": 1, "maxLength": 128},
                            "file": {"type": "string", "format": "binary"},
                        },
                    }
                }
            },
        }
    },
)
async def upload(
    request: Request,
    response: Response,
    context: RunContext,
    connection: Connection,
    storage: StoragePort,
    external_key: Annotated[str | None, Header(alias="X-Import-Key")] = None,
) -> ImportBatchRead:
    """External keys replay batches; Idempotency-Key is ignored (D-M1-19)."""
    payload = await read_upload(request)
    service = ImportService(connection, current_scope(), context.user_id, storage)
    batch = service.upload(
        payload, external_key if external_key is not None else payload.external_key
    )
    if service.replayed:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    return batch


@router.get("", response_model=ImportHistory)
def history(
    context: ReadContext,
    connection: Connection,
    status: BatchStatus | None = None,
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
) -> ImportHistory:
    return ImportService(connection, current_scope()).history(status, cursor, page_size)


@router.post("/{id}/cancel", status_code=202, response_model=ImportBatchRead)
def cancel(id: UUID, context: BatchRunContext, connection: Connection) -> ImportBatchRead:
    return ImportService(connection, current_scope(), context.user_id).cancel(id)


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


@router.post(
    "/{id}/validate",
    response_model=ImportBatchRead,
    responses={202: {"model": ImportBatchRead, "description": "Validation job queued"}},
)
def validate(
    id: UUID,
    request: Request,
    response: Response,
    context: BatchRunContext,
    connection: Connection,
    storage: StoragePort,
) -> ImportBatchRead:
    batch = ValidationService(connection, current_scope(), context.user_id, storage).validate(
        id, lambda permission, target: permits(request, permission, target)
    )
    if batch.status == "validating":
        response.status_code = 202
        response.headers["Location"] = f"/api/v1/imports/{id}"
    return batch


@router.post(
    "/{id}/commit",
    response_model=ImportBatchRead,
    responses={202: {"model": ImportBatchRead, "description": "Commit job queued"}},
)
def commit(
    id: UUID,
    body: CommitBody,
    request: Request,
    response: Response,
    context: BatchRunContext,
    connection: Connection,
) -> ImportBatchRead:
    batch = CommitService(connection, current_scope(), context.user_id).commit(
        id, body.mode, lambda permission, target: permits(request, permission, target)
    )
    if batch.status == "committing":
        response.status_code = 202
        response.headers["Location"] = f"/api/v1/imports/{id}"
    return batch


@router.get("/{id}/preview", response_model=ImportPreview)
def preview(
    id: UUID,
    context: BatchReadContext,
    connection: Connection,
    kind: PreviewKind | None = None,
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 50,
) -> ImportPreview:
    return ReportService(connection, current_scope()).preview(id, kind, cursor, page_size)


@router.get(
    "/{id}/errors.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def errors_csv(id: UUID, context: BatchReadContext, connection: Connection) -> StreamingResponse:
    return StreamingResponse(
        ReportService(connection, current_scope()).errors_csv(id),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="import-{id}-errors.csv"'},
    )
