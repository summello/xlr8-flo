"""PO approval before issue: submit, withdraw, outcomes, adapter and evidence (E13-S04)."""

import asyncio
from copy import deepcopy

import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals import engine
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import decide, get_instance
from tests.approvals.test_engine import graph
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.isolation.test_real_sessions import client, login
from tests.purchasing.test_purchase_orders import create, pos, ready, send
from tests.requisitions.test_approval_effects import grant, reviewer
from tests.requisitions.test_header import bootstrap_db as bootstrap_db  # noqa: F401
from tests.requisitions.test_header import empty_database as empty_database  # noqa: F401
from tests.requisitions.test_header import header_db as header_db  # noqa: F401
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db  # noqa: F401
from tests.requisitions.test_lines import lines_db as lines_db  # noqa: F401
from tests.sourcing.test_rfq import refused, sql
from tests.vendors.test_vendors import regrant


def publish(db, flow=None):
    workflow = definition(db, document_type="po")
    draft(db, workflow, flow or graph())
    svc(db).publish(workflow.id, 1, True)
    grant(db, "purchase_order.approve")


def one(db, qty="2", workflow=True):
    if workflow:
        publish(db)
    rfq, _, a, _, award_id, _ = ready(db)
    return create(db, rfq, a, award_id, qty)


def status(db, po):
    return sql(db, "SELECT status, version FROM purchase_order WHERE id=%s", (po.id,))[0]


def submit(db, po, actor=None):
    with correlation_context("po"):
        return pos(db, actor=actor).submit(po.id)


def decide_po(db, po, outcome="approve"):
    [(instance_id,)] = sql(
        db,
        "SELECT id FROM approval_instance WHERE document_type='po' AND document_id=%s "
        "ORDER BY started_at DESC LIMIT 1",
        (po.id,),
    )
    instance = get_instance(db[0], Scope(db[1]), instance_id)
    version = sql(db, "SELECT version FROM purchase_order WHERE id=%s", (po.id,))[0][0]
    with correlation_context("decide"):
        return decide(
            db[0], Scope(db[1]), task_id=instance.tasks[-1].id, actor_id=reviewer(db),
            body=DecisionWrite(
                outcome=outcome, document_version=version, comment="ok",
                reject_mode="return_to_draft" if outcome == "reject" else None,
            ),
        )  # fmt: skip


def test_no_published_po_workflow_is_a_409_and_writes_nothing(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db, workflow=False)
    error = refused(lambda: submit(db, po))
    assert error.checks["problem"] == "workflow_not_set_up"
    assert status(db, po) == ("draft", 1)  # plant 2: auto-approving here fails this
    assert sql(db, "SELECT count(*) FROM approval_instance WHERE document_type='po'") == [(0,)]


def test_approve_moves_to_approved_and_posts_nothing(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    ledger, drift = (
        sql(db, "SELECT count(*) FROM ledger_entry"),
        sql(db, "SELECT sum(reserved) FROM project_balance"),
    )
    submitted = submit(db, po)
    assert submitted.status == "approval_pending" and submitted.version == 2
    decide_po(db, po)
    assert status(db, po)[0] == "approved"
    # plant 1: committing on approval changes the ledger or the balances
    assert sql(db, "SELECT count(*) FROM ledger_entry") == ledger
    assert sql(db, "SELECT sum(reserved) FROM project_balance") == drift
    assert sql(db, "SELECT count(*) FROM audit_log WHERE action='purchase_order.approve'") == [(1,)]


def test_reject_and_withdraw_return_to_draft_and_can_be_resubmitted(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    submit(db, po)
    decide_po(db, po, "reject")
    assert status(db, po)[0] == "draft"
    submit(db, po)
    with correlation_context("po"):
        withdrawn = pos(db).withdraw(po.id)
    assert withdrawn.status == "draft"
    assert sql(db, "SELECT status FROM approval_instance ORDER BY started_at DESC LIMIT 1") == [
        ("withdrawn",)
    ]
    refused(lambda: pos(db).withdraw(po.id))  # nothing pending now
    assert submit(db, po).status == "approval_pending"


def test_a_vendor_that_became_ineligible_since_the_draft_is_a_409(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    sql(db, "UPDATE vendor SET status='suspended' WHERE id=%s RETURNING 1", (po.vendor_id,))
    error = refused(lambda: submit(db, po))
    assert error.checks["problem"] == "vendor_not_eligible"
    assert status(db, po) == ("draft", 1)


def test_a_po_created_on_an_override_submits_despite_the_ineligible_vendor(lifecycle_db):
    db, _ = lifecycle_db
    publish(db)
    rfq, _, a, _, award_id, _ = ready(db)
    sql(db, "UPDATE vendor SET status='suspended' WHERE id=%s RETURNING 1", (a,))
    po = create(db, rfq, a, award_id, "2", override_reason="Sole source")
    assert submit(db, po).status == "approval_pending"


def test_an_amended_requisition_since_create_is_a_stale_version_409(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    assert sql(db, "SELECT requisition_version FROM purchase_order") == [
        sql(db, "SELECT version FROM requisition WHERE id=%s", (po.requisition_id,))[0]
    ]
    # a second PO's ordered write does not move the requisition, so it is not "amended"
    sql(
        db,
        "UPDATE requisition SET version = version + 1 WHERE id=%s RETURNING 1",
        (po.requisition_id,),
    )
    error = refused(lambda: submit(db, po))
    assert error.checks["problem"] == "stale_version"
    assert status(db, po) == ("draft", 1)


def conditional(threshold):
    flow = deepcopy(graph(conditional=True))
    [edge] = [e for e in flow["edges"] if "when" in e]
    edge["when"] = {
        "field": "total", "op": "gt", "value": {"amount": threshold, "currency": "USD"}
    }  # fmt: skip
    return flow


@pytest.mark.parametrize("threshold,expected", [("1", "approval_pending"), ("999999", "approved")])
def test_conditions_on_total_route_as_configured(lifecycle_db, threshold, expected):
    db, _ = lifecycle_db
    publish(db, conditional(threshold))
    rfq, _, a, _, award_id, _ = ready(db)
    po = create(db, rfq, a, award_id, "2")
    assert submit(db, po).status == expected


def test_only_the_creator_submits_or_withdraws(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    other = reviewer(db)
    grant(db, "purchase_order.create")
    assert refused(lambda: submit(db, po, other), ErrorCode.FORBIDDEN)
    assert status(db, po) == ("draft", 1)
    submit(db, po)
    with correlation_context("po"):
        assert refused(lambda: pos(db, actor=other).withdraw(po.id), ErrorCode.FORBIDDEN)
    assert status(db, po)[0] == "approval_pending"
    [instance] = sql(db, "SELECT requester_id FROM approval_instance WHERE document_type='po'")
    assert instance[0] == db[3]  # the approval requester is the PO creator


def test_adapter_is_registered_with_conditions_evidence_and_inbox_summary(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    adapter = engine.adapter_for("po")
    assert adapter.approve_permission == "purchase_order.approve"
    assert adapter.read_permission == "purchase_order.view"
    assert adapter.accepts_project_assignment is False
    assert adapter.fields() == {
        "total": "money", "budget_total_project": "money", "vendor_id": "code", "unit_id": "code"
    }  # fmt: skip
    submit(db, po)
    [(instance_id,)] = sql(db, "SELECT id FROM approval_instance WHERE document_type='po'")
    detail = get_instance(db[0], Scope(db[1]), instance_id)
    assert detail.evidence is not None
    assert set(detail.evidence) >= {
        "award",
        "budget_effect",
        "vendor_eligibility_now",
        "recipients",
    }
    assert detail.evidence["budget_effect"][0]["amount"]
    assert detail.evidence["vendor_eligibility_now"]["eligible"] is True
    row = adapter.summaries(db[0], Scope(db[1]), [po.id])[po.id]
    assert (row.number, row.title, row.href) == (
        po.number, f"Purchase order {po.number}", f"/purchase-orders/{po.id}"
    )  # fmt: skip
    assert str(row.amount.amount) == pos(db).get(po.id).total
    assert str(row.amount.currency) == "USD" and row.requester_id == db[3] and row.note is None
    snapshot = adapter.snapshot(db[0], Scope(db[1]), po.id)
    assert snapshot.requester_id == db[3] and snapshot.version == 2


def test_submit_and_withdraw_routes_authorize_and_replay(lifecycle_db):
    db, _ = lifecycle_db
    po = one(db)
    key = {"Idempotency-Key": "submit-once"}

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["purchase_order.view"])
            assert (await send(browser, "POST", f"/{po.id}/submit")).status_code == 403
            assert (await send(browser, "POST", f"/{po.id}/withdraw")).status_code == 403
            regrant(db, ["purchase_order.view", "purchase_order.create"])
            first = await send(browser, "POST", f"/{po.id}/submit", None, key)
            assert first.status_code == 200 and first.json()["status"] == "approval_pending"
            assert first.headers["ETag"] == '"2"'
            replay = await send(browser, "POST", f"/{po.id}/submit", None, key)
            assert replay.status_code == 200 and replay.json() == first.json()
            gone = await send(browser, "POST", f"/{po.id}/withdraw")
            assert gone.status_code == 200 and gone.json()["status"] == "draft"

    asyncio.run(scenario())
