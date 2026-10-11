"""Authorized, tenant-scoped notes and retained private attachment versions."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import psycopg
from starlette.requests import Request

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.authz import AuthorizationTarget, permits
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.multipart import MultipartUpload
from flo.kernel.ports.scan import Scanner
from flo.kernel.ports.storage import Storage, opaque_storage_key
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.collaboration.models import CollaborationRepository
from flo.modules.collaboration.scan_rules import BasicChecksScanner, safe_name
from flo.modules.collaboration.schemas import (
    AttachmentRead,
    NoteCreate,
    NotePatch,
    NoteRead,
    NoteRevisionRead,
)

__all__ = ["CollaborationService", "DocumentAccess", "register_document_access", "lock_note"]


@dataclass(frozen=True)
class DocumentAccess:
    document_type: str
    read_permission: str
    write_permission: str
    target: Callable[[psycopg.Connection[tuple[object, ...]], Scope, UUID], AuthorizationTarget]


_ACCESS: dict[str, DocumentAccess] = {}


def register_document_access(access: DocumentAccess) -> None:
    _ACCESS[access.document_type] = access


NOTE_FIELDS = tuple(NoteRead.model_fields)
NOTE_SQL = """SELECT n.id,n.author_id,i.email,n.body,n.visibility,n.locked,n.created_at,
 n.edited_at,n.version FROM note n JOIN identity i ON i.id=n.author_id"""
ATT_FIELDS = tuple(AttachmentRead.model_fields)
ATT_SQL = """SELECT a.id,a.document_type,a.document_id,v.version,
 (SELECT count(*) FROM attachment_version c WHERE c.org_id=a.org_id AND c.attachment_id=a.id),
 v.original_filename,v.display_name,v.media_type,v.size_bytes,v.sha256,v.scan_status,
 v.uploaded_by,i.email,v.uploaded_at,a.deleted_at FROM attachment a
 JOIN attachment_version v ON v.org_id=a.org_id AND v.attachment_id=a.id
 JOIN identity i ON i.id=v.uploaded_by"""


class CollaborationService:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        actor_id: UUID | None = None,
        storage: Storage | None = None,
        scanner: Scanner | None = None,
    ) -> None:
        self.connection, self.scope, self.actor_id = connection, scope, actor_id
        self.repo = CollaborationRepository(connection, scope)
        self.storage = storage
        self.scanner = scanner or BasicChecksScanner()

    def document_target(self, document_type: str, document_id: UUID) -> AuthorizationTarget:
        access = _ACCESS.get(document_type)
        if access is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return access.target(self.connection, self.scope, document_id)

    def authorize(
        self, request: Request, document_type: str, document_id: UUID, *, write: bool = False
    ) -> None:
        target = self.document_target(document_type, document_id)
        access = _ACCESS[document_type]
        if not permits(
            request, access.write_permission if write else access.read_permission, target
        ):
            raise ProblemError(ErrorCode.FORBIDDEN)

    def linked_document(self, kind: str, id: UUID) -> tuple[str, UUID]:
        assert kind in {"note", "attachment"}
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                f"SELECT document_type,document_id FROM {kind} "
                "WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": id},
            ).fetchone()
            if row is None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            return cast(str, row[0]), cast(UUID, row[1])

    def _audit(
        self, action: str, kind: str, id: UUID, values: dict[str, object], *, refused: bool = False
    ) -> None:
        AuditWriter(cast(AuditConnection, self.connection), self.scope).write(
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type=kind,
            target_id=id,
            outcome=Outcome.DENIED if refused else Outcome.SUCCESS,
            after_source=values,
            after_fields=tuple(values),
        )

    def _note(self, id: UUID, *, lock: bool = False) -> NoteRead:
        row = self.repo.execute(
            NOTE_SQL
            + " WHERE n.org_id=%(org_id)s AND n.id=%(id)s"
            + (" FOR UPDATE OF n" if lock else ""),
            {"id": id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return NoteRead.model_validate(dict(zip(NOTE_FIELDS, row, strict=True)))

    def notes(self, request: Request, document_type: str, document_id: UUID) -> list[NoteRead]:
        self.authorize(request, document_type, document_id)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rows = self.repo.execute(
                NOTE_SQL + " WHERE n.org_id=%(org_id)s AND "
                "n.document_type=%(type)s AND n.document_id=%(doc)s "
                "ORDER BY n.created_at DESC,n.id DESC",
                {"type": document_type, "doc": document_id},
            ).fetchall()
            return [
                NoteRead.model_validate(dict(zip(NOTE_FIELDS, row, strict=True))) for row in rows
            ]

    def add_note(
        self, request: Request, document_type: str, document_id: UUID, body: NoteCreate
    ) -> NoteRead:
        self.authorize(request, document_type, document_id)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            id = uuid4()
            self.repo.execute(
                "INSERT INTO note(id,org_id,document_type,document_id,author_id,body,visibility) "
                "VALUES(%(id)s,%(org_id)s,%(type)s,%(doc)s,%(actor)s,%(body)s,%(visibility)s)",
                body.model_dump()
                | {"id": id, "type": document_type, "doc": document_id, "actor": self.actor_id},
            )
            self._audit("note.create", "note", id, body.model_dump())
            return self._note(id)

    def edit_note(
        self, request: Request, id: UUID, body: NotePatch, version: str | None
    ) -> NoteRead:
        self.authorize(request, *self.linked_document("note", id))
        if version is None:
            raise ProblemError(
                ErrorCode.BAD_REQUEST, detail="Supply If-Match with the note version."
            )
        try:
            expected = int(version.strip('"'))
        except ValueError:
            raise ProblemError(
                ErrorCode.BAD_REQUEST, detail="If-Match must contain an integer version."
            ) from None
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            before = self._note(id, lock=True)
            if before.author_id != self.actor_id:
                raise ProblemError(ErrorCode.FORBIDDEN)
            if before.locked:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="This note is locked as rationale and cannot be edited.",
                    checks={"problem": "note_locked"},
                )
            if before.version != expected:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="The note changed. Reload it before editing.",
                    checks={"problem": "stale_version"},
                )
            if any(value is None for value in body.model_dump(exclude_unset=True).values()):
                raise ProblemError(
                    ErrorCode.VALIDATION_FAILED, detail="Body and visibility cannot be null."
                )
            self.repo.execute(
                "INSERT INTO note_revision(org_id,note_id,body,visibility,edited_by) "
                "VALUES(%(org_id)s,%(id)s,%(body)s,%(visibility)s,%(actor)s)",
                {
                    "id": id,
                    "body": before.body,
                    "visibility": before.visibility,
                    "actor": self.actor_id,
                },
            )
            self.repo.execute(
                "UPDATE note SET body=%(body)s,visibility=%(visibility)s,version=version+1,"
                "edited_at=now() WHERE org_id=%(org_id)s AND id=%(id)s",
                {
                    "id": id,
                    "body": body.body if body.body is not None else before.body,
                    "visibility": body.visibility or before.visibility,
                },
            )
            self._audit("note.edit", "note", id, body.model_dump(exclude_unset=True))
            return self._note(id)

    def revisions(self, request: Request, id: UUID) -> list[NoteRevisionRead]:
        self.authorize(request, *self.linked_document("note", id))
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rows = self.repo.execute(
                "SELECT id,body,visibility,edited_by,edited_at FROM note_revision "
                "WHERE org_id=%(org_id)s AND note_id=%(id)s ORDER BY id",
                {"id": id},
            ).fetchall()
            return [
                NoteRevisionRead.model_validate(
                    dict(zip(NoteRevisionRead.model_fields, row, strict=True))
                )
                for row in rows
            ]

    def _attachment(self, id: UUID, version: int | None = None) -> AttachmentRead:
        row = self.repo.execute(
            ATT_SQL + " WHERE a.org_id=%(org_id)s AND a.id=%(id)s "
            "AND (%(version)s::int IS NULL OR v.version=%(version)s) "
            "ORDER BY v.version DESC LIMIT 1",
            {"id": id, "version": version},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        return AttachmentRead.model_validate(dict(zip(ATT_FIELDS, row, strict=True)))

    def attachments(
        self, request: Request, document_type: str, document_id: UUID
    ) -> list[AttachmentRead]:
        self.authorize(request, document_type, document_id)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            rows = self.repo.execute(
                "SELECT id FROM attachment WHERE org_id=%(org_id)s "
                "AND document_type=%(type)s AND document_id=%(doc)s "
                "ORDER BY created_at DESC,id",
                {"type": document_type, "doc": document_id},
            ).fetchall()
            return [self._attachment(cast(UUID, row[0])) for row in rows]

    def upload(
        self,
        request: Request,
        document_type: str,
        document_id: UUID,
        upload: MultipartUpload,
        id: UUID | None = None,
    ) -> AttachmentRead:
        self.authorize(request, document_type, document_id, write=True)
        result = self.scanner.scan(upload.data, filename=upload.file_name)
        if result.verdict == "rejected":
            # A failed POST rolls back the idempotency transaction. Retain the refusal
            # on a separate tenant transaction, without any attachment rows or object.
            with psycopg.connect(
                self.connection.info.dsn, password=self.connection.info.password
            ) as audit_connection:
                audit_service = CollaborationService(audit_connection, self.scope, self.actor_id)
                with tenant_transaction(cast(RlsSession, audit_connection), self.scope):
                    audit_service._audit(
                        "attachment.refused",
                        "attachment",
                        id or document_id,
                        {
                            "name": upload.file_name,
                            "size": len(upload.data),
                            "sha256": upload.checksum,
                            "reason": result.reason,
                        },
                        refused=True,
                    )
            raise ProblemError(
                ErrorCode.VALIDATION_FAILED,
                detail=result.reason,
                checks={"problem": "unsupported_file"},
            )
        assert result.media_type is not None and self.storage is not None
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            if id is not None:
                row = self.repo.execute(
                    "SELECT deleted_at FROM attachment WHERE org_id=%(org_id)s "
                    "AND id=%(id)s FOR UPDATE",
                    {"id": id},
                ).fetchone()
                if row is None or row[0] is not None:
                    raise ProblemError(ErrorCode.NOT_FOUND)
                version = self._attachment(id).version + 1
            else:
                id, version = uuid4(), 1
            key = opaque_storage_key()
            # ponytail: rollback can leave an orphan; add a sweep when retention is decided.
            self.storage.put(key, upload.data, content_type=result.media_type)
            if version == 1:
                self.repo.execute(
                    "INSERT INTO attachment(id,org_id,document_type,document_id,created_by) "
                    "VALUES(%(id)s,%(org_id)s,%(type)s,%(doc)s,%(actor)s)",
                    {"id": id, "type": document_type, "doc": document_id, "actor": self.actor_id},
                )
            self.repo.execute(
                "INSERT INTO attachment_version(id,org_id,attachment_id,version,original_filename,"
                "display_name,storage_key,media_type,size_bytes,sha256,scan_status,uploaded_by) "
                "VALUES(%(vid)s,%(org_id)s,%(id)s,%(version)s,%(name)s,%(display)s,%(key)s,"
                "%(media)s,%(size)s,%(sha)s,'basic_checks_passed',%(actor)s)",
                {
                    "vid": uuid4(),
                    "id": id,
                    "version": version,
                    "name": upload.file_name,
                    "display": safe_name(upload.file_name),
                    "key": key,
                    "media": result.media_type,
                    "size": len(upload.data),
                    "sha": upload.checksum,
                    "actor": self.actor_id,
                },
            )
            self._audit(
                "attachment.upload",
                "attachment",
                id,
                {"version": version, "size": len(upload.data)},
            )
            return self._attachment(id)

    def upload_version(self, request: Request, id: UUID, upload: MultipartUpload) -> AttachmentRead:
        return self.upload(request, *self.linked_document("attachment", id), upload, id)

    def content(
        self, request: Request, id: UUID, version: int | None
    ) -> tuple[AttachmentRead, bytes]:
        self.authorize(request, *self.linked_document("attachment", id))
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self._attachment(id, version)
            if row.deleted_at is not None:
                raise ProblemError(ErrorCode.NOT_FOUND)
            if row.scan_status not in {"basic_checks_passed", "clean"}:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=f"This file is {row.scan_status} and cannot be opened. "
                    "Ask an administrator for help.",
                    checks={"problem": "file_unavailable"},
                )
            key = self.repo.execute(
                "SELECT storage_key FROM attachment_version WHERE org_id=%(org_id)s "
                "AND attachment_id=%(id)s AND version=%(version)s",
                {"id": id, "version": row.version},
            ).fetchone()
            assert key is not None and self.storage is not None
            # ponytail: 10 MiB fits in memory; use streaming Storage.get when the cap rises.
            data = self.storage.get(cast(str, key[0]))
            self._audit(
                "attachment.download",
                "attachment",
                id,
                {"version": row.version, "size": row.size_bytes},
            )
            return row, data

    def delete(self, request: Request, id: UUID) -> None:
        self.authorize(request, *self.linked_document("attachment", id), write=True)
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            row = self.repo.execute(
                "UPDATE attachment SET deleted_at=now(),deleted_by=%(actor)s "
                "WHERE org_id=%(org_id)s AND id=%(id)s AND deleted_at IS NULL RETURNING id",
                {"id": id, "actor": self.actor_id},
            ).fetchone()
            if row is not None:
                # ponytail: retain all objects; add a purge job after a retention policy is decided.
                self._audit("attachment.delete", "attachment", id, {})


def lock_note(
    connection: psycopg.Connection[tuple[object, ...]], scope: Scope, note_id: UUID
) -> None:
    with tenant_transaction(cast(RlsSession, connection), scope):
        repo = CollaborationRepository(connection, scope)
        row = repo.execute(
            "UPDATE note SET locked=true WHERE org_id=%(org_id)s AND id=%(id)s RETURNING id",
            {"id": note_id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
