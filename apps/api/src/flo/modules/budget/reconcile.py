"""Report ledger drift from a consistent snapshot; never repair posted money."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.jobs import Job
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget.ledger import LedgerRepository
from flo.modules.budget.models import LedgerBucket

logger = logging.getLogger(__name__)


class ReconcileStatus(BaseModel):
    last_run_at: datetime | None
    status: Literal["ok", "drift", "failed"] | None
    drift_rows: int


class DriftRead(BaseModel):
    id: UUID
    run_id: UUID
    project_id: UUID
    bucket: LedgerBucket
    currency: str
    ledger_total: str
    balance_total: str
    difference: str
    detected_at: datetime


class DriftPage(BaseModel):
    entries: list[DriftRead]
    next_cursor: UUID | None


def difference(balance_total: Decimal, ledger_total: Decimal) -> Decimal:
    """Use the same balance-minus-ledger sign as the existing reconciliation read."""
    return balance_total - ledger_total


def _snapshot(repo: LedgerRepository) -> tuple[int, list[tuple[object, ...]]]:
    count = repo.execute("SELECT count(*) FROM project_balance WHERE org_id=%(org_id)s").fetchone()
    assert count is not None
    rows = repo.execute(
        "WITH totals AS (SELECT project_id,bucket,SUM(amount) AS total FROM ledger_entry "
        "WHERE org_id=%(org_id)s GROUP BY project_id,bucket) "
        "SELECT b.project_id,v.bucket,b.currency,COALESCE(t.total,0),v.balance "
        "FROM project_balance b CROSS JOIN LATERAL (VALUES "
        "('allocated'::ledger_bucket,b.allocated),('reserved'::ledger_bucket,b.reserved),"
        "('committed'::ledger_bucket,b.committed),('actual'::ledger_bucket,b.actual)) "
        "v(bucket,balance) LEFT JOIN totals t ON t.project_id=b.project_id AND t.bucket=v.bucket "
        "WHERE b.org_id=%(org_id)s AND COALESCE(t.total,0) <> v.balance"
    ).fetchall()
    return cast(int, count[0]), rows


def reconcile_org(conn: psycopg.Connection[tuple[object, ...]], scope: Scope) -> UUID:
    """Own a top-level writable repeatable-read transaction on a worker connection."""
    run_id, started = uuid4(), datetime.now(UTC)
    drift: list[tuple[object, ...]] = []
    try:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            with tenant_transaction(cast(RlsSession, conn), scope):
                repo = LedgerRepository(conn, scope)
                checked, drift = _snapshot(repo)
                _record_run(repo, run_id, started, checked, len(drift), "drift" if drift else "ok")
                for project, bucket, currency, ledger, balance in drift:
                    repo.execute(
                        "INSERT INTO balance_drift_report "
                        "(id,org_id,run_id,project_id,bucket,currency,ledger_total,"
                        "balance_total,difference) "
                        "VALUES (%(id)s,%(org_id)s,%(run)s,%(project)s,%(bucket)s,%(currency)s,"
                        "%(ledger)s,%(balance)s,%(difference)s)",
                        {
                            "id": uuid4(),
                            "run": run_id,
                            "project": project,
                            "bucket": bucket,
                            "currency": currency,
                            "ledger": ledger,
                            "balance": balance,
                            "difference": difference(cast(Decimal, balance), cast(Decimal, ledger)),
                        },
                    )
    except Exception as exc:
        with tenant_transaction(cast(RlsSession, conn), scope):
            _record_run(
                LedgerRepository(conn, scope), run_id, started, 0, 0, "failed", type(exc).__name__
            )
        return run_id
    if drift:
        largest = max(
            (difference(cast(Decimal, r[4]), cast(Decimal, r[3])) for r in drift), key=abs
        )
        logger.error(
            "business_monitor=unreconciled_balance org_id=%s projects=%s difference=%s",
            scope.org_id,
            len({r[0] for r in drift}),
            format(largest, ".4f"),
            extra={
                "business_monitor": "unreconciled_balance",
                "org_id": str(scope.org_id),
                "projects": len({r[0] for r in drift}),
                "difference": format(largest, ".4f"),
            },
        )
    return run_id


def _record_run(
    repo: LedgerRepository,
    run_id: UUID,
    started: datetime,
    checked: int,
    drift_rows: int,
    status: str,
    error_class: str | None = None,
) -> None:
    repo.execute(
        "INSERT INTO reconcile_run "
        "(id,org_id,started_at,finished_at,projects_checked,drift_rows,status,error_class) "
        "VALUES "
        "(%(id)s,%(org_id)s,%(started)s,%(finished)s,%(checked)s,%(rows)s,%(status)s,%(error)s)",
        {
            "id": run_id,
            "started": started,
            "finished": datetime.now(UTC),
            "checked": checked,
            "rows": drift_rows,
            "status": status,
            "error": error_class,
        },
    )


def reconcile_all(conn: psycopg.Connection[tuple[object, ...]]) -> int:
    """Service-role enumeration contains ids only; all tenant work is scoped."""
    # ponytail: organizations without members are not enumerated; extend the worker
    # tenant registry if memberless organizations need reconciliation.
    with conn.transaction():
        orgs = conn.execute(
            "SELECT DISTINCT org_id FROM identity_membership ORDER BY org_id"
        ).fetchall()
    for row in orgs:
        reconcile_org(conn, Scope(cast(UUID, row[0])))
    return len(orgs)


def job_handler(
    connect: Callable[[], psycopg.Connection[tuple[object, ...]]],
) -> Callable[[Job], None]:
    """Use an independent connection: JobRunner already owns its queue transaction."""

    def handle(job: Job) -> None:
        with connect() as conn:
            reconcile_org(conn, Scope(job.org_id))

    return handle


def reconciliation_status(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope
) -> ReconcileStatus:
    with tenant_transaction(cast(RlsSession, conn), scope):
        row = (
            LedgerRepository(conn, scope)
            .execute(
                "SELECT finished_at,status,drift_rows FROM reconcile_run WHERE org_id=%(org_id)s "
                "ORDER BY started_at DESC,id DESC LIMIT 1"
            )
            .fetchone()
        )
        return ReconcileStatus(
            last_run_at=cast(datetime, row[0]) if row else None,
            status=cast(Literal["ok", "drift", "failed"], row[1]) if row else None,
            drift_rows=cast(int, row[2]) if row else 0,
        )


def reconciliation_drift(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    run_id: UUID,
    cursor: UUID | None,
    page_size: int,
) -> DriftPage:
    with tenant_transaction(cast(RlsSession, conn), scope):
        repo = LedgerRepository(conn, scope)
        if not repo.execute(
            "SELECT id FROM reconcile_run WHERE org_id=%(org_id)s AND id=%(run)s", {"run": run_id}
        ).fetchone():
            raise ProblemError(ErrorCode.NOT_FOUND)
        rows = repo.execute(
            "SELECT id,run_id,project_id,bucket,currency,ledger_total,balance_total,difference,"
            "detected_at FROM balance_drift_report WHERE org_id=%(org_id)s AND run_id=%(run)s "
            "AND (%(cursor)s::uuid IS NULL OR id > %(cursor)s) ORDER BY id LIMIT %(limit)s",
            {"run": run_id, "cursor": cursor, "limit": page_size + 1},
        ).fetchall()
        entries = [
            DriftRead(
                id=cast(UUID, r[0]),
                run_id=cast(UUID, r[1]),
                project_id=cast(UUID, r[2]),
                bucket=LedgerBucket(cast(str, r[3])),
                currency=cast(str, r[4]),
                ledger_total=format(cast(Decimal, r[5]), ".4f"),
                balance_total=format(cast(Decimal, r[6]), ".4f"),
                difference=format(cast(Decimal, r[7]), ".4f"),
                detected_at=cast(datetime, r[8]),
            )
            for r in rows[:page_size]
        ]
        return DriftPage(
            entries=entries, next_cursor=entries[-1].id if len(rows) > page_size else None
        )
