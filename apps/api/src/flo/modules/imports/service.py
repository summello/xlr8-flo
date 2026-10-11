"""Scoped, transactional import upload and column mapping."""

import base64
import csv
import io
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.db.repo import ScopedRepo
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.outbox.store import OutboxConnection, OutboxStore
from flo.kernel.ports.storage import Storage
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.identity.service import IdentityAuthorizationConnection, identity_email
from flo.modules.imports.handlers import HANDLERS
from flo.modules.imports.multipart import MAX_FILE_SIZE, Upload
from flo.modules.imports.parsers import invalid, parse_value
from flo.modules.imports.schemas import (
    BatchStatus,
    ImportBatchRead,
    ImportHistory,
    Template,
    TemplateColumnRead,
    TemplateRead,
)
from flo.modules.imports.templates import REGISTRY, get_template
from flo.modules.imports.xlsx_reader import MAX_ROWS, read_xlsx, write_xlsx

# Permit text cells up to the documented upload cap rather than csv's 128 KiB default.
csv.field_size_limit(MAX_FILE_SIZE)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_FIELDS = (
    "id",
    "template",
    "template_version",
    "uploader_id",
    "file_name",
    "file_sha256",
    "file_size",
    "status",
    "mapping",
    "counts",
    "created_at",
    "validated_at",
    "committed_at",
    "result",
    "external_key",
    "progress",
    "error_class",
)


class ImportRepository(ScopedRepo[object]):
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]], scope: Scope) -> None:
        super().__init__(connection, scope)
        self.connection = connection

    def execute(
        self, query: str, params: Mapping[str, object] | None = None
    ) -> psycopg.Cursor[tuple[object, ...]]:
        return self.connection.execute(query, self.scoped_params(params))

    def get(self, id: UUID) -> ImportBatchRead:
        row = self.execute(
            "SELECT "
            + ", ".join(_FIELDS)
            + " FROM import_batch WHERE org_id = %(org_id)s AND id = %(id)s",
            {"id": id},
        ).fetchone()
        if row is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        values = dict(zip(_FIELDS, row, strict=True))
        counts = cast(dict[str, object], values["counts"])
        values["progress"] = values["progress"] or None
        values["headers"] = counts["headers"]
        values["row_count"] = counts["rows"]
        values["counts"] = {key: value for key, value in counts.items() if key != "headers"}
        return ImportBatchRead.model_validate(values)


def read_rows(data: bytes, filename: str) -> list[Sequence[str]]:
    rows: list[Sequence[str]]
    if filename.lower().endswith(".xlsx"):
        rows = read_xlsx(data)
    else:
        try:
            reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
            rows = []
            for row in reader:
                if not rows and len(row) == 1 and row[0].startswith("# XLR8 FLO template "):
                    continue
                rows.append(row)
                if len(rows) > MAX_ROWS + 1:
                    raise invalid("file", "Maximum 50,000 data rows allowed.")
        except (UnicodeDecodeError, csv.Error):
            raise invalid("file", "Invalid UTF-8 CSV structure.") from None
    if not rows or not rows[0] or any(not header.strip() for header in rows[0]):
        raise invalid("headers", "A non-empty header row is required.")
    if len(set(rows[0])) != len(rows[0]):
        raise invalid("headers", "Duplicate source headers are not allowed.")
    width = len(rows[0])
    for number, source_row in enumerate(rows[1:], 2):
        if len(source_row) > width or (
            not filename.lower().endswith(".xlsx") and len(source_row) != width
        ):
            raise invalid(f"rows[{number}]", "Row width must match the header row.")
    return rows


def check_types(template: Template, rows: list[Sequence[str]], mapping: dict[str, str]) -> None:
    columns = {column.name: column for column in template.columns}
    for target, source in mapping.items():
        if target not in columns:
            raise invalid(f"mapping.{target}", "Unknown target column.")
        if source not in rows[0]:
            raise invalid(f"mapping.{target}", "Unknown source header.")
        index = rows[0].index(source)
        for number, row in enumerate(rows[1:], 2):
            value = row[index] if index < len(row) else ""
            if not value:
                continue  # Required cell values belong to E08-S02 row validation.
            if target in getattr(HANDLERS.get(template.name), "parsed_columns", ()):
                continue
            try:
                parse_value(columns[target].type, value)
            except ValueError:
                raise invalid(
                    f"rows[{number}].{source}", f"Expected canonical {columns[target].type}."
                ) from None


def list_templates() -> list[TemplateRead]:
    return [
        TemplateRead(
            name=template.name,
            version=template.version,
            columns=[
                TemplateColumnRead(
                    name=column.name,
                    type=column.type,
                    required=column.required,
                    example=column.example,
                    accepted_codes_url=column.accepted_codes_source,
                )
                for column in template.columns
            ],
        )
        for template in sorted(REGISTRY.values(), key=lambda item: item.name)
    ]


def template_file(name: str, format: str) -> tuple[bytes, str, int]:
    template = get_template(name)
    rows = [
        [column.name for column in template.columns],
        [column.example for column in template.columns],
    ]
    version = f"# XLR8 FLO template {template.name} version={template.version}"
    if format == "xlsx":
        return write_xlsx(rows, version), XLSX_TYPE, template.version
    output = io.StringIO(newline="")
    output.write(version + "\r\n")
    csv.writer(output).writerows(rows)
    return output.getvalue().encode(), "text/csv", template.version


class ImportService:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        scope: Scope,
        actor_id: UUID | None = None,
        storage: Storage | None = None,
    ) -> None:
        self.connection = connection
        self.scope = scope
        self.actor_id = actor_id
        self.storage = storage
        self.repo = ImportRepository(connection, scope)
        self.replayed = False

    def _audit(self, action: str, id: UUID, after: dict[str, object]) -> None:
        AuditWriter(cast(AuditConnection, self.connection), self.scope).write(
            actor=AuditActor(ActorKind.USER, self.actor_id),
            action=action,
            target_type="import_batch",
            target_id=id,
            outcome=Outcome.SUCCESS,
            after_source=after,
            after_fields=tuple(after),
        )

    def notify(self, id: UUID, status: str, previous: str) -> None:
        if previous == status:
            return
        batch = self.repo.get(id)
        address = identity_email(
            cast(IdentityAuthorizationConnection, self.connection), batch.uploader_id
        )
        if address is None:
            raise RuntimeError("uploader identity unavailable")
        event = "finished" if status == "committed" else "failed"
        counts = {
            name: batch.counts.get(name, 0)
            for name in ("create", "update", "skip", "warning", "error")
        }
        counts["committed"] = batch.result.committed if batch.result else 0
        OutboxStore(cast(OutboxConnection, self.connection), self.scope).add_email(
            to=address,
            template=f"import-{event}-v1",
            context={
                "template": batch.template,
                "counts": counts,
                "batch_id": str(id),
                "link": f"https://xlr8flo.summello.com/imports/{id}",
            },
            idempotency_key=f"import:{id}:{event}",
        )

    def get(self, id: UUID) -> ImportBatchRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return self.repo.get(id)

    def history(
        self, status: BatchStatus | None, cursor: str | None, page_size: int
    ) -> ImportHistory:
        params: dict[str, object] = {"status": status, "limit": page_size + 1}
        after = ""
        if cursor is not None:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor))
                if value["status"] != status or value["org"] != str(self.scope.org_id):
                    raise ValueError("cursor mismatch")
                params.update(time=datetime.fromisoformat(value["time"]), id=UUID(value["id"]))
                after = " AND (created_at,id) < (%(time)s,%(id)s)"
            except (ValueError, KeyError, TypeError):
                raise invalid("cursor", "Invalid cursor. Restart paging.") from None
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            ids = self.repo.execute(
                "SELECT id FROM import_batch WHERE org_id=%(org_id)s "
                "AND (%(status)s::text IS NULL OR status=%(status)s)"
                + after
                + " ORDER BY created_at DESC,id DESC LIMIT %(limit)s",
                params,
            ).fetchall()
            rows = [self.repo.get(cast(UUID, row[0])) for row in ids[:page_size]]
            next_cursor = None
            if len(ids) > page_size:
                last = rows[-1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "org": str(self.scope.org_id),
                            "status": status,
                            "time": last.created_at.isoformat(),
                            "id": str(last.id),
                        }
                    ).encode()
                ).decode()
            return ImportHistory(rows=rows, next_cursor=next_cursor)

    def cancel(self, id: UUID) -> ImportBatchRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.repo.get(id)
            updated = self.repo.execute(
                "UPDATE import_batch SET cancel_requested=true WHERE org_id=%(org_id)s "
                "AND id=%(id)s AND status IN ('validating','committing')",
                {"id": id},
            )
            if updated.rowcount != 1:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail="This batch cannot be cancelled in its current state.",
                    checks={"problem": "batch_not_cancellable"},
                )
            self._audit("import.cancel", id, {})
            return self.repo.get(id)

    def upload(self, upload: Upload, external_key: str | None = None) -> ImportBatchRead:
        self.replayed = False
        # Serialize keyed uploads before storage, including concurrent replays.
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            if external_key is not None:
                external_key = external_key.strip()
                if not 1 <= len(external_key) <= 128:
                    raise invalid("external_key", "Use between 1 and 128 characters.")
                self.repo.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%(key)s,0))",
                    {"key": f"import:{self.scope.org_id}:{upload.template}:{external_key}"},
                )
                existing = self.repo.execute(
                    "SELECT id,file_sha256 FROM import_batch WHERE org_id=%(org_id)s "
                    "AND template=%(template)s AND external_key=%(key)s",
                    {"template": upload.template, "key": external_key},
                ).fetchone()
                if existing is not None:
                    if existing[1] != upload.checksum:
                        raise ProblemError(
                            ErrorCode.CONFLICT,
                            detail="This import key was used for a different file. Use a new key.",
                            checks={"problem": "import_key_conflict"},
                        )
                    self.replayed = True
                    return self.repo.get(cast(UUID, existing[0]))
            try:
                return self._upload(upload, external_key)
            except psycopg.errors.UniqueViolation as error:
                # The insert savepoint has rolled back and removed its stored object.
                # A writer outside the advisory-lock convention may have won the index.
                if external_key is None or error.diag.constraint_name != "import_external_key":
                    raise
                return self.upload(upload, external_key)

    def _upload(self, upload: Upload, external_key: str | None) -> ImportBatchRead:
        template = get_template(upload.template)
        filename = upload.file_name.lower()
        if filename.endswith(".xlsm"):
            raise invalid("file", "Macro-enabled workbooks are not allowed.")
        if not (
            (filename.endswith(".csv") and upload.content_type == "text/csv")
            or (filename.endswith(".xlsx") and upload.content_type == XLSX_TYPE)
        ):
            raise ProblemError(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
        rows = read_rows(upload.data, filename)
        mapping = {
            column.name: column.name for column in template.columns if column.name in rows[0]
        }
        check_types(template, rows, mapping)
        id = uuid4()
        if self.storage is None:
            raise RuntimeError("storage is required for upload")
        self.storage.put(str(id), upload.data, content_type=upload.content_type)
        try:
            with tenant_transaction(cast(RlsSession, self.connection), self.scope):
                self.repo.execute(
                    """INSERT INTO import_batch
                    (id, org_id, template, template_version, uploader_id, file_name,
                     file_sha256, file_size, mapping, counts, external_key)
                    VALUES (%(id)s, %(org_id)s, %(template)s, %(version)s, %(actor)s,
                     %(filename)s, %(checksum)s, %(size)s, %(mapping)s, %(counts)s,
                     %(external_key)s)""",
                    {
                        "id": id,
                        "external_key": external_key,
                        "template": template.name,
                        "version": template.version,
                        "actor": self.actor_id,
                        "filename": upload.file_name,
                        "checksum": upload.checksum,
                        "size": len(upload.data),
                        "mapping": Jsonb(mapping),
                        "counts": Jsonb({"headers": list(rows[0]), "rows": len(rows) - 1}),
                    },
                )
                self._audit("import.upload", id, {"file_sha256": upload.checksum})
                return self.repo.get(id)
        except Exception:
            self.storage.delete(str(id))
            raise

    def save_mapping(self, id: UUID, mapping: dict[str, str]) -> ImportBatchRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            batch = self.repo.get(id)
            template = get_template(batch.template)
            for column in template.columns:
                if column.required and column.name not in mapping:
                    raise invalid(f"mapping.{column.name}", "Required target column is missing.")
            if self.storage is None:
                raise RuntimeError("storage is required for mapping")
            rows = read_rows(self.storage.get(str(id)), batch.file_name)
            check_types(template, rows, mapping)
            updated = self.repo.execute(
                "UPDATE import_batch SET mapping = %(mapping)s "
                "WHERE org_id = %(org_id)s AND id = %(id)s AND status = 'uploaded'",
                {"mapping": Jsonb(mapping), "id": id},
            )
            if updated.rowcount == 0:
                raise ProblemError(
                    ErrorCode.CONFLICT,
                    detail=(
                        "This batch is no longer uploaded. Upload a new file to edit its mapping."
                    ),
                    checks={"problem": "batch_not_editable"},
                )
            self._audit("import.mapping", id, {"mapping": mapping})
            return self.repo.get(id)
