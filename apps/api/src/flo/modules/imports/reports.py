"""Keyset previews and streaming, spreadsheet-safe issue CSV."""

import base64
import csv
import io
import json
from collections.abc import Iterator
from typing import Literal, cast
from uuid import UUID

from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.imports.parsers import invalid
from flo.modules.imports.schemas import ImportIssueRead, ImportPreview, ImportRowRead
from flo.modules.imports.service import ImportService

PreviewKind = Literal["create", "update", "skip", "warning", "error"]


def safe_cell(value: object) -> str:
    text = str(value) if value is not None else ""
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


class ReportService(ImportService):
    def preview(
        self, id: UUID, kind: PreviewKind | None, cursor: str | None, page_size: int
    ) -> ImportPreview:
        after = 0
        if cursor:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor))
                if decoded["batch"] != str(id) or decoded["kind"] != kind:
                    raise ValueError("cursor mismatch")
                after = decoded["row"]
                if type(after) is not int or after < 0:
                    raise ValueError("invalid row")
            except (ValueError, KeyError, TypeError):
                raise invalid("cursor", "Invalid cursor. Restart paging.") from None
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            self.repo.get(id)
            rows = self.repo.execute(
                """SELECT row_no,action,has_warning,preview,issues FROM import_row
                WHERE org_id=%(org_id)s AND batch_id=%(id)s AND row_no > %(after)s
                AND (%(kind)s::text IS NULL OR action=%(kind)s OR
                (%(kind)s='warning' AND has_warning))
                ORDER BY row_no LIMIT %(limit)s""",
                {"id": id, "after": after, "kind": kind, "limit": page_size + 1},
            ).fetchall()
            result = [
                ImportRowRead(
                    row_no=cast(int, row[0]),
                    action=cast(Literal["create", "update", "skip", "error"], row[1]),
                    has_warning=cast(bool, row[2]),
                    record_preview=cast(dict[str, str], row[3]),
                    issues=[
                        ImportIssueRead.model_validate(issue)
                        for issue in cast(list[object], row[4])
                    ],
                )
                for row in rows[:page_size]
            ]
            next_cursor = None
            if len(rows) > page_size:
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps({"batch": str(id), "kind": kind, "row": result[-1].row_no}).encode()
                ).decode()
            return ImportPreview(rows=result, next_cursor=next_cursor)

    def errors_csv(self, id: UUID) -> Iterator[str]:
        # Resolve the batch before starting the streaming response, so 404 is an
        # ordinary problem response. The generator opens its own read transaction.
        self.get(id)
        return self._csv_rows(id)

    def _csv_rows(self, id: UUID) -> Iterator[str]:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["row_no", "column", "code", "message", "original_value"])
        yield output.getvalue()
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            batch = self.repo.get(id)
            rows = self.repo.execute(
                "SELECT row_no,raw,issues FROM import_row WHERE org_id=%(org_id)s AND "
                "batch_id=%(id)s ORDER BY row_no",
                {"id": id},
            )
            for row in rows:
                raw = cast(dict[str, str], row[1])
                for issue in cast(list[dict[str, str]], row[2]):
                    output.seek(0)
                    output.truncate(0)
                    column = issue["column"]
                    source = (batch.mapping or {}).get(column, column)
                    writer.writerow(
                        [
                            safe_cell(value)
                            for value in (
                                row[0],
                                column,
                                issue["code"],
                                issue["message"],
                                raw.get(source, ""),
                            )
                        ]
                    )
                    yield output.getvalue()
