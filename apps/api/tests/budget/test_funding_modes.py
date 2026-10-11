"""PROJ-006/007/008: root policy, funded children and currency-safe subtree reads."""

import asyncio
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from time import monotonic, sleep
from uuid import uuid4

import httpx
import psycopg
import pytest

from flo.kernel import setting_guards
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.budget import funding_policy
from flo.modules.budget.aggregate import aggregate_balance, buckets
from flo.modules.budget.models import LedgerType
from flo.modules.budget.schemas import AllocationCreate, TransferCreate
from flo.modules.budget.service import allocate, get_balance, post_entry, transfer
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity, service_for
from tests.budget.test_allocation import allocation_db as allocation_db
from tests.budget.test_allocation import app, payload, request
from tests.budget.test_post_entry import posting_db as posting_db
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import body, service, settings, unit
from tests.projects.conftest import project_db as project_db


def org(db):
    return OrgService(db.connection, Scope(db.org_a), db.actor_id)


def tree(db, mode="roll_down"):
    bu = unit(db)
    org(db).set_setting("funding_mode", SettingPut(unit_id=bu.id, value=mode))
    root = service(db).create(body(bu))
    a = service(db).create(body(bu, parent_id=root.id))
    b = service(db).create(body(bu, parent_id=root.id))
    return root, a, b


def aggregate(db, project, viewer=None, **params):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app(db, viewer)), base_url="https://testserver"
        ) as client:
            return await client.get(
                f"/api/v1/projects/{project.id}/balance/aggregate", params=params
            )

    return asyncio.run(send())


def read(db, project, **params):
    return aggregate_balance(db.connection, Scope(db.org_a), project.id, **params)


def balance(db, p):
    return get_balance(db.connection, Scope(db.org_a), p.id)


def call(db, p, amount="40", key=None, conn=None, **changes):
    return allocate(
        conn or db.connection,
        Scope(db.org_a),
        p.id,
        AllocationCreate.model_validate(payload(amount=amount, **changes)),
        db.actor_id,
        key or uuid4().hex,
        permitted_at=lambda _: True,
    )


def test_roll_down_available_and_transfer_pair(allocation_db):
    db = allocation_db
    root, a, b = tree(db)
    assert request(db, root).status_code == 201
    first = request(db, a, data=payload(amount="60"), key="child")
    assert first.status_code == 201, first.text
    entries = first.json()["entries"]
    assert [Decimal(e["amount"]) for e in entries] == [Decimal(-60), Decimal(60)]
    assert entries[0]["transfer_group_id"] == entries[1]["transfer_group_id"]
    assert all(e["source_type"] == "transfer" for e in entries)
    before = [balance(db, p) for p in (root, a, b)]
    org(db).set_setting("allow_negative_budget", SettingPut(value=True))
    result = request(db, b, data=payload(amount="50"))
    assert result.status_code == 409
    assert result.json()["checks"]["problem"] == "insufficient_budget"
    assert result.json()["checks"]["available"] == "40.0000"
    assert [balance(db, p) for p in (root, a, b)] == before
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (3,)
    assert request(db, a, data=payload(amount="60"), key="child").json() == first.json()
    assert request(db, b, data=payload(amount="40")).status_code == 201
    assert balance(db, root).available == 0
    totals = read(db, root)
    assert totals.mode == "roll_down" and totals.node_count == 3
    assert totals.totals[0].total.allocated == "0.0000"
    assert totals.totals[0].descendants.allocated == "100.0000"


def grant(db, viewer, p, permission="budget.allocate"):
    with service_for(db, Scope(db.org_a), uuid4().hex) as identity:
        role = identity.create_role(
            "funding-" + uuid4().hex.translate(str.maketrans("0123456789", "abcdefghij")), "Funding"
        )
        identity.grant_permission(role.id, permission)
        identity.grant_role(viewer, role.id, AuthorizationTarget(ScopeType.PROJECT, p.id))


def test_parent_authorization_and_planted_bypass(allocation_db, monkeypatch):
    db = allocation_db
    root, a, _ = tree(db)
    viewer = insert_identity(db, "child-funder")
    grant(db, viewer, a)
    # Zero balances make the forbidden operation's lack of ledger side effects explicit.
    result = request(db, a, viewer=viewer, data=payload(amount="40"))
    assert result.status_code == 403, result.text
    assert "budget.allocate" in result.json()["detail"] and str(root.id) in result.json()["detail"]
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)
    assert balance(db, root).allocated == balance(db, a).allocated == 0
    assert request(db, root).status_code == 201
    from flo.api import budget

    with monkeypatch.context() as patch:
        patch.setattr(budget, "permits", lambda *args: True)
        # Bypassing the parent check makes our 403 assertion fail.
        violated = request(db, a, viewer=viewer, data=payload(amount="1"))
        with pytest.raises(AssertionError):
            assert violated.status_code == 403
        assert violated.status_code == 201
    grant(db, viewer, root)
    assert request(db, a, viewer=viewer, data=payload(amount="40")).status_code == 201


def test_roll_up_child_only_grant_and_root_precedence(allocation_db):
    db = allocation_db
    org(db).set_setting("funding_mode", SettingPut(value="roll_down"))
    root, a, _ = tree(db, "roll_up")
    other = unit(db, "OTHER")
    org(db).set_setting("funding_mode", SettingPut(unit_id=other.id, value="roll_down"))
    child = service(db).create(body(other, parent_id=a.id))
    viewer = insert_identity(db, "roll-up-funder")
    grant(db, viewer, child)
    result = request(db, child, viewer=viewer, data=payload(amount="30"))
    assert result.status_code == 201, result.text
    assert len(result.json()["entries"]) == 1
    assert balance(db, root).allocated == balance(db, a).allocated == 0
    assert read(db, a).mode == "roll_up"
    assert read(db, root).totals[0].total.allocated == "30.0000"


def test_roll_up_golden_sibling_transfer(allocation_db):
    db = allocation_db
    root, a, b = tree(db, "roll_up")
    for p, amount in ((root, "10"), (a, "30"), (b, "20")):
        call(db, p, amount)
    before = read(db, root)
    transfer(
        db.connection,
        Scope(db.org_a),
        TransferCreate.model_validate(
            payload(amount="5") | dict(from_project_id=str(a.id), to_project_id=str(b.id))
        ),
        db.actor_id,
        "siblings",
    )
    assert read(db, root) == before
    assert before.totals[0].own.allocated == "10.0000"
    assert before.totals[0].total.allocated == "60.0000"
    assert balance(db, a).allocated == 25 and balance(db, b).allocated == 25


@pytest.mark.parametrize("seed", [7, 23, 51])
def test_random_tree_reconciles_every_bucket_to_ledger(allocation_db, seed):
    db = allocation_db
    root, a, b = tree(db, "roll_up")
    rng = random.Random(seed)
    nodes = [root, a, b]
    # Limit parent depth to the project's supported hierarchy ceiling.
    for i in range(12):
        nodes.append(
            service(db).create(body(unit(db, f"BU{i}"), parent_id=rng.choice([root, a, b]).id))
        )
    for p in nodes:
        db.connection.execute("UPDATE project SET status='active' WHERE id=%s", (p.id,))
        call(db, p, str(rng.randrange(100, 200)))
        for kind in (LedgerType.RESERVATION, LedgerType.COMMITMENT, LedgerType.ACTUAL):
            post_entry(
                db.connection,
                Scope(db.org_a),
                project_id=p.id,
                entry_type=kind,
                amount=Decimal(rng.randrange(1, 20)),
                currency=p.currency,
                source_type="system",
                source_id=None,
                effective_date=date(2026, 8, 26),
                actor_id=db.actor_id,
                department_code="D",
                ledger_account_code="L",
                idempotency_key=uuid4().hex,
            )
    for p in nodes:
        rows = db.connection.execute(
            "WITH RECURSIVE t AS (SELECT id FROM project WHERE id=%s UNION ALL "
            "SELECT p.id FROM project p JOIN t ON p.parent_id=t.id) "
            "SELECT bucket,sum(amount) FROM ledger_entry WHERE project_id IN (SELECT id FROM t) "
            "GROUP BY bucket",
            (p.id,),
        ).fetchall()
        expected = dict(rows)
        for result in (
            read(db, p),
            read(db, p, period="range", start=date(2026, 8, 1), end=date(2026, 8, 31)),
        ):
            total = result.totals[0].total
            for bucket in ("allocated", "reserved", "committed", "actual"):
                assert Decimal(getattr(total, bucket)) == expected[bucket]
            assert Decimal(total.available) == expected["allocated"] - sum(
                (expected[k] for k in ("reserved", "committed", "actual")), Decimal(0)
            )


def test_currency_groups_and_roll_down_currency_guard(allocation_db):
    db = allocation_db
    root, a, b = tree(db, "roll_up")
    db.connection.execute("UPDATE project SET currency=%s WHERE id=%s", ("EUR", b.id))
    db.connection.execute(
        "UPDATE project_balance SET currency=%s WHERE project_id=%s", ("EUR", b.id)
    )
    call(db, root, "10")
    call(db, a, "30")
    call(db, b, "20", currency="EUR")
    result = read(db, root)
    assert [(t.currency, t.total.allocated) for t in result.totals] == [
        ("EUR", "20.0000"),
        ("USD", "40.0000"),
    ]
    other = service(db).create(body(unit(db, "DOWN")))
    child = service(db).create(body(unit(db, "DOWNCHILD"), parent_id=other.id))
    call(db, other, "100")
    db.connection.execute("UPDATE project SET currency=%s WHERE id=%s", ("EUR", child.id))
    failed = request(db, child, data=payload(amount="1", currency="EUR"))
    assert failed.status_code == 409
    assert failed.json()["checks"]["problem"] == "currency_mismatch"
    assert balance(db, other).allocated == 100 and balance(db, child).allocated == 0


def test_mode_setting_lock_and_registry_violation(allocation_db, monkeypatch):
    db = allocation_db
    root, a, _ = tree(db)
    org(db).set_setting("funding_mode", SettingPut(value="roll_down"))
    org(db).set_setting("funding_mode", SettingPut(unit_id=root.bu_id, value="roll_up"))
    call(db, a, "10")
    for scope in (None, root.bu_id):
        with pytest.raises(ProblemError) as failure:
            org(db).set_setting(
                "funding_mode",
                SettingPut(unit_id=scope, value="roll_up" if scope is None else "roll_down"),
            )
        assert failure.value.code == ErrorCode.CONFLICT
        assert failure.value.checks["problem"] == "funding_mode_locked"
        with pytest.raises(ProblemError):
            org(db).clear_setting("funding_mode", scope)
    org(db).set_setting("funding_mode", SettingPut(unit_id=root.bu_id, value="roll_up"))
    untouched = unit(db, "EMPTY")
    org(db).set_setting("funding_mode", SettingPut(unit_id=untouched.id, value="roll_up"))
    with monkeypatch.context() as patch:
        patch.setattr(setting_guards, "_GUARDS", {})

        def denied():
            try:
                org(db).set_setting(
                    "funding_mode", SettingPut(unit_id=root.bu_id, value="roll_down")
                )
            except ProblemError:
                return True
            return False

        with pytest.raises(AssertionError):
            assert denied()


def test_concurrent_child_allocations_last_forty(allocation_db):
    db = allocation_db
    root, a, b = tree(db)
    call(db, root, "40")
    barrier = Barrier(2)

    def run(p):
        with psycopg.connect(settings(db).database_url.get_secret_value(), autocommit=True) as conn:
            conn.execute("SET lock_timeout='5s'")
            with correlation_context(str(p.id)):
                barrier.wait(timeout=5)
                try:
                    return call(db, p, conn=conn)
                except ProblemError as error:
                    return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [a, b]))
    errors = [r for r in results if isinstance(r, ProblemError)]
    assert len(errors) == 1 and errors[0].code == ErrorCode.INSUFFICIENT_BUDGET
    assert balance(db, root).available == 0
    assert balance(db, a).allocated + balance(db, b).allocated == 40
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (3,)


def test_first_allocation_serializes_with_funding_mode_change(allocation_db):
    db = allocation_db
    for attempt in range(20):
        bu = unit(db, f"RACE{attempt}")
        org(db).set_setting("funding_mode", SettingPut(unit_id=bu.id, value="roll_up"))
        root = service(db).create(body(bu))
        child = service(db).create(body(bu, parent_id=root.id))
        with psycopg.connect(
            settings(db).database_url.get_secret_value(), autocommit=True
        ) as allocation_conn, ThreadPoolExecutor(max_workers=1) as pool:
            allocation_conn.execute("SET lock_timeout='5s'")

            def fund():
                with correlation_context(f"funding-mode-race-{attempt}"):
                    try:
                        return call(db, child, conn=allocation_conn)
                    except ProblemError as error:
                        return error

            with db.connection.transaction():
                # The guard accepted the change, but its organization lock and
                # setting write remain uncommitted while the first funding starts.
                org(db).set_setting(
                    "funding_mode", SettingPut(unit_id=bu.id, value="roll_down")
                )
                pending = pool.submit(fund)
                deadline = monotonic() + 3
                blocked = False
                while monotonic() < deadline and not pending.done():
                    db.connection.execute("SELECT pg_stat_clear_snapshot()")
                    blocked = db.connection.execute(
                        "SELECT pg_backend_pid() = ANY(pg_blocking_pids(%s)) "
                        "AND query LIKE 'SELECT id FROM organization%%FOR UPDATE' "
                        "FROM pg_stat_activity WHERE pid=%s",
                        (allocation_conn.info.backend_pid, allocation_conn.info.backend_pid),
                    ).fetchone() == (True,)
                    if blocked:
                        break
                    sleep(0.01)
                assert blocked, "First allocation did not wait for the organization lock"
            # Setting-first is the forced serial order: the child now needs funds
            # from its empty parent, rather than a direct roll-up allocation.
            result = pending.result(timeout=5)
        assert isinstance(result, ProblemError)
        assert result.code == ErrorCode.INSUFFICIENT_BUDGET
        assert read(db, root).mode == "roll_down"
        assert balance(db, root).allocated == balance(db, child).allocated == 0
        assert db.connection.execute(
            "SELECT count(*) FROM ledger_entry WHERE project_id IN (%s, %s)",
            (root.id, child.id),
        ).fetchone() == (0,)
        call(db, root)
        with pytest.raises(ProblemError) as failure:
            org(db).set_setting("funding_mode", SettingPut(unit_id=bu.id, value="roll_up"))
        assert failure.value.code == ErrorCode.CONFLICT
        assert failure.value.checks["problem"] == "funding_mode_locked"
        assert read(db, root).mode == "roll_down"


def test_foreign_aggregate(allocation_db):
    db = allocation_db
    grant_permissions(db, db.actor_id, "ledger.read")
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    assert aggregate(db, foreign).status_code == 404


def test_aggregate_authorization_and_periods(allocation_db):
    db = allocation_db
    root, a, _ = tree(db, "roll_up")
    viewer = insert_identity(db, "aggregate-viewer")
    assert aggregate(db, root, viewer=viewer).status_code == 404
    grant(db, viewer, root, "project.read")
    assert aggregate(db, root, viewer=viewer).status_code == 403
    grant(db, viewer, root, "ledger.read")
    call(db, a, "30")
    assert aggregate(db, root, viewer=viewer).json()["totals"][0]["total"]["allocated"] == "30.0000"
    assert aggregate(db, root, viewer=viewer, period="range").status_code == 422
    assert (
        aggregate(db, root, viewer=viewer, period="life", as_of="2026-08-25").json()["totals"][0][
            "total"
        ]["allocated"]
        == "0.0000"
    )


def test_decimal_bucket_calculation_and_plan(allocation_db):
    root, a, _ = tree(allocation_db)
    assert isinstance(
        funding_policy.plan_allocation(root, Decimal("1"), "roll_down"), funding_policy.Direct
    )
    assert funding_policy.plan_allocation(
        a, Decimal("1"), "roll_down"
    ) == funding_policy.FromParent(root.id)
    assert isinstance(
        funding_policy.plan_allocation(a, Decimal("1"), "roll_up"), funding_policy.Direct
    )
    assert (
        buckets([Decimal("1.01"), Decimal(".10"), Decimal(".20"), Decimal(".30")]).available
        == "0.4100"
    )


def test_roll_down_atomic_failure_evidence_and_service_replay(allocation_db, monkeypatch):
    from flo.modules.budget import service as budget_service

    db = allocation_db
    root, a, _ = tree(db)
    call(db, root, "100")
    before = [balance(db, p) for p in (root, a)]
    original = budget_service.post_entry

    def fail(*args, **kwargs):
        if kwargs["idempotency_key"].endswith(":in"):
            raise RuntimeError("planted child leg failure")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(budget_service, "post_entry", fail)
        with pytest.raises(RuntimeError, match="planted child leg failure"):
            call(db, a, key="failed", evidence_ref="support")
    assert [balance(db, p) for p in (root, a)] == before
    assert db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (1,)
    assert db.connection.execute("SELECT count(*) FROM ledger_evidence").fetchone() == (0,)
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action='ledger.post'"
    ).fetchone() == (1,)
    org(db).set_setting("budget.evidence_required", SettingPut(value=True))
    assert request(db, a, data=payload(amount="40")).status_code == 422
    first = call(db, a, key="fund", evidence_ref="support")
    assert call(db, a, key="fund", evidence_ref="support") == first
    assert db.connection.execute(
        "SELECT ref FROM ledger_evidence ORDER BY entry_id"
    ).fetchall() == [("support",), ("support",)]
    for changes in ({"evidence_ref": "changed"}, {"amount": "41"}):
        with pytest.raises(ProblemError) as failure:
            call(db, a, key="fund", **({"evidence_ref": "support"} | changes))
        assert failure.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED
    assert balance(db, root).available == 60 and balance(db, a).allocated == 40


def test_unit_ancestor_precedence_and_one_resolution_query(allocation_db, monkeypatch):
    from flo.modules.budget.ledger import LedgerRepository
    from flo.modules.org.schemas import OrgUnitCreate

    db = allocation_db
    bu = unit(db)
    ou = org(db).create_unit(OrgUnitCreate(code="OU", name="OU", kind="ou", parent_id=bu.id))
    org(db).set_setting("funding_mode", SettingPut(value="roll_down"))
    org(db).set_setting("funding_mode", SettingPut(unit_id=bu.id, value="roll_up"))
    root = service(db).create(body(ou))
    child = service(db).create(body(ou, parent_id=root.id))
    original = LedgerRepository.execute
    observed = []

    def count(self, query, params=None):
        if "candidates AS" in query:
            observed.append(query)
        return original(self, query, params)

    monkeypatch.setattr(LedgerRepository, "execute", count)
    result = call(db, child, "10")
    assert len(result.entries) == 1 and len(observed) == 1
    observed.clear()
    assert read(db, child).mode == "roll_up"
    assert len(observed) == 1
    # The parent BU controls a funded tree rooted at its OU, even when only a child has money.
    with pytest.raises(ProblemError) as failure:
        org(db).set_setting("funding_mode", SettingPut(unit_id=bu.id, value="roll_down"))
    assert failure.value.checks["problem"] == "funding_mode_locked"


def test_registry_dispatches_only_the_registered_key(monkeypatch):
    seen = []
    monkeypatch.setattr(setting_guards, "_GUARDS", {})

    def reject(connection, scope, unit_id, value):
        seen.append((scope, unit_id, value))
        raise ProblemError(ErrorCode.CONFLICT, checks={"problem": "planted_guard"})

    setting_guards.register("planted", reject)
    scope = Scope(uuid4())
    setting_guards.check("other", None, scope, None, "roll_up")
    assert seen == []
    with pytest.raises(ProblemError) as failure:
        setting_guards.check("planted", None, scope, None, "roll_up")
    assert failure.value.checks["problem"] == "planted_guard"
    assert seen == [(scope, None, "roll_up")]


def test_decimal_bucket_calculation_without_database():
    assert (
        buckets([Decimal("1.01"), Decimal(".10"), Decimal(".20"), Decimal(".30")]).available
        == "0.4100"
    )
