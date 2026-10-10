"""Scoped, transactional import upload and column mapping."""

import csv
import io
from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.audit import ActorKind, AuditActor, AuditWriter, Outcome
from flo.kernel.audit.writer import AuditConnection
from flo.kernel.db.repo import ScopedRepo
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.ports.storage import Storage
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.imports.multipart import MAX_FILE_SIZE, Upload
from flo.modules.imports.parsers import invalid, parse_value
from flo.modules.imports.schemas import ImportBatchRead, Template, TemplateColumnRead, TemplateRead
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

    def get(self, id: UUID) -> ImportBatchRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            return self.repo.get(id)

    def upload(self, upload: Upload) -> ImportBatchRead:
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
                     file_sha256, file_size, mapping, counts)
                    VALUES (%(id)s, %(org_id)s, %(template)s, %(version)s, %(actor)s,
                     %(filename)s, %(checksum)s, %(size)s, %(mapping)s, %(counts)s)""",
                    {
                        "id": id,
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
                        "This batch is no longer uploaded. "
                        "Upload a new file to edit its mapping."
                    ),
                    checks={"problem": "batch_not_editable"},
                )
            self._audit("import.mapping", id, {"mapping": mapping})
            return self.repo.get(id)
