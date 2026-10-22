"""BUD-007/WF-003: validated requests and atomic, exactly-once posting."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import Outcome, OutcomeContext
from flo.modules.approvals.service import adapter_for, instances_for
from flo.modules.budget.schemas import AllocationCreate
from flo.modules.budget.service import allocate, get_balance, get_request
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from flo.modules.projects.schemas import ProjectCreate
from flo.modules.projects.service import ProjectService
from tests.approvals.test_project_wiring import authenticate, publish
from tests.approvals.test_project_wiring import wiring as wiring
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.authz.conftest import ROOT, load_migration
from tests.isolation.test_real_sessions import client, headers
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db


def payload(**changes):
    return {
        "amount": "100.0000",
        "currency": "USD",
        "effective_date": "2026-10-12",
        "reason": "Authorized budget change",
    } | changes


def request_budget(wiring, transfer=False, allocation=False, project_id=None, **changes):
    db, project, _ = wiring
    project_id = project_id or project.id

    async def run():
        async with client(app) as browser:
            await authenticate(wiring, browser)
            path = (
                "/api/v1/budget/transfers"
                if transfer
                else (
                    f"/api/v1/projects/{project_id}/budget/"
                    + ("allocations" if allocation else "adjustments")
                )
            )
            response = await browser.post(
                path, json=payload(**changes), headers=headers(browser, uuid4().hex)
            )
            return response

    return asyncio.run(run())


def decide_budget(wiring, request, outcome="approve", requester=False, key=None, **extra):
    db = wiring[0]
    current = instances_for(db[0], Scope(db[1]), "budget_change", request.id)[-1]

    async def run():
        async with client(app) as browser:
            await authenticate(
                wiring,
                browser,
                email="admin@example.test" if requester else "approver@example.test",
            )
            path = (
                f"instances/{current.id}/withdraw"
                if outcome == "withdraw"
                else f"tasks/{current.tasks[0].id}/decisions"
            )
            body = (
                {} if outcome == "withdraw" else {"outcome": outcome, "document_version": 1} | extra
            )
            first = await browser.post(
                "/api/v1/approvals/" + path, json=body, headers=headers(browser, key or uuid4().hex)
            )
            if key:
                second = await browser.post(
                    "/api/v1/approvals/" + path, json=body, headers=headers(browser, key)
                )
                assert second.status_code == first.status_code and second.json() == first.json()
            return first

    return asyncio.run(run())


def read(wiring, response):
    assert response.status_code == 202, response.text
    from uuid import UUID

    return get_request(wiring[0][0], Scope(wiring[0][1]), UUID(response.json()["id"]))


def reconcile(wiring):
    from flo.modules.budget.reconcile import reconcile_org, reconciliation_drift

    db = wiring[0]
    run = reconcile_org(db[0], Scope(db[1]))
    assert reconciliation_drift(db[0], Scope(db[1]), run, None, 50).entries == []


def test_no_workflow_refuses_and_writes_nothing(wiring):
    db = wiring[0]
    for response in (request_budget(wiring), request_budget(wiring, allocation=True)):
        assert response.status_code == 409, response.text
        assert response.json()["checks"]["problem"] == "workflow_not_set_up"
        assert "Ask an administrator" in response.json()["detail"]
    assert db[0].execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)
    assert db[0].execute("SELECT count(*) FROM budget_change_request").fetchone() == (0,)


def test_allocation_pending_then_posts_once_and_rejection_posts_nothing(wiring):
    db, project, _ = wiring
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring, allocation=True))
    assert (request.kind, request.status) == ("allocation", "pending")
    assert db[0].execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)
    assert decide_budget(wiring, request, key=uuid4().hex).status_code == 200
    assert db[0].execute("SELECT idempotency_key FROM ledger_entry").fetchall() == [
        (f"bcr:{request.id}",)
    ]
    adapter_for("budget_change").on_outcome(
        db[0],
        Scope(db[1]),
        request.id,
        Outcome.APPROVED,
        OutcomeContext(wiring[2], None, None, lambda p, t: True),
    )
    assert get_balance(db[0], Scope(db[1]), project.id).allocated == Decimal("100")
    rejected = read(wiring, request_budget(wiring, allocation=True))
    refusal = decide_budget(wiring, rejected, "reject", reject_mode="permanent", comment="No")
    assert refusal.status_code == 200
    assert get_request(db[0], Scope(db[1]), rejected.id).status == "rejected"
    assert get_balance(db[0], Scope(db[1]), project.id).allocated == Decimal("100")


def test_adjustment_pending_then_exactly_once_requester_posting(wiring):
    db, project, approver = wiring
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring))
    assert request.status == "pending"
    assert db[0].execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)
    assert db[0].execute(
        "SELECT count(*) FROM audit_log WHERE action='ledger.post'"
    ).fetchone() == (0,)
    assert get_balance(db[0], Scope(db[1]), project.id).allocated == 0
    refused = decide_budget(wiring, request, requester=True)
    assert refused.status_code == 403, refused.text
    approved = decide_budget(wiring, request, key=uuid4().hex)
    assert approved.status_code == 200, approved.text
    assert get_request(db[0], Scope(db[1]), request.id).status == "approved"
    assert db[0].execute("SELECT actor_id,idempotency_key FROM ledger_entry").fetchall() == [
        (db[3], f"bcr:{request.id}")
    ]
    # The posting key remains stable even if the callback is retried independently.
    adapter_for("budget_change").on_outcome(
        db[0],
        Scope(db[1]),
        request.id,
        Outcome.APPROVED,
        OutcomeContext(approver, None, None, lambda p, t: True),
    )
    assert db[0].execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)
    assert get_balance(db[0], Scope(db[1]), project.id).allocated == Decimal("100")
    reconcile(wiring)


@pytest.mark.parametrize(
    "outcome,extra",
    [
        ("reject", {"reject_mode": "permanent", "comment": "Not justified"}),
        ("reject", {"reject_mode": "return_to_draft", "comment": "Revise evidence"}),
        ("return", {"comment": "Needs new request"}),
        ("withdraw", {}),
    ],
)
def test_nonapproval_has_no_ledger_effect(wiring, outcome, extra):
    db = wiring[0]
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring))
    response = decide_budget(wiring, request, outcome, requester=outcome == "withdraw", **extra)
    assert response.status_code == 200, response.text
    assert get_request(db[0], Scope(db[1]), request.id).status == (
        "withdrawn" if outcome == "withdraw" else "rejected"
    )
    assert db[0].execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)
    reconcile(wiring)


def test_transfer_insufficient_at_approval_rolls_back_entire_decision(wiring):
    db, giving, _ = wiring
    receiving = ProjectService(db[0], Scope(db[1]), db[3]).create(
        ProjectCreate(
            bu_id=giving.bu_id,
            name="Receiving",
            department_code="D",
            ledger_account_code="L",
            currency="USD",
        )
    )
    allocate(db[0], Scope(db[1]), giving.id, AllocationCreate(**payload()), db[3], uuid4().hex)
    publish(wiring, "budget_change")
    request = read(
        wiring,
        request_budget(
            wiring, transfer=True, from_project_id=str(giving.id), to_project_id=str(receiving.id)
        ),
    )
    # Spend the giving project's balance after request-time validation.
    from flo.modules.budget.schemas import AdjustmentCreate
    from flo.modules.budget.service import adjust

    adjust(
        db[0],
        Scope(db[1]),
        giving.id,
        AdjustmentCreate(**payload(amount="-100")),
        db[3],
        uuid4().hex,
    )
    before = db[0].execute("SELECT id FROM ledger_entry ORDER BY id").fetchall()
    response = decide_budget(wiring, request)
    assert response.status_code == 409, response.text
    assert response.json()["type"].endswith("/insufficient-budget")
    assert get_request(db[0], Scope(db[1]), request.id).status == "pending"
    current = instances_for(db[0], Scope(db[1]), "budget_change", request.id)[-1]
    assert current.status == "running" and current.tasks[0].status == "open"
    assert db[0].execute("SELECT count(*) FROM approval_decision").fetchone() == (0,)
    assert db[0].execute("SELECT id FROM ledger_entry ORDER BY id").fetchall() == before
    reconcile(wiring)


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": "short"},
        {"amount": 5},
        {"currency": "EUR"},
        {"amount": "0"},
        {"effective_date": "invalid"},
    ],
)
def test_invalid_request_refused_before_approval(wiring, changes):
    db = wiring[0]
    publish(wiring, "budget_change")
    response = request_budget(wiring, **changes)
    assert response.status_code == 422, response.text
    assert db[0].execute("SELECT count(*) FROM budget_change_request").fetchone() == (0,)
    assert db[0].execute("SELECT count(*) FROM approval_instance").fetchone() == (0,)


def test_evidence_validation_at_request_time(wiring):
    db = wiring[0]
    publish(wiring, "budget_change")
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(
        "budget.evidence_required", SettingPut(value=True)
    )
    response = request_budget(wiring)
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["field"] == "evidence_ref"
    assert db[0].execute("SELECT count(*) FROM budget_change_request").fetchone() == (0,)


def test_change_request_foreign_tenant(wiring):
    db, project, _ = wiring
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring))
    with pytest.raises(ProblemError) as exc:
        get_request(db[0], Scope(db[2]), request.id)
    assert exc.value.code == ErrorCode.NOT_FOUND

    async def run():
        async with client(app) as browser:
            await authenticate(wiring, browser, email="second@example.test")
            response = await browser.get(f"/api/v1/budget/change-requests/{request.id}")
            assert response.status_code == 404, response.text
            response = await browser.delete(
                f"/api/v1/budget/change-requests/{request.id}",
                headers=headers(browser, uuid4().hex),
            )
            assert response.status_code == 404, response.text
            response = await browser.post(
                f"/api/v1/projects/{project.id}/budget/adjustments",
                json=payload(),
                headers=headers(browser, uuid4().hex),
            )
            assert response.status_code == 404, response.text
            response = await browser.post(
                "/api/v1/budget/transfers",
                json=payload(from_project_id=str(project.id), to_project_id=str(project.id)),
                headers=headers(browser, uuid4().hex),
            )
            assert response.status_code == 404, response.text

    asyncio.run(run())
    assert get_request(db[0], Scope(db[1]), request.id).status == "pending"


def test_get_request_authorization_and_openapi_202(wiring):
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring))

    async def run():
        async with client(app) as browser:
            await authenticate(wiring, browser)
            response = await browser.get(f"/api/v1/budget/change-requests/{request.id}")
            assert response.status_code == 200 and response.json()["status"] == "pending"

    asyncio.run(run())
    for path in ["/api/v1/projects/{project_id}/budget/adjustments", "/api/v1/budget/transfers"]:
        assert "202" in app.openapi()["paths"][path]["post"]["responses"]


@pytest.mark.parametrize("column,value", [("kind", "bogus"), ("status", "returned")])
def test_request_database_checks_reject_planted_invalid_values(wiring, column, value):
    db = wiring[0]
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring))
    with pytest.raises(psycopg.errors.CheckViolation), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            sql.SQL("UPDATE budget_change_request SET {}=%s WHERE id=%s").format(
                sql.Identifier(column)
            ),
            (value, request.id),
        )


def test_request_unique_key_and_cross_tenant_fk_plants(wiring):
    db = wiring[0]
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring))
    with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "INSERT INTO budget_change_request SELECT %s,org_id,kind,project_id,payload,"
            "amount,currency,requested_by,idempotency_key,status,created_at,decided_at,result "
            "FROM budget_change_request WHERE id=%s",
            (uuid4(), request.id),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation), tenant_transaction(db[0], Scope(db[2])):
        db[0].execute(
            "INSERT INTO budget_change_request(id,org_id,kind,project_id,payload,amount,"
            "currency,requested_by,idempotency_key,status) VALUES(%s,%s,'adjustment',%s,'{}',1,"
            "'USD',%s,%s,'pending')",
            (uuid4(), db[2], wiring[1].id, db[3], uuid4().hex),
        )


def test_migration_down_up_preserves_seeded_ledger(wiring):
    db, project, _ = wiring
    allocate(db[0], Scope(db[1]), project.id, AllocationCreate(**payload()), db[3], uuid4().hex)
    before = db[0].execute("SELECT * FROM ledger_entry ORDER BY id").fetchall()
    balance = get_balance(db[0], Scope(db[1]), project.id)
    migration = load_migration(ROOT / "migrations/20261009_0050_budget_change_requests.py", "bcr")
    with db[0].transaction():
        migration.downgrade(db[0])
        migration.upgrade(db[0])
    assert db[0].execute("SELECT * FROM ledger_entry ORDER BY id").fetchall() == before
    assert get_balance(db[0], Scope(db[1]), project.id) == balance


def test_transfer_approved_posts_balanced_legs_once(wiring):
    db, giving, _ = wiring
    receiving = ProjectService(db[0], Scope(db[1]), db[3]).create(
        ProjectCreate(
            bu_id=giving.bu_id,
            name="Recipient",
            department_code="D",
            ledger_account_code="L",
            currency="USD",
        )
    )
    allocate(db[0], Scope(db[1]), giving.id, AllocationCreate(**payload()), db[3], uuid4().hex)
    publish(wiring, "budget_change")
    request = read(
        wiring,
        request_budget(
            wiring, transfer=True, from_project_id=str(giving.id), to_project_id=str(receiving.id)
        ),
    )
    assert get_balance(db[0], Scope(db[1]), giving.id).available == Decimal("100")
    assert decide_budget(wiring, request, key=uuid4().hex).status_code == 200
    assert get_balance(db[0], Scope(db[1]), giving.id).available == 0
    assert get_balance(db[0], Scope(db[1]), receiving.id).available == Decimal("100")
    assert db[0].execute(
        "SELECT actor_id,idempotency_key FROM ledger_entry WHERE entry_type='transfer' ORDER BY id"
    ).fetchall() == [(db[3], f"bcr:{request.id}:out"), (db[3], f"bcr:{request.id}:in")]
    assert get_request(db[0], Scope(db[1]), request.id).result is not None
    reconcile(wiring)


def test_budget_approve_permission_is_required_for_eligibility(wiring):
    from flo.modules.identity.service import (
        AuthorizationTarget,
        IdentityAuthorizationService,
        role_code,
    )

    db, _, approver = wiring
    publish(wiring, "budget_change")
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    # The only other user loses budget.approve but keeps grants in scope.
    assignment = (
        db[0]
        .execute("SELECT id FROM user_role WHERE user_id=%s AND org_id=%s", (approver, db[1]))
        .fetchone()[0]
    )
    auth.revoke_role(assignment)
    role = auth.seed_baseline_roles()[role_code("project-administrator")]
    auth.grant_role(approver, role.id, AuthorizationTarget.organization(db[1]))
    response = request_budget(wiring)
    assert response.status_code == 409, response.text
    assert response.json()["checks"]["problem"] == "no_eligible_approver"
    assert db[0].execute("SELECT count(*) FROM budget_change_request").fetchone() == (0,)
    assert db[0].execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)


def test_change_request_rls_as_application_role(wiring):
    db = wiring[0]
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring))
    role = "bcr_test_" + uuid4().hex
    conn = db[0]
    conn.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        conn.execute(
            sql.SQL("GRANT SELECT,INSERT,UPDATE ON budget_change_request TO {}").format(
                sql.Identifier(role)
            )
        )
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            with tenant_transaction(conn, Scope(db[2])):
                assert conn.execute("SELECT id FROM budget_change_request").fetchall() == []
                with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                    conn.execute(
                        "INSERT INTO budget_change_request(id,org_id,kind,project_id,payload,"
                        "amount,currency,requested_by,idempotency_key,status) VALUES(%s,%s,"
                        "'adjustment',%s,'{}',1,'USD',%s,%s,'pending')",
                        (uuid4(), db[1], wiring[1].id, db[3], uuid4().hex),
                    )
            with tenant_transaction(conn, Scope(db[1])):
                assert conn.execute("SELECT id FROM budget_change_request").fetchall() == [
                    (request.id,)
                ]
    finally:
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_request_service_replay_and_changed_content_key_guard(wiring):
    from flo.modules.budget.schemas import AdjustmentCreate
    from flo.modules.budget.service import request_change

    db, project, _ = wiring
    publish(wiring, "budget_change")
    key = uuid4().hex
    request = request_change(
        db[0], Scope(db[1]), project.id, AdjustmentCreate(**payload()), db[3], key
    )
    same = request_change(
        db[0], Scope(db[1]), project.id, AdjustmentCreate(**payload()), db[3], key
    )
    assert same.id == request.id
    with pytest.raises(ProblemError) as exc:
        request_change(
            db[0], Scope(db[1]), project.id, AdjustmentCreate(**payload(amount="200")), db[3], key
        )
    assert exc.value.code.value == "idempotency_key_reused"
    with pytest.raises(ProblemError) as other:
        request_change(db[0], Scope(db[1]), project.id, AdjustmentCreate(**payload()), uuid4(), key)
    assert other.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED
    assert db[0].execute("SELECT count(*) FROM budget_change_request").fetchone() == (1,)


def test_decided_request_refuses_a_different_outcome(wiring):
    db, _, approver = wiring
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring))
    assert decide_budget(wiring, request, key=uuid4().hex).status_code == 200
    with pytest.raises(ProblemError) as exc:
        adapter_for("budget_change").on_outcome(
            db[0],
            Scope(db[1]),
            request.id,
            Outcome.REJECTED,
            OutcomeContext(approver, None, None, lambda p, t: True),
        )
    assert exc.value.code == ErrorCode.CONFLICT
    assert get_request(db[0], Scope(db[1]), request.id).status == "approved"


@pytest.mark.parametrize("prefix", ["bcr:", "req:"])
def test_server_reserved_idempotency_prefix_refused(wiring, prefix):
    db, project, _ = wiring

    async def run():
        async with client(app) as browser:
            await authenticate(wiring, browser)
            return await browser.post(
                f"/api/v1/projects/{project.id}/budget/adjustments",
                json=payload(),
                headers=headers(browser, prefix + uuid4().hex),
            )

    assert asyncio.run(run()).status_code == 422


def test_negative_adjustment_routes_by_absolute_amount(wiring):
    db, project, _ = wiring
    allocate(db[0], Scope(db[1]), project.id, AllocationCreate(**payload()), db[3], uuid4().hex)
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring, amount="-50"))
    snapshot = adapter_for("budget_change").snapshot(db[0], Scope(db[1]), request.id)
    assert snapshot.attributes["amount"].amount == request.amount == Decimal("50")
    assert request.payload["amount"] == "-50"
    assert decide_budget(wiring, request).status_code == 200
    assert get_balance(db[0], Scope(db[1]), project.id).allocated == Decimal("50")
    reconcile(wiring)


def test_allocation_kind_migration_down_refuses_then_preserves_requests(wiring):
    db, project, _ = wiring
    publish(wiring, "budget_change")
    adjustment = read(wiring, request_budget(wiring))
    allocation = read(wiring, request_budget(wiring, allocation=True))
    migration = load_migration(
        ROOT / "migrations/20261014_0055_budget_change_allocation_kind.py", "bcr_allocation"
    )
    with pytest.raises(psycopg.errors.RaiseException), db[0].transaction():
        migration.downgrade(db[0])
    db[0].execute("DELETE FROM budget_change_request WHERE id=%s", (allocation.id,))
    with db[0].transaction():
        migration.downgrade(db[0])
        with pytest.raises(psycopg.errors.CheckViolation), db[0].transaction():
            db[0].execute("UPDATE budget_change_request SET kind='allocation'")
        migration.upgrade(db[0])
    assert get_request(db[0], Scope(db[1]), adjustment.id).status == "pending"


def child_under_roll_up(wiring):
    from flo.modules.org.schemas import SettingPut
    from flo.modules.org.service import OrgService

    db, parent, _ = wiring
    child = ProjectService(db[0], Scope(db[1]), db[3]).create(
        ProjectCreate(
            bu_id=parent.bu_id,
            name="Child",
            parent_id=parent.id,
            department_code="D",
            ledger_account_code="L",
            currency="USD",
        )
    )
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(
        "funding_mode", SettingPut(unit_id=parent.bu_id, value="roll_up")
    )
    return child


def test_pending_allocation_locks_funding_mode(wiring):
    from flo.modules.org.schemas import SettingPut
    from flo.modules.org.service import OrgService

    db, parent, _ = wiring
    publish(wiring, "budget_change")
    child = child_under_roll_up(wiring)
    request = read(wiring, request_budget(wiring, allocation=True, project_id=child.id))
    assert request.payload["planned_parent_id"] is None
    with pytest.raises(ProblemError) as locked:
        OrgService(db[0], Scope(db[1]), db[3]).set_setting(
            "funding_mode", SettingPut(unit_id=parent.bu_id, value="roll_down")
        )
    assert locked.value.checks["problem"] == "funding_mode_locked"


def test_approval_refuses_when_funding_plan_changed_after_request(wiring):
    db, parent, approver = wiring
    publish(wiring, "budget_change")
    child = child_under_roll_up(wiring)
    request = read(wiring, request_budget(wiring, allocation=True, project_id=child.id))
    # Bypass the guard: flip the mode in storage, as a pre-lock deployment could have.
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "UPDATE org_setting SET value='\"roll_down\"' WHERE key='funding_mode' AND unit_id=%s",
            (parent.bu_id,),
        )
    with pytest.raises(ProblemError) as refused:
        adapter_for("budget_change").on_outcome(
            db[0],
            Scope(db[1]),
            request.id,
            Outcome.APPROVED,
            OutcomeContext(approver, None, None, lambda p, t: True),
        )
    assert refused.value.checks["problem"] == "funding_plan_changed"
    assert db[0].execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)
    assert get_request(db[0], Scope(db[1]), request.id).status == "pending"


def test_idempotency_key_reuse_across_kinds_is_refused(wiring):
    from flo.modules.budget.schemas import AdjustmentCreate
    from flo.modules.budget.service import request_change

    db, project, _ = wiring
    publish(wiring, "budget_change")
    scope = Scope(db[1])
    first = request_change(db[0], scope, project.id, AllocationCreate(**payload()), db[3], "same")
    assert first.kind == "allocation"
    with pytest.raises(ProblemError) as reused:
        request_change(db[0], scope, project.id, AdjustmentCreate(**payload()), db[3], "same")
    assert reused.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED
    replay = request_change(db[0], scope, project.id, AllocationCreate(**payload()), db[3], "same")
    assert replay.id == first.id


def test_budget_change_request_is_never_deletable(wiring):
    """E22-S02 (DATA-002): no draft status exists; DELETE is 409 not_draft and changes nothing."""
    db, project, _ = wiring
    publish(wiring, "budget_change")
    request = read(wiring, request_budget(wiring))
    decided = read(wiring, request_budget(wiring, allocation=True))
    assert decide_budget(wiring, decided, key=uuid4().hex).status_code == 200

    async def run():
        async with client(app) as browser:
            await authenticate(wiring, browser)
            sent = headers(browser, "k-bcr-delete")
            unkeyed = {k: v for k, v in sent.items() if k != "Idempotency-Key"}
            path = f"/api/v1/budget/change-requests/{request.id}"
            assert (await browser.delete(path, headers=unkeyed)).status_code == 400
            for id in (request.id, decided.id):
                refused = await browser.delete(
                    f"/api/v1/budget/change-requests/{id}", headers=headers(browser, uuid4().hex)
                )
                assert refused.status_code == 409, refused.text
                assert refused.json()["checks"]["problem"] == "not_draft"
            unknown = await browser.delete(
                f"/api/v1/budget/change-requests/{uuid4()}", headers=headers(browser, uuid4().hex)
            )
            assert unknown.status_code == 404

    asyncio.run(run())
    scope = Scope(db[1])
    assert get_request(db[0], scope, request.id).status == "pending"
    assert instances_for(db[0], scope, "budget_change", request.id)[-1].status == "running"
    assert get_request(db[0], scope, decided.id).status == "approved"
    actions = db[0].execute(
        "SELECT action FROM audit_log WHERE target_id=%s", (request.id,)
    ).fetchall()
    assert ("budget_change_request.delete",) not in actions
