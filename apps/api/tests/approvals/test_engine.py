"""APR-009/018 and WF-002 against live membership and real Postgres races."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Event
from uuid import UUID, uuid4

import psycopg
import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.logging import correlation_context
from flo.kernel.money import Money
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals import conditions, engine
from flo.modules.approvals.models import WorkflowRepository
from flo.modules.approvals.schemas import DocumentSnapshot, DocumentSummary, Outcome, OutcomeContext
from flo.modules.approvals.service import (
    active_instance,
    advance,
    get_instance,
    instances_for,
    preview_route,
    register_document_type,
    start,
    step_complete,
)
from flo.modules.identity.service import (
    AuthorizationResolver,
    AuthorizationTarget,
    IdentityAuthorizationService,
    role_code,
)
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db
from tests.org.test_bootstrap import connection_url


class Adapter:
    document_type = "requisition"
    approve_permission = "requisition.approve"
    read_permission = "requisition.view"
    accepts_project_assignment = False

    def __init__(self, db):
        self.org = db[1]
        self.document = uuid4()
        self.value = DocumentSnapshot(
            1,
            db[3],
            AuthorizationTarget.organization(db[1]),
            {"amount": Money(Decimal("300000"), "USD")},
            {"amount": "300000"},
        )
        self.calls = []
        self.fail = False
        self.pause = None

    def fields(self):
        return {"amount": "money"}

    def snapshot(self, conn, scope, document_id):
        if document_id != self.document or scope.org_id != self.org:
            from flo.kernel.errors import ErrorCode

            raise ProblemError(ErrorCode.NOT_FOUND)
        if self.pause and self.pause[0] == conn.info.backend_pid:
            self.pause[1].set()
            assert self.pause[2].wait(10)
        return self.value

    def summaries(self, conn, scope, document_ids):
        return {
            id: DocumentSummary(
                number="R-1",
                title="T",
                amount=None,
                requester_id=self.value.requester_id,
                href="/r/1",
                note=None,
            )
            for id in document_ids
        }

    def on_outcome(self, conn, scope, document_id, outcome, context):
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
        assert document_id == self.document
        self.calls.append((outcome, context))
        if self.fail:
            raise RuntimeError("adapter rollback plant")


@pytest.fixture
def setup(workflow_db, monkeypatch):
    db = workflow_db
    monkeypatch.setattr(conditions, "_adapters", {})
    adapter = Adapter(db)
    register_document_type(adapter)
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = auth.seed_baseline_roles()[role_code("approver")]
    users = []
    grants = []
    for letter in ("a", "b", "c"):
        id = IdentityId(uuid4())
        db[0].execute(
            "INSERT INTO identity(id,email,password_hash) VALUES(%s,%s,%s)",
            (id, letter + "@example.test", "$argon2id$test-placeholder"),
        )
        db[0].execute(
            "INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)", (id, db[1])
        )
        users.append(id)
        grants.append(auth.grant_role(id, role.id, adapter.value.target))
    return db, adapter, users, grants, role


def graph(mode="any", two=False, repeat=False, conditional=False):
    nodes = [
        {"id": "start", "kind": "start"},
        {
            "id": "one",
            "kind": "approval",
            "name": "Finance",
            "approvers": {"type": "role", "role_code": "approver"},
            "mode": mode,
            "due_days": 2,
        },
        {"id": "end", "kind": "end"},
    ]
    edges = [{"from": "start", "to": "one"}, {"from": "one", "to": "end"}]
    if two:
        nodes.insert(
            2,
            {
                "id": "two",
                "kind": "approval",
                "name": "Director",
                "allow_repeat": repeat,
                # A permission rule overlaps the role's members without tripping E10-S03's
                # duplicate-approval error, so the one-decision-per-person rule is what excludes.
                "approvers": {"type": "permission"},
                "mode": "all",
            },
        )
        edges = [
            {"from": "start", "to": "one"},
            {"from": "one", "to": "two"},
            {"from": "two", "to": "end"},
        ]
    if conditional:
        nodes.insert(1, {"id": "condition", "kind": "condition", "name": "Estimate"})
        edges = [
            {"from": "start", "to": "condition"},
            {
                "from": "condition",
                "to": "one",
                "when": {
                    "field": "amount",
                    "op": "gt",
                    "value": {"amount": "200000", "currency": "USD"},
                },
            },
            {"from": "condition", "to": "end", "default": True},
            {"from": "one", "to": "end"},
        ]
    return {"nodes": nodes, "edges": edges}


def published(setup, **kwargs):
    db = setup[0]
    row = definition(db)
    version = draft(db, row, graph(**kwargs))
    svc(db).publish(row.id, 1, True)
    return row, version


def resolver(db, conn=None):
    return AuthorizationResolver(conn or db[0], Scope(db[1]))


def begin(setup, conn=None, **kwargs):
    db, adapter, *_ = setup
    return start(
        conn or db[0],
        Scope(db[1]),
        document_type="requisition",
        document_id=adapter.document,
        actor_id=db[3],
        resolver=resolver(db, conn),
        **kwargs,
    )


def context(user):
    return OutcomeContext(user, uuid4(), None, lambda permission, target: True)


def finish(setup, instance, user=None, conn=None):
    db = setup[0]
    return advance(
        conn or db[0],
        Scope(db[1]),
        instance_id=instance.id,
        task_id=instance.tasks[-1].id,
        context=context(user or setup[2][0]),
        resolver=resolver(db, conn),
        decisions=(),
    )


def evidence(user, outcome="approve", task=None, at=None, id=None):
    return {
        "id": id or uuid4(),
        "task_id": task,
        "user_id": user,
        "outcome": outcome,
        "decided_at": at or datetime.now(UTC),
    }


def test_no_workflow_preview_and_start_write_nothing(setup):
    db, adapter, *_ = setup
    preview = preview_route(
        db[0],
        Scope(db[1]),
        document_type="requisition",
        document_id=adapter.document,
        resolver=resolver(db),
    )
    assert preview.workflow_version_id is None and preview.workflow_version is None
    assert not preview.steps and preview.problems[0].code == "no_workflow"
    with pytest.raises(ProblemError) as exc:
        begin(setup)
    assert exc.value.checks["problem"] == "no_workflow"
    assert db[0].execute("SELECT count(*) FROM approval_instance").fetchone() == (0,)


def test_condition_route_auto_approval_and_atomic_callback(setup):
    db, adapter, *_ = setup
    _, version = published(setup, conditional=True)
    routed = begin(setup)
    assert routed.tasks[0].label == "Finance" and routed.workflow_version_id == version.id
    assert routed.tasks[0].due_at > routed.started_at + timedelta(days=1)
    assert (
        db[0]
        .execute("SELECT route FROM approval_instance WHERE id=%s", (routed.id,))
        .fetchone()[0][0]["trace"][0]["result"]
    )
    # A separate document submission taking the default path.
    db[0].execute("UPDATE approval_instance SET status='withdrawn' WHERE id=%s", (routed.id,))
    adapter.value = replace(adapter.value, attributes={"amount": Money(Decimal("100000"), "USD")})
    approved = begin(setup)
    assert approved.status == "approved" and not approved.tasks and approved.finished_at
    assert len(adapter.calls) == 1 and adapter.calls[0][0] == Outcome.APPROVED
    # The auto-approval leaves the same decision audit an interactive approval does.
    assert db[0].execute(
        "SELECT count(*) FROM audit_log WHERE action='approval.decision' AND target_id=%s",
        (approved.id,),
    ).fetchone() == (1,)
    assert db[0].execute(
        "SELECT count(*) FROM outbox WHERE payload->>'template'='approval-outcome-v1'"
    ).fetchone() == (1,)
    ctx = adapter.calls[0][1]
    assert ctx.actor_id == db[3] and ctx.decision_id is None and ctx.assign_project_id is None
    assert ctx.permitted("requisition.view", adapter.value.target)
    adapter.fail = True
    with pytest.raises(RuntimeError):
        begin(setup)
    assert len(instances_for(db[0], Scope(db[1]), "requisition", adapter.document)) == 2


def test_preview_two_steps_shape_no_writes_and_blocked_conditions(setup):
    db, adapter, *_ = setup
    _, version = published(setup, two=True)
    preview = preview_route(
        db[0],
        Scope(db[1]),
        document_type="requisition",
        document_id=adapter.document,
        resolver=resolver(db),
    )
    assert preview.workflow_version_id == version.id and preview.workflow_version == 1
    assert not preview.problems and len(preview.steps) == 2
    first, second = preview.steps
    assert first.approver_emails == ["a@example.test", "b@example.test", "c@example.test"]
    assert first.mode == "any" and first.quorum is None and first.rule == "role approver"
    assert first.excluded == [] and first.due_at is None
    # The second step is a permission rule: the requester is excluded by separation of duty.
    assert second.mode == "all" and second.rule.startswith("permission ")
    assert second.approver_emails == ["a@example.test", "b@example.test", "c@example.test"]
    assert [x.reason for x in second.excluded] == ["separation_of_duty"]
    assert [s.state for s in preview.steps] == ["open", "upcoming"]
    assert db[0].execute("SELECT count(*) FROM approval_instance").fetchone() == (0,)


def test_currency_mismatch_preview_problem_and_start_rollback(setup):
    db, adapter, *_ = setup
    published(setup, conditional=True)
    adapter.value = replace(adapter.value, attributes={"amount": Money(Decimal("300000"), "INR")})
    preview = preview_route(
        db[0],
        Scope(db[1]),
        document_type="requisition",
        document_id=adapter.document,
        resolver=resolver(db),
    )
    assert preview.problems[0].code == "route_blocked" and "amount" in preview.problems[0].message
    with pytest.raises(ProblemError) as exc:
        begin(setup)
    assert exc.value.checks["problem"] == "route_blocked"
    assert not instances_for(db[0], Scope(db[1]), "requisition", adapter.document)


def test_all_live_members_removed_and_added(setup):
    db, adapter, users, grants, role = setup
    published(setup, mode="all")
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    auth.revoke_role(grants[2].id)
    instance = begin(setup)
    task = {"id": instance.tasks[0].id, "mode": "all"}
    decisions = [evidence(users[0], task=task["id"])]

    def current():
        with tenant_transaction(db[0], Scope(db[1])):
            row = engine._instance(WorkflowRepository(db[0], Scope(db[1])), instance.id)
            node = graph_node(db, instance, "one")
            return [
                m.user_id
                for m in engine.current_members(
                    db[0], Scope(db[1]), row, node, adapter.value, resolver(db)
                ).members
            ]

    assert not step_complete(task, decisions, current())
    auth.revoke_role(grants[1].id)
    assert step_complete(task, decisions, current()) is True
    auth.grant_role(users[2], role.id, adapter.value.target)
    assert step_complete(task, decisions, current()) is False
    decisions.append(evidence(users[2], task=task["id"]))
    assert step_complete(task, decisions, current()) is True
    assert get_instance(db[0], Scope(db[1]), instance.id).tasks[0].approver_emails == [
        "a@example.test",
        "c@example.test",
    ]


def graph_node(db, instance, node):
    return next(
        n
        for n in engine._version(
            WorkflowRepository(db[0], Scope(db[1])), instance.workflow_version_id
        ).graph.nodes
        if n.id == node
    )


def test_quorum_counts_current_members_only(setup):
    db, adapter, users, grants, role = setup
    published(setup, mode={"quorum": 2})
    instance = begin(setup)
    task = {"id": instance.tasks[0].id, "mode": {"quorum": 2}}
    decisions = [evidence(users[0], task=task["id"])]
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    auth.revoke_role(grants[0].id)
    with tenant_transaction(db[0], Scope(db[1])):
        row = engine._instance(WorkflowRepository(db[0], Scope(db[1])), instance.id)
        members = [
            m.user_id
            for m in engine.current_members(
                db[0],
                Scope(db[1]),
                row,
                graph_node(db, instance, "one"),
                adapter.value,
                resolver(db),
            ).members
        ]
    decisions.append(evidence(users[1], task=task["id"]))
    assert step_complete(task, decisions, members) is False
    decisions.append(evidence(users[2], task=task["id"]))
    assert step_complete(task, decisions, members) is True


@pytest.mark.parametrize("mode", ["any", "all", {"quorum": 2}])
@pytest.mark.parametrize("outcome,result", [("reject", "rejected"), ("return", "returned")])
def test_step_complete_terminal_re_evaluation_agrees(mode, outcome, result):
    a, b = uuid4(), uuid4()
    task = {"id": uuid4(), "mode": mode}
    # Persisted evidence is identical to the terminal outcome E10-S06 applies directly.
    decisions = [evidence(a, outcome, task["id"])]
    assert step_complete(task, decisions, [a, b]) == result
    assert step_complete(task, decisions, [b]) is False
    decisions += [evidence(b, "comment", task["id"]), evidence(b, "withdraw", task["id"])]
    assert step_complete(task, decisions, [b]) is False
    early = evidence(
        b,
        "return" if outcome == "reject" else "reject",
        task["id"],
        datetime.now(UTC) - timedelta(days=1),
    )
    assert step_complete(task, decisions + [early], [a, b]) == (
        "returned" if outcome == "reject" else "rejected"
    )
    same = datetime.now(UTC)
    assert (
        step_complete(
            task,
            [
                evidence(a, "return", task["id"], same, UUID(int=2)),
                evidence(b, "reject", task["id"], same, UUID(int=1)),
            ],
            [a, b],
        )
        == "rejected"
    )
    assert step_complete(task, [evidence(a, "approve", uuid4())], [a]) is False
    assert step_complete(task, [], []) is False


@pytest.mark.parametrize("repeat", [False, True])
def test_repeat_approver_membership(setup, repeat):
    db, adapter, users, *_ = setup
    published(setup, two=True, repeat=repeat)
    instance = finish(setup, begin(setup), users[0])
    assert instance.route[0].state == "completed" and instance.route[1].state == "open"
    assert ("a@example.test" in instance.tasks[-1].approver_emails) == repeat
    assert [t.status for t in instance.tasks] == ["completed", "open"]


def test_no_eligible_start_preview_and_advance_are_atomic(setup):
    db, adapter, users, grants, role = setup
    row, _ = published(setup, two=True)
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    for grant in grants:
        auth.revoke_role(grant.id)
    preview = preview_route(
        db[0],
        Scope(db[1]),
        document_type="requisition",
        document_id=adapter.document,
        resolver=resolver(db),
    )
    assert (
        preview.problems[0].code == "no_eligible_approver"
        and preview.problems[0].step_label == "Finance"
    )
    with pytest.raises(ProblemError) as exc:
        begin(setup)
    assert exc.value.checks["problem"] == "no_eligible_approver" and "Finance" in exc.value.detail
    assert not instances_for(db[0], Scope(db[1]), "requisition", adapter.document)
    auth.grant_role(users[0], role.id, adapter.value.target)
    instance = begin(setup)
    with pytest.raises(ProblemError) as exc:
        finish(setup, instance)
    assert exc.value.checks["problem"] == "no_eligible_approver" and "Director" in exc.value.detail
    assert get_instance(db[0], Scope(db[1]), instance.id).tasks[0].status == "open"


def test_pinned_version_route_after_superseding(setup):
    db, adapter, users, *_ = setup
    row, v1 = published(setup, two=True)
    instance = begin(setup)
    draft(db, row, graph())
    svc(db).publish(row.id, 2)
    instance = finish(setup, instance)
    assert instance.current_node_id == "two"
    assert instance.workflow_version_id == v1.id and instance.workflow_version == 1
    assert (
        instances_for(db[0], Scope(db[1]), "requisition", adapter.document)[0].workflow_version_id
        == v1.id
    )
    assert active_instance(db[0], Scope(db[1]), "requisition", adapter.document).id == instance.id
    assert finish(setup, instance, users[1]).status == "approved"
    assert len(adapter.calls) == 1
    assert active_instance(db[0], Scope(db[1]), "requisition", adapter.document) is None


def test_resume_carries_completed_steps_and_validates_latest_material(setup):
    db, adapter, users, *_ = setup
    row, v1 = published(setup, two=True)
    returned = finish(setup, begin(setup))
    db[0].execute(
        "UPDATE approval_task SET status='completed',outcome='returned',"
        "completed_at=now() WHERE id=%s",
        (returned.tasks[-1].id,),
    )
    db[0].execute(
        "UPDATE approval_instance SET status='returned',finished_at=now() WHERE id=%s",
        (returned.id,),
    )
    draft(db, row, graph())
    svc(db).publish(row.id, 2)
    adapter.value = replace(adapter.value, material={"amount": "400000"})
    with pytest.raises(ProblemError) as exc:
        begin(setup, resume_from=returned.id)
    assert exc.value.checks["problem"] == "cannot_resume" and "material" in exc.value.detail
    adapter.value = replace(adapter.value, material={"amount": "300000"}, version=2)
    resumed = begin(setup, resume_from=returned.id)
    assert resumed.workflow_version_id == v1.id and resumed.workflow_version == 1
    assert resumed.document_version == 2 and resumed.current_node_id == "two"
    assert resumed.route[0].state == "carried" and len(resumed.tasks) == 1
    stored = (
        db[0]
        .execute("SELECT route FROM approval_instance WHERE id=%s", (resumed.id,))
        .fetchone()[0]
    )
    assert stored[0]["carried"] is True and stored[0]["source_instance_id"] == str(returned.id)
    # Non-latest and non-returned are independently refused.
    db[0].execute("UPDATE approval_instance SET status='withdrawn' WHERE id=%s", (resumed.id,))
    for id in (returned.id, resumed.id):
        with pytest.raises(ProblemError) as exc:
            begin(setup, resume_from=id)
        assert exc.value.checks["problem"] == "cannot_resume"
    assert [i.id for i in instances_for(db[0], Scope(db[1]), "requisition", adapter.document)] == [
        returned.id,
        resumed.id,
    ]


def test_concurrent_any_advances_once_then_task_closed(setup):
    db, adapter, users, *_ = setup
    published(setup)
    instance = begin(setup)
    entered, release = Event(), Event()
    with (
        psycopg.connect(connection_url(db[0]), autocommit=True) as first,
        psycopg.connect(connection_url(db[0]), autocommit=True) as second,
    ):
        adapter.pause = (first.info.backend_pid, entered, release)

        def run(conn, user):
            with correlation_context("any-race"):
                try:
                    return finish(setup, instance, user, conn).status
                except ProblemError as exc:
                    return exc.checks["problem"]

        with ThreadPoolExecutor(2) as pool:
            one = pool.submit(run, first, users[0])
            assert entered.wait(10)
            two = pool.submit(run, second, users[1])
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    wait = (
                        db[0]
                        .execute(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                            (second.info.backend_pid,),
                        )
                        .fetchone()
                    )
                    if wait == ("Lock",):
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("second decision never waited on the instance lock")
            finally:
                release.set()
            assert sorted([one.result(10), two.result(10)]) == ["approved", "task_closed"]
    assert len(adapter.calls) == 1
    assert db[0].execute(
        "SELECT count(*) FROM approval_task WHERE instance_id=%s AND status='completed'",
        (instance.id,),
    ).fetchone() == (1,)


def test_concurrent_start_unique_index_is_authoritative(setup, monkeypatch):
    db, adapter, *_ = setup
    published(setup)
    barrier = Barrier(2)
    original = adapter.snapshot
    seen = set()

    def snapshot(conn, scope, id):
        if conn.info.backend_pid not in seen:
            seen.add(conn.info.backend_pid)
            barrier.wait(10)
        return original(conn, scope, id)

    monkeypatch.setattr(adapter, "snapshot", snapshot)

    def run():
        with (
            psycopg.connect(connection_url(db[0]), autocommit=True) as conn,
            correlation_context("start-race"),
        ):
            try:
                return begin(setup, conn).status
            except ProblemError as exc:
                return exc.checks["problem"]

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        assert sorted(f.result(15) for f in futures) == ["approval_in_progress", "running"]
    assert db[0].execute(
        "SELECT count(*) FROM approval_instance WHERE status='running'"
    ).fetchone() == (1,)


def test_instance_pin_database_guard(setup):
    db, adapter, *_ = setup
    row, v1 = published(setup)
    instance = begin(setup)
    v2 = draft(db, row, graph())
    for column, value in [
        ("workflow_version_id", v2.id),
        ("document_version", 2),
        ("requester_id", setup[2][0]),
        ("document_type", "project"),
        ("document_id", uuid4()),
        ("org_id", db[2]),
    ]:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState), db[0].transaction():
            db[0].execute(
                f"UPDATE approval_instance SET {column}=%s WHERE id=%s", (value, instance.id)
            )
    assert get_instance(db[0], Scope(db[1]), instance.id).workflow_version_id == v1.id


def test_instance_route_foreign_tenant_and_roles(setup):
    db, adapter, *_ = setup
    row, v1 = published(setup)
    instance = begin(setup)
    draft(db, row, graph())
    svc(db).publish(row.id, 2)

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await browser.get(f"/api/v1/approvals/instances/{instance.id}")
            assert response.status_code == 200
            body = response.json()
            assert body["workflow_version"] == 1 and body["workflow_version_id"] == str(v1.id)
            assert set(body) == {
                "separation_of_duty_applied",
                "id",
                "document_type",
                "document_id",
                "workflow_version",
                "workflow_version_id",
                "document_version",
                "status",
                "requester_email",
                "requester_name",
                "current_node_id",
                "route",
                "tasks",
                "started_at",
                "finished_at",
                "evidence",
            }
            assert body["evidence"] is None  # an adapter that supplies none (E11-S15)
            assert body["separation_of_duty_applied"] is True
            assert (await browser.get(f"/api/v1/approvals/instances/{uuid4()}")).status_code == 404
            # Real baseline roles: read granted to approver, requestor, auditor and administrator.
            auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
            roles = auth.seed_baseline_roles()
            existing = (
                db[0]
                .execute("SELECT id FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
                .fetchall()
            )
            for r in existing:
                auth.revoke_role(r[0])
            for code in ("approver", "requestor", "auditor", "organization-administrator"):
                grant = auth.grant_role(
                    IdentityId(db[3]), roles[role_code(code)].id, adapter.value.target
                )
                assert (
                    await browser.get(f"/api/v1/approvals/instances/{instance.id}")
                ).status_code == 200
                auth.revoke_role(grant.id)
            # The fake adapter's target is the organization, which `require` never conceals
            # (convention A): no grant is 403 here, 404 only for a record-scoped target.
            assert (
                await browser.get(f"/api/v1/approvals/instances/{instance.id}")
            ).status_code == 403
            custom = auth.create_role("no-read", "No read")
            auth.grant_permission(custom.id, "workflow.manage")
            auth.grant_role(IdentityId(db[3]), custom.id, adapter.value.target)
            assert (
                await browser.get(f"/api/v1/approvals/instances/{instance.id}")
            ).status_code == 403
        async with client(app) as browser:
            await login(browser, "second@example.test")
            response = await browser.get(f"/api/v1/approvals/instances/{instance.id}")
            assert response.status_code == 404

    asyncio.run(run())
