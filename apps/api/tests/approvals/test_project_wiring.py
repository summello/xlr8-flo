"""PROJ-002 and APR-003 through real middleware, engine and Postgres."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from uuid import uuid4

import psycopg
import pyotp
import pytest

from flo.api.document_types import register_document_types
from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.schemas import DecisionWrite, WorkflowCreate, WorkflowVersionWrite
from flo.modules.approvals.service import (
    WorkflowService,
    assert_current_approval,
    decide,
    instances_for,
    withdraw_instance,
)
from flo.modules.identity.service import (
    AuthorizationTarget,
    IdentityAuthorizationService,
    role_code,
)
from flo.modules.org.schemas import MasterCreate, OrgUnitCreate
from flo.modules.org.service import OrgService
from flo.modules.projects.schemas import ProjectCreate
from flo.modules.projects.service import ProjectService
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.isolation.test_real_sessions import client, headers
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db
from tests.org.test_bootstrap import connection_url


@pytest.fixture
def wiring(workflow_db):
    db = workflow_db
    register_document_types()
    org = OrgService(db[0], Scope(db[1]), db[3])
    for kind, code, attributes in [
        ("department", "D", {}),
        ("ledger_account", "L", {"account_type": "expense"}),
    ]:
        org.create_master(
            kind,
            MasterCreate(
                code=code, name=code, effective_from=date(2000, 1, 1), attributes=attributes
            ),
        )
    unit = org.create_unit(OrgUnitCreate(code="BU", name="BU", kind="bu"))
    approver = uuid4()
    db[0].execute(
        "INSERT INTO identity(id,email,password_hash) SELECT %s,%s,password_hash "
        "FROM identity WHERE id=%s",
        (approver, "approver@example.test", db[3]),
    )
    db[0].execute(
        "INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)", (approver, db[1])
    )
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = auth.seed_baseline_roles()[role_code("organization-administrator")]
    auth.grant_role(approver, role.id, AuthorizationTarget.organization(db[1]))
    row = ProjectService(db[0], Scope(db[1]), db[3]).create(
        ProjectCreate(
            bu_id=unit.id,
            name="Review project",
            department_code="D",
            ledger_account_code="L",
            currency="USD",
        )
    )
    return db, row, approver


async def authenticate(wiring, browser, email="admin@example.test"):
    # Each fresh test client enrolls a disposable factor through real middleware.
    wiring[0][0].execute(
        "DELETE FROM mfa_factor WHERE identity_id=(SELECT id FROM identity WHERE email=%s)",
        (email,),
    )
    await browser.get("/api/v1/auth/sessions")
    response = await browser.post(
        "/api/v1/auth/login",
        headers=headers(browser, uuid4().hex),
        json={"email": email, "password": "disposable bootstrap test passphrase"},
    )
    assert response.status_code == 204, response.text
    enrollment = await browser.post(
        "/api/v1/auth/mfa/enroll",
        headers=headers(browser, uuid4().hex),
        json={"password": "disposable bootstrap test passphrase"},
    )
    assert enrollment.status_code == 200, enrollment.text
    confirmed = await browser.post(
        "/api/v1/auth/mfa/confirm",
        headers=headers(browser, uuid4().hex),
        json={"code": pyotp.TOTP(enrollment.json()["secret"]).now()},
    )
    assert confirmed.status_code == 204, confirmed.text


def publish(wiring, document_type="project", empty=False):
    db, row, approver = wiring
    service = WorkflowService(db[0], Scope(db[1]), db[3])
    definition = service.create(
        WorkflowCreate(document_type=document_type, name="Review", scope_type="org")
    )
    graph = {
        "nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
        "edges": [{"from": "start", "to": "end"}],
    }
    if not empty:
        graph["nodes"].insert(
            1,
            {
                "id": "review",
                "kind": "approval",
                "name": "Review",
                "approvers": {"type": "permission"},
                "mode": "any",
            },
        )
        graph["edges"] = [{"from": "start", "to": "review"}, {"from": "review", "to": "end"}]
    service.create_version(definition.id, WorkflowVersionWrite(graph=graph))
    service.publish(definition.id, 1)
    return definition


async def submit(browser, row, version=None):
    return await browser.post(
        f"/api/v1/projects/{row.id}/transitions",
        json={"to": "approval_pending"},
        headers=headers(browser, uuid4().hex) | {"If-Match": str(version or row.version)},
    )


def instance(wiring, id=None):
    db, row, _ = wiring
    return instances_for(db[0], Scope(db[1]), "project", id or row.id)[-1]


def submit_once(wiring):
    async def run():
        async with client(app) as browser:
            await authenticate(wiring, browser)
            response = await submit(browser, wiring[1])
            assert response.status_code == 200, response.text
            return response.json()

    return asyncio.run(run())


def test_no_workflow_refuses_submit_and_writes_nothing(wiring):
    db, row, _ = wiring

    async def run():
        async with client(app) as browser:
            await authenticate(wiring, browser)
            return await submit(browser, row)

    response = asyncio.run(run())
    assert response.status_code == 409, response.text
    assert response.json()["checks"]["problem"] == "workflow_not_set_up"
    assert db[0].execute("SELECT status FROM project WHERE id=%s", (row.id,)).fetchone() == (
        "draft",
    )
    assert db[0].execute("SELECT count(*) FROM project_status_event").fetchone() == (0,)


@pytest.mark.parametrize(
    "outcome,extra,target",
    [
        ("approve", {}, "active"),
        ("reject", {"reject_mode": "permanent", "comment": "Decline project"}, "draft"),
        ("reject", {"reject_mode": "return_to_draft", "comment": "Revise project"}, "draft"),
        ("return", {"comment": "Change the scope"}, "draft"),
        ("withdraw", {}, "draft"),
    ],
)
def test_project_outcomes_and_version_pin(wiring, outcome, extra, target):
    db, row, approver = wiring
    publish(wiring)
    result = submit_once(wiring)
    current = instance(wiring)
    assert result["status"] == "approval_pending"
    assert current.document_version == result["version"] == 2

    async def run():
        async with client(app) as browser:
            await authenticate(
                wiring,
                browser,
                email="admin@example.test" if outcome == "withdraw" else "approver@example.test",
            )
            path = (
                f"instances/{current.id}/withdraw"
                if outcome == "withdraw"
                else f"tasks/{current.tasks[0].id}/decisions"
            )
            body = (
                {} if outcome == "withdraw" else {"outcome": outcome, "document_version": 2} | extra
            )
            response = await browser.post(
                "/api/v1/approvals/" + path, json=body, headers=headers(browser, uuid4().hex)
            )
            assert response.status_code == 200, response.text

    asyncio.run(run())
    project = ProjectService(db[0], Scope(db[1])).get(row.id)
    assert project.status == target
    event = (
        db[0]
        .execute(
            "SELECT reason FROM project_status_event WHERE project_id=%s ORDER BY id DESC LIMIT 1",
            (row.id,),
        )
        .fetchone()
    )
    assert event[0] == extra.get("comment")
    if outcome == "approve":
        assert_current_approval(db[0], Scope(db[1]), "project", row.id, project.version)


def test_manual_workflow_guard_and_legacy_completion(wiring):
    db, row, _ = wiring
    publish(wiring)
    submit_once(wiring)

    async def run():
        async with client(app) as browser:
            await authenticate(wiring, browser, email="approver@example.test")
            for to in ["active", "draft"]:
                response = await browser.post(
                    f"/api/v1/projects/{row.id}/transitions",
                    json={"to": to, "reason": "Manual completion"},
                    headers=headers(browser, uuid4().hex) | {"If-Match": "2"},
                )
                assert response.status_code == 409, response.text
                assert response.json()["checks"]["problem"] == "approval_workflow_active"
            # A pre-story pending project has no workflow instance.
            other = ProjectService(db[0], Scope(db[1]), db[3]).create(
                ProjectCreate(
                    bu_id=row.bu_id,
                    name="Legacy",
                    department_code="D",
                    ledger_account_code="L",
                    currency="USD",
                )
            )
            db[0].execute("UPDATE project SET status='approval_pending' WHERE id=%s", (other.id,))
            response = await browser.post(
                f"/api/v1/projects/{other.id}/transitions",
                json={"to": "active"},
                headers=headers(browser, uuid4().hex) | {"If-Match": "1"},
            )
            assert response.status_code == 200, response.text

    asyncio.run(run())


@pytest.mark.parametrize("problem", ["no_workflow", "no_eligible_approver", "route_blocked"])
def test_start_failure_rolls_back_status_event_and_audit(wiring, monkeypatch, problem):
    from flo.api import projects
    from flo.kernel.errors import conflict

    db, row, _ = wiring
    publish(wiring)

    def fail(*args, **kwargs):
        raise conflict(problem, "Approval could not start.")

    monkeypatch.setattr(projects, "start", fail)

    async def run():
        async with client(app) as browser:
            await authenticate(wiring, browser)
            response = await submit(browser, row)
            assert response.status_code == 409
            assert response.json()["checks"]["problem"] == problem

    asyncio.run(run())
    assert ProjectService(db[0], Scope(db[1])).get(row.id).status == "draft"
    assert db[0].execute(
        "SELECT count(*) FROM project_status_event WHERE project_id=%s", (row.id,)
    ).fetchone() == (0,)
    assert db[0].execute(
        "SELECT count(*) FROM audit_log WHERE target_id=%s AND action='project.transition'",
        (row.id,),
    ).fetchone() == (0,)


def test_concurrent_submits_one_success_one_invalid_transition(wiring):
    db, row, _ = wiring
    publish(wiring)

    # Independent real-app requests acquire independent database connections.
    async def run():
        async with client(app) as browser:
            await authenticate(wiring, browser)
            responses = await asyncio.gather(submit(browser, row), submit(browser, row))
            assert sorted(r.status_code for r in responses) == [200, 409]
            loser = next(r for r in responses if r.status_code == 409)
            assert loser.json()["checks"]["problem"] == "invalid_transition"

    asyncio.run(run())
    assert len(instances_for(db[0], Scope(db[1]), "project", row.id)) == 1
    assert db[0].execute(
        "SELECT count(*) FROM project_status_event WHERE project_id=%s", (row.id,)
    ).fetchone() == (1,)


def test_submit_interleaves_decide_and_old_withdraw_without_deadlock(wiring):
    from flo.modules.approvals.service import start
    from flo.modules.identity.service import AuthorizationResolver
    from flo.modules.projects.schemas import TransitionCreate

    db, row, approver = wiring
    publish(wiring)
    submit_once(wiring)
    old = instance(wiring)
    decide(
        db[0],
        Scope(db[1]),
        task_id=old.tasks[0].id,
        actor_id=approver,
        body=DecisionWrite(outcome="return", comment="Revise", document_version=2),
    )
    other = ProjectService(db[0], Scope(db[1]), db[3]).create(
        ProjectCreate(
            bu_id=row.bu_id,
            name="Other",
            department_code="D",
            ledger_account_code="L",
            currency="USD",
        )
    )
    with tenant_transaction(db[0], Scope(db[1])):
        ProjectService(db[0], Scope(db[1]), db[3]).transition(
            other.id, TransitionCreate(to="approval_pending"), "1", lambda p: True
        )
        other_instance = start(
            db[0],
            Scope(db[1]),
            document_type="project",
            document_id=other.id,
            actor_id=db[3],
            resolver=AuthorizationResolver(db[0], Scope(db[1])),
        )
    barrier = Barrier(3)

    def run(action):
        with psycopg.connect(connection_url(db[0]), autocommit=True) as conn:
            with correlation_context("project-interleave"):
                conn.execute("SET lock_timeout='5s'")
                barrier.wait(10)
                if action == "submit":
                    with tenant_transaction(conn, Scope(db[1])):
                        ProjectService(conn, Scope(db[1]), db[3]).transition(
                            row.id, TransitionCreate(to="approval_pending"), "3", lambda p: True
                        )
                        return start(
                            conn,
                            Scope(db[1]),
                            document_type="project",
                            document_id=row.id,
                            actor_id=db[3],
                            resolver=AuthorizationResolver(conn, Scope(db[1])),
                        ).status
                if action == "decide":
                    return decide(
                        conn,
                        Scope(db[1]),
                        task_id=other_instance.tasks[0].id,
                        actor_id=approver,
                        body=DecisionWrite(outcome="approve", document_version=2),
                    ).instance.status
                try:
                    withdraw_instance(
                        conn, Scope(db[1]), instance_id=old.id, actor_id=db[3], comment=None
                    )
                except ProblemError as exc:
                    return exc.checks["problem"]

    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(run, action) for action in ["submit", "decide", "withdraw"]]
        assert [f.result(15) for f in futures] == ["running", "approved", "task_closed"]


def test_project_adapter_requires_submission_and_outcome_requires_pending(wiring):
    from flo.modules.approvals.service import adapter_for

    db, row, _ = wiring
    with pytest.raises(ProblemError) as exc:
        adapter_for("project").snapshot(db[0], Scope(db[1]), row.id)
    assert exc.value.code.value == "conflict"
    with pytest.raises(ProblemError) as exc:
        ProjectService(db[0], Scope(db[1]), db[3]).approval_outcome(row.id, "active", None)
    assert exc.value.checks["problem"] == "invalid_transition"
    assert ProjectService(db[0], Scope(db[1])).get(row.id).status == "draft"


def test_resubmit_pins_post_write_version(wiring):
    from flo.modules.approvals.service import start
    from flo.modules.identity.service import AuthorizationResolver
    from flo.modules.projects.schemas import TransitionCreate

    db, row, approver = wiring
    publish(wiring)
    # Prepare the previous round without the HTTP submit route under test.
    with tenant_transaction(db[0], Scope(db[1])):
        ProjectService(db[0], Scope(db[1]), db[3]).transition(
            row.id, TransitionCreate(to="approval_pending"), "1", lambda p: True
        )
        previous = start(
            db[0],
            Scope(db[1]),
            document_type="project",
            document_id=row.id,
            actor_id=db[3],
            resolver=AuthorizationResolver(db[0], Scope(db[1])),
        )
    decide(
        db[0],
        Scope(db[1]),
        task_id=previous.tasks[0].id,
        actor_id=approver,
        body=DecisionWrite(outcome="return", comment="Revise", document_version=2),
    )
    draft = ProjectService(db[0], Scope(db[1])).get(row.id)
    submitted = submit_once((db, draft, approver))
    current = instance(wiring)
    assert current.document_version == submitted["version"] == 4
    assert current.id != previous.id
