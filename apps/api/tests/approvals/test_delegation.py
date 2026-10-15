"""Delegation, no re-delegation, revocation, SoD for delegates and admin reassignment."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals import conditions, engine
from flo.modules.approvals.delegation import (
    create_delegation,
    eligible_delegates,
    list_delegations,
    reassign_task,
    revoke_delegation,
)
from flo.modules.approvals.schemas import (
    DecisionWrite,
    DelegationCreate,
    DocumentSummary,
    ReassignWrite,
)
from flo.modules.approvals.service import decide
from flo.modules.identity.service import IdentityAuthorizationService, role_code
from tests.approvals.test_engine import Adapter, begin, resolver
from tests.approvals.test_engine import setup as setup  # noqa: F401  (fixture)
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db  # noqa: F401
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def users_graph(*user_ids, repeat=False, due_days=None, escalate=None):
    node = {
        "id": "one",
        "kind": "approval",
        "name": "Finance",
        "approvers": {"type": "users", "user_ids": [str(u) for u in user_ids]},
        "mode": "any",
        "allow_repeat": repeat,
    }
    if due_days:
        node["due_days"] = due_days
    if escalate:
        node["escalate_after_days"] = 1
        node["escalate_to"] = {"type": "users", "user_ids": [str(u) for u in escalate]}
    return {
        "nodes": [{"id": "start", "kind": "start"}, node, {"id": "end", "kind": "end"}],
        "edges": [{"from": "start", "to": "one"}, {"from": "one", "to": "end"}],
    }


def publish(setup, graph):
    db, adapter, *_ = setup
    adapter.summaries = lambda conn, scope, ids: {
        i: DocumentSummary(
            number="REQ-00001",
            title="Request",
            amount=None,
            requester_id=db[3],
            href="/requisitions/1",
            note=None,
        )
        for i in ids
    }
    row = definition(db)
    draft(db, row, graph)
    svc(db).publish(row.id, 1, True)
    return row


def delegate(setup, owner, to, start=NOW - timedelta(days=1), days=7, types=None, now=NOW):
    db = setup[0]
    return create_delegation(
        db[0],
        Scope(db[1]),
        actor_id=owner,
        body=DelegationCreate(
            delegate_id=to,
            starts_at=start,
            ends_at=start + timedelta(days=days),
            document_types=types,
            reason="Out of office",
        ),
        resolver=resolver(db),
        now=now,
    )


def approve(setup, task_id, user):
    db, adapter, *_ = setup
    return decide(
        db[0],
        Scope(db[1]),
        task_id=task_id,
        actor_id=user,
        body=DecisionWrite(outcome="approve", document_version=adapter.value.version),
    )


def test_delegate_decides_a_covered_task_with_attribution(setup):
    db, adapter, users, *_ = setup
    owner, other = users[0], users[1]
    publish(setup, users_graph(owner))
    instance = begin(setup)
    created = delegate(setup, owner, other)
    assert created.state == "active" and created.delegate_email == "b@example.test"
    # Without the delegation the delegate is not an approver of this step.
    result = approve(setup, instance.tasks[-1].id, other)
    decision = result.decision
    assert decision.authority == "delegate" and decision.actor_email == "b@example.test"
    assert decision.on_behalf_of_email == "a@example.test"
    assert result.instance.status == "approved"


def test_delegation_covering_only_another_type_grants_no_power(setup, monkeypatch):
    db, adapter, users, *_ = setup
    owner, other = users[0], users[1]

    class Po(Adapter):
        document_type = "po"

    po = Po(db)
    conditions.register_document_type(po)
    publish(setup, users_graph(owner))
    instance = begin(setup)
    delegate(setup, owner, other, types=["po"])
    with pytest.raises(ProblemError) as error:
        approve(setup, instance.tasks[-1].id, other)
    assert error.value.code == ErrorCode.FORBIDDEN


def test_overlap_refused_back_to_back_allowed_and_no_redelegation(setup):
    db, adapter, users, *_ = setup
    owner, other, third = users
    delegate(setup, owner, other, start=NOW, days=3)
    with pytest.raises(ProblemError) as overlap:
        delegate(setup, owner, third, start=NOW + timedelta(days=1), days=3)
    assert overlap.value.checks["problem"] == "delegation_overlaps"
    delegate(setup, owner, third, start=NOW + timedelta(days=3), days=3)  # back to back
    with pytest.raises(ProblemError) as chain:
        delegate(setup, other, third, start=NOW + timedelta(hours=1), days=1)
    assert chain.value.checks["problem"] == "no_redelegation"


def test_revoked_delegate_loses_power_owner_decides_history_unchanged(setup):
    db, adapter, users, *_ = setup
    owner, other = users[0], users[1]
    publish(setup, users_graph(owner))
    instance = begin(setup)
    created = delegate(setup, owner, other)
    revoked = revoke_delegation(
        db[0],
        Scope(db[1]),
        actor_id=owner,
        delegation_id=created.id,
        is_admin=lambda: False,
        now=NOW,
    )
    assert revoked.state == "revoked"
    again = revoke_delegation(
        db[0],
        Scope(db[1]),
        actor_id=owner,
        delegation_id=created.id,
        is_admin=lambda: False,
        now=NOW + timedelta(hours=1),
    )
    assert again.revoked_at == revoked.revoked_at  # idempotent
    with pytest.raises(ProblemError) as error:
        approve(setup, instance.tasks[-1].id, other)
    assert error.value.code == ErrorCode.FORBIDDEN
    assert approve(setup, instance.tasks[-1].id, owner).decision.authority == "member"


def test_revoke_authorization_owner_admin_other_and_foreign(setup):
    db, adapter, users, *_ = setup
    owner, other, third = users
    created = delegate(setup, owner, other)
    with pytest.raises(ProblemError) as stranger:
        revoke_delegation(
            db[0], Scope(db[1]), actor_id=third, delegation_id=created.id, is_admin=lambda: False
        )
    assert stranger.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(ProblemError) as absent:
        revoke_delegation(
            db[0], Scope(db[1]), actor_id=third, delegation_id=uuid4(), is_admin=lambda: True
        )
    assert absent.value.code == ErrorCode.NOT_FOUND
    # A delegation of another organization is indistinguishable from an unknown id.
    with pytest.raises(ProblemError) as foreign:
        revoke_delegation(
            db[0], Scope(db[2]), actor_id=third, delegation_id=created.id, is_admin=lambda: True
        )
    assert foreign.value.code == ErrorCode.NOT_FOUND
    assert (
        revoke_delegation(
            db[0], Scope(db[1]), actor_id=third, delegation_id=created.id, is_admin=lambda: True
        ).state
        == "revoked"
    )


def test_delegate_cannot_decide_a_document_they_requested(setup):
    db, adapter, users, *_ = setup
    owner, requester = users[0], users[1]
    adapter.value = replace(adapter.value, requester_id=IdentityId(requester))
    publish(setup, users_graph(owner))
    instance = engine.get_instance(
        db[0],
        Scope(db[1]),
        begin_as(setup, requester).id,
    )
    delegate(setup, owner, requester)
    with pytest.raises(ProblemError) as error:
        approve(setup, instance.tasks[-1].id, requester)
    assert error.value.code == ErrorCode.FORBIDDEN


def begin_as(setup, requester):
    from flo.modules.approvals.service import start

    db, adapter, *_ = setup
    return start(
        db[0],
        Scope(db[1]),
        document_type="requisition",
        document_id=adapter.document,
        actor_id=requester,
        resolver=resolver(db),
    )


def test_eligible_delegates_exclude_self_and_list_views(setup):
    db, adapter, users, *_ = setup
    owner, other, third = users
    picker = eligible_delegates(db[0], Scope(db[1]), owner, ["requisition"], resolver(db))
    emails = {r.email for r in picker.rows}
    assert "a@example.test" not in emails and {"b@example.test", "c@example.test"} <= emails
    created = delegate(setup, owner, other)
    mine = list_delegations(db[0], Scope(db[1]), actor_id=owner, view="mine", now=NOW)
    covering = list_delegations(db[0], Scope(db[1]), actor_id=other, view="covering", now=NOW)
    nobody = list_delegations(db[0], Scope(db[1]), actor_id=third, view="mine", now=NOW)
    assert [r.id for r in mine.rows] == [created.id] == [r.id for r in covering.rows]
    assert nobody.rows == []


def test_admin_reassign_requires_reason_eligibility_and_is_audited(setup):
    db, adapter, users, *_ = setup
    owner, other = users[0], users[1]
    publish(setup, users_graph(owner))
    instance = begin(setup)
    task = instance.tasks[-1].id
    with pytest.raises(ProblemError):
        ReassignWrite(user_ids=[other], reason="   ")
    outsider = IdentityId(uuid4())
    with pytest.raises(ProblemError) as ineligible:
        reassign_task(
            db[0],
            Scope(db[1]),
            actor_id=db[3],
            task_id=task,
            body=ReassignWrite(user_ids=[outsider], reason="Cover"),
            resolver=resolver(db),
        )
    assert ineligible.value.code == ErrorCode.VALIDATION_FAILED
    reassign_task(
        db[0],
        Scope(db[1]),
        actor_id=db[3],
        task_id=task,
        body=ReassignWrite(user_ids=[other], reason="Owner away"),
        resolver=resolver(db),
    )
    with tenant_transaction(db[0], Scope(db[1])):
        audit = (
            db[0]
            .execute("SELECT count(*) FROM audit_log WHERE action='approval.task.reassign'")
            .fetchone()
        )
    assert audit == (1,)
    assert approve(setup, task, other).instance.status == "approved"
    with pytest.raises(ProblemError):
        approve(setup, task, owner)  # task closed after the reassigned approver decided


def test_revoke_route_uses_member_guard_with_owner_or_admin(setup):
    """HTTP: the logged-in administrator revokes someone else's delegation; unknown id is 404."""
    db, adapter, users, *_ = setup
    created = delegate(setup, users[0], users[1])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            missing = await browser.post(
                f"/api/v1/approvals/delegations/{uuid4()}/revoke",
                headers=headers(browser, uuid4().hex),
            )
            assert missing.status_code == 404
            ok = await browser.post(
                f"/api/v1/approvals/delegations/{created.id}/revoke",
                headers=headers(browser, uuid4().hex),
            )
            assert ok.status_code == 200 and ok.json()["state"] == "revoked"
            # Drop the administrator role: a plain member who is not the owner gets 403.
            auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
            for row in (
                db[0]
                .execute("SELECT id FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
                .fetchall()
            ):
                auth.revoke_role(row[0])
            second = delegate(setup, users[0], users[2], start=NOW + timedelta(days=30))
            denied = await browser.post(
                f"/api/v1/approvals/delegations/{second.id}/revoke",
                headers=headers(browser, uuid4().hex),
            )
            assert denied.status_code == 403
            assert role_code("approver")

    asyncio.run(scenario())


def test_reassign_of_a_foreign_tenants_task_is_not_found(setup):
    db, adapter, users, *_ = setup
    publish(setup, users_graph(users[0]))
    instance = begin(setup)
    with pytest.raises(ProblemError) as error:
        reassign_task(
            db[0],
            Scope(db[2]),
            actor_id=db[3],
            task_id=instance.tasks[-1].id,
            body=ReassignWrite(user_ids=[users[1]], reason="Cover"),
            resolver=resolver(db),
        )
    assert error.value.code == ErrorCode.NOT_FOUND


def two_steps(first, second, *, repeat=False, quorum=False, escalate=None):
    graph = users_graph(*first)
    if quorum:
        graph["nodes"][1]["mode"] = {"quorum": 2}
    next_node = users_graph(*second, repeat=repeat, escalate=escalate)["nodes"][1]
    next_node["id"] = "two"
    graph["nodes"].insert(2, next_node)
    graph["edges"] = [
        {"from": "start", "to": "one"},
        {"from": "one", "to": "two"},
        {"from": "two", "to": "end"},
    ]
    return graph


def test_delegate_with_revoked_approval_access_is_refused(setup):
    db, adapter, users, grants, _ = setup
    publish(setup, users_graph(users[0]))
    instance = begin(setup)
    delegate(setup, users[0], users[1])
    IdentityAuthorizationService(db[0], Scope(db[1]), db[3]).revoke_role(grants[1].id)
    with pytest.raises(ProblemError) as error:
        approve(setup, instance.tasks[-1].id, users[1])
    assert error.value.code == ErrorCode.FORBIDDEN


@pytest.mark.parametrize("repeat", [False, True])
def test_delegate_actor_and_principal_both_count_as_earlier_deciders(setup, repeat):
    db, adapter, users, *_ = setup
    owner, delegate_user, completer = users
    fourth = IdentityId(uuid4())
    db[0].execute(
        "INSERT INTO identity(id,email,password_hash) VALUES(%s,'d@example.test',"
        "'$argon2id$test-placeholder')",
        (fourth,),
    )
    db[0].execute(
        "INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)", (fourth, db[1])
    )
    IdentityAuthorizationService(db[0], Scope(db[1]), db[3]).grant_role(
        fourth, setup[4].id, adapter.value.target
    )
    publish(
        setup, two_steps([owner, completer], [delegate_user, fourth], repeat=repeat, quorum=True)
    )
    instance = begin(setup)
    delegate(setup, owner, delegate_user)
    approve(setup, instance.tasks[-1].id, delegate_user)
    result = approve(setup, instance.tasks[-1].id, completer)
    with tenant_transaction(db[0], Scope(db[1])):
        route = (
            db[0]
            .execute("SELECT route FROM approval_instance WHERE id=%s", (instance.id,))
            .fetchone()[0]
        )
    assert set(route[0]["decided_by"]) == {str(owner), str(delegate_user), str(completer)}
    task = result.instance.tasks[-1].id
    if repeat:
        assert approve(setup, task, delegate_user).instance.status == "approved"
    else:
        with pytest.raises(ProblemError) as error:
            approve(setup, task, delegate_user)
        assert error.value.code == ErrorCode.FORBIDDEN


def test_earlier_member_cannot_decide_later_as_delegate(setup):
    db, adapter, users, *_ = setup
    owner, delegate_user, _ = users
    publish(setup, two_steps([delegate_user], [owner]))
    instance = begin(setup)
    delegate(setup, owner, delegate_user)
    result = approve(setup, instance.tasks[-1].id, delegate_user)
    with pytest.raises(ProblemError) as error:
        approve(setup, result.instance.tasks[-1].id, delegate_user)
    assert error.value.code == ErrorCode.FORBIDDEN


def test_escalation_excludes_earlier_decider(setup):
    from flo.modules.approvals.reminders import run_org

    db, adapter, users, *_ = setup
    first, owner, _ = users
    publish(setup, two_steps([first], [owner], escalate=[first]))
    instance = begin(setup)
    result = approve(setup, instance.tasks[-1].id, first)
    task = result.instance.tasks[-1].id
    with tenant_transaction(db[0], Scope(db[1])):
        created = (
            db[0].execute("SELECT created_at FROM approval_task WHERE id=%s", (task,)).fetchone()[0]
        )
    assert run_org(db[0], Scope(db[1]), now=created + timedelta(days=2)).escalated == 1
    with pytest.raises(ProblemError) as error:
        approve(setup, task, first)
    assert error.value.code == ErrorCode.FORBIDDEN
    assert approve(setup, task, owner).instance.status == "approved"


def drop_roles(db, user):
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    for row in (
        db[0]
        .execute("SELECT id FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], user))
        .fetchall()
    ):
        auth.revoke_role(row[0])


def test_revoke_http_owner_without_delegate_permission_and_replay(setup):
    db, adapter, users, *_ = setup
    # The owner remains an organization member after all approval permissions lapse.
    adapter.value = replace(adapter.value, requester_id=IdentityId(users[2]))
    publish(setup, users_graph(db[3]))
    instance = begin_as(setup, users[2])
    created = delegate(setup, db[3], users[0])
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    reduced = auth.create_role("approval-only", "Approval only")
    auth.grant_permission(reduced.id, "requisition.approve")

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            drop_roles(db, db[3])
            auth.grant_role(db[3], reduced.id, adapter.value.target)
            key = uuid4().hex
            path = f"/api/v1/approvals/delegations/{created.id}/revoke"
            first = await browser.post(path, headers=headers(browser, key))
            assert first.status_code == 200 and first.json()["revoked_at"] is not None
            replay = await browser.post(path, headers=headers(browser, key))
            assert replay.status_code == 200 and replay.json() == first.json()
            with pytest.raises(ProblemError) as error:
                approve(setup, instance.tasks[-1].id, users[0])
            assert error.value.code == ErrorCode.FORBIDDEN
            assert approve(setup, instance.tasks[-1].id, db[3]).instance.status == "approved"
            with tenant_transaction(db[0], Scope(db[1])):
                assert db[0].execute(
                    "SELECT count(*) FROM audit_log WHERE action='approval.delegation.revoke' "
                    "AND target_id=%s",
                    (created.id,),
                ).fetchone() == (1,)

    asyncio.run(scenario())


def test_revoke_http_nonowner_denied_unchanged_admin_nonowner_allowed(setup):
    db, adapter, users, *_ = setup
    created = delegate(setup, users[0], users[1])

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            # The administrator is not the owner.
            allowed = await browser.post(
                f"/api/v1/approvals/delegations/{created.id}/revoke",
                headers=headers(browser, uuid4().hex),
            )
            assert allowed.status_code == 200
            second = delegate(setup, users[0], users[2], start=NOW + timedelta(days=30))
            before = (
                db[0]
                .execute("SELECT * FROM approval_delegation WHERE id=%s", (second.id,))
                .fetchone()
            )
            drop_roles(db, db[3])
            denied = await browser.post(
                f"/api/v1/approvals/delegations/{second.id}/revoke",
                headers=headers(browser, uuid4().hex),
            )
            assert denied.status_code == 403
            assert (
                db[0]
                .execute("SELECT * FROM approval_delegation WHERE id=%s", (second.id,))
                .fetchone()
                == before
            )

    asyncio.run(scenario())


def test_revoke_http_foreign_and_unknown_ids_have_identical_problem_bodies(setup):
    db, adapter, users, *_ = setup
    created = delegate(setup, users[0], users[1])

    async def scenario():
        async with client(app) as browser:
            await login(browser, "second@example.test")
            bodies = []
            for id in (created.id, uuid4()):
                result = await browser.post(
                    f"/api/v1/approvals/delegations/{id}/revoke",
                    headers=headers(browser, uuid4().hex),
                )
                assert result.status_code == 404
                body = result.json()
                # Request path and correlation are request metadata, not existence information.
                instance, correlation = body.pop("instance"), body.pop("correlation_id")
                assert instance == f"/api/v1/approvals/delegations/{id}/revoke"
                assert correlation
                bodies.append(body)
            assert bodies[0] == bodies[1]

    asyncio.run(scenario())


def test_delegation_routes_unauthenticated_forbidden_and_foreign_scope(setup):
    db, adapter, users, *_ = setup
    publish(setup, users_graph(users[0]))
    instance = begin(setup)
    created = delegate(setup, users[0], users[1])
    body = DelegationCreate(
        delegate_id=users[1], starts_at=NOW, ends_at=NOW + timedelta(days=2), reason="Cover"
    ).model_dump(mode="json")
    calls = [
        ("POST", "/api/v1/approvals/delegations", body),
        ("GET", "/api/v1/approvals/delegations", None),
        ("GET", "/api/v1/approvals/delegations/eligible-delegates", None),
        ("POST", f"/api/v1/approvals/delegations/{created.id}/revoke", None),
        (
            "POST",
            f"/api/v1/approvals/tasks/{instance.tasks[-1].id}/reassign",
            {"user_ids": [str(users[1])], "reason": "Cover"},
        ),
    ]

    async def scenario():
        async with client(app) as browser:
            await browser.get("/api/v1/auth/sessions")
            for method, path, data in calls:
                result = await browser.request(
                    method, path, json=data, headers=headers(browser, uuid4().hex)
                )
                assert result.status_code == 401, (path, result.text)
            await login(browser)
            drop_roles(db, db[3])
            for index in (0, 2, 4):
                method, path, data = calls[index]
                result = await browser.request(
                    method, path, json=data, headers=headers(browser, uuid4().hex)
                )
                assert result.status_code == 403, (path, result.text)
            result = await browser.get("/api/v1/approvals/delegations?view=organization")
            assert result.status_code == 403
        async with client(app) as browser:
            await login(browser, "second@example.test")
            for index in (3, 4):
                method, path, data = calls[index]
                result = await browser.request(
                    method, path, json=data, headers=headers(browser, uuid4().hex)
                )
                assert result.status_code == 404, (path, result.text)
            # Self-scoped collections do not address another tenant's records.
            rows = await browser.get("/api/v1/approvals/delegations?view=organization")
            assert rows.status_code == 200 and rows.json()["rows"] == []
            picker = await browser.get("/api/v1/approvals/delegations/eligible-delegates")
            assert picker.status_code == 200
            assert not ({str(u) for u in users} & {r["user_id"] for r in picker.json()["rows"]})
            foreign_create = await browser.post(
                calls[0][1], json=body, headers=headers(browser, uuid4().hex)
            )
            assert foreign_create.status_code == 404

    asyncio.run(scenario())
