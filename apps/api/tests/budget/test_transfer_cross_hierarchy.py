"""BUD-003/004, WF-003: tree paths, atomic groups and ordered funding locks."""

import random
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.budget import transfers
from flo.modules.budget.ledger import LedgerRepository
from flo.modules.budget.service import get_balance
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from flo.modules.projects.schemas import PathRead
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_resolver import insert_identity, service_for
from tests.budget.test_allocation import allocation_db as allocation_db
from tests.budget.test_funding_modes import call as allocate
from tests.budget.test_funding_modes import org
from tests.budget.test_post_entry import posting_db as posting_db
from tests.budget.test_transfer_same_level import call, reconciles, send
from tests.budget.test_transfer_same_level import transfer_db as transfer_db
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import body, service, settings, unit
from tests.projects.conftest import project_db as project_db


def tree(db, mode="roll_down", depth=2):
    bu = unit(db)
    from flo.modules.org.schemas import SettingPut

    org(db).set_setting("funding_mode", SettingPut(unit_id=bu.id, value=mode))
    root = service(db).create(body(bu))
    branches = []
    for _ in range(2):
        branch = []
        parent = root
        for _ in range(depth):
            node = service(db).create(body(bu, parent_id=parent.id))
            branch.append(node)
            parent = node
        branches.append(branch)
    nodes = [root, *branches[0], *branches[1]]
    if mode == "roll_down":
        allocate(db, root, str(len(nodes) * 1000))
        for branch in branches:
            for i, node in enumerate(branch):
                allocate(db, node, str((depth - i) * 1000))
    else:
        for node in nodes:
            allocate(db, node, "1000")
    return nodes, branches[0][-1], branches[1][-1]


def balances(db, nodes):
    return [get_balance(db.connection, Scope(db.org_a), p.id) for p in nodes]


def count(db):
    return db.connection.execute("SELECT count(*) FROM ledger_entry").fetchone()[0]


def assert_group(result, expected):
    assert len(result.entries) == expected
    assert sum((e.amount for e in result.entries), Decimal(0)) == 0
    assert all(e.transfer_group_id == result.transfer_group_id for e in result.entries)


@pytest.mark.parametrize(
    "mode,depth,legs",
    [("roll_down", 2, 8), ("roll_down", 4, 16), ("roll_up", 2, 2), ("roll_up", 4, 2)],
)
def test_path_and_replay(transfer_db, mode, depth, legs):
    db = transfer_db
    nodes, giver, recipient = tree(db, mode, depth)
    before = balances(db, nodes)
    first = call(db, giver, recipient, evidence_ref="support")
    assert_group(first, legs)
    after = balances(db, nodes)
    for node, old, new in zip(nodes, before, after, strict=True):
        delta = (
            Decimal(-25)
            if node.id == giver.id
            else (Decimal(25) if node.id == recipient.id else Decimal(0))
        )
        assert new.allocated == old.allocated + delta
    if mode == "roll_down":
        route = (
            [p.id for p in reversed(nodes[1 : depth + 1])]
            + [nodes[0].id]
            + [p.id for p in nodes[depth + 1 :]]
        )
        assert [e.project_id for e in first.entries] == [
            project for a, b in zip(route, route[1:]) for project in (a, b)
        ]
    assert call(db, giver, recipient, evidence_ref="support") == first
    with pytest.raises(ProblemError) as failure:
        call(db, giver, recipient, evidence_ref="changed")
    assert failure.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED
    with pytest.raises(ProblemError) as failure:
        call(db, giver, recipient, amount="26", evidence_ref="support")
    assert failure.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED
    assert (
        count(db)
        == len(nodes) * (2 if mode == "roll_down" else 1) - (1 if mode == "roll_down" else 0) + legs
    )
    assert db.connection.execute(
        "SELECT sum(amount) FROM ledger_entry WHERE transfer_group_id=%s",
        (first.transfer_group_id,),
    ).fetchone() == (Decimal(0),)
    assert (
        len(db.connection.execute("SELECT ref FROM ledger_evidence WHERE ref='support'").fetchall())
        == legs
    )
    reconciles(db, *nodes)


@pytest.mark.parametrize("mode", ["roll_down", "roll_up"])
def test_siblings_keep_two_legs(transfer_db, mode):
    db = transfer_db
    nodes, _, _ = tree(db, mode)
    assert_group(call(db, nodes[1], nodes[3]), 2)


@pytest.mark.parametrize("direction", ["up", "down"])
@pytest.mark.parametrize("mode,legs", [("roll_down", 4), ("roll_up", 2)])
def test_unequal_depth_and_ancestor_endpoints(transfer_db, direction, mode, legs):
    nodes, giver, _ = tree(transfer_db, mode)
    a, b = (giver, nodes[0]) if direction == "up" else (nodes[0], giver)
    assert_group(call(transfer_db, a, b), legs)
    reconciles(transfer_db, *nodes)


def test_running_balances_receive_before_spending(transfer_db):
    db = transfer_db
    nodes, giver, recipient = tree(db)
    # Empty intermediate pools using ordinary sibling transfers first.
    call(db, nodes[1], nodes[3], key="empty-left", amount="1000")
    call(db, nodes[3], nodes[1], key="empty-right", amount="2000")
    assert get_balance(db.connection, Scope(db.org_a), nodes[3].id).available == 0
    before = balances(db, nodes)
    assert_group(call(db, giver, recipient), 8)
    assert balances(db, nodes)[3].allocated == before[3].allocated
    reconciles(db, *nodes)


@pytest.mark.parametrize("mode", ["roll_down", "roll_up"])
def test_overspend_writes_nothing(transfer_db, mode):
    db = transfer_db
    nodes, giver, recipient = tree(db, mode)
    before, rows = balances(db, nodes), count(db)
    with pytest.raises(ProblemError) as failure:
        call(db, giver, recipient, amount="1001")
    assert failure.value.code == ErrorCode.INSUFFICIENT_BUDGET
    assert balances(db, nodes) == before and count(db) == rows


@pytest.mark.parametrize("mode", ["roll_down", "roll_up"])
def test_last_leg_failure_rolls_back_group(transfer_db, mode, monkeypatch):
    db = transfer_db
    nodes, giver, recipient = tree(db, mode)
    before, rows = balances(db, nodes), count(db)
    audit = db.connection.execute("SELECT count(*) FROM audit_log").fetchone()
    original = transfers.post_entry

    def fail(*args, **kwargs):
        if kwargs["project_id"] == recipient.id:
            raise RuntimeError("planted last leg failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(transfers, "post_entry", fail)
    with pytest.raises(RuntimeError, match="planted last leg failure"):
        call(db, giver, recipient, evidence_ref="support")
    assert balances(db, nodes) == before and count(db) == rows
    assert db.connection.execute("SELECT count(*) FROM audit_log").fetchone() == audit
    assert db.connection.execute("SELECT count(*) FROM ledger_evidence").fetchone() == (0,)


@pytest.mark.parametrize("index", [0, 1, 3, 4])
@pytest.mark.parametrize("status", ["completed", "abandoned", "deferred", "approval_pending"])
def test_receiving_intermediate_status_guard(transfer_db, index, status, monkeypatch):
    db = transfer_db
    nodes, giver, recipient = tree(db)
    db.connection.execute("UPDATE project SET status=%s WHERE id=%s", (status, nodes[index].id))
    before, rows = balances(db, nodes), count(db)

    def never_post(*args, **kwargs):
        pytest.fail("Status must be rejected before posting any leg")

    monkeypatch.setattr(transfers, "post_entry", never_post)
    with pytest.raises(ProblemError) as failure:
        call(db, giver, recipient)
    assert failure.value.code == ErrorCode.CONFLICT
    assert failure.value.checks["problem"] == "transfer_not_eligible"
    assert str(nodes[index].id) in failure.value.detail and status in failure.value.detail
    assert balances(db, nodes) == before and count(db) == rows


@pytest.mark.parametrize("mode", ["roll_down", "roll_up"])
def test_residual_funds_can_leave_completed_giver(transfer_db, mode):
    nodes, giver, recipient = tree(transfer_db, mode)
    transfer_db.connection.execute("UPDATE project SET status='completed' WHERE id=%s", (giver.id,))
    assert_group(call(transfer_db, giver, recipient), 8 if mode == "roll_down" else 2)
    reconciles(transfer_db, *nodes)


@pytest.mark.parametrize("mode", ["roll_down", "roll_up"])
def test_intermediate_currency_guard(transfer_db, mode):
    db = transfer_db
    nodes, giver, recipient = tree(db, mode)
    db.connection.execute("UPDATE project SET currency='EUR' WHERE id=%s", (nodes[0].id,))
    rows = count(db)
    with pytest.raises(ProblemError) as failure:
        call(db, giver, recipient)
    assert failure.value.checks["problem"] == "transfer_not_eligible"
    assert count(db) == rows


def test_different_trees_guard(transfer_db):
    db = transfer_db
    _, giver, _ = tree(db)
    other = service(db).create(body(unit(db, "OTHER")))
    result = send(db, giver, other)
    assert result.status_code == 409
    assert result.json()["checks"] == {
        "problem": "transfer_not_eligible",
        "reason": "different_trees",
    }


def test_endpoint_only_permissions(transfer_db):
    db = transfer_db
    nodes, giver, recipient = tree(db)
    viewer = insert_identity(db, "leaf-only")
    with service_for(db, Scope(db.org_a), "leaf-grants") as identity:
        role = identity.create_role("leaf-transfer", "Leaf transfer")
        identity.grant_permission(role.id, "budget.transfer")
        for p in (giver, recipient):
            identity.grant_role(viewer, role.id, AuthorizationTarget(ScopeType.PROJECT, p.id))
    first = send(db, giver, recipient, viewer=viewer, key="leaf-only")
    assert first.status_code == 201, first.text
    assert len(first.json()["entries"]) == 8
    assert send(db, giver, recipient, viewer=viewer, key="leaf-only").json() == first.json()
    reconciles(db, *nodes)


@pytest.mark.parametrize("side", ["giver", "recipient"])
@pytest.mark.parametrize("in_scope", [True, False])
def test_endpoint_authorization_guard(transfer_db, side, in_scope):
    db = transfer_db
    _, giver, recipient = tree(db)
    viewer = insert_identity(db, "limited")
    allowed, denied = (giver, recipient) if side == "recipient" else (recipient, giver)
    with service_for(db, Scope(db.org_a), "limited-grants") as identity:
        role = identity.create_role("leaf-transfer", "Leaf transfer")
        identity.grant_permission(role.id, "budget.transfer")
        identity.grant_role(viewer, role.id, AuthorizationTarget(ScopeType.PROJECT, allowed.id))
        if in_scope:
            read = identity.create_role("leaf-reader", "Leaf reader")
            identity.grant_permission(read.id, "project.read")
            identity.grant_role(viewer, read.id, AuthorizationTarget(ScopeType.PROJECT, denied.id))
    rows = count(db)
    assert send(db, giver, recipient, viewer=viewer).status_code == (403 if in_scope else 404)
    assert count(db) == rows


@pytest.mark.parametrize("side", ["giver", "recipient"])
def test_foreign_endpoint(transfer_db, side):
    db = transfer_db
    _, giver, recipient = tree(db)
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    rows = count(db)
    assert (
        send(
            db, foreign if side == "giver" else giver, foreign if side == "recipient" else recipient
        ).status_code
        == 404
    )
    assert count(db) == rows


@pytest.mark.parametrize("mode", ["roll_down", "roll_up"])
def test_50_concurrent_mixed_transfers_reconcile(transfer_db, mode):
    db = transfer_db
    nodes, _, _ = tree(db, mode)
    rng = random.Random(708)
    jobs = [(rng.sample(nodes, 2), f"mixed-{i}") for i in range(50)]
    barrier = Barrier(50)
    before = sum((b.allocated for b in balances(db, nodes)), Decimal(0))

    def run(job):
        (a, b), key = job
        with psycopg.connect(settings(db).database_url.get_secret_value(), autocommit=True) as conn:
            conn.execute("SET lock_timeout='20s'")
            with correlation_context(key):
                barrier.wait(timeout=20)
                return call(db, a, b, conn=conn, key=key, amount="1")

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(run, jobs))
    assert all(len(r.entries) >= 2 for r in results)
    assert sum((b.allocated for b in balances(db, nodes)), Decimal(0)) == before
    reconciles(db, *nodes)


def test_group_invariant_rejects_planted_unbalanced_leg(transfer_db, monkeypatch):
    db = transfer_db
    nodes, giver, recipient = tree(db)
    rows, before = count(db), balances(db, nodes)
    original = transfers.post_entry

    def corrupt(*args, **kwargs):
        if kwargs["project_id"] == recipient.id:
            kwargs["amount"] += Decimal(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(transfers, "post_entry", corrupt)
    with pytest.raises(ProblemError) as failure:
        call(db, giver, recipient)
    assert failure.value.checks["problem"] == "transfer_invariant_violation"
    assert count(db) == rows and balances(db, nodes) == before


def test_single_ordered_lock_statement_before_validation(transfer_db, monkeypatch):
    db = transfer_db
    nodes, giver, recipient = tree(db)
    original = LedgerRepository.execute
    locks = []

    def track(self, query, params=None):
        if "ORDER BY project_id FOR UPDATE" in query:
            locks.append(params["ids"])
        return original(self, query, params)

    monkeypatch.setattr(LedgerRepository, "execute", track)
    call(db, giver, recipient)
    assert locks == [sorted(p.id for p in nodes)]


def test_decimal_path_calculation_without_database():
    # Path planning only needs the project identity; no DB or rounding side effects.
    ids = [uuid4() for _ in range(5)]
    nodes = dict(zip(ids, ids, strict=True))
    path = PathRead(up=ids[:2], lca=ids[2], down=ids[3:])
    legs = transfers.plan_legs(nodes, path, ids[0], ids[-1], False, "roll_down", Decimal("25.1234"))
    assert [node for node, _, _ in legs] == [ids[i] for i in (0, 1, 1, 2, 2, 3, 3, 4)]
    assert sum((amount for _, amount, _ in legs), Decimal(0)) == 0
    assert len({suffix for _, _, suffix in legs}) == 8
    assert [amount for _, amount, _ in legs] == [Decimal("-25.1234"), Decimal("25.1234")] * 4
    assert (
        len(transfers.plan_legs(nodes, path, ids[0], ids[-1], False, "roll_up", Decimal("25.1234")))
        == 2
    )
