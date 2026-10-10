"""Locked re-planning, atomic writes, and recoverable partial savepoints."""

import logging
from collections.abc import Callable
from copy import deepcopy
from typing import Literal, cast
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.authz import AuthorizationTarget
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.imports.handlers import get_handler
from flo.modules.imports.schemas import ImportBatchRead, ImportReport
from flo.modules.imports.service import ImportRepository
from flo.modules.imports.templates import get_template
from flo.modules.imports.validation import ValidationService, conflict, plan_rows

logger = logging.getLogger(__name__)


def recovery_text(rows: list[int]) -> str:
    shown = [str(number) for number in rows[:20]]
    if len(rows) > 20:
        shown.append(f"{len(rows) - 20} more")
    text = " and ".join(shown) if len(shown) <= 2 else ", ".join(shown[:-1]) + " and " + shown[-1]
    return (
        f"Fix rows {text} in the file and upload it again. "
        "Rows that were committed will be skipped as unchanged."
    )


class CommitService(ValidationService):
    def commit(
        self,
        id: UUID,
        mode: Literal["atomic", "partial"],
        can: Callable[[str, AuthorizationTarget], bool],
    ) -> ImportBatchRead:
        applying = False
        try:
            with tenant_transaction(cast(RlsSession, self.connection), self.scope):
                batch = self.repo.get(id)
                self.repo.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%(lock)s, 0))",
                    {"lock": f"{self.scope.org_id}:{batch.template}"},
                )
                self.repo.execute(
                    "SELECT id FROM import_batch WHERE org_id=%(org_id)s AND id=%(id)s FOR UPDATE",
                    {"id": id},
                )
                batch = self.repo.get(id)
                if batch.status in {"uploaded", "validating"}:
                    raise conflict("not_validated")
                if batch.status not in {"validated", "failed_validation"}:
                    raise conflict("batch_not_editable")
                if mode == "atomic" and batch.status == "failed_validation":
                    raise conflict("has_errors")
                stored = self.repo.execute(
                    "SELECT row_no,raw,action,state_token FROM import_row WHERE "
                    "org_id=%(org_id)s AND batch_id=%(id)s ORDER BY row_no",
                    {"id": id},
                ).fetchall()
                if mode == "partial" and not any(row[2] != "error" for row in stored):
                    raise conflict("nothing_to_commit")
                self.repo.execute(
                    "UPDATE import_batch SET status='committing' WHERE org_id=%(org_id)s "
                    "AND id=%(id)s",
                    {"id": id},
                )
                handler = get_handler(batch.template)
                planned = plan_rows(
                    get_template(batch.template),
                    handler,
                    self.row_context(can),
                    batch.mapping or {},
                    [(cast(int, row[0]), cast(dict[str, str], row[1])) for row in stored],
                )
                if any(
                    (row.plan.action, row.plan.state_token) != (old[2], old[3])
                    for row, old in zip(planned, stored, strict=True)
                ):
                    raise conflict("stale_validation")
                applying = True
                committed: list[int] = []
                skipped: list[int] = []
                unchanged = 0
                context = self.row_context(can)
                for row in planned:
                    if row.plan.action == "error":
                        skipped.append(row.row_no)
                        continue
                    if row.plan.action == "skip":
                        unchanged += 1
                        continue
                    # Restore in-memory apply state as well as SQL when a savepoint fails.
                    scratch = deepcopy(context.scratch)
                    try:
                        with self.connection.transaction():
                            record_type, record_id = handler.apply_row(
                                context, row.plan, row.parsed
                            )
                            self.repo.execute(
                                "INSERT INTO "
                                "import_result(org_id,batch_id,row_no,record_type,record_id) "
                                "VALUES(%(org_id)s,%(id)s,%(row)s,%(type)s,%(record)s)",
                                {
                                    "id": id,
                                    "row": row.row_no,
                                    "type": record_type,
                                    "record": record_id,
                                },
                            )
                    except Exception:
                        if mode == "atomic":
                            raise
                        context.scratch = scratch
                        skipped.append(row.row_no)
                        logger.warning(
                            "import row rolled back",
                            extra={"batch_id": str(id), "row_no": row.row_no},
                        )
                    else:
                        committed.append(row.row_no)
                report = ImportReport(
                    mode=mode,
                    committed=len(committed),
                    skipped_errors=len(skipped),
                    unchanged=unchanged,
                    committed_row_numbers=committed,
                    skipped_row_numbers=skipped,
                    recovery=recovery_text(skipped) if mode == "partial" and skipped else None,
                )
                self.repo.execute(
                    "UPDATE import_batch SET "
                    "status='committed',committed_at=now(),result=%(result)s "
                    "WHERE org_id=%(org_id)s AND id=%(id)s",
                    {"id": id, "result": Jsonb(report.model_dump())},
                )
                self._audit(
                    "import.commit",
                    id,
                    {
                        "committed": len(committed),
                        "skipped_errors": len(skipped),
                        "unchanged": unchanged,
                    },
                )
                return self.repo.get(id)
        except Exception:
            if applying and mode == "atomic":
                # The request's idempotency transaction rolls back on 500; persist only
                # the safe failure status using a separate, short tenant transaction.
                with psycopg.connect(
                    self.connection.info.dsn,
                    password=self.connection.info.password,
                    autocommit=True,
                ) as connection:
                    with tenant_transaction(cast(RlsSession, connection), self.scope):
                        ImportRepository(connection, self.scope).execute(
                            "UPDATE import_batch SET status='failed' "
                            "WHERE org_id=%(org_id)s AND id=%(id)s",
                            {"id": id},
                        )
                logger.error("atomic import rolled back", extra={"batch_id": str(id)})
            raise
