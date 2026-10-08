"""One-snapshot subtree totals, keeping currencies and funding semantics separate."""

from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

import psycopg

from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.budget.funding_policy import mode_for
from flo.modules.budget.ledger import LedgerRepository
from flo.modules.budget.queries import BUCKETS, Period, bounds
from flo.modules.budget.schemas import AggregateBuckets, AggregateRead, CurrencyAggregate
from flo.modules.projects.service import ProjectService


def buckets(values: list[Decimal]) -> AggregateBuckets:
    return AggregateBuckets(
        **{key: format(value, ".4f") for key, value in zip(BUCKETS, values, strict=True)},
        available=format(values[0] - sum(values[1:], Decimal(0)), ".4f"),
    )


def aggregate_balance(
    conn: psycopg.Connection[tuple[object, ...]],
    scope: Scope,
    project_id: UUID,
    period: Period = "life",
    as_of: date | None = None,
    start: date | None = None,
    end: date | None = None,
) -> AggregateRead:
    with tenant_transaction(cast(RlsSession, conn), scope):
        project = ProjectService(conn, scope).get(project_id)
        mode = mode_for(conn, scope, project)
        lower, upper = bounds(conn, scope, period, as_of or date.today(), start, end)
        current = period == "life" and as_of is None
        if current:
            join = "LEFT JOIN project_balance b ON b.project_id=t.id AND b.org_id=%(org_id)s"
            expressions = [f"COALESCE(b.{bucket},0)" for bucket in BUCKETS]
        else:
            join = (
                "LEFT JOIN ledger_entry b ON b.project_id=t.id AND b.org_id=%(org_id)s "
                "AND (%(lower)s::date IS NULL OR b.effective_date >= %(lower)s) "
                "AND (%(upper)s::date IS NULL OR b.effective_date <= %(upper)s)"
            )
            expressions = [
                f"CASE WHEN b.bucket='{bucket}' THEN b.amount ELSE 0 END" for bucket in BUCKETS
            ]
        sums = ", ".join(
            f"COALESCE(SUM({expression}) FILTER (WHERE t.id {comparison} %(id)s),0)"
            for comparison in ("=", "<>")
            for expression in expressions
        )
        rows = (
            LedgerRepository(conn, scope)
            .execute(
                "WITH RECURSIVE tree AS (SELECT id,currency FROM project "
                "WHERE org_id=%(org_id)s AND id=%(id)s UNION ALL "
                "SELECT p.id,p.currency FROM project p JOIN tree t ON p.parent_id=t.id "
                "WHERE p.org_id=%(org_id)s) SELECT t.currency, "
                f"{sums}, (SELECT count(*) FROM tree) FROM tree t {join} "
                "GROUP BY t.currency ORDER BY t.currency",
                {"id": project_id, "lower": lower, "upper": upper},
            )
            .fetchall()
        )
        totals = []
        for row in rows:
            own = [cast(Decimal, n) for n in row[1:5]]
            descendants = [cast(Decimal, n) for n in row[5:9]]
            total = (
                [a + b for a, b in zip(own, descendants, strict=True)] if mode == "roll_up" else own
            )
            totals.append(
                CurrencyAggregate(
                    currency=cast(str, row[0]),
                    own=buckets(own),
                    descendants=buckets(descendants),
                    total=buckets(total),
                )
            )
        return AggregateRead(mode=mode, totals=totals, node_count=cast(int, rows[0][9]))
