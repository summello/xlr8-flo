"""Real Postgres races prove balance serialization and instance-first lock order."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event, current_thread
from time import monotonic
from uuid import uuid4

import psycopg
import pytest

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import decide, get_instance
from flo.modules.budget.service import reservation_remaining
from flo.modules.requisitions.schemas import RequisitionTransitionCreate
from flo.modules.requisitions.service import RequisitionAdapter, RequisitionService, transition
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import connection_url, project
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lifecycle import funded, sql
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lines import lines_db as lines_db
from tests.requisitions.test_submission_check import complete


def run_command(db, row, to, barrier=None):
    with (
        psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
        correlation_context("requisition-race"),
    ):
        if barrier:
            barrier.wait(timeout=10)
        try:
            transition(
                conn,
                Scope(db[1]),
                row.id,
                RequisitionTransitionCreate(to=to),
                str(row.version),
                db[3],
                lambda p: True,
            )
            return "success"
        except ProblemError as exc:
            label = exc.checks.get("problem")
            if label == "submission_invalid":
                assert exc.code == ErrorCode.VALIDATION_FAILED
                assert any(problem.field == "funds/estimate" for problem in exc.errors)
            return label


def test_submit_concurrency_twenty_runs(lifecycle_db):
    db, _ = lifecycle_db
    observations = []
    for _ in range(20):
        from tests.requisitions.test_header import unit

        p = project(db, bu=unit(db, uuid4().hex))
        first, address = complete(db, p, price="60")
        from flo.modules.org.service import today
        from flo.modules.requisitions.schemas import LineCreate
        from tests.requisitions.test_header import draft
        from tests.requisitions.test_lines import BODY, service

        second = draft(
            db,
            p=p,
            justification="Needed",
            needed_by=today(),
            department_code="D",
            ledger_account_code="L",
            delivery_address_id=address.id,
        )
        service(db).write(
            second.id, "1", body=LineCreate(**(BODY | {"quantity": "1", "unit_price": "60"}))
        )
        first = RequisitionService(db[0], Scope(db[1])).get(first.id)
        second = RequisitionService(db[0], Scope(db[1])).get(second.id)
        barrier = Barrier(2)
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda row: run_command(db, row, "approval_pending", barrier), [first, second]
                )
            )
        reserved = sql(db, "SELECT reserved FROM project_balance WHERE project_id=%s", (p.id,))[0][
            0
        ]
        remaining = sql(
            db,
            "SELECT COALESCE(sum(v.remaining),0) FROM reservation_remaining v JOIN ledger_entry e "
            "ON e.id=v.reservation_entry_id WHERE e.project_id=%s",
            (p.id,),
        )[0][0]
        observations.append((sorted(results), reserved, remaining))
    assert observations == [(["submission_invalid", "success"], Decimal(60), Decimal(60))] * 20


@pytest.mark.parametrize("first_action", ["approve", "withdraw"])
def test_final_approve_withdraw_forced_orders_twenty_runs(lifecycle_db, monkeypatch, first_action):
    db, user = lifecycle_db
    original = RequisitionAdapter.snapshot
    for _ in range(20):
        row = funded(lifecycle_db)
        instance_id = row.approval.instance_id
        instance = get_instance(db[0], Scope(db[1]), instance_id)
        locked = Event()
        release = Event()
        second_started = Event()
        second_pid = []

        def paused(self, conn, scope, id):
            result = original(self, conn, scope, id)
            if current_thread().name.endswith("_0"):
                locked.set()
                assert release.wait(10)
            return result

        monkeypatch.setattr(RequisitionAdapter, "snapshot", paused)

        def work(action):
            with (
                psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
                correlation_context("approval-withdraw-race"),
            ):
                if action != first_action:
                    second_pid.append(conn.info.backend_pid)
                    second_started.set()
                try:
                    if action == "approve":
                        decide(
                            conn,
                            Scope(db[1]),
                            task_id=instance.tasks[-1].id,
                            actor_id=user,
                            body=DecisionWrite(outcome="approve", document_version=row.version),
                        )
                    else:
                        transition(
                            conn,
                            Scope(db[1]),
                            row.id,
                            RequisitionTransitionCreate(to="withdrawn"),
                            str(row.version),
                            db[3],
                            lambda p: True,
                        )
                    return "success"
                except ProblemError as exc:
                    assert exc.code == ErrorCode.CONFLICT
                    return exc.checks["problem"]

        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(work, first_action)
            assert locked.wait(10)
            second = pool.submit(work, "withdraw" if first_action == "approve" else "approve")
            assert second_started.wait(10)
            # Prove overlap: the competing transaction is waiting on the first
            # instance lock before the first is permitted to take funding locks.
            deadline = monotonic() + 10
            while monotonic() < deadline:
                blockers = (
                    db[0].execute("SELECT pg_blocking_pids(%s)", (second_pid[0],)).fetchone()[0]
                )
                if blockers:
                    break
                Event().wait(0.01)
            else:
                release.set()
                pytest.fail("The second transaction never waited on the first lock.")
            release.set()
            results = {
                first_action: first.result(timeout=15),
                ("withdraw" if first_action == "approve" else "approve"): second.result(timeout=15),
            }
        monkeypatch.setattr(RequisitionAdapter, "snapshot", original)
        loser = {"task_closed", "invalid_transition"}
        after = RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)
        generated = sql(
            db, "SELECT count(*) FROM project WHERE generated_by_requisition_id=%s", (row.id,)
        )[0][0]
        releases = sql(
            db,
            "SELECT count(*) FROM ledger_entry WHERE source_id=%s AND entry_type='release' "
            "AND reason<>'approved'",
            (row.id,),
        )[0][0]
        if first_action == "approve":
            assert results["approve"] == "success" and results["withdraw"] in loser
            assert after.status == "sourcing_in_progress"
            assert after.reservation.remaining == "60.0000"
            assert (generated, releases) == (1, 0)
        else:
            assert results["withdraw"] == "success" and results["approve"] in loser
            assert after.status == "withdrawn" and after.approval.status == "withdrawn"
            assert after.reservation.remaining == "0.0000"
            assert (generated, releases) == (0, 1)
            assert (
                sql(
                    db,
                    "SELECT count(*) FROM requisition_status_event "
                    "WHERE requisition_id=%s AND to_status='withdrawn'",
                    (row.id,),
                )[0][0]
                == 1
            )


def test_returned_requisitions_swap_projects_twenty_runs(lifecycle_db):
    from flo.modules.approvals.schemas import Outcome
    from flo.modules.org.service import today
    from flo.modules.requisitions.schemas import LineCreate, RequisitionPatch
    from tests.requisitions.test_header import draft, unit
    from tests.requisitions.test_lifecycle import command, outcome
    from tests.requisitions.test_lines import BODY, service
    from tests.requisitions.test_submission_check import change

    db, _ = lifecycle_db
    for _ in range(20):
        bu = unit(db, uuid4().hex)
        a = project(db, bu)
        b = project(db, bu)
        first, address = complete(db, a, price="60")
        second = draft(
            db,
            p=b,
            justification="Needed",
            needed_by=today(),
            delivery_address_id=address.id,
            department_code="D",
            ledger_account_code="L",
        )
        service(db).write(
            second.id, "1", body=LineCreate(**(BODY | {"quantity": "1", "unit_price": "60"}))
        )
        change(db, "project", b.id, "status='active'")
        sql(
            db,
            "UPDATE project_balance SET allocated=200 "
            "WHERE project_id IN (%s,%s) RETURNING allocated",
            (a.id, b.id),
        )
        command(db, first)
        command(db, second)
        outcome(db, first, Outcome.RETURNED)
        outcome(db, second, Outcome.RETURNED)
        svc = RequisitionService(db[0], Scope(db[1]), db[3])
        for row, target in [(first, b), (second, a)]:
            current = svc.get(row.id)
            svc.update(
                row.id, RequisitionPatch(project_id=target.id), str(current.version), lambda p: True
            )
        barrier = Barrier(2)
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda row: run_command(db, row, "approval_pending", barrier),
                    [svc.get(first.id), svc.get(second.id)],
                )
            )
        assert results == ["success", "success"]
        for row, target in [(first, b), (second, a)]:
            current = svc.detail(row.id, lambda p: True)
            assert current.reservation.funding_round == 2
            assert (
                reservation_remaining(db[0], Scope(db[1]), current.reservation.entry_id).project_id
                == target.id
            )
        assert sql(
            db, "SELECT reserved FROM project_balance WHERE project_id IN (%s,%s)", (a.id, b.id)
        ) == [(Decimal(60),), (Decimal(60),)]


def test_concurrent_withdraw_releases_once(lifecycle_db):
    db, _ = lifecycle_db
    row = funded(lifecycle_db)
    barrier = Barrier(2)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: run_command(db, row, "withdrawn", barrier), range(2)))
    assert results.count("success") == 1
    assert (
        sql(
            db,
            "SELECT count(*) FROM ledger_entry WHERE source_id=%s AND entry_type='release' "
            "AND reason<>'approved'",
            (row.id,),
        )[0][0]
        == 1
    )
