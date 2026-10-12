"""APR-013/014, AUD-001 and COL-003 through the production app and Postgres."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals import engine
from flo.modules.approvals.models import WorkflowRepository
from flo.modules.approvals.schemas import DecisionWrite, DocumentSummary, Outcome
from flo.modules.approvals.service import decide, decisions_for, get_instance, withdraw
from flo.modules.budget.service import InsufficientBudget
from flo.modules.identity.service import (
    AuthorizationTarget,
    IdentityAuthorizationService,
    ScopeType,
)
from tests.approvals.test_engine import begin, published
from tests.approvals.test_engine import setup as setup
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db
from tests.org.test_bootstrap import connection_url


@pytest.fixture
def case(setup, monkeypatch):
    db, adapter, users, grants, role = setup
    # The production session's administrator is a named member of the approver role.
    adapter.value = replace(
        adapter.value,
        requester_id=users[2],
        target=AuthorizationTarget(ScopeType.ORG, db[1], "requisition", adapter.document),
    )
    grant = IdentityAuthorizationService(db[0], Scope(db[1]), db[3]).grant_role(
        db[3], role.id, AuthorizationTarget.organization(db[1])
    )
    monkeypatch.setattr(
        adapter,
        "summaries",
        lambda conn, scope, ids: {
            adapter.document: DocumentSummary(
                "REQ-001",
                "Request",
                None,
                users[2],
                "/requisitions/" + str(adapter.document),
                "private note",
            )
        },
    )
    return setup, grant


def body(outcome="approve", **changes):
    return {"outcome": outcome, "document_version": 1} | changes


async def post(browser, path, data, key=None):
    return await browser.post(
        "/api/v1/approvals/" + path, json=data, headers=headers(browser, key or uuid4().hex)
    )


def decision_path(instance):
    return f"tasks/{instance.tasks[-1].id}/decisions"


@pytest.mark.parametrize(
    "outcome,extra,status",
    [
        ("approve", {}, "approved"),
        ("reject", {"reject_mode": "return_to_draft", "comment": "Revise scope"}, "rejected"),
        ("reject", {"reject_mode": "permanent", "comment": "Not proceeding"}, "rejected"),
        ("return", {"comment": "Explain scope"}, "returned"),
        ("comment", {"comment": "Reviewed evidence"}, "running"),
    ],
)
def test_each_decision_records_attribution_audit_and_private_outbox(case, outcome, extra, status):
    setup, _ = case
    db, adapter, users, *_ = setup
    published(setup)
    instance = begin(setup)

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await post(browser, decision_path(instance), body(outcome, **extra))
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["instance"]["status"] == status
            d = result["decision"]
            assert d["actor_email"] == "admin@example.test"
            assert d["authority"] == "member" and d["on_behalf_of_email"] is None
            assert d["task_id"] == str(instance.tasks[0].id) and d["node_id"] == "one"
            assert d["step_label"] == "Finance" and d["document_version"] == 1
            assert d["comment"] == extra.get("comment") and d["reject_mode"] == extra.get(
                "reject_mode"
            )
            assert d["outcome"] == outcome and d["decided_at"]
            assert d["members_at_decision"] == [
                "a@example.test",
                "admin@example.test",
                "b@example.test",
            ]
            trail = await browser.get(f"/api/v1/approvals/instances/{instance.id}/decisions")
            assert trail.status_code == 200 and trail.json() == [d]

    asyncio.run(run())
    assert db[0].execute("SELECT count(*) FROM approval_decision").fetchone() == (1,)
    assert db[0].execute(
        "SELECT count(*) FROM audit_log WHERE action='approval.decision'"
    ).fetchone() == (1,)
    assert db[0].execute(
        "SELECT before,after->>'status' FROM audit_log WHERE action='approval.decision'"
    ).fetchone() == ({"status": "running"}, status)
    payloads = (
        db[0]
        .execute("SELECT payload FROM outbox WHERE payload->>'template'='approval-outcome-v1'")
        .fetchall()
    )
    if status == "running":
        assert payloads == [] and not adapter.calls
    else:
        assert len(adapter.calls) == 1 and adapter.calls[0][0].value == status
        assert payloads == [
            (
                {
                    "to": "c@example.test",
                    "template": "approval-outcome-v1",
                    "context": {
                        "document_type": "requisition",
                        "number": "REQ-001",
                        "outcome": status,
                        "link": "/requisitions/" + str(adapter.document),
                    },
                },
            )
        ]
        assert "300000" not in json.dumps(payloads) and extra.get(
            "comment", "private note"
        ) not in json.dumps(payloads)


@pytest.mark.parametrize(
    "data,field",
    [
        (body("reject", comment="Reason"), "reject_mode"),
        (body("approve", reject_mode="permanent"), "reject_mode"),
        (body("return"), "comment"),
        (body("comment"), "comment"),
        (body("reject", reject_mode="permanent"), "comment"),
        (body("comment", comment=""), "comment"),
        (body("comment", comment="x" * 4001), "comment"),
        (body("comment", comment="Reason", assign_project_id=str(uuid4())), "assign_project_id"),
    ],
)
def test_decision_field_guards(case, data, field):
    setup, _ = case
    published(setup)
    instance = begin(setup)

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await post(browser, decision_path(instance), data)
            assert response.status_code == 422, response.text
            assert any(e["field"].strip("/") == field for e in response.json()["errors"])

    asyncio.run(run())
    assert setup[0][0].execute("SELECT count(*) FROM approval_decision").fetchone() == (0,)


@pytest.mark.parametrize("outcome", ["approve", "reject", "return", "withdraw"])
def test_callback_insufficient_budget_rolls_back_every_effect(case, monkeypatch, outcome):
    setup, _ = case
    db, adapter, *_ = setup
    if outcome == "withdraw":
        adapter.value = replace(adapter.value, requester_id=db[3])
    published(setup)
    instance = begin(setup)

    def fail(conn, scope, document_id, outcome, context):
        # The callback can observe the evidence but it has not committed.
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
        assert conn.execute("SELECT count(*) FROM approval_decision").fetchone() == (1,)
        raise InsufficientBudget(Decimal("0"), Decimal("300000"))

    monkeypatch.setattr(adapter, "on_outcome", fail)
    data = body(
        outcome, **({"reject_mode": "permanent"} if outcome == "reject" else {}), comment="Reason"
    )

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await post(
                browser,
                f"instances/{instance.id}/withdraw"
                if outcome == "withdraw"
                else decision_path(instance),
                {"comment": "Reason"} if outcome == "withdraw" else data,
            )
            assert response.status_code == 409, response.text
            assert response.json()["checks"]["problem"] == "insufficient_budget"

    asyncio.run(run())
    assert db[0].execute("SELECT count(*) FROM approval_decision").fetchone() == (0,)
    current = get_instance(db[0], Scope(db[1]), instance.id)
    assert current.status == "running" and current.tasks[0].status == "open"
    assert db[0].execute(
        "SELECT count(*) FROM audit_log WHERE action='approval.decision'"
    ).fetchone() == (0,)
    assert db[0].execute(
        "SELECT count(*) FROM outbox WHERE payload->>'template'='approval-outcome-v1'"
    ).fetchone() == (0,)

    # Document modules also call the service outside the route's idempotency wrapper.
    # That caller must receive the same atomicity guarantee from this service itself.
    with pytest.raises(InsufficientBudget):
        if outcome == "withdraw":
            withdraw(
                db[0],
                Scope(db[1]),
                document_type="requisition",
                document_id=adapter.document,
                actor_id=db[3],
                reason="Reason",
            )
        else:
            decide(
                db[0],
                Scope(db[1]),
                task_id=instance.tasks[0].id,
                actor_id=db[3],
                body=DecisionWrite(**data),
            )
    assert db[0].execute("SELECT count(*) FROM approval_decision").fetchone() == (0,)
    current = get_instance(db[0], Scope(db[1]), instance.id)
    assert current.status == "running" and current.tasks[0].status == "open"
    assert db[0].execute(
        "SELECT count(*) FROM audit_log WHERE action='approval.decision'"
    ).fetchone() == (0,)
    assert db[0].execute(
        "SELECT count(*) FROM outbox WHERE payload->>'template'='approval-outcome-v1'"
    ).fetchone() == (0,)


def test_replay_and_key_body_mismatch(case):
    setup, _ = case
    published(setup)
    instance = begin(setup)

    async def run():
        async with client(app) as browser:
            await login(browser)
            first = await post(browser, decision_path(instance), body(), "decision-replay")
            again = await post(browser, decision_path(instance), body(), "decision-replay")
            assert first.status_code == again.status_code == 200
            assert first.json() == again.json()
            mismatch = await post(
                browser,
                decision_path(instance),
                body("comment", comment="Different"),
                "decision-replay",
            )
            assert mismatch.status_code == 422 and mismatch.json()["type"].endswith(
                "/idempotency_key_reused"
            )
            closed = await post(browser, decision_path(instance), body())
            assert closed.status_code == 409 and closed.json()["checks"]["problem"] == "task_closed"

    asyncio.run(run())
    assert setup[0][0].execute("SELECT count(*) FROM approval_decision").fetchone() == (1,)


@pytest.mark.parametrize(
    "two,supported,valid", [(False, True, True), (False, False, False), (True, True, False)]
)
def test_assignment_final_step_and_adapter_guard(case, two, supported, valid):
    setup, _ = case
    db, adapter, *_ = setup
    adapter.accepts_project_assignment = supported
    published(setup, two=two)
    instance = begin(setup)
    project = uuid4()

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await post(
                browser, decision_path(instance), body(assign_project_id=str(project))
            )
            assert response.status_code == (200 if valid else 422), response.text
            if not valid:
                assert response.json()["errors"][0]["field"] == "assign_project_id"

    asyncio.run(run())
    if valid:
        assert adapter.calls[0][1].assign_project_id == project
    else:
        assert db[0].execute("SELECT count(*) FROM approval_decision").fetchone() == (0,)


def test_all_and_quorum_live_evidence_comments_do_not_complete(case):
    setup, _ = case
    db, adapter, users, grants, role = setup
    published(setup, mode="all")
    instance = begin(setup)
    for outcome in ("comment", "approve"):
        result = decide(
            db[0],
            Scope(db[1]),
            task_id=instance.tasks[0].id,
            actor_id=db[3],
            body=DecisionWrite(**body(outcome, comment="Evidence")),
        )
        assert result.instance.status == "running"
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    auth.revoke_role(grants[1].id)
    result = decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[0].id,
        actor_id=users[0],
        body=DecisionWrite(**body()),
    )
    assert result.instance.status == "approved"
    assert result.decision.members_at_decision == ["a@example.test", "admin@example.test"]
    assert len(decisions_for(db[0], Scope(db[1]), instance.id)) == 3


def test_approval_routes_foreign_tenant(case):
    setup, _ = case
    published(setup)
    instance = begin(setup)

    async def run():
        async with client(app) as browser:
            await login(browser, "second@example.test")
            assert (await post(browser, decision_path(instance), body())).status_code == 404
            assert (await post(browser, f"instances/{instance.id}/withdraw", {})).status_code == 404
            assert (
                await browser.get(f"/api/v1/approvals/instances/{instance.id}/decisions")
            ).status_code == 404

    asyncio.run(run())


def test_decision_and_withdraw_authorization_by_role(case):
    setup, member_grant = case
    db, adapter, *_ = setup
    published(setup)
    instance = begin(setup)
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    roles = auth.seed_baseline_roles()

    async def run():
        async with client(app) as browser:
            await login(browser)
            # Remove administrator grants: then grant each actual baseline role independently.
            for row in (
                db[0]
                .execute("SELECT id FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
                .fetchall()
            ):
                auth.revoke_role(row[0])
            for code in ("requestor", "auditor", "organization-administrator", "approver"):
                grant = auth.grant_role(
                    db[3], roles[code].id, AuthorizationTarget.organization(db[1])
                )
                response = await post(
                    browser, decision_path(instance), body("comment", comment="Evidence")
                )
                assert response.status_code == (200 if code == "approver" else 403), response.text
                if code == "organization-administrator":
                    assert response.json()["detail"] == "You are not an approver for this step."
                response = await post(browser, f"instances/{instance.id}/withdraw", {})
                assert response.status_code == 403
                assert response.json()["detail"] == "Only the requester may withdraw this approval."
                auth.revoke_role(grant.id)
            assert (await post(browser, decision_path(instance), body())).status_code == 404
            assert (await post(browser, f"instances/{instance.id}/withdraw", {})).status_code == 404
            assert (
                await browser.get(f"/api/v1/approvals/instances/{instance.id}/decisions")
            ).status_code == 404

    asyncio.run(run())


def test_requester_withdraw_replay_and_closed_instance(case):
    setup, _ = case
    db, adapter, *_ = setup
    adapter.value = replace(adapter.value, requester_id=db[3])
    published(setup)
    instance = begin(setup)

    async def run():
        async with client(app) as browser:
            await login(browser)
            path = f"instances/{instance.id}/withdraw"
            first = await post(browser, path, {"comment": "No longer needed"}, "withdraw-replay")
            assert first.status_code == 200 and first.json()["status"] == "withdrawn", first.text
            assert (
                await post(browser, path, {"comment": "No longer needed"}, "withdraw-replay")
            ).json() == first.json()
            closed = await post(browser, path, {})
            assert closed.status_code == 409 and closed.json()["checks"]["problem"] == "task_closed"

    asyncio.run(run())
    trail = decisions_for(db[0], Scope(db[1]), instance.id)
    assert len(trail) == 1 and trail[0].outcome == "withdraw"
    d = trail[0]
    assert d.authority == "requester" and d.actor_email == "admin@example.test"
    assert d.task_id is d.node_id is d.step_label is None
    assert d.comment == "No longer needed" and d.document_version == 1 and d.decided_at
    assert d.members_at_decision == [] and adapter.calls[0][0] == Outcome.WITHDRAWN
    assert get_instance(db[0], Scope(db[1]), instance.id).tasks[0].status == "cancelled"
    assert get_instance(db[0], Scope(db[1]), instance.id).route[0].state == "completed"


def test_document_withdraw_admin_in_callers_transaction(case):
    setup, _ = case
    db, adapter, *_ = setup
    published(setup)
    instance = begin(setup)
    with pytest.raises(RuntimeError), tenant_transaction(db[0], Scope(db[1])):
        result = withdraw(
            db[0],
            Scope(db[1]),
            document_type="requisition",
            document_id=adapter.document,
            actor_id=db[3],
            reason="Cancelled by authorized caller",
        )
        assert result.status == "withdrawn"
        assert decisions_for(db[0], Scope(db[1]), instance.id)[0].authority == "admin"
        raise RuntimeError("caller rolls back")
    assert not decisions_for(db[0], Scope(db[1]), instance.id)
    assert get_instance(db[0], Scope(db[1]), instance.id).status == "running"


def test_decision_append_only_as_application_role_and_tenant_rls(case):
    setup, _ = case
    db, adapter, *_ = setup
    published(setup)
    instance = begin(setup)
    decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[0].id,
        actor_id=db[3],
        body=DecisionWrite(**body("comment", comment="Immutable rationale")),
    )
    conn = db[0]
    role = "decision_test_" + uuid4().hex
    conn.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role))
    )
    try:
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        conn.execute(
            sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON approval_decision TO {}").format(
                sql.Identifier(role)
            )
        )
        for statement in (
            "UPDATE approval_decision SET comment='altered'",
            "DELETE FROM approval_decision",
            "TRUNCATE approval_decision",
        ):
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState), conn.transaction():
                conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                with tenant_transaction(conn, Scope(db[1])):
                    conn.execute(statement)
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            with tenant_transaction(conn, Scope(db[2])):
                assert conn.execute("SELECT * FROM approval_decision").fetchall() == []
                with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                    conn.execute(
                        "INSERT INTO approval_decision(id,org_id,instance_id,actor_id,authority,"
                        "outcome,document_version,members_at_decision) "
                        "VALUES(%s,%s,%s,%s,'member','comment',1,'[]')",
                        (uuid4(), db[1], instance.id, db[3]),
                    )
                with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
                    conn.execute(
                        "INSERT INTO approval_decision(id,org_id,instance_id,actor_id,authority,"
                        "outcome,document_version,members_at_decision) "
                        "VALUES(%s,%s,%s,%s,'member','comment',1,'[]')",
                        (uuid4(), db[2], instance.id, db[3]),
                    )
    finally:
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
    assert decisions_for(conn, Scope(db[1]), instance.id)[0].comment == "Immutable rationale"


def test_membership_is_resolved_after_waiting_for_instance_lock(case):
    setup, grant = case
    db, adapter, *_ = setup
    published(setup)
    instance = begin(setup)
    entered = Event()
    with psycopg.connect(connection_url(db[0]), autocommit=True) as worker:

        def run():
            with correlation_context("membership-race"):
                entered.set()
                try:
                    return decide(
                        worker,
                        Scope(db[1]),
                        task_id=instance.tasks[0].id,
                        actor_id=db[3],
                        body=DecisionWrite(**body()),
                    ).instance.status
                except ProblemError as exc:
                    return exc.code

        with ThreadPoolExecutor(1) as pool:
            with tenant_transaction(db[0], Scope(db[1])):
                engine._instance(WorkflowRepository(db[0], Scope(db[1])), instance.id, lock=True)
                future = pool.submit(run)
                assert entered.wait(10)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    wait = (
                        db[0]
                        .execute(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                            (worker.info.backend_pid,),
                        )
                        .fetchone()
                    )
                    if wait == ("Lock",):
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("decision never waited for the instance lock")
                IdentityAuthorizationService(db[0], Scope(db[1]), db[3]).revoke_role(grant.id)
            assert future.result(10).value == "forbidden"
    assert not decisions_for(db[0], Scope(db[1]), instance.id)
    assert get_instance(db[0], Scope(db[1]), instance.id).status == "running"


@pytest.mark.parametrize("column,value", [("authority", "owner"), ("outcome", "accept")])
def test_decision_database_catalogue_guards(case, column, value):
    setup, _ = case
    db = setup[0]
    published(setup)
    instance = begin(setup)
    # Each CHECK is exercised by an actual malformed row, rather than schema inspection.
    with pytest.raises(psycopg.errors.CheckViolation), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            sql.SQL(
                "INSERT INTO approval_decision(id,org_id,instance_id,actor_id,authority,"
                "outcome,document_version,members_at_decision) "
                "VALUES(%s,%s,%s,%s,%s,%s,1,'[]')"
            ),
            (
                uuid4(),
                db[1],
                instance.id,
                db[3],
                value if column == "authority" else "member",
                value if column == "outcome" else "comment",
            ),
        )


def test_decision_removed_identity_and_chronological_trail(case):
    setup, _ = case
    db = setup[0]
    published(setup)
    instance = begin(setup)
    ghost = uuid4()
    with tenant_transaction(db[0], Scope(db[1])):
        for minute, actor in [(2, db[3]), (1, ghost)]:
            db[0].execute(
                "INSERT INTO approval_decision(id,org_id,instance_id,task_id,actor_id,on_behalf_of,"
                "authority,outcome,comment,document_version,members_at_decision,decided_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,'delegate','comment',%s,1,'[\"historic@example.test\"]',"
                "now() + make_interval(mins => %s))",
                (
                    uuid4(),
                    db[1],
                    instance.id,
                    instance.tasks[0].id,
                    actor,
                    ghost,
                    str(minute),
                    minute,
                ),
            )
    trail = decisions_for(db[0], Scope(db[1]), instance.id)
    assert [d.comment for d in trail] == ["1", "2"]
    assert [d.actor_email for d in trail] == ["(removed user)", "admin@example.test"]
    assert all(d.on_behalf_of_email == "(removed user)" for d in trail)
    assert all(d.members_at_decision == ["historic@example.test"] for d in trail)


@pytest.mark.parametrize("mode", ["any", "all", {"quorum": 2}])
@pytest.mark.parametrize(
    "outcome,extra",
    [
        ("reject", {"reject_mode": "permanent", "comment": "Declined"}),
        ("return", {"comment": "Revise"}),
    ],
)
def test_terminal_decisions_end_every_mode_and_reject_mode_reaches_callback(
    case, mode, outcome, extra
):
    setup, _ = case
    db, adapter, *_ = setup
    published(setup, mode=mode)
    instance = begin(setup)
    result = decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[0].id,
        actor_id=db[3],
        body=DecisionWrite(**body(outcome, **extra)),
    )
    assert result.instance.status == ("rejected" if outcome == "reject" else "returned")
    assert result.instance.tasks[0].status == "completed"
    assert result.instance.route[0].state == "completed"
    assert db[0].execute(
        "SELECT route FROM approval_instance WHERE id=%s", (instance.id,)
    ).fetchone()[0][0]["decided_by"] == [str(db[3])]
    assert adapter.calls[0][1].reject_mode == extra.get("reject_mode")
    assert adapter.calls[0][1].decision_id == result.decision.id


def test_quorum_actual_decisions_and_pinned_two_step_progression(case):
    setup, _ = case
    db, adapter, users, *_ = setup
    published(setup, mode={"quorum": 2}, two=True, repeat=True)
    instance = begin(setup)
    first = decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[0].id,
        actor_id=db[3],
        body=DecisionWrite(**body()),
    )
    assert first.instance.current_node_id == "one"
    second = decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[0].id,
        actor_id=users[0],
        body=DecisionWrite(**body()),
    )
    assert second.instance.current_node_id == "two" and second.instance.status == "running"
    assert [t.status for t in second.instance.tasks] == ["completed", "open"]
    for actor in [users[0], users[1], db[3]]:
        final = decide(
            db[0],
            Scope(db[1]),
            task_id=second.instance.tasks[1].id,
            actor_id=actor,
            body=DecisionWrite(**body()),
        )
    assert final.instance.status == "approved" and len(adapter.calls) == 1
    assert len(decisions_for(db[0], Scope(db[1]), instance.id)) == 5


@pytest.mark.parametrize(
    "instance_status,task_status", [("returned", "open"), ("running", "cancelled")]
)
def test_comment_rejects_closed_instance_or_task(case, instance_status, task_status):
    setup, _ = case
    db = setup[0]
    published(setup)
    instance = begin(setup)
    db[0].execute(
        "UPDATE approval_instance SET status=%s WHERE id=%s", (instance_status, instance.id)
    )
    db[0].execute(
        "UPDATE approval_task SET status=%s WHERE id=%s", (task_status, instance.tasks[0].id)
    )

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await post(
                browser, decision_path(instance), body("comment", comment="Too late")
            )
            assert (
                response.status_code == 409
                and response.json()["checks"]["problem"] == "task_closed"
            )

    asyncio.run(run())
    assert not decisions_for(db[0], Scope(db[1]), instance.id)


def test_document_withdraw_without_running_instance(case):
    setup, _ = case
    db, adapter, *_ = setup
    with pytest.raises(ProblemError) as exc:
        withdraw(
            db[0],
            Scope(db[1]),
            document_type="requisition",
            document_id=adapter.document,
            actor_id=db[3],
            reason="Withdraw",
        )
    assert exc.value.checks["problem"] == "task_closed"


def test_concurrent_any_records_only_one_decision(case):
    setup, _ = case
    db, adapter, users, *_ = setup
    published(setup)
    instance = begin(setup)
    entered, release = Event(), Event()
    with (
        psycopg.connect(connection_url(db[0]), autocommit=True) as first,
        psycopg.connect(connection_url(db[0]), autocommit=True) as second,
    ):
        adapter.pause = (first.info.backend_pid, entered, release)

        def run(conn, actor):
            with correlation_context("decision-race"):
                try:
                    return decide(
                        conn,
                        Scope(db[1]),
                        task_id=instance.tasks[0].id,
                        actor_id=actor,
                        body=DecisionWrite(**body()),
                    ).instance.status
                except ProblemError as exc:
                    return exc.checks["problem"]

        with ThreadPoolExecutor(2) as pool:
            one = pool.submit(run, first, db[3])
            assert entered.wait(10)
            two = pool.submit(run, second, users[0])
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if db[0].execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                        (second.info.backend_pid,),
                    ).fetchone() == ("Lock",):
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("second decision never waited for the instance lock")
            finally:
                release.set()
            assert sorted([one.result(10), two.result(10)]) == ["approved", "task_closed"]
    assert len(decisions_for(db[0], Scope(db[1]), instance.id)) == 1
    assert len(adapter.calls) == 1


def test_withdraw_old_instance_cannot_withdraw_new_running_instance(case):
    setup, _ = case
    db, adapter, *_ = setup
    adapter.value = replace(adapter.value, requester_id=db[3])
    published(setup)
    old = begin(setup)
    withdraw(
        db[0],
        Scope(db[1]),
        document_type="requisition",
        document_id=adapter.document,
        actor_id=db[3],
        reason="First submission withdrawn",
    )
    current = begin(setup)

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await post(browser, f"instances/{old.id}/withdraw", {})
            assert (
                response.status_code == 409
                and response.json()["checks"]["problem"] == "task_closed"
            )

    asyncio.run(run())
    assert get_instance(db[0], Scope(db[1]), current.id).status == "running"
    assert not decisions_for(db[0], Scope(db[1]), current.id)


@pytest.mark.parametrize("outcome", ["reject", "return", "comment"])
def test_assignment_requires_approve_even_on_supported_final_step(case, outcome):
    setup, _ = case
    adapter = setup[1]
    adapter.accepts_project_assignment = True
    published(setup)
    instance = begin(setup)
    extra = {"comment": "Reason", "assign_project_id": str(uuid4())}
    if outcome == "reject":
        extra["reject_mode"] = "permanent"

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await post(browser, decision_path(instance), body(outcome, **extra))
            assert response.status_code == 422
            assert response.json()["errors"][0]["field"] == "assign_project_id"

    asyncio.run(run())
    assert not decisions_for(setup[0][0], Scope(setup[0][1]), instance.id)


def test_stale_client_version_is_refused(case):
    setup, _ = case
    published(setup)
    instance = begin(setup)

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await post(browser, decision_path(instance), body(document_version=99))
            assert response.status_code == 409
            assert response.json()["checks"]["problem"] == "stale_version"

    asyncio.run(run())
    assert not decisions_for(setup[0][0], Scope(setup[0][1]), instance.id)


@pytest.mark.parametrize("outcome", ["approve", "reject", "return"])
def test_actor_cannot_decide_twice_but_comments_remain_allowed(case, outcome):
    setup, _ = case
    db = setup[0]
    published(setup, mode="all")
    instance = begin(setup)
    decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[0].id,
        actor_id=db[3],
        body=DecisionWrite(**body()),
    )

    async def run():
        async with client(app) as browser:
            await login(browser)
            data = body(outcome, comment="Reason")
            if outcome == "reject":
                data["reject_mode"] = "permanent"
            response = await post(browser, decision_path(instance), data)
            assert response.status_code == 409
            assert response.json()["checks"]["problem"] == "already_decided"
            for _ in range(2):
                assert (
                    await post(browser, decision_path(instance), body("comment", comment="Note"))
                ).status_code == 200

    asyncio.run(run())
    assert [d.outcome for d in decisions_for(db[0], Scope(db[1]), instance.id)] == [
        "approve",
        "comment",
        "comment",
    ]
    with pytest.raises(psycopg.errors.UniqueViolation), tenant_transaction(db[0], Scope(db[1])):
        db[0].execute(
            "INSERT INTO approval_decision(id,org_id,instance_id,task_id,actor_id,authority,"
            "outcome,document_version,members_at_decision) "
            "VALUES(%s,%s,%s,%s,%s,'member','approve',1,'[]')",
            (uuid4(), db[1], instance.id, instance.tasks[0].id, db[3]),
        )


@pytest.mark.parametrize("outcome", ["reject", "return", "comment", "withdraw"])
def test_whitespace_rationale_is_rejected(case, outcome):
    setup, _ = case
    db, adapter, *_ = setup
    adapter.value = replace(adapter.value, requester_id=db[3])
    published(setup)
    instance = begin(setup)

    async def run():
        async with client(app) as browser:
            await login(browser)
            data = body(outcome, comment=" \t ")
            if outcome == "reject":
                data["reject_mode"] = "permanent"
            response = await post(
                browser,
                f"instances/{instance.id}/withdraw"
                if outcome == "withdraw"
                else decision_path(instance),
                {"comment": " \t "} if outcome == "withdraw" else data,
            )
            assert response.status_code == 422
            assert response.json()["errors"][0]["field"] == "comment"

    asyncio.run(run())
    assert not decisions_for(db[0], Scope(db[1]), instance.id)


def test_withdraw_without_comment_records_null_and_audit_before(case):
    setup, _ = case
    db, adapter, *_ = setup
    adapter.value = replace(adapter.value, requester_id=db[3])
    published(setup)
    instance = begin(setup)

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await post(browser, f"instances/{instance.id}/withdraw", {})
            assert response.status_code == 200
            assert response.json()["route"][0]["state"] == "completed"
            trail = await browser.get(f"/api/v1/approvals/instances/{instance.id}/decisions")
            assert trail.json()[0]["comment"] is None

    asyncio.run(run())
    assert db[0].execute(
        "SELECT before,after FROM audit_log WHERE action='approval.decision'"
    ).fetchone() == (
        {"status": "running"},
        {
            "status": "withdrawn",
            "decision_id": str(decisions_for(db[0], Scope(db[1]), instance.id)[0].id),
        },
    )
