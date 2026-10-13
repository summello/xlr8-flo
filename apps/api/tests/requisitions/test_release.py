"""Release after approval: withdraw, cancel, reduce free the reservation exactly once (E09-S07)."""

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.budget.service import lock_balance
from flo.modules.requisitions import lifecycle
from flo.modules.requisitions.funding import reduce_to
from flo.modules.requisitions.schemas import RequisitionTransitionCreate
from flo.modules.requisitions.service import RequisitionService, transition
from tests.requisitions.test_approval_effects import approve, drift, grant, linked, standalone
from tests.requisitions.test_approval_effects import state as state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import connection_url, project, unit
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lifecycle import command, sql
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lines import lines_db as lines_db

REASON = "Scope was withdrawn by the board"


def approved(db, price="60"):
    p = project(db, unit(db, uuid4().hex))
    row = linked(db, p, price=price)
    approve(db, row)
    return p, row


def releases(db, id):
    return sql(
        db,
        "SELECT amount,reason FROM ledger_entry WHERE source_id=%s AND entry_type='release'",
        (id,),
    )


def sub_project(db, id):
    return sql(
        db,
        "SELECT status,id FROM project WHERE generated_by_requisition_id=%s",
        (id,),
    )[0]


def test_withdraw_after_approval_releases_once_and_abandons_sub_project(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db)
    command(db, row, "withdrawn")
    after = state(db, row.id)
    assert after.status == "withdrawn" and after.reservation.remaining == "0.0000"
    assert releases(db, row.id) == [(Decimal(-60), "withdrawn")]
    status, sub = sub_project(db, row.id)
    assert status == "abandoned"
    event = sql(
        db,
        "SELECT actor_id,system_actor,reason FROM project_status_event WHERE project_id=%s",
        (sub,),
    )
    assert event == [(db[3], None, f"Abandoned because {row.number} was withdrawn")]
    assert sql(
        db,
        "SELECT ledger_entry_id IS NOT NULL FROM requisition_status_event "
        "WHERE requisition_id=%s AND to_status='withdrawn'",
        (row.id,),
    ) == [(True,)]
    assert sql(db, "SELECT reserved FROM project_balance WHERE project_id=%s", (p.id,)) == [
        (Decimal(0),)
    ]
    assert drift(db) == []


def test_withdraw_refused_once_sourcing_started_and_nothing_written(lifecycle_db, monkeypatch):
    db, _ = lifecycle_db
    _, row = approved(db)
    monkeypatch.setattr(lifecycle, "_sourcing_probes", [lambda conn, scope, id: id == row.id])
    before = sql(db, "SELECT count(*) FROM ledger_entry")
    with pytest.raises(ProblemError) as error:
        command(db, row, "withdrawn")
    assert error.value.checks["problem"] == "sourcing_started"
    assert error.value.detail == (
        f"Sourcing has started on {row.number}. Ask Procurement to cancel it."
    )
    assert state(db, row.id).status == "sourcing_in_progress"
    assert sql(db, "SELECT count(*) FROM ledger_entry") == before
    assert sub_project(db, row.id)[0] == "active"
    # Cancelling stays available once sourcing has started.
    command(db, row, "cancelled", reason=REASON)
    assert state(db, row.id).status == "cancelled"


def test_cancel_after_approval_needs_a_reason_and_releases_once(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)
    with pytest.raises(ProblemError) as error:
        command(db, row, "cancelled", reason="too short")
    assert error.value.code == ErrorCode.VALIDATION_FAILED
    assert releases(db, row.id) == []
    command(db, row, "cancelled", reason=REASON)
    assert state(db, row.id).status == "cancelled"
    assert releases(db, row.id) == [(Decimal(-60), "cancelled")]
    assert sub_project(db, row.id)[0] == "abandoned"
    with pytest.raises(ProblemError) as again:
        command(db, row, "cancelled", reason=REASON)
    assert again.value.checks["problem"] in {"invalid_transition", "stale_version"}
    assert len(releases(db, row.id)) == 1
    assert drift(db) == []


def test_only_the_requester_withdraws_after_approval(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)
    current = RequisitionService(db[0], Scope(db[1])).get(row.id)
    with pytest.raises(ProblemError) as error:
        transition(
            db[0],
            Scope(db[1]),
            row.id,
            RequisitionTransitionCreate(to="withdrawn"),
            str(current.version),
            uuid4(),
            lambda p: True,
        )
    assert error.value.code == ErrorCode.FORBIDDEN
    assert releases(db, row.id) == []


def test_standalone_created_project_is_left_alone_on_cancel(lifecycle_db):
    db, _ = lifecycle_db
    grant(db, "budget.allocate")
    row = standalone(db)
    approve(db, row)
    command(db, row, "cancelled", reason=REASON)
    assert state(db, row.id).status == "cancelled"
    assert releases(db, row.id) == [(Decimal(-60), "cancelled")]
    project_status = sql(
        db,
        "SELECT status FROM project WHERE id=(SELECT project_id FROM requisition WHERE id=%s)",
        (row.id,),
    )
    assert project_status == [("active",)]
    assert drift(db) == []


def stop_on_own_connection(db, row, to, version, barrier=None):
    with (
        psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
        correlation_context("release-race"),
    ):
        if barrier:
            barrier.wait(timeout=10)
        try:
            transition(
                conn,
                Scope(db[1]),
                row.id,
                RequisitionTransitionCreate(to=to, reason=REASON),
                str(version),
                db[3],
                lambda p: True,
            )
            return "success"
        except ProblemError as exc:
            assert exc.code == ErrorCode.CONFLICT, exc
            assert exc.checks["problem"] in {"invalid_transition", "stale_version"}
            return "conflict"


@pytest.mark.parametrize("pair", [("cancelled", "cancelled"), ("withdrawn", "cancelled")])
def test_two_concurrent_stops_release_once_twenty_runs(lifecycle_db, pair):
    db, _ = lifecycle_db
    for _ in range(20 if pair[0] == "cancelled" else 5):
        _, row = approved(db)
        version = state(db, row.id).version
        barrier = Barrier(2)
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda to: stop_on_own_connection(db, row, to, version, barrier),
                    pair,
                )
            )
        assert sorted(results) == ["conflict", "success"]
        assert len(releases(db, row.id)) == 1
        assert state(db, row.id).status in {"cancelled", "withdrawn"}
        assert (
            len(
                sql(
                    db,
                    "SELECT id FROM requisition_status_event WHERE requisition_id=%s "
                    "AND from_status='sourcing_in_progress'",
                    (row.id,),
                )
            )
            == 1
        )
        assert drift(db) == []


def test_reduce_to_releases_the_difference_once_then_cancel_releases_the_rest(lifecycle_db):
    db, _ = lifecycle_db
    p, row = approved(db, price="100")
    scope = Scope(db[1])

    def reduce(amount, version=7):
        return reduce_to(
            db[0], scope, row.id, new_amount=Decimal(amount), version=version, actor_id=db[3]
        )

    assert reduce("100") is None
    entry = reduce("60")
    assert entry is not None and entry.amount == Decimal(-40)
    assert entry.entry_type == "release" and entry.source_id == row.id
    assert entry.idempotency_key == (
        f"req:{row.id}:release:{state(db, row.id).reservation.funding_round}:reduce:7"
    )
    assert releases(db, row.id) == [(Decimal(-40), "reduced")]
    assert sql(db, "SELECT idempotency_key FROM ledger_entry WHERE entry_type='release'") == [
        (f"req:{row.id}:release:{state(db, row.id).reservation.funding_round}:reduce:7",)
    ]
    assert reduce("60") is None  # replaying the same version releases nothing
    assert len(releases(db, row.id)) == 1
    for bad in ("61", "-1"):
        with pytest.raises(ProblemError) as error:
            reduce(bad, version=8)
        assert error.value.checks["problem"] == "release_exceeds_reservation"
    command(db, row, "cancelled", reason=REASON)
    assert sorted(a for a, _ in releases(db, row.id)) == [Decimal(-60), Decimal(-40)]
    assert sql(db, "SELECT reserved FROM project_balance WHERE project_id=%s", (p.id,)) == [
        (Decimal(0),)
    ]
    assert drift(db) == []


def test_foreign_requisition_cannot_be_stopped(lifecycle_db):
    db, _ = lifecycle_db
    _, row = approved(db)
    with pytest.raises(ProblemError) as error:
        transition(
            db[0],
            Scope(UUID(int=0xBAD) if db[2] is None else db[2]),
            row.id,
            RequisitionTransitionCreate(to="cancelled", reason=REASON),
            "1",
            db[3],
            lambda p: True,
        )
    assert error.value.code == ErrorCode.NOT_FOUND


def test_repeated_reject_to_draft_releases_each_funding_round_once(lifecycle_db):
    """A release key without the round would replay round 1's release and strand round 2's."""
    from flo.modules.approvals.schemas import Outcome
    from tests.requisitions.test_lifecycle import outcome

    db, _ = lifecycle_db
    row = linked(db, project(db, unit(db, uuid4().hex)))
    for round_ in (1, 2):
        outcome(db, row, Outcome.REJECTED, "return_to_draft")
        after = state(db, row.id)
        assert after.status == "draft" and after.reservation.remaining == "0.0000"
        assert len(releases(db, row.id)) == round_
        if round_ == 1:
            command(db, row)
    keys = sql(db, "SELECT idempotency_key FROM ledger_entry WHERE entry_type='release'")
    assert len(set(keys)) == 2
    assert drift(db) == []


def reduce_on_own_connection(db, row, version, barrier=None, amount=60):
    with (
        psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
        correlation_context("reduce-race"),
    ):
        if barrier:
            barrier.wait(timeout=10)
        try:
            return reduce_to(
                conn,
                Scope(db[1]),
                row.id,
                new_amount=Decimal(amount),
                version=version,
                actor_id=db[3],
            )
        except ProblemError as exc:
            assert exc.checks["problem"] == "release_exceeds_reservation"
            return None


def test_two_concurrent_reductions_to_60_leave_60_twenty_runs(lifecycle_db):
    db, _ = lifecycle_db
    for _ in range(20):
        p, row = approved(db, price="100")
        barrier = Barrier(2)
        with ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(reduce_on_own_connection, db, row, v, barrier) for v in (1, 2)]
            results = [job.result(timeout=60) for job in jobs]
        assert len([r for r in results if r is not None]) == 1
        assert sql(db, "SELECT reserved FROM project_balance WHERE project_id=%s", (p.id,)) == [
            (Decimal(60),)
        ]
        assert releases(db, row.id) == [(Decimal(-40), "reduced")]
        assert drift(db) == []


@pytest.mark.parametrize("stop", ["cancel", "reduce"])
def test_stops_take_project_balance_before_the_requisition(lifecycle_db, stop):
    """While another transaction holds the balance lock, a stop must wait on it without already
    holding the requisition lock: a stop that locks the requisition first (or skips the balance
    lock) leaves it locked and deadlocks against any balance-first path."""
    db, _ = lifecycle_db
    p, row = approved(db, price="100")
    version = state(db, row.id).version
    scope = Scope(db[1])
    with (
        psycopg.connect(connection_url(db[0]), autocommit=True) as holder,
        correlation_context("lock-order"),
        ThreadPoolExecutor(1) as pool,
    ):
        with holder.transaction():
            lock_balance(holder, scope, p.id)
            job = pool.submit(
                stop_on_own_connection if stop == "cancel" else reduce_on_own_connection,
                *((db, row, "cancelled", version) if stop == "cancel" else (db, row, 1, None, 60)),
            )
            for _ in range(100):
                waiting = holder.execute(
                    "SELECT count(*) FROM pg_locks WHERE NOT granted AND pid<>pg_backend_pid()"
                ).fetchone()
                if waiting and waiting[0]:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("the stop never waited on the balance lock")
            # Not yet taken by the waiting stop: we can lock the requisition without waiting.
            with tenant_transaction(db[0], scope):
                db[0].execute(
                    "SELECT 1 FROM requisition WHERE org_id=%s AND id=%s FOR UPDATE NOWAIT",
                    (db[1], row.id),
                )
        job.result(timeout=60)
    assert sql(db, "SELECT reserved FROM project_balance WHERE project_id=%s", (p.id,)) == [
        (Decimal(0) if stop == "cancel" else Decimal(60),)
    ]
