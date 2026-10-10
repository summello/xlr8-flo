"""BUD-001/006/007: authorized manual postings and immutable evidence."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from typing import cast
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql

from flo.api.budget import router
from flo.kernel.idempotency import install_idempotency
from flo.kernel.idempotency.store import IdempotencyConnection
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget.schemas import AllocationCreate
from flo.modules.budget.service import allocate, get_balance
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from tests.authz.conftest import ROOT, load_migration
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity, service_for
from tests.budget.test_post_entry import posting_db as posting_db
from tests.isolation.conftest import tenant_database as tenant_database
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import app_for, body, service, settings, unit
from tests.projects.conftest import project_db as project_db

EVIDENCE = load_migration(ROOT / "migrations/20260826_0021_ledger_evidence.py", "evidence")
IDEMPOTENCY = load_migration(ROOT / "migrations/20260825_0003_idempotency.py", "allocation_keys")
HEADERS = load_migration(
    ROOT / "migrations/20260825_0009_idempotency_response_headers.py", "allocation_headers"
)


@pytest.fixture
def allocation_db(posting_db):
    db = posting_db
    EVIDENCE.upgrade(db.connection)
    IDEMPOTENCY.upgrade(db.connection)
    HEADERS.upgrade(db.connection)
    grant_permissions(db, db.actor_id, "budget.allocate", "budget.adjust")
    try:
        yield db
    finally:
        IDEMPOTENCY.downgrade(db.connection)
        EVIDENCE.downgrade(db.connection)


def payload(**changes):
    return (
        dict(
            amount="100.0000",
            currency="USD",
            effective_date="2026-08-26",
            reason="Authorized manual funding",
        )
        | changes
    )


def app(db, viewer=None):
    result = app_for(db, viewer)
    result.include_router(router)

    @contextmanager
    def factory():
        yield cast(IdempotencyConnection, db.connection)

    install_idempotency(result, factory)
    # build_app's session middleware must be outside the idempotency middleware.
    result.user_middleware.insert(0, result.user_middleware.pop(-1))
    from flo.kernel.errors.handler import CorrelationIdMiddleware

    result.add_middleware(CorrelationIdMiddleware)
    return result


def request(db, project, kind="allocations", data=None, viewer=None, key=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app(db, viewer)), base_url="https://testserver"
        ) as client:
            return await client.post(
                f"/api/v1/projects/{project.id}/budget/{kind}",
                json=data or payload(),
                headers={"Idempotency-Key": key or uuid4().hex},
            )

    return asyncio.run(send())


def project(db):
    row = service(db).create(body(unit(db)))
    db.connection.execute("UPDATE project SET status = 'active' WHERE id = %s", (row.id,))
    return row


def test_allocation_adjustment_and_manual_audit(allocation_db):
    db = allocation_db
    row = project(db)
    first = request(db, row)
    assert first.status_code == 201, first.text
    second = request(db, row, "adjustments", payload(amount="-30.0000"))
    assert second.status_code == 201, second.text
    assert Decimal(second.json()["balance"]["allocated"]) == Decimal("70.0000")
    assert db.connection.execute(
        "SELECT bucket, source_type, reason FROM ledger_entry"
    ).fetchall() == [
        ("allocated", "manual", payload()["reason"]),
        ("allocated", "manual", payload()["reason"]),
    ]
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action='ledger.post'"
    ).fetchone() == (2,)


@pytest.mark.parametrize("allowed", [False, True])
def test_adjustment_available_guard(allocation_db, allowed):
    db = allocation_db
    row = project(db)
    request(db, row)
    OrgService(db.connection, Scope(db.org_a), db.actor_id).set_setting(
        "allow_negative_budget", SettingPut(value=allowed)
    )
    result = request(db, row, "adjustments", payload(amount="-101"))
    assert result.status_code == (201 if allowed else 409), result.text
    if not allowed:
        assert result.json()["checks"]["problem"] == "insufficient_budget"
        assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)
    assert get_balance(db.connection, Scope(db.org_a), row.id).allocated == Decimal(
        -1 if allowed else 100
    )


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"reason": None}, "reason"),
        ({"reason": "short"}, "reason"),
        ({"amount": 100}, "amount"),
        ({"amount": "0"}, "amount"),
        ({"amount": "-1"}, "amount"),
        ({"amount": "NaN"}, "amount"),
        ({"reason": "          "}, "reason"),
        ({"evidence_ref": " "}, "evidence_ref"),
    ],
)
def test_request_guards(allocation_db, changes, field):
    row = project(allocation_db)
    result = request(allocation_db, row, data=payload(**changes))
    assert result.status_code == 422, result.text
    assert any(error["field"].endswith(field) for error in result.json()["errors"])
    assert allocation_db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)


def test_missing_reason(allocation_db):
    data = payload()
    del data["reason"]
    result = request(allocation_db, project(allocation_db), data=data)
    assert result.status_code == 422
    assert any(error["field"].endswith("reason") for error in result.json()["errors"])


def test_idempotency_original_response_and_conflict(allocation_db):
    db = allocation_db
    row = project(db)
    first = request(db, row, key="once")
    request(db, row, "adjustments", payload(amount="-30"))
    replay = request(db, row, key="once")
    assert first.status_code == replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    conflict = request(db, row, data=payload(evidence_ref="different"), key="once")
    assert conflict.status_code == 422
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (2,)


def test_evidence_required_and_atomic_failure(allocation_db, monkeypatch):
    db = allocation_db
    row = project(db)
    OrgService(db.connection, Scope(db.org_a), db.actor_id).set_setting(
        "budget.evidence_required", SettingPut(value=True)
    )
    missing = request(db, row)
    assert missing.status_code == 422
    assert missing.json()["errors"][0]["field"] == "evidence_ref"
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)
    present = request(db, row, data=payload(evidence_ref="support/document"))
    assert present.status_code == 201, present.text
    assert db.connection.execute("SELECT ref FROM ledger_evidence").fetchall() == [
        ("support/document",)
    ]
    from flo.modules.budget.ledger import LedgerRepository

    original = LedgerRepository.execute

    def fail(self, query, params=None):
        if query.startswith("INSERT INTO ledger_evidence"):
            raise RuntimeError("planted evidence failure")
        return original(self, query, params)

    monkeypatch.setattr(LedgerRepository, "execute", fail)
    with pytest.raises(RuntimeError, match="planted evidence failure"):
        allocate(
            db.connection,
            Scope(db.org_a),
            row.id,
            AllocationCreate.model_validate(payload(evidence_ref="second")),
            db.actor_id,
            "failed",
        )
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action='ledger.post'"
    ).fetchone() == (1,)


@pytest.mark.parametrize(
    "status", ["draft", "active", "deferred", "completed", "abandoned", "approval_pending"]
)
def test_project_funding_status_guard(allocation_db, status):
    db = allocation_db
    row = project(db)
    db.connection.execute("UPDATE project SET status = %s WHERE id = %s", (status, row.id))
    result = request(db, row)
    assert result.status_code == (201 if status in ("draft", "active") else 409), (
        result.text
    )
    if result.status_code == 409:
        assert result.json()["checks"]["problem"] == "posting_not_allowed"
        assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)


def test_foreign_allocation(allocation_db):
    db = allocation_db
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    assert request(db, foreign).status_code == 404


def test_foreign_adjustment(allocation_db):
    db = allocation_db
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    assert request(db, foreign, "adjustments").status_code == 404


@pytest.mark.parametrize(
    "kind,permission", [("allocations", "budget.allocate"), ("adjustments", "budget.adjust")]
)
def test_authorization_scope_and_permission_guards(allocation_db, kind, permission):
    db = allocation_db
    bu = unit(db)
    row, sibling = service(db).create(body(bu)), service(db).create(body(bu))
    viewer = insert_identity(db, "budget-viewer")
    with service_for(db, Scope(db.org_a), "sibling-grant") as identity:
        role = identity.create_role("sibling", "Sibling")
        identity.grant_permission(role.id, permission)
        identity.grant_role(viewer, role.id, AuthorizationTarget(ScopeType.PROJECT, sibling.id))
    assert request(db, row, kind, viewer=viewer).status_code == 404
    with service_for(db, Scope(db.org_a), "scope-grant") as identity:
        role = identity.create_role("scope", "Scope")
        identity.grant_permission(role.id, "project.read")
        identity.grant_role(viewer, role.id, AuthorizationTarget(ScopeType.BU, bu.id))
    assert request(db, row, kind, viewer=viewer).status_code == 403
    with service_for(db, Scope(db.org_a), "permission-grant") as identity:
        identity.grant_permission(role.id, permission)
    assert request(db, row, kind, viewer=viewer).status_code == 201


def test_two_concurrent_allocations_exact_sum(allocation_db):
    db = allocation_db
    row = project(db)

    def post(i):
        with psycopg.connect(settings(db).database_url.get_secret_value(), autocommit=True) as conn:
            with correlation_context(f"allocate-{i}"):
                return allocate(
                    conn,
                    Scope(db.org_a),
                    row.id,
                    AllocationCreate.model_validate(payload(amount="100.12")),
                    db.actor_id,
                    f"concurrent-{i}",
                )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(post, range(2)))
    assert len(results) == 2
    assert get_balance(db.connection, Scope(db.org_a), row.id).allocated == Decimal("200.24")


def test_evidence_migration_preserves_existing_data(allocation_db):
    db = allocation_db
    row = project(db)
    request(db, row, data=payload(evidence_ref="support"))
    tables = ["project", "ledger_entry", "project_balance", "audit_log"]
    before = {
        table: db.connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in tables
    }
    EVIDENCE.downgrade(db.connection)
    assert db.connection.execute("SELECT to_regclass('ledger_evidence')").fetchone() == (None,)
    EVIDENCE.upgrade(db.connection)
    for table in tables:
        assert (
            db.connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == before[table]
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ledger_evidence SET ref = 'changed'",
        "DELETE FROM ledger_evidence",
        "TRUNCATE ledger_evidence",
    ],
)
def test_evidence_append_only_rejects_real_violations(allocation_db, statement):
    db = allocation_db
    request(db, project(db), data=payload(evidence_ref="support"))
    with pytest.raises(psycopg.Error, match="append.only"):
        db.connection.execute(statement)
    assert db.connection.execute("SELECT ref FROM ledger_evidence").fetchall() == [("support",)]


def test_evidence_foreign_link_and_empty_ref_rejected(allocation_db):
    db = allocation_db
    result = request(db, project(db))
    entry_id = result.json()["entries"][0]["id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.connection.execute(
            "INSERT INTO ledger_evidence VALUES (%s, %s, 'foreign')", (entry_id, db.org_b)
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        db.connection.execute(
            "INSERT INTO ledger_evidence VALUES (%s, %s, ' ')", (entry_id, db.org_a)
        )


def test_evidence_rls_rejects_real_foreign_write(allocation_db, tenant_database):
    db = allocation_db
    result = request(db, project(db), data=payload(evidence_ref="own"))
    own = result.json()["entries"][0]["id"]
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    with correlation_context("foreign-evidence"):
        foreign_result = allocate(
            db.connection,
            Scope(db.org_b),
            foreign.id,
            AllocationCreate.model_validate(payload(evidence_ref="foreign")),
            db.actor_id,
            "foreign",
        )
    role = tenant_database.owner
    db.connection.execute(
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
    )
    db.connection.execute(
        sql.SQL("GRANT SELECT, INSERT ON ledger_evidence TO {}").format(sql.Identifier(role))
    )
    db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
    try:
        with tenant_transaction(db.connection, Scope(db.org_a)):
            assert db.connection.execute("SELECT entry_id FROM ledger_evidence").fetchall() == [
                (own,)
            ]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_transaction(db.connection, Scope(db.org_a)):
                db.connection.execute(
                    "INSERT INTO ledger_evidence VALUES (%s, %s, 'attack')",
                    (foreign_result.entries[0].id, db.org_b),
                )
    finally:
        db.connection.execute("RESET ROLE")
        db.connection.execute(
            sql.SQL("REVOKE ALL ON ledger_evidence FROM {}").format(sql.Identifier(role))
        )
        db.connection.execute(
            sql.SQL("REVOKE USAGE ON SCHEMA public FROM {}").format(sql.Identifier(role))
        )


@pytest.mark.parametrize("kind", ["allocations", "adjustments"])
def test_closed_period_rejects_without_writes(allocation_db, kind):
    from tests.budget.test_post_entry import close_periods

    db = allocation_db
    row = project(db)
    assert request(db, row).status_code == 201
    before = get_balance(db.connection, Scope(db.org_a), row.id)
    close_periods(db)
    result = request(db, row, kind)
    assert result.status_code == 409
    assert result.json()["type"].endswith("/conflict")
    assert result.json()["checks"]["problem"] == "period_closed"
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)
    assert get_balance(db.connection, Scope(db.org_a), row.id) == before


def test_funding_hook_receives_project_and_decimal(allocation_db, monkeypatch):
    from flo.kernel.errors import ErrorCode, ProblemError
    from flo.modules.budget import funding_policy

    db = allocation_db
    row = project(db)
    observed = []

    def reject(project, amount, mode):
        observed.append((project.id, amount))
        raise ProblemError(ErrorCode.CONFLICT, checks={"problem": "parent_insufficient"})

    monkeypatch.setattr(funding_policy, "plan_allocation", reject)
    result = request(db, row)
    assert result.status_code == 409
    assert observed == [(row.id, Decimal("100.0000"))]
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)


@pytest.mark.parametrize(
    "first_ref,second_ref", [(None, "added"), ("existing", None), ("existing", "changed")]
)
def test_service_replay_cannot_change_evidence(allocation_db, first_ref, second_ref):
    from flo.kernel.errors import ErrorCode, ProblemError

    db = allocation_db
    row = project(db)
    first = allocate(
        db.connection,
        Scope(db.org_a),
        row.id,
        AllocationCreate.model_validate(payload(evidence_ref=first_ref)),
        db.actor_id,
        "manual-key",
    )
    replay = allocate(
        db.connection,
        Scope(db.org_a),
        row.id,
        AllocationCreate.model_validate(payload(evidence_ref=first_ref)),
        db.actor_id,
        "manual-key",
    )
    assert replay == first
    with pytest.raises(ProblemError) as failure:
        allocate(
            db.connection,
            Scope(db.org_a),
            row.id,
            AllocationCreate.model_validate(payload(evidence_ref=second_ref)),
            db.actor_id,
            "manual-key",
        )
    assert failure.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)
    assert get_balance(db.connection, Scope(db.org_a), row.id).version == 1


def test_key_is_required_by_middleware(allocation_db):
    db = allocation_db
    row = project(db)

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app(db)), base_url="https://testserver"
        ) as client:
            return await client.post(
                f"/api/v1/projects/{row.id}/budget/allocations", json=payload()
            )

    result = asyncio.run(send())
    assert result.status_code == 400
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)


def test_evidence_setting_rejects_non_boolean(allocation_db):
    from flo.kernel.errors import ErrorCode, ProblemError

    db = allocation_db
    row = project(db)
    org = OrgService(db.connection, Scope(db.org_a), db.actor_id)
    assert org.effective_setting(row.bu_id, "budget.evidence_required").value is False
    with pytest.raises(ProblemError) as failure:
        org.set_setting("budget.evidence_required", SettingPut(value="false"))
    assert failure.value.code == ErrorCode.VALIDATION_FAILED
    assert org.effective_setting(row.bu_id, "budget.evidence_required").value is False
