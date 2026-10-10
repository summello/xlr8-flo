"""Tenant-scoped import slices with durable checkpoints and atomic cancellation."""

import time
from collections.abc import Callable
from copy import deepcopy
from typing import cast
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from flo.kernel.authz import AuthorizationTarget, PermissionResolverFactory
from flo.kernel.config import Settings
from flo.kernel.identity import IdentityId
from flo.kernel.jobs.queue import Job, JobConnection, JobQueue
from flo.kernel.jobs.runner import JobHandler, PermanentJobError, TransientJobError
from flo.kernel.ports.storage import Storage
from flo.kernel.storage import create_storage
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.imports.commit import recovery_text
from flo.modules.imports.handlers import get_handler
from flo.modules.imports.parsers import parse_value
from flo.modules.imports.schemas import ImportBatchRead, ImportReport
from flo.modules.imports.service import ImportRepository, ImportService, read_rows
from flo.modules.imports.templates import get_template
from flo.modules.imports.validation import ValidationService, conflict, plan_rows, store_planned

type ConnectionFactory = Callable[[], psycopg.Connection[tuple[object, ...]]]
CHUNK = 500


class Cancelled(Exception):
    """Roll back an atomic write before recording cancellation."""


class ImportWorker:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        resolver_factory: PermissionResolverFactory,
        *,
        storage: Storage | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.factory = connection_factory
        self.resolver_factory = resolver_factory
        self.storage = storage
        self.monotonic = monotonic

    def control(self, job: Job, done: int | None = None) -> bool:
        """Publish progress and read cancellation outside the target transaction."""
        with self.factory() as connection:
            with tenant_transaction(cast(RlsSession, connection), Scope(job.org_id)):
                repo = ImportRepository(connection, Scope(job.org_id))
                if done is not None:
                    repo.execute(
                        "UPDATE import_batch SET "
                        "progress=jsonb_set(progress,'{rows_done}',%(done)s) WHERE "
                        "org_id=%(org_id)s AND id=%(id)s",
                        {"id": UUID(str(job.payload["batch_id"])), "done": Jsonb(done)},
                    )
                row = repo.execute(
                    "SELECT cancel_requested FROM import_batch WHERE "
                    "org_id=%(org_id)s AND id=%(id)s",
                    {"id": UUID(str(job.payload["batch_id"]))},
                ).fetchone()
                return bool(row and row[0])

    def terminal(self, job: Job, status: str, error_class: str | None = None) -> None:
        scope = Scope(job.org_id)
        id = UUID(str(job.payload["batch_id"]))
        with self.factory() as connection:
            with tenant_transaction(cast(RlsSession, connection), scope):
                service = ImportService(connection, scope)
                service.repo.execute(
                    "SELECT id FROM import_batch WHERE org_id=%(org_id)s AND id=%(id)s FOR UPDATE",
                    {"id": id},
                )
                batch = service.repo.get(id)
                service.actor_id = batch.uploader_id
                if batch.status in {"committed", "cancelled", "failed"}:
                    return
                if status == "cancelled" and batch.status == "validating":
                    service.repo.execute(
                        "DELETE FROM import_row WHERE org_id=%(org_id)s AND batch_id=%(id)s",
                        {"id": id},
                    )
                if status == "cancelled" and batch.result and batch.result.mode == "atomic":
                    service.repo.execute(
                        "UPDATE import_batch SET result=%(result)s WHERE "
                        "org_id=%(org_id)s AND id=%(id)s",
                        {
                            "id": id,
                            "result": Jsonb(
                                batch.result.model_copy(
                                    update={"committed": 0, "committed_row_numbers": []}
                                ).model_dump()
                            ),
                        },
                    )
                service.repo.execute(
                    "UPDATE import_batch SET status=%(status)s,error_class=%(error)s "
                    "WHERE org_id=%(org_id)s AND id=%(id)s",
                    {"id": id, "status": status, "error": error_class},
                )
                if status == "failed":
                    service.notify(id, status, batch.status)
                service._audit(f"import.{status}", id, {"error_class": error_class})

    def run(self, job: Job) -> None:
        deadline = self.monotonic() + 20
        scope = Scope(job.org_id)
        id = UUID(str(job.payload["batch_id"]))
        try:
            with self.factory() as connection:
                # Session lock serializes slices but never locks the cancellable batch row.
                connection.autocommit = True
                connection.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s,0))",
                    (f"import-worker:{scope.org_id}:{id}",),
                )
                try:
                    service = ValidationService(connection, scope, storage=self.storage)
                    batch = service.get(id)
                    expected = "validating" if job.kind == "import.validate" else "committing"
                    if batch.status != expected:
                        return
                    service.actor_id = batch.uploader_id
                    with self.resolver_factory(scope) as resolver:

                        def can(permission: str, target: AuthorizationTarget) -> bool:
                            return resolver.check(
                                IdentityId(batch.uploader_id), permission, target
                            ).allowed

                        if job.kind == "import.validate":
                            self.validate(job, service, batch, can, deadline)
                        else:
                            self.commit(job, service, batch, can, deadline)
                finally:
                    connection.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s,0))",
                        (f"import-worker:{scope.org_id}:{id}",),
                    )
        except Cancelled:
            self.terminal(job, "cancelled")
        except (
            psycopg.OperationalError,
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
            TransientJobError,
        ) as error:
            if job.attempts >= job.max_attempts:
                self.terminal(job, "failed", type(error).__name__)
                raise PermanentJobError(type(error).__name__) from None
            raise TransientJobError(type(error).__name__) from None
        except Exception as error:
            self.terminal(job, "failed", type(error).__name__)
            raise PermanentJobError(type(error).__name__) from None

    def continuation(self, job: Job, service: ValidationService) -> None:
        with tenant_transaction(cast(RlsSession, service.connection), service.scope):
            JobQueue(cast(JobConnection, service.connection), service.scope).enqueue(
                job.kind, job.payload, max_attempts=5
            )

    def validate(
        self,
        job: Job,
        service: ValidationService,
        batch: ImportBatchRead,
        can: Callable[[str, AuthorizationTarget], bool],
        deadline: float,
    ) -> None:
        storage = self.storage or create_storage(Settings())
        source = read_rows(storage.get(str(batch.id)), batch.file_name)
        raw = [(n, dict(zip(source[0], row, strict=False))) for n, row in enumerate(source[1:], 2)]
        done = batch.progress.rows_done if batch.progress else 0
        template = get_template(batch.template)
        handler = get_handler(batch.template)
        seen: set[tuple[object, ...]] = set()
        # Rebuild duplicate-key state from prior chunks, using the same canonical parsing.

        columns = {column.name: column for column in template.columns}
        for _, row in raw[:done]:
            try:
                seen.add(
                    tuple(
                        parse_value(
                            columns[key].type, row.get((batch.mapping or {}).get(key, ""), "")
                        )
                        for key in template.key_columns
                    )
                )
            except ValueError:
                pass
        counts = {**batch.counts}
        if done == 0:
            counts.update(dict.fromkeys(("create", "update", "skip", "warning", "error"), 0))
        context = service.row_context(can)
        while done < len(raw):
            if self.control(job):
                raise Cancelled
            chunk = raw[done : done + CHUNK]
            with tenant_transaction(cast(RlsSession, service.connection), service.scope):
                planned = plan_rows(template, handler, context, batch.mapping or {}, chunk, seen)
                store_planned(service.repo, batch.id, planned, counts)
                done += len(chunk)
                service.repo.execute(
                    "UPDATE import_batch SET counts=%(counts)s,progress=%(progress)s "
                    "WHERE org_id=%(org_id)s AND id=%(id)s",
                    {
                        "id": batch.id,
                        "counts": Jsonb({"headers": batch.headers, **counts}),
                        "progress": Jsonb(
                            {
                                "phase": "validating",
                                "rows_done": done,
                                "rows_total": batch.row_count,
                            }
                        ),
                    },
                )
            if self.control(job):
                raise Cancelled
            if done < len(raw) and self.monotonic() >= deadline:
                self.continuation(job, service)
                return
        with tenant_transaction(cast(RlsSession, service.connection), service.scope):
            # Conditional transition prevents a late cancellation from being lost.
            changed = service.repo.execute(
                "UPDATE import_batch SET status=%(status)s,validated_at=now() "
                "WHERE org_id=%(org_id)s AND id=%(id)s AND NOT cancel_requested",
                {"id": batch.id, "status": "failed_validation" if counts["error"] else "validated"},
            )
            if changed.rowcount != 1:
                raise Cancelled

    def apply_chunk(
        self,
        service: ValidationService,
        batch: ImportBatchRead,
        can: Callable[[str, AuthorizationTarget], bool],
        start: int,
        report: ImportReport,
    ) -> int:
        stored = service.repo.execute(
            "SELECT row_no,raw,action,state_token FROM import_row WHERE "
            "org_id=%(org_id)s AND batch_id=%(id)s ORDER BY row_no OFFSET "
            "%(start)s LIMIT %(limit)s",
            {"id": batch.id, "start": start, "limit": CHUNK},
        ).fetchall()
        handler = get_handler(batch.template)
        context = service.row_context(can)
        template = get_template(batch.template)
        columns = {column.name: column for column in template.columns}
        seen: set[tuple[object, ...]] = set()
        # Reconstruct key state across slices before re-planning this chunk.
        previous = service.repo.execute(
            "SELECT raw FROM import_row WHERE org_id=%(org_id)s AND batch_id=%(id)s "
            "AND row_no < %(first)s ORDER BY row_no",
            {"id": batch.id, "first": start + 2},
        ).fetchall()
        for previous_row in previous:
            raw = cast(dict[str, str], previous_row[0])
            try:
                seen.add(
                    tuple(
                        parse_value(
                            columns[key].type, raw.get((batch.mapping or {}).get(key, ""), "")
                        )
                        for key in template.key_columns
                    )
                )
            except ValueError:
                pass
        planned = plan_rows(
            template,
            handler,
            service.row_context(can),
            batch.mapping or {},
            [(cast(int, row[0]), cast(dict[str, str], row[1])) for row in stored],
            seen,
        )
        committed = set(report.committed_row_numbers)
        skipped = set(report.skipped_row_numbers)
        for old, row in zip(stored, planned, strict=True):
            number = cast(int, old[0])
            existing = service.repo.execute(
                "SELECT row_no FROM import_result WHERE org_id=%(org_id)s AND "
                "batch_id=%(id)s AND row_no=%(row)s",
                {"id": batch.id, "row": number},
            ).fetchone()
            if existing is not None:
                committed.add(number)
                continue
            if number in skipped:
                continue
            if (row.plan.action, row.plan.state_token) != (old[2], old[3]):
                raise conflict("stale_validation")
            if row.plan.action == "error":
                skipped.add(number)
                continue
            if row.plan.action == "skip":
                report.unchanged += 1
                continue
            scratch = deepcopy(context.scratch)
            try:
                with service.connection.transaction():
                    record_type, record_id = handler.apply_row(context, row.plan, row.parsed)
                    service.repo.execute(
                        "INSERT INTO "
                        "import_result(org_id,batch_id,row_no,record_type,record_id) "
                        "VALUES(%(org_id)s,%(id)s,%(row)s,%(type)s,%(record)s)",
                        {"id": batch.id, "row": number, "type": record_type, "record": record_id},
                    )
            except (
                psycopg.OperationalError,
                psycopg.errors.SerializationFailure,
                psycopg.errors.DeadlockDetected,
                TransientJobError,
            ):
                raise
            except Exception:
                if report.mode == "atomic":
                    raise
                context.scratch = scratch
                skipped.add(number)
            else:
                committed.add(number)
        report.committed_row_numbers = sorted(committed)
        report.skipped_row_numbers = sorted(skipped)
        report.committed = len(committed)
        report.skipped_errors = len(skipped)
        report.recovery = recovery_text(sorted(skipped)) if skipped else None
        return len(stored)

    def save_report(
        self, service: ValidationService, batch: ImportBatchRead, report: ImportReport, done: int
    ) -> None:
        unchanged = service.repo.execute(
            "SELECT count(*) FROM import_row WHERE org_id=%(org_id)s AND batch_id=%(id)s "
            "AND action='skip' AND row_no <= %(last)s",
            {"id": batch.id, "last": done + 1},
        ).fetchone()
        report.unchanged = cast(int, unchanged[0]) if unchanged else 0
        service.repo.execute(
            "UPDATE import_batch SET result=%(result)s,progress=%(progress)s "
            "WHERE org_id=%(org_id)s AND id=%(id)s",
            {
                "id": batch.id,
                "result": Jsonb(report.model_dump()),
                "progress": Jsonb(
                    {"phase": "committing", "rows_done": done, "rows_total": batch.row_count}
                ),
            },
        )

    def finish(self, service: ValidationService, batch: ImportBatchRead) -> None:
        previous = service.repo.get(batch.id).status
        changed = service.repo.execute(
            "UPDATE import_batch SET status='committed',committed_at=now() "
            "WHERE org_id=%(org_id)s AND id=%(id)s AND NOT cancel_requested",
            {"id": batch.id},
        )
        if changed.rowcount != 1:
            raise Cancelled
        service.notify(batch.id, "committed", previous)
        service._audit("import.commit", batch.id, {})

    def commit(
        self,
        job: Job,
        service: ValidationService,
        batch: ImportBatchRead,
        can: Callable[[str, AuthorizationTarget], bool],
        deadline: float,
    ) -> None:
        if batch.result is None:
            raise RuntimeError("commit mode unavailable")
        report = batch.result.model_copy(deep=True)
        done = batch.progress.rows_done if batch.progress else 0
        if report.mode == "atomic":
            # All target writes and identifiers share one transaction. Progress uses control.
            with tenant_transaction(cast(RlsSession, service.connection), service.scope):
                service.repo.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%(lock)s,0))",
                    {"lock": f"{service.scope.org_id}:{batch.template}"},
                )
                done = 0
                while done < batch.row_count:
                    if self.control(job):
                        raise Cancelled
                    done += self.apply_chunk(service, batch, can, done, report)
                    if self.control(job, done):
                        raise Cancelled
                self.save_report(service, batch, report, done)
                self.finish(service, batch)
            return
        while done < batch.row_count:
            if self.control(job):
                raise Cancelled
            with tenant_transaction(cast(RlsSession, service.connection), service.scope):
                service.repo.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%(lock)s,0))",
                    {"lock": f"{service.scope.org_id}:{batch.template}"},
                )
                done += self.apply_chunk(service, batch, can, done, report)
                self.save_report(service, batch, report, done)
            if self.control(job):
                raise Cancelled
            if done < batch.row_count and self.monotonic() >= deadline:
                self.continuation(job, service)
                return
        with tenant_transaction(cast(RlsSession, service.connection), service.scope):
            self.finish(service, batch)


def handlers(
    connection_factory: ConnectionFactory,
    resolver_factory: PermissionResolverFactory,
    *,
    storage: Storage | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, JobHandler]:
    worker = ImportWorker(
        connection_factory, resolver_factory, storage=storage, monotonic=monotonic
    )
    return {"import.validate": worker.run, "import.commit": worker.run}
