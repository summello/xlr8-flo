"""PO lock order: withdraw instance-first, change submit award-first (E13-S23)."""

from unittest.mock import patch

import psycopg

from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import decide, get_instance
from flo.modules.purchasing import changes as changes_module
from flo.modules.purchasing import service as service_module
from flo.modules.purchasing.cancel import cancel
from flo.modules.purchasing.schemas import PoCancelCreate
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.purchasing.test_changes import changes, issued, line, open_change, publish_change, submit
from tests.purchasing.test_issue import approve, run
from tests.purchasing.test_po_approval import one, status
from tests.purchasing.test_po_approval import submit as submit_po
from tests.purchasing.test_purchase_orders import _race, create, pos, ready
from tests.requisitions.test_approval_effects import reviewer
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import connection_url
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import sql

ROUNDS = 5


def probe_po_unlocked(db, po_id):
    """A second connection takes the PO row NOWAIT: it raises `LockNotAvailable` if held."""
    with psycopg.connect(connection_url(db[0]), autocommit=True) as contender:
        with contender.transaction():
            row = contender.execute(
                "SELECT 1 FROM purchase_order WHERE id=%s FOR UPDATE NOWAIT", (po_id,)
            ).fetchone()
    assert row == (1,)  # a probe that sees no row cannot pass vacuously


def test_withdraw_holds_no_po_lock_when_it_takes_the_instance_lock(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    submit_po(db, po)
    original, calls = service_module.withdraw, []

    def wrapper(*args, **kwargs):
        calls.append(1)
        probe_po_unlocked(db, po.id)  # plant 1: a locked PO here raises LockNotAvailable
        return original(*args, **kwargs)

    with patch.object(service_module, "withdraw", wrapper), correlation_context("po"):
        pos(db).withdraw(po.id)
    assert calls == [1] and status(db, po)[0] == "draft"
    assert sql(db, "SELECT status FROM approval_instance WHERE document_id=%s", (po.id,)) == [
        ("withdrawn",)
    ]


def test_change_submit_takes_the_award_lock_before_the_po_lock(lifecycle_db):
    db, _ = lifecycle_db
    po, _ = issued(db)
    publish_change(db)
    change = open_change(db, po, lines=[line(quantity="5")])
    original, calls = changes_module.lock_award, []

    def wrapper(*args, **kwargs):
        calls.append(1)
        probe_po_unlocked(db, po.id)  # the PO is still free when the award is taken
        return original(*args, **kwargs)

    with patch.object(changes_module, "lock_award", wrapper):
        pending = submit(db, change)
    assert calls and pending.status == "approval_pending"  # plant 2: never called


def fresh_pending(db, rfq, a, award_id):
    po = create(db, rfq, a, award_id, "1")
    submit_po(db, po)
    return po


def test_withdraw_and_the_final_approve_have_one_winner_and_no_deadlock(lifecycle_db):
    db, _ = lifecycle_db
    from tests.purchasing.test_po_approval import publish

    publish(db)
    rfq, _, a, _, award_id, _ = ready(db)
    for _round in range(ROUNDS):
        po = fresh_pending(db, rfq, a, award_id)
        [(instance_id,)] = sql(
            db, "SELECT id FROM approval_instance WHERE document_id=%s", (po.id,)
        )
        task_id = get_instance(db[0], Scope(db[1]), instance_id).tasks[-1].id
        version = status(db, po)[1]

        def work(conn, n, po=po, task_id=task_id, version=version):
            with correlation_context("withdraw-approve"):
                if n == 0:
                    return pos(db, conn).withdraw(po.id).status
                decide(
                    conn, Scope(db[1]), task_id=task_id, actor_id=reviewer(db),
                    body=DecisionWrite(outcome="approve", document_version=version, comment="ok"),
                )  # fmt: skip
                return "approved"

        outcome = _race(db, work)
        assert outcome in (
            ["approved", "invalid_transition"],  # decide committed before withdraw read the PO
            ["approved", "task_closed"],
            ["draft", "task_closed"],
            ["draft", "invalid_transition"],
        ), outcome
        after = status(db, po)[0]
        instance = sql(db, "SELECT status FROM approval_instance WHERE id=%s", (instance_id,))
        assert (after, instance) in (("draft", [("withdrawn",)]), ("approved", [("approved",)]))


def test_withdraw_and_cancel_of_a_pending_po_have_one_winner_and_no_deadlock(lifecycle_db):
    db, _ = lifecycle_db
    from tests.purchasing.test_po_approval import publish

    publish(db)
    rfq, _, a, _, award_id, _ = ready(db)
    for _round in range(ROUNDS):
        po = fresh_pending(db, rfq, a, award_id)
        version = status(db, po)[1]
        body = PoCancelCreate(reason="x")

        def work(conn, n, po=po, version=version):
            with correlation_context("withdraw-cancel"):
                if n == 0:
                    return pos(db, conn).withdraw(po.id).status
                return cancel(conn, Scope(db[1]), po.id, db[3], f'"{version}"', body).status

        outcome = _race(db, work)
        after = status(db, po)
        assert after[0] in ("draft", "cancelled"), outcome
        if after[0] == "cancelled":
            assert after[1] == version + 2
        losers = {"task_closed", "invalid_transition", "stale_version"}
        assert sum(x in losers for x in outcome) == 1, outcome


def test_auto_approving_submit_and_cancel_of_one_issued_po_do_not_deadlock(lifecycle_db):
    db, _ = lifecycle_db
    workflow = definition(db, document_type="po_change")
    draft(db, workflow, {"nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
                         "edges": [{"from": "start", "to": "end"}]})  # fmt: skip
    svc(db).publish(workflow.id, 1, True)
    body = PoCancelCreate(reason="x")
    rfq, _, a, _, award_id, _ = ready(db, "10", "10")
    for _round in range(ROUNDS):
        po = create(db, rfq, a, award_id, "2")
        approve(db, po)
        run(db, po)
        po = pos(db).get(po.id)
        change = open_change(db, po, lines=[line(quantity="1")])

        def work(conn, n, po=po, change=change):
            with correlation_context("submit-cancel"):
                if n == 0:
                    return (
                        changes(db, conn).submit(po.id, change.number, f'"{change.version}"').status
                    )
                return cancel(conn, Scope(db[1]), po.id, db[3], '"2"', body).status

        cancels = sql(db, "SELECT count(*) FROM audit_log WHERE action LIKE '%%cancel%%'")
        outcome = _race(db, work)  # a DeadlockDetected would propagate and fail the test
        assert len(outcome) == 2 and "approved" in outcome and "cancelled" not in outcome, outcome
        [loser] = [x for x in outcome if x != "approved"]
        assert loser in {"change_in_progress", "stale_version", "invalid_transition"}, outcome
        assert sql(
            db,
            "SELECT count(*) FROM purchase_order WHERE number=%s AND revision=1 "
            "AND status='issued'",
            (po.number,),
        ) == [(1,)]
        assert sql(db, "SELECT count(*) FROM purchase_order WHERE status='cancelled'") == [(0,)]
        assert sql(db, "SELECT count(*) FROM audit_log WHERE action LIKE '%%cancel%%'") == cancels
