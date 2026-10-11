"""Documented M1 risk defaults, with decimal budget facts and bounded evidence."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, cast
from uuid import UUID

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.rls import RlsSession, tenant_transaction
from flo.modules.projects.models import ProjectRepository
from flo.modules.projects.schemas import RiskFact, RiskIndicator, RiskIndicatorsRead


def today() -> date:
    return date.today()


def budget_indicator(allocated: Decimal, consumed: Decimal) -> RiskIndicator:
    level: Literal["low", "medium", "high", "unknown"] = "low"
    if allocated != 0:
        if consumed * 100 >= allocated * 95:
            level = "high"
        elif consumed * 100 >= allocated * 80:
            level = "medium"
    facts = [
        RiskFact(label="allocated", value=str(allocated)),
        RiskFact(label="consumed", value=str(consumed)),
        RiskFact(
            label="percent",
            value=str((consumed * 100 / allocated).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
            if allocated
            else "No budget allocated",
        ),
    ]
    return RiskIndicator(code="budget_variance", level=level, facts=facts)


def recorded_level(score: int) -> Literal["low", "medium", "high"]:
    return "high" if score >= 15 else "medium" if score >= 8 else "low"


def risk_indicators(repo: ProjectRepository, project_id: UUID) -> RiskIndicatorsRead:
    with tenant_transaction(cast(RlsSession, repo.connection), repo.scope):
        if repo.get(project_id) is None:
            raise ProblemError(ErrorCode.NOT_FOUND)
        balance = repo.execute(
            "SELECT allocated, reserved+committed+actual FROM project_balance "
            "WHERE org_id=%(org_id)s AND project_id=%(id)s",
            {"id": project_id},
        ).fetchone()
        budget = (
            budget_indicator(cast(Decimal, balance[0]), cast(Decimal, balance[1]))
            if balance
            else budget_indicator(Decimal(0), Decimal(0))
        )
        milestones = repo.execute(
            "SELECT name, %(today)s::date-due_date FROM project_milestone "
            "WHERE org_id=%(org_id)s AND project_id=%(id)s AND completed_on IS NULL "
            "AND due_date < %(today)s ORDER BY due_date, id LIMIT 10",
            {"id": project_id, "today": today()},
        ).fetchall()
        schedule = RiskIndicator(
            code="schedule",
            level="high"
            if milestones and cast(int, milestones[0][1]) > 14
            else "medium"
            if milestones
            else "low",
            facts=[RiskFact(label=str(r[0]), value=f"{r[1]} days late") for r in milestones]
            or [RiskFact(label="Schedule", value="No overdue milestones")],
        )
        risks = repo.execute(
            "SELECT title, score, due_date FROM project_risk WHERE org_id=%(org_id)s "
            "AND project_id=%(id)s AND status IN ('open','mitigating') "
            "ORDER BY score DESC, id LIMIT 3",
            {"id": project_id},
        ).fetchall()
        score = cast(int, risks[0][1]) if risks else 0
        recorded = RiskIndicator(
            code="recorded_risks",
            level=recorded_level(score),
            facts=[
                RiskFact(label=str(r[0]), value=f"Score {r[1]}; due {r[2] or 'not set'}")
                for r in risks
            ]
            or [RiskFact(label="Recorded risks", value="No open or mitigating risks")],
        )
        return RiskIndicatorsRead(
            indicators=[
                budget,
                schedule,
                recorded,
                RiskIndicator(
                    code="requirements",
                    level="unknown",
                    facts=[RiskFact(label="Not available", value="Requires requisitions (M2)")],
                ),
            ]
        )
