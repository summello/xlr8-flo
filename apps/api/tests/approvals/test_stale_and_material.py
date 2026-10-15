"""APR-015/016, DATA-002 and WF-001 against real Postgres."""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import psycopg
import pytest

from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import (
    approval_state,
    assert_current_approval,
    decide,
    decisions_for,
    material_digest,
    requires_resubmission,
    supersede,
    withdraw,
)
from tests.approvals.test_decisions import body
from tests.approvals.test_decisions import case as case
from tests.approvals.test_engine import begin, published
from tests.approvals.test_engine import setup as setup
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db
from tests.org.test_bootstrap import connection_url

MATERIAL = {
    "amount": "300000.0000",
    "project_id": "project",
    "ledger_account_code": "CAPEX",
    "catalogue_items": "-,entry",
    "line_count": "2",
}


def configure(case, **kwargs):
    setup, _ = case
    setup[1].value = replace(setup[1].value, material=dict(MATERIAL))
    published(setup, **kwargs)
    return setup, begin(setup)


def decision(setup, instance, actor=None, **kwargs):
    db = setup[0]
    return decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[0].id,
        actor_id=actor or db[3],
        body=DecisionWrite(**body(**kwargs)),
    )


def state_args(setup):
    db, adapter, *_ = setup
    return db[0], Scope(db[1]), "requisition", adapter.document


def stale(call):
    with pytest.raises(ProblemError) as exc:
        call()
    assert exc.value.code.value == "conflict"
    assert exc.value.checks["problem"] == "stale_version"


@pytest.mark.parametrize(
    "outcome,extra",
    [
        ("approve", {}),
        ("reject", {"reject_mode": "permanent", "comment": "No"}),
        ("return", {"comment": "Revise"}),
        ("comment", {"comment": "Note"}),
    ],
)
def test_stale_decision_writes_nothing(case, outcome, extra):
    setup, instance = configure(case)
    setup[1].value = replace(setup[1].value, version=2)
    stale(lambda: decision(setup, instance, outcome=outcome, **extra))
    assert not decisions_for(setup[0][0], Scope(setup[0][1]), instance.id)
    assert setup[0][0].execute(
        "SELECT status FROM approval_task WHERE id=%s", (instance.tasks[0].id,)
    ).fetchone() == ("open",)


def test_edited_between_decisions_and_current_nonmaterial_version(case):
    setup, instance = configure(case, mode="all")
    decision(setup, instance)
    setup[1].value = replace(setup[1].value, version=2)
    stale(lambda: decision(setup, instance, actor=setup[2][0]))
    result = decision(setup, instance, actor=setup[2][0], document_version=2)
    assert result.instance.status == "running"
    result = decision(setup, instance, actor=setup[2][1], document_version=2)
    assert result.instance.status == "approved"
    assert approval_state(*state_args(setup)).approved_version == 2


def test_digest_key_order(case):
    setup, _ = configure(case)
    snapshot = setup[1].value
    assert material_digest(snapshot) == material_digest(
        replace(snapshot, material=dict(reversed(list(MATERIAL.items()))))
    )


@pytest.mark.parametrize("field", MATERIAL)
def test_each_material_field_requires_resubmission(case, field):
    setup, instance = configure(case)
    decision(setup, instance)
    args = state_args(setup)
    assert not requires_resubmission(*args)
    assert_current_approval(*args, 1)
    setup[1].value = replace(setup[1].value, version=2)
    assert not requires_resubmission(*args)
    assert_current_approval(*args, 2)
    stale(lambda: assert_current_approval(*args, 1))
    original = setup[1].value
    setup[1].value = replace(original, material=original.material | {field: "changed"})
    assert material_digest(original) != material_digest(setup[1].value)
    assert requires_resubmission(*args)
    stale(lambda: assert_current_approval(*args, 2))


def test_superseded_approval_is_not_current_and_audited(case):
    setup, instance = configure(case)
    decision(setup, instance)
    args = state_args(setup)
    supersede(*args, setup[0][3], "Estimate changed")
    assert approval_state(*args).status == "superseded"
    stale(lambda: assert_current_approval(*args, 1))
    stale(lambda: supersede(*args, setup[0][3], "Again"))
    assert setup[0][0].execute(
        "SELECT after FROM audit_log WHERE action='approval.supersede'"
    ).fetchone() == ({"status": "superseded", "reason": "Estimate changed"},)


@pytest.mark.parametrize("outcome", ["reject", "return", "withdraw"])
def test_failed_amendment_restores_in_force_approval(case, outcome):
    setup, original = configure(case)
    decision(setup, original)
    args = state_args(setup)
    digest = approval_state(*args).approved_material_digest
    amendment = begin(setup)
    state = approval_state(*args)
    assert state.status == "running" and state.running_instance_id == amendment.id
    stale(lambda: assert_current_approval(*args, 1))
    if outcome == "withdraw":
        withdraw(
            args[0],
            args[1],
            document_type=args[2],
            document_id=args[3],
            actor_id=setup[0][3],
            reason="Withdraw amendment",
        )
    else:
        extra = {"reject_mode": "permanent"} if outcome == "reject" else {}
        decision(setup, amendment, outcome=outcome, comment="Amendment declined", **extra)
    state = approval_state(*args)
    assert state.status == "approved" and state.instance_id == original.id
    assert state.approved_material_digest == digest and state.running_instance_id is None
    assert_current_approval(*args, 1)
    assert not requires_resubmission(*args)


def test_none_and_lone_rejection_are_not_approved(case):
    setup, _ = case
    assert approval_state(*state_args(setup)).status == "none"
    setup, instance = configure(case)
    decision(setup, instance, outcome="reject", reject_mode="permanent", comment="Declined")
    assert approval_state(*state_args(setup)).status == "rejected"
    stale(lambda: assert_current_approval(*state_args(setup), 1))


def test_supersede_and_decision_serialize_on_instance(case):
    setup, instance = configure(case)
    decision(setup, instance)
    args = state_args(setup)
    entered = Event()
    with psycopg.connect(connection_url(args[0]), autocommit=True) as worker:

        def run():
            with correlation_context("supersede-race"):
                entered.set()
                try:
                    decide(
                        worker,
                        args[1],
                        task_id=instance.tasks[0].id,
                        actor_id=setup[2][0],
                        body=DecisionWrite(**body()),
                    )
                    return "decided"
                except ProblemError as exc:
                    return exc.checks["problem"]

        with ThreadPoolExecutor(1) as pool:
            with tenant_transaction(args[0], args[1]):
                supersede(*args, setup[0][3], "Material edit")
                future = pool.submit(run)
                assert entered.wait(10)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if args[0].execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                        (worker.info.backend_pid,),
                    ).fetchone() == ("Lock",):
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("decision did not wait for supersede's instance lock")
            assert future.result(10) == "task_closed"
    assert len(decisions_for(args[0], args[1], instance.id)) == 1


def test_two_supersedes_serialize_so_exactly_one_audit_row_is_written(case):
    """Plant: supersede's lock=False must fail this (the decision race alone cannot prove it)."""
    setup, instance = configure(case)
    decision(setup, instance)
    args = state_args(setup)
    entered = Event()
    with psycopg.connect(connection_url(args[0]), autocommit=True) as worker:

        def run():
            with correlation_context("supersede-supersede"):
                entered.set()
                try:
                    supersede(worker, args[1], args[2], args[3], setup[2][0], "Second edit")
                    return "superseded"
                except ProblemError as exc:
                    return exc.checks["problem"]

        with ThreadPoolExecutor(1) as pool:
            with tenant_transaction(args[0], args[1]):
                supersede(*args, setup[0][3], "First edit")
                future = pool.submit(run)
                assert entered.wait(10)
                time.sleep(0.5)  # the second supersede is now queued behind the first
            assert future.result(10) == "stale_version"
    with tenant_transaction(args[0], args[1]):
        count = (
            args[0]
            .execute("SELECT count(*) FROM audit_log WHERE action='approval.supersede'")
            .fetchone()
        )
    assert count == (1,)
