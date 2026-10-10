"""PROJ-021 decimal thresholds, facts, scoped reads and planted ledger drift."""

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest

from flo.api.health import app
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget.service import balance_query
from flo.modules.identity.service import IdentityAuthorizationService
from flo.modules.projects.risk_indicators import budget_indicator, recorded_level
from flo.modules.projects.schemas import MilestoneCreate, RiskCreate
from flo.modules.projects.service import ProjectService, risk_indicators
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db
from tests.projects.test_schedule import schedule_db as schedule_db


@pytest.mark.parametrize(
    "consumed,level",
    [("79", "low"), ("80", "medium"), ("94", "medium"), ("95", "high"), ("0", "low")],
)
def test_budget_threshold_boundaries(consumed, level):
    indicator = budget_indicator(Decimal(100), Decimal(consumed))
    assert indicator.level == level
    assert {f.label: f.value for f in indicator.facts} == {
        "allocated": "100",
        "consumed": consumed,
        "percent": consumed + ".0",
    }


def test_budget_exact_boundary_and_zero_facts():
    # A float ratio rounds this below-threshold value up to 0.80.
    assert (
        budget_indicator(Decimal("99999999999999"), Decimal("79999999999999.1999")).level == "low"
    )
    assert (
        budget_indicator(Decimal("99999999999999"), Decimal("94999999999999.0499")).level
        == "medium"
    )
    assert budget_indicator(Decimal(0), Decimal(0)).facts[-1].value == "No budget allocated"
    assert budget_indicator(Decimal(2000), Decimal("15.79")).facts[-1].value == "0.8"


@pytest.mark.parametrize(
    "days,score,expected_schedule,expected_risk",
    [
        (0, 7, "low", "low"),
        (1, 8, "medium", "medium"),
        (14, 14, "medium", "medium"),
        (15, 15, "high", "high"),
    ],
)
def test_schedule_risk_boundaries_and_requirements_facts(
    schedule_db, monkeypatch, days, score, expected_schedule, expected_risk
):
    conn, org, _, (root, _) = schedule_db
    now = date(2026, 6, 1)
    monkeypatch.setattr("flo.modules.projects.risk_indicators.today", lambda: now)
    with correlation_context("indicator-boundaries"):
        svc = ProjectService(conn, Scope(org), root.owner_id)
        svc.write_milestone(
            root.id, MilestoneCreate(name="Delivery", due_date=now - timedelta(days=days))
        )
        # Scores 7 and 14 are not products of valid likelihood/impact; test score
        # thresholds directly below, and seed the adjacent realizable values here.
        impact, likelihood = (
            (1, 5) if score == 7 else (2, 4) if score == 8 else (3, 4) if score == 14 else (3, 5)
        )
        svc.risks.write(
            root.id,
            RiskCreate(
                title="Delivery risk", impact=impact, likelihood=likelihood, owner_id=root.owner_id
            ),
        )
        data = risk_indicators(conn, Scope(org), root.id)
    assert data.indicators[1].level == expected_schedule
    assert data.indicators[2].level == expected_risk
    assert data.indicators[2].facts[0].label == "Delivery risk"
    assert data.indicators[3].model_dump() == {
        "code": "requirements",
        "level": "unknown",
        "facts": [{"label": "Not available", "value": "Requires requisitions (M2)"}],
    }
    if days:
        assert data.indicators[1].facts[0].value == f"{days} days late"


def test_foreign_dashboard_reads(schedule_db):
    _, _, _, (_, foreign) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            for suffix in ("risk-indicators", "tree", "tree?include=balances"):
                response = await browser.get(f"/api/v1/projects/{foreign.id}/{suffix}")
                assert response.status_code == 404, response.text

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "permissions,status",
    [
        (["project.read"], 403),
        (["ledger.read"], 403),
        (["project.read", "ledger.read"], 200),
        ([], 404),
    ],
)
def test_dashboard_permission_matrix(schedule_db, permissions, status):
    conn, org, _, (root, _) = schedule_db

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            with correlation_context("dashboard-permissions"), tenant_transaction(conn, Scope(org)):
                identity = IdentityAuthorizationService(conn, Scope(org), root.owner_id)
                role = identity.create_role("dashboard-reader", "Dashboard reader")
                for permission in permissions:
                    identity.grant_permission(role.id, permission)
                conn.execute(
                    "UPDATE user_role SET role_id=%s WHERE user_id=%s", (role.id, root.owner_id)
                )
                if not permissions:
                    conn.execute("DELETE FROM user_role WHERE user_id=%s", (root.owner_id,))
            result = await browser.get(f"/api/v1/projects/{root.id}/risk-indicators")
            assert result.status_code == status, result.text
            result = await browser.get(f"/api/v1/projects/{root.id}/tree?include=balances")
            assert result.status_code == status, result.text

    asyncio.run(scenario())


def test_planted_bypassing_ledger_row_reports_drift(schedule_db):
    conn, org, _, (root, _) = schedule_db
    with tenant_transaction(conn, Scope(org)):
        conn.execute(
            "INSERT INTO ledger_entry(org_id,bu_id,project_id,entry_type,bucket,amount,"
            "currency,source_type,actor_id,department_code,ledger_account_code,effective_date,"
            "reason) VALUES (%s,%s,%s,'allocation','allocated',12.3456,'USD','manual',%s,"
            "'D','L',CURRENT_DATE,'Planted bypass')",
            (org, root.bu_id, root.id, root.owner_id),
        )
        result = balance_query(conn, Scope(org), root.id, reconcile=True)
    assert result.difference_by_bucket["allocated"] == "-12.3456"
    assert result.balance.reconciles is False


def test_tree_balances_decimal_payload(schedule_db):
    conn, org, _, (root, _) = schedule_db
    svc = ProjectService(conn, Scope(org), root.owner_id)
    with tenant_transaction(conn, Scope(org)):
        conn.execute(
            "UPDATE project_balance SET allocated=100,reserved=80 WHERE project_id=%s", (root.id,)
        )
    tree = svc.tree(root.id, balances=True)
    assert tree.tree.balance.allocated == "100.0000"
    assert tree.tree.balance.consumed == "80.0000"
    assert tree.tree.consumption_percent == "80.0"
    assert svc.tree(root.id).tree.balance is None


@pytest.mark.parametrize("score,level", [(7, "low"), (8, "medium"), (14, "medium"), (15, "high")])
def test_recorded_score_boundaries(score, level):
    assert recorded_level(score) == level


def test_indicator_evidence_order_bounds_and_closed_exclusion(schedule_db, monkeypatch):
    conn, org, _, (root, _) = schedule_db
    now = date(2026, 6, 1)
    monkeypatch.setattr("flo.modules.projects.risk_indicators.today", lambda: now)
    with correlation_context("bounded-evidence"):
        svc = ProjectService(conn, Scope(org), root.owner_id)
        for days in range(1, 13):
            svc.write_milestone(
                root.id, MilestoneCreate(name=f"Late {days}", due_date=now - timedelta(days=days))
            )
        svc.write_milestone(
            root.id,
            MilestoneCreate(name="Completed", due_date=now - timedelta(days=20), completed_on=now),
        )
        for score in (1, 2, 3, 4, 5):
            svc.risks.write(
                root.id,
                RiskCreate(
                    title=f"Risk {score}",
                    likelihood=score,
                    impact=1,
                    owner_id=root.owner_id,
                    status="mitigating" if score == 5 else "open",
                    due_date=now,
                ),
            )
        svc.risks.write(
            root.id,
            RiskCreate(
                title="Closed",
                likelihood=5,
                impact=5,
                owner_id=root.owner_id,
                status="closed",
                closed_reason="Resolved",
            ),
        )
        data = risk_indicators(conn, Scope(org), root.id)
    assert len(data.indicators[1].facts) == 10
    assert data.indicators[1].facts[0].label == "Late 12"
    assert [f.label for f in data.indicators[2].facts] == ["Risk 5", "Risk 4", "Risk 3"]
    assert all("2026-06-01" in f.value for f in data.indicators[2].facts)
    assert data.indicators[2].level == "low"
