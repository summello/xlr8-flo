"""Resolve the root's funding policy and plan a single allocation command."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.budget.ledger import LedgerRepository
from flo.modules.projects.schemas import ProjectRead

type FundingMode = Literal["roll_down", "roll_up"]


@dataclass(frozen=True)
class Direct:
    pass


@dataclass(frozen=True)
class FromParent:
    parent_id: UUID


def mode_for(
    conn: psycopg.Connection[tuple[object, ...]], scope: Scope, project: ProjectRead
) -> FundingMode:
    row = (
        LedgerRepository(conn, scope)
        .execute(
            "WITH RECURSIVE ancestors AS ("
            "SELECT u.id, u.parent_id, 0 distance FROM project p "
            "JOIN project root ON root.id=p.root_id AND root.org_id=p.org_id "
            "JOIN org_unit u ON u.id=root.bu_id AND u.org_id=root.org_id "
            "WHERE p.org_id=%(org_id)s AND p.id=%(id)s UNION ALL "
            "SELECT u.id,u.parent_id,a.distance+1 FROM org_unit u JOIN ancestors a "
            "ON u.id=a.parent_id WHERE u.org_id=%(org_id)s), candidates AS ("
            "SELECT s.value,a.distance FROM ancestors a JOIN org_setting s ON s.unit_id=a.id "
            "WHERE s.org_id=%(org_id)s AND s.key='funding_mode' UNION ALL "
            "SELECT value,6 FROM org_setting WHERE org_id=%(org_id)s "
            "AND unit_id IS NULL AND key='funding_mode' UNION ALL SELECT '\"roll_down\"'::jsonb,7) "
            "SELECT value FROM candidates WHERE EXISTS(SELECT 1 FROM ancestors) "
            "ORDER BY distance LIMIT 1",
            {"id": project.id},
        )
        .fetchone()
    )
    if row is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return cast(FundingMode, row[0])


def plan_allocation(
    project: ProjectRead, amount: Decimal, mode: FundingMode
) -> Direct | FromParent:
    if mode == "roll_down" and project.parent_id is not None:
        return FromParent(project.parent_id)
    return Direct()
