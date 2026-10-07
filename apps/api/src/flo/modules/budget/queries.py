"""Scoped ledger aggregation and bounded keyset drill-down reads."""

from calendar import monthrange
from dataclasses import asdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, cast
from uuid import UUID

import psycopg

from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget.ledger import ENTRY_FIELDS, LedgerRepository, invalid, read_entry
from flo.modules.budget.models import LedgerBucket, LedgerType
from flo.modules.budget.schemas import (
    BalanceQueryRead,
    BalanceRange,
    LedgerEntryRead,
    LedgerPage,
    ReconciliationRead,
)
from flo.modules.org.service import range_for
from flo.modules.projects.service import ProjectService

Period = Literal["mtd", "qtd", "ytd", "fiscal_year", "life", "range"]
BUCKETS = ("allocated", "reserved", "committed", "actual")


def consumption_ratio(allocated: Decimal, consumed: Decimal) -> str | None:
    if allocated == 0:
        return None
    return format((consumed / allocated).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def bounds(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    period: Period,
    as_of: date,
    start: date | None,
    end: date | None,
) -> tuple[date | None, date | None]:
    if period == "life":
        return None, as_of
    if period == "range":
        if start is None or end is None or end < start:
            raise invalid("Supply from and to with to on or after from.", field="range")
        # A calendar anniversary handles leap days without a fixed-day approximation.
        limit_year = min(start.year + 20, 9999)
        limit = start.replace(
            year=limit_year, day=min(start.day, monthrange(limit_year, start.month)[1])
        )
        if end > limit:
            raise invalid("Select a range spanning at most 20 years.", field="range")
        return start, end
    return range_for(conn, scope, period, as_of)


def balance_query(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
    period: Period = "life",
    as_of: date | None = None,
    start: date | None = None,
    end: date | None = None,
    *,
    reconcile: bool = False,
) -> BalanceQueryRead | ReconciliationRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        project = ProjectService(conn, scope).get(project_id)
        current_life = period == "life" and as_of is None
        if reconcile and not current_life:
            raise invalid(
                "Reconcile the current life-to-date balance without as_of.",
                "period_not_reconcilable",
            )
        resolved_as_of = as_of or date.today()
        lower, upper = bounds(conn, scope, period, resolved_as_of, start, end)
        if current_life:
            upper = None
        repo = LedgerRepository(conn, scope)
        # One statement gives totals and rollup a single MVCC snapshot during concurrent posts.
        row = repo.execute(
            "SELECT b.allocated, b.reserved, b.committed, b.actual, "
            "COALESCE(SUM(e.amount) FILTER (WHERE e.bucket='allocated'), 0), "
            "COALESCE(SUM(e.amount) FILTER (WHERE e.bucket='reserved'), 0), "
            "COALESCE(SUM(e.amount) FILTER (WHERE e.bucket='committed'), 0), "
            "COALESCE(SUM(e.amount) FILTER (WHERE e.bucket='actual'), 0) "
            "FROM project p LEFT JOIN project_balance b ON b.project_id=p.id AND b.org_id=p.org_id "
            "LEFT JOIN ledger_entry e ON e.project_id=p.id AND e.org_id=p.org_id "
            "AND (%(lower)s::date IS NULL OR e.effective_date >= %(lower)s) "
            "AND (%(upper)s::date IS NULL OR e.effective_date <= %(upper)s) "
            "WHERE p.org_id=%(org_id)s AND p.id=%(project_id)s "
            "GROUP BY b.allocated, b.reserved, b.committed, b.actual",
            {"project_id": project_id, "lower": lower, "upper": upper},
        ).fetchone()
        assert row is not None
        rollup = [cast(Decimal, value) if value is not None else Decimal(0) for value in row[:4]]
        totals = [cast(Decimal, value) for value in row[4:]]
        values = rollup if current_life else totals
        allocated, reserved, committed, actual = values
        consumption = reserved + committed + actual
        available = allocated - consumption
        differences = {
            key: format(a - b, ".4f") for key, a, b in zip(BUCKETS, rollup, totals, strict=True)
        }
        balance = BalanceQueryRead(
            project_id=project_id,
            currency=project.currency,
            as_of=resolved_as_of,
            range=BalanceRange.model_validate({"from": lower, "to": upper}),
            **{key: format(value, ".4f") for key, value in zip(BUCKETS, values, strict=True)},
            available=format(available, ".4f"),
            variance=format(available, ".4f"),
            consumption_pct=consumption_ratio(allocated, consumption),
            reconciles=all(value == "0.0000" for value in differences.values())
            if current_life
            else None,
        )
        if reconcile:
            return ReconciliationRead(
                balance=balance,
                entries_total_by_bucket={
                    key: format(value, ".4f") for key, value in zip(BUCKETS, totals, strict=True)
                },
                difference_by_bucket=differences,
            )
        return balance


def ledger_query(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
    bucket: LedgerBucket | None = None,
    entry_type: LedgerType | None = None,
    start: date | None = None,
    end: date | None = None,
    cursor: str | None = None,
    page_size: int = 50,
) -> LedgerPage:
    if not 1 <= page_size <= 50:
        raise invalid("Choose a page size between 1 and 50.", field="page_size")
    if start is not None and end is not None and end < start:
        raise invalid("Choose to on or after from.", field="to")
    cursor_date, cursor_id = None, None
    if cursor is not None:
        try:
            raw_date, raw_id = cursor.split(":")
            cursor_date, cursor_id = date.fromisoformat(raw_date), int(raw_id)
            if not 1 <= cursor_id <= 9223372036854775807:
                raise ValueError
        except ValueError as exc:
            raise invalid(
                "Supply the next_cursor returned by the previous page.", field="cursor"
            ) from exc
    with tenant_transaction(cast(RlsSession, conn), scope):
        ProjectService(conn, scope).get(project_id)
        rows = (
            LedgerRepository(conn, scope)
            .execute(
                f"SELECT {', '.join(ENTRY_FIELDS)} FROM ledger_entry "
                "WHERE org_id=%(org_id)s AND project_id=%(project_id)s "
                "AND (%(bucket)s::text IS NULL OR bucket::text=%(bucket)s) "
                "AND (%(entry_type)s::text IS NULL OR entry_type::text=%(entry_type)s) "
                "AND (%(start)s::date IS NULL OR effective_date >= %(start)s) "
                "AND (%(end)s::date IS NULL OR effective_date <= %(end)s) "
                "AND (%(cursor_date)s::date IS NULL OR "
                "(effective_date,id) > (%(cursor_date)s,%(cursor_id)s)) "
                "ORDER BY effective_date,id LIMIT %(limit)s",
                {
                    "project_id": project_id,
                    "bucket": bucket,
                    "entry_type": entry_type,
                    "start": start,
                    "end": end,
                    "cursor_date": cursor_date,
                    "cursor_id": cursor_id,
                    "limit": page_size + 1,
                },
            )
            .fetchall()
        )
        entries = [LedgerEntryRead(**asdict(read_entry(row))) for row in rows[:page_size]]
        last = entries[-1] if entries else None
        return LedgerPage(
            entries=entries,
            next_cursor=(
                f"{last.effective_date}:{last.id}" if len(rows) > page_size and last else None
            ),
        )
