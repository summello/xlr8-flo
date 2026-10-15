"""Thin document collaboration routes with record concealment before dynamic permissions."""

import io
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse

from flo.api.auth import get_auth_settings
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationContext, AuthorizationTarget, require
from flo.kernel.config import Settings
from flo.kernel.multipart import read_multipart
from flo.kernel.ports.storage import Storage
from flo.kernel.storage import create_storage
from flo.kernel.tenancy.context import current_scope
from flo.modules.collaboration.scan_rules import MAX_FILE_BYTES
from flo.modules.collaboration.schemas import (
    AttachmentRead,
    NoteCreate,
    NotePatch,
    NoteRead,
    NoteRevisionRead,
)
from flo.modules.collaboration.service import CollaborationService

router = APIRouter(prefix="/api/v1", tags=["collaboration"])


def get_collaboration_storage(settings: Annotated[Settings, Depends(get_auth_settings)]) -> Storage:
    return create_storage(settings)


StoragePort = Annotated[Storage, Depends(get_collaboration_storage)]


def document_target(
    document_type: str, document_id: UUID, connection: Connection
) -> AuthorizationTarget:
    return CollaborationService(connection, current_scope()).document_target(
        document_type, document_id
    )


def note_target(id: UUID, connection: Connection) -> AuthorizationTarget:
    service = CollaborationService(connection, current_scope())
    return service.document_target(*service.linked_document("note", id))


def attachment_target(id: UUID, connection: Connection) -> AuthorizationTarget:
    service = CollaborationService(connection, current_scope())
    return service.document_target(*service.linked_document("attachment", id))


DocumentContext = Annotated[AuthorizationContext, Depends(require("record.read", document_target))]
NoteContext = Annotated[AuthorizationContext, Depends(require("record.read", note_target))]
AttachmentContext = Annotated[
    AuthorizationContext, Depends(require("record.read", attachment_target))
]


def service(
    connection: Connection, request: Request, storage: Storage | None = None
) -> CollaborationService:
    return CollaborationService(
        connection, current_scope(), request.state.session.identity_id, storage
    )


@router.get("/documents/{document_type}/{document_id}/notes", response_model=list[NoteRead])
def notes(
    document_type: str,
    document_id: UUID,
    context: DocumentContext,
    connection: Connection,
    request: Request,
) -> list[NoteRead]:
    return service(connection, request).notes(request, document_type, document_id)


@router.post(
    "/documents/{document_type}/{document_id}/notes", status_code=201, response_model=NoteRead
)
def add_note(
    document_type: str,
    document_id: UUID,
    body: NoteCreate,
    context: DocumentContext,
    connection: Connection,
    request: Request,
    response: Response,
) -> NoteRead:
    row = service(connection, request).add_note(request, document_type, document_id, body)
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.patch("/notes/{id}", response_model=NoteRead)
def edit_note(
    id: UUID,
    body: NotePatch,
    context: NoteContext,
    connection: Connection,
    request: Request,
    response: Response,
    version: Annotated[str | None, Header(alias="If-Match")] = None,
) -> NoteRead:
    row = service(connection, request).edit_note(request, id, body, version)
    response.headers["ETag"] = f'"{row.version}"'
    return row


@router.get("/notes/{id}/revisions", response_model=list[NoteRevisionRead])
def revisions(
    id: UUID, context: NoteContext, connection: Connection, request: Request
) -> list[NoteRevisionRead]:
    return service(connection, request).revisions(request, id)


@router.get(
    "/documents/{document_type}/{document_id}/attachments", response_model=list[AttachmentRead]
)
def attachments(
    document_type: str,
    document_id: UUID,
    context: DocumentContext,
    connection: Connection,
    request: Request,
) -> list[AttachmentRead]:
    return service(connection, request).attachments(request, document_type, document_id)


UPLOAD_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}


@router.post(
    "/documents/{document_type}/{document_id}/attachments",
    status_code=201,
    response_model=AttachmentRead,
    openapi_extra=UPLOAD_SCHEMA,
)
async def upload(
    document_type: str,
    document_id: UUID,
    context: DocumentContext,
    connection: Connection,
    request: Request,
    storage: StoragePort,
) -> AttachmentRead:
    svc = service(connection, request, storage)
    svc.authorize(request, document_type, document_id, write=True)
    file = await read_multipart(
        request, text_fields={}, required=frozenset(), max_file_bytes=MAX_FILE_BYTES
    )
    return svc.upload(request, document_type, document_id, file)


@router.post(
    "/attachments/{id}/versions",
    status_code=201,
    response_model=AttachmentRead,
    openapi_extra=UPLOAD_SCHEMA,
)
async def upload_version(
    id: UUID,
    context: AttachmentContext,
    connection: Connection,
    request: Request,
    storage: StoragePort,
) -> AttachmentRead:
    svc = service(connection, request, storage)
    svc.authorize(request, *svc.linked_document("attachment", id), write=True)
    file = await read_multipart(
        request, text_fields={}, required=frozenset(), max_file_bytes=MAX_FILE_BYTES
    )
    return svc.upload_version(request, id, file)


@router.get("/attachments/{id}/content")
def content(
    id: UUID,
    context: AttachmentContext,
    connection: Connection,
    request: Request,
    storage: StoragePort,
    version: int | None = None,
) -> StreamingResponse:
    row, data = service(connection, request, storage).content(request, id, version)
    return StreamingResponse(
        io.BytesIO(data),
        media_type=row.media_type,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(row.display_name, safe=""),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/attachments/{id}", status_code=204)
def delete(
    id: UUID, context: AttachmentContext, connection: Connection, request: Request
) -> Response:
    service(connection, request).delete(request, id)
    return Response(status_code=204)
