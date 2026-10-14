"""ACC-001 on the assign path: concurrent standalone approvals share one project's funds."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import decide, get_instance
from flo.modules.requisitions.service import RequisitionService
from tests.requisitions.test_approval_effects import drift, fund, reviewer, standalone
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import connection_url, project, unit
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lifecycle import sql
from tests.requisitions.test_lines import lines_db as lines_db


def prepare(db, row):
    current = RequisitionService(db[0], Scope(db[1])).detail(row.id, lambda p: True)
    task = get_instance(db[0], Scope(db[1]), current.approval.instance_id).tasks[-1].id
    return task, current.version, reviewer(db)


def approve_on_own_connection(db, prepared, project_id, barrier):
    task, version, actor = prepared
    with (
        psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
        correlation_context("approval-assign-race"),
    ):
        barrier.wait(timeout=10)
        try:
            decide(
                conn,
                Scope(db[1]),
                task_id=task,
                actor_id=actor,
                body=DecisionWrite(
                    outcome="approve",
                    document_version=version,
                    assign_project_id=project_id,
                ),
            )
            return "success"
        except ProblemError as exc:
            assert exc.code == ErrorCode.INSUFFICIENT_BUDGET, exc
            # The funds check ran under the lock, so it is the one that refused (not the ledger).
            assert "Assigning this request to" in exc.detail, exc.detail
            return "insufficient"


def test_two_assigned_approvals_of_60_against_100_twenty_runs(lifecycle_db):
    db, _ = lifecycle_db
    for _ in range(20):
        p = project(db, unit(db, uuid4().hex))
        fund(db, p, "100")
        first, second = standalone(db), standalone(db)
        barrier = Barrier(2)
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda row: approve_on_own_connection(db, row, p.id, barrier),
                    [prepare(db, first), prepare(db, second)],
                )
            )
        assert sorted(results) == ["insufficient", "success"]
        # The winner's 60 moved to its sub-project; the main project keeps 40, reserved 0.
        assert sql(
            db, "SELECT allocated,reserved FROM project_balance WHERE project_id=%s", (p.id,)
        ) == [(Decimal(40), Decimal(0))]
        statuses = sorted(
            RequisitionService(db[0], Scope(db[1])).get(r.id).status for r in (first, second)
        )
        assert statuses == ["approval_pending", "sourcing_in_progress"]
        assert drift(db) == []
