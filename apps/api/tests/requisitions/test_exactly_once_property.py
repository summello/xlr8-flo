"""REQ-013/FIN-011/FIN-012: random lifecycles, each mutation issued twice at once, release once."""

import random
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg

from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import decide, get_instance
from flo.modules.budget import reconcile
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from flo.modules.requisitions.funding import reduce_to
from flo.modules.requisitions.schemas import LineCreate, RequisitionTransitionCreate
from flo.modules.requisitions.service import RequisitionService, expire_org, transition
from tests.requisitions.test_approval_effects import fund, reviewer
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import connection_url, project, unit
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lifecycle import sql
from tests.requisitions.test_lines import BODY, service
from tests.requisitions.test_lines import lines_db as lines_db
from tests.requisitions.test_submission_check import change, complete

RUNS = 300
TERMINAL = {"withdrawn", "cancelled", "rejected", "expired"}
NEXT_OPS = {
    "draft": ["submit"] * 6 + ["edit", "withdraw", "cancel"],
    "approval_pending": ["approve"] * 3
    + ["return", "reject_draft", "reject", "withdraw", "cancel"],
    "sourcing_in_progress": ["withdraw", "cancel", "expire", "reduce", "reduce"],
}


def mutate(db, row, op, rng_value, barrier):
    """One mutation on its own connection; the product's refusals are expected, nothing else."""
    scope = Scope(db[1])
    with (
        psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
        correlation_context("exactly-once-property"),
    ):
        barrier.wait(timeout=10)
        try:
            detail = RequisitionService(conn, scope).detail(row.id, lambda p: True)
            if op in {"submit", "withdraw", "cancel"}:
                to = {"submit": "approval_pending", "withdraw": "withdrawn", "cancel": "cancelled"}[
                    op
                ]
                transition(
                    conn,
                    scope,
                    row.id,
                    RequisitionTransitionCreate(to=to, reason="Property test cancellation"),
                    str(detail.version),
                    db[3],
                    lambda p: True,
                )
            elif op in {"approve", "return", "reject_draft", "reject"}:
                instance = get_instance(conn, scope, detail.approval.instance_id)
                outcome = {"approve": "approve", "return": "return"}.get(op, "reject")
                decide(
                    conn,
                    scope,
                    task_id=instance.tasks[-1].id,
                    actor_id=reviewer(db),
                    body=DecisionWrite(
                        outcome=outcome,
                        reject_mode="return_to_draft" if op == "reject_draft" else None,
                        comment="Property test",
                        document_version=detail.version,
                    ),
                )
            elif op == "expire":
                expire_org(conn, scope)
            elif op == "reduce":
                held = detail.reservation
                reduce_to(
                    conn,
                    scope,
                    row.id,
                    new_amount=Decimal(held.remaining) * Decimal(rng_value) / 100,
                    version=detail.version,
                    actor_id=db[3],
                )
        except ProblemError:
            return


def reservations_ok(db, id, status):
    rows = sql(
        db,
        "SELECT e.amount,v.remaining FROM ledger_entry e JOIN reservation_remaining v "
        "ON v.org_id=e.org_id AND v.reservation_entry_id=e.id "
        "WHERE e.source_id=%s AND e.entry_type='reservation'",
        (id,),
    )
    assert all(0 <= remaining <= amount for amount, remaining in rows), rows
    assert sum(1 for _, remaining in rows if remaining > 0) <= 1, rows
    # A draft may hold one: return-for-changes keeps the reservation (D-M2-15).
    if status in TERMINAL:
        assert all(remaining == 0 for _, remaining in rows), (status, rows)
    released = sql(
        db,
        "SELECT sum(-amount) FROM ledger_entry WHERE source_id=%s AND entry_type='release'",
        (id,),
    )[0][0] or Decimal(0)
    reserved = sum(amount for amount, _ in rows) if rows else Decimal(0)
    assert released <= reserved, (released, reserved)
    keys = sql(
        db,
        "SELECT idempotency_key FROM ledger_entry WHERE source_id=%s AND entry_type='release'",
        (id,),
    )
    assert len(keys) == len(set(keys))


def test_release_is_exactly_once_across_random_concurrent_lifecycles(lifecycle_db):
    db, _ = lifecycle_db
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(
        "requisition.expiry_days", SettingPut(value=1)
    )
    visited = set()
    for run in range(RUNS):
        rng = random.Random(20261013 + run)
        p = project(db, unit(db, uuid4().hex))
        row, _ = complete(db, p, price="60")
        sql(db, "UPDATE project_balance SET allocated=0 WHERE project_id=%s RETURNING 1", (p.id,))
        fund(db, p, "100")
        for _ in range(rng.randint(3, 8)):
            status = RequisitionService(db[0], Scope(db[1])).get(row.id).status
            visited.add(status)
            op = rng.choice(NEXT_OPS.get(status, ["submit", "withdraw", "cancel", "expire"]))
            if op == "edit":
                version = RequisitionService(db[0], Scope(db[1])).get(row.id).version
                try:
                    service(db).write(
                        row.id,
                        str(version),
                        body=LineCreate(
                            **(BODY | {"quantity": "1", "unit_price": str(rng.randint(5, 30))})
                        ),
                    )
                except ProblemError:
                    pass  # a refused edit is a valid outcome of the random sequence
                continue
            if op == "expire":
                change(db, "requisition", row.id, "decided_at=now() - interval '5 days'")
            barrier = Barrier(2)
            with ThreadPoolExecutor(2) as pool:
                list(
                    pool.map(
                        lambda _: mutate(db, row, op, rng.randint(1, 99), barrier),
                        range(2),
                    )
                )
            reservations_ok(db, row.id, RequisitionService(db[0], Scope(db[1])).get(row.id).status)
        final = RequisitionService(db[0], Scope(db[1])).get(row.id).status
        visited.add(final)
        reservations_ok(db, row.id, final)
    # The walk must actually reach the terminal states the invariant is about.
    assert {"sourcing_in_progress", "cancelled", "withdrawn", "expired"} <= visited, visited
    reconcile.reconcile_org(db[0], Scope(db[1]))
    status = reconcile.reconciliation_status(db[0], Scope(db[1]))
    assert status.status == "ok" and status.drift_rows == 0
