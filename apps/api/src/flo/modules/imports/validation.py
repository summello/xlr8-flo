"""One-pass dry-run planning without target writes."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from flo.kernel.authz import AuthorizationTarget
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.imports.handlers import Issue, RowContext, RowPlan, TemplateHandler, get_handler
from flo.modules.imports.parsers import parse_value
from flo.modules.imports.schemas import ImportBatchRead, Template
from flo.modules.imports.service import ImportService, read_rows
from flo.modules.imports.templates import get_template


@dataclass(frozen=True)
class PlannedRow:
    row_no: int
    raw: dict[str, str]
    parsed: dict[str, object]
    plan: RowPlan


def conflict(problem: str) -> ProblemError:
    return ProblemError(
        ErrorCode.CONFLICT,
        detail="The batch cannot be processed in its current state. Re-validate or upload again.",
        checks={"problem": problem},
    )


def plan_rows(
    template: Template,
    handler: TemplateHandler,
    context: RowContext,
    mapping: dict[str, str],
    rows: list[tuple[int, dict[str, str]]],
) -> list[PlannedRow]:
    seen: set[tuple[object, ...]] = set()
    planned = []
    for row_no, raw in rows:
        values: dict[str, object] = {}
        issues = []
        for column in template.columns:
            value = raw.get(mapping.get(column.name, ""), "")
            if value == "":
                if column.required:
                    issues.append(
                        Issue(column.name, "required_cell", f"Required value {value!r} is empty.")
                    )
                continue
            try:
                values[column.name] = parse_value(column.type, value)
            except ValueError:
                issues.append(
                    Issue(
                        column.name,
                        "invalid_value",
                        f"Value {value!r} is not canonical {column.type}.",
                    )
                )
        if template.key_columns and all(key in values for key in template.key_columns):
            key = tuple(values[name] for name in template.key_columns)
            if key in seen:
                issues.append(
                    Issue(
                        template.key_columns[0],
                        "duplicate_key",
                        f"Key {key!r} occurs earlier in this file.",
                    )
                )
            seen.add(key)
        plan = handler.plan_row(context, row_no, values)
        combined = tuple(issues) + plan.issues
        if any(issue.severity == "error" for issue in combined):
            plan = RowPlan("error", combined, plan.preview, None)
        else:
            plan = RowPlan(plan.action, combined, plan.preview, plan.state_token)
        planned.append(PlannedRow(row_no, raw, values, plan))
    return planned


class ValidationService(ImportService):
    def row_context(self, can: Callable[[str, AuthorizationTarget], bool]) -> RowContext:
        if self.actor_id is None:
            raise RuntimeError("actor required for row planning")
        return RowContext(self.connection, self.scope, self.actor_id, can, {})

    def validate(
        self, id: UUID, can: Callable[[str, AuthorizationTarget], bool]
    ) -> ImportBatchRead:
        with tenant_transaction(cast(RlsSession, self.connection), self.scope):
            # Serialize re-validation with other batch commands, including commit.
            self.repo.execute(
                "SELECT id FROM import_batch WHERE org_id=%(org_id)s AND id=%(id)s FOR UPDATE",
                {"id": id},
            )
            batch = self.repo.get(id)
            if batch.status not in {"uploaded", "validated", "failed_validation"}:
                raise conflict("batch_not_editable")
            if batch.row_count > 5000:
                raise ProblemError(
                    ErrorCode.VALIDATION_FAILED,
                    detail=(
                        "This file exceeds 5,000 rows. "
                        "Split it into smaller files and upload again."
                    ),
                    checks={"problem": "file_too_large_for_inline"},
                )
            if self.storage is None:
                raise RuntimeError("storage required for validation")
            source = read_rows(self.storage.get(str(id)), batch.file_name)
            raw = [
                (number, dict(zip(source[0], row, strict=False)))
                for number, row in enumerate(source[1:], 2)
            ]
            self.repo.execute(
                "UPDATE import_batch SET status='validating' WHERE org_id=%(org_id)s AND id=%(id)s",
                {"id": id},
            )
            planned = plan_rows(
                get_template(batch.template),
                get_handler(batch.template),
                self.row_context(can),
                batch.mapping or {},
                raw,
            )
            self.repo.execute(
                "DELETE FROM import_row WHERE org_id=%(org_id)s AND batch_id=%(id)s", {"id": id}
            )
            counts = {
                **batch.counts,
                **dict.fromkeys(("create", "update", "skip", "warning", "error"), 0),
            }
            for row in planned:
                plan = row.plan
                warning = any(issue.severity == "warning" for issue in plan.issues)
                counts[plan.action] += 1
                counts["warning"] += int(warning)
                self.repo.execute(
                    """INSERT INTO import_row(org_id,batch_id,row_no,action,has_warning,raw,
                parsed,preview,issues,state_token)
                    VALUES(%(org_id)s,%(id)s,%(number)s,%(action)s,%(warning)s,%(raw)s,%(parsed)s,%(preview)s,%(issues)s,%(token)s)""",
                    {
                        "id": id,
                        "number": row.row_no,
                        "action": plan.action,
                        "warning": warning,
                        "raw": Jsonb(row.raw),
                        "parsed": Jsonb(
                            {
                                key: str(value) if not isinstance(value, (str, int)) else value
                                for key, value in row.parsed.items()
                            }
                        ),
                        "preview": Jsonb(plan.preview),
                        "issues": Jsonb([asdict(issue) for issue in plan.issues]),
                        "token": plan.state_token,
                    },
                )
            self.repo.execute(
                "UPDATE import_batch SET status=%(status)s, counts=%(counts)s, "
                "validated_at=now() WHERE org_id=%(org_id)s AND id=%(id)s",
                {
                    "id": id,
                    "status": "failed_validation" if counts["error"] else "validated",
                    "counts": Jsonb({"headers": batch.headers, **counts}),
                },
            )
            return self.repo.get(id)
