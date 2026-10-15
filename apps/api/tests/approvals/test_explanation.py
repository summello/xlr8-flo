"""APR-021 explanations through production middleware and real Postgres."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from flo.api.health import app
from flo.kernel.identity import IdentityId
from flo.kernel.money import Money
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.delegation import create_delegation
from flo.modules.approvals.schemas import (
    DecisionWrite,
    DelegationCreate,
    WorkflowCreate,
    WorkflowVersionWrite,
)
from flo.modules.approvals.service import decide, explain
from flo.modules.identity.service import (
    AuthorizationTarget,
    IdentityAuthorizationService,
    ScopeType,
    role_code,
)
from flo.modules.org.schemas import OrgUnitCreate
from flo.modules.org.service import OrgService
from tests.approvals.test_engine import begin, resolver
from tests.approvals.test_engine import setup as setup  # noqa: F401  (fixture)
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db  # noqa: F401
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401


def explanation_graph(second_users, escalation_user):
    return {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "c1", "kind": "condition", "name": "Large request?"},
            {
                "id": "a1",
                "kind": "approval",
                "name": "Finance",
                "approvers": {"type": "role", "role_code": "approver"},
                "mode": "any",
            },
            {
                "id": "a2",
                "kind": "approval",
                "name": "Operations",
                "approvers": {
                    "type": "users",
                    "user_ids": [str(user) for user in second_users],
                },
                "mode": "any",
                "escalate_after_days": 1,
                "escalate_to": {"type": "users", "user_ids": [str(escalation_user)]},
            },
            {"id": "end", "kind": "end"},
        ],
        "edges": [
            {"from": "start", "to": "c1"},
            {
                "from": "c1",
                "to": "a1",
                "when": {
                    "field": "amount",
                    "op": "gt",
                    "value": {"amount": "200000", "currency": "USD"},
                },
            },
            {"from": "c1", "to": "end", "default": True},
            {"from": "a1", "to": "a2"},
            {"from": "a2", "to": "end"},
        ],
    }


def add_user(setup, email, role):
    db = setup[0]
    user = IdentityId(uuid4())
    db[0].execute(
        "INSERT INTO identity(id,email,password_hash) VALUES(%s,%s,%s)",
        (user, email, "$argon2id$test-placeholder"),
    )
    db[0].execute(
        "INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)", (user, db[1])
    )
    IdentityAuthorizationService(db[0], Scope(db[1]), db[3]).grant_role(
        user, role.id, AuthorizationTarget.organization(db[1])
    )
    return user


def test_recorded_condition_and_members_then_live_open_explanation(setup):
    db, adapter, users, grants, approver_role = setup
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    roles = auth.seed_baseline_roles()
    no_permission = add_user(setup, "no-permission@example.test", approver_role)
    delegate = add_user(setup, "delegate@example.test", approver_role)
    adapter.value = replace(
        adapter.value,
        target=AuthorizationTarget(ScopeType.ORG, db[1], "requisition", adapter.document),
    )
    row = definition(db)
    draft(
        db,
        row,
        explanation_graph(
            [users[0], users[1], db[3], no_permission],
            users[2],
        ),
    )
    svc(db).publish(row.id, 1, True)
    no_permission_grant = (
        db[0]
        .execute("SELECT id FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], no_permission))
        .fetchone()[0]
    )
    auth.revoke_role(no_permission_grant)
    auth.grant_role(
        no_permission,
        roles[role_code("requestor")].id,
        AuthorizationTarget.organization(db[1]),
    )
    instance = begin(setup)

    decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[0].id,
        actor_id=users[0],
        body=DecisionWrite(outcome="approve", document_version=1),
    )
    # The first approver leaves the named role but still holds the document permission;
    # their recorded membership must remain while the open step excludes them as earlier.
    permission_role = auth.create_role("explanation-permission", "Explanation permission")
    auth.grant_permission(permission_role.id, "requisition.approve")
    auth.grant_role(users[0], permission_role.id, AuthorizationTarget.organization(db[1]))
    auth.revoke_role(grants[0].id)

    now = datetime.now(UTC)
    create_delegation(
        db[0],
        Scope(db[1]),
        actor_id=users[1],
        body=DelegationCreate(
            delegate_id=delegate,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=5),
            document_types=["requisition"],
            reason="Coverage",
        ),
        resolver=resolver(db),
        now=now,
    )
    current_task = (
        db[0]
        .execute(
            "SELECT id FROM approval_task WHERE instance_id=%s AND node_id='a2'", (instance.id,)
        )
        .fetchone()[0]
    )
    db[0].execute("UPDATE approval_task SET escalated_at=%s WHERE id=%s", (now, current_task))
    # Plant-sensitive setup: current data would take the default edge if history were re-run.
    adapter.value = replace(
        adapter.value,
        version=2,
        attributes={"amount": Money(Decimal("1"), "USD")},
    )
    audits_before = db[0].execute("SELECT count(*) FROM audit_log").fetchone()[0]

    async def run():
        async with client(app) as browser:
            await login(browser)
            response = await browser.get(f"/api/v1/approvals/instances/{instance.id}/explanation")
            assert response.status_code == 200, response.text
            return response.json()

    body = asyncio.run(run())
    assert body["workflow"] == {
        "definition_id": str(row.id),
        "name": "Review",
        "version": 1,
        "bound_scope": f"org:{db[1]}",
    }
    assert "organization level" in body["why_this_workflow"]
    condition, passed, opened = body["steps"]
    assert condition["taken"] == "a1"
    assert condition["evaluated"] == [
        {
            "edge_to": "a1",
            "result": True,
            "leaves": [
                {
                    "field": "amount",
                    "op": "gt",
                    "expected": "200000 USD",
                    "actual": "300000 USD",
                    "result": True,
                }
            ],
        }
    ]
    assert passed["members_as_of"] == "at_decision"
    assert "a@example.test" in {member["email"] for member in passed["members"]}
    assert any(member["basis"].startswith("role approver at") for member in passed["members"])
    assert passed["decisions"][0]["actor"] == "a@example.test"

    assert opened["members_as_of"] == "as_of_now"
    bases = {member["email"]: member["basis"] for member in opened["members"]}
    assert bases["b@example.test"].startswith("listed by name; holds requisition.approve")
    assert bases["c@example.test"].startswith("added by escalation on")
    assert bases["delegate@example.test"].startswith("delegate for b@example.test until")
    exclusions = {item["reason"]: item["detail"] for item in opened["excluded"]}
    assert exclusions == {
        "already_decided": "Already decided an earlier step",
        "not_eligible_in_scope": "No requisition.approve in First tenant",
        "separation_of_duty": "Requested this document",
    }
    assert db[0].execute("SELECT count(*) FROM audit_log").fetchone()[0] == audits_before


def test_bu_binding_reason_names_the_unit(setup):
    db, adapter, *_ = setup
    unit = OrgService(db[0], Scope(db[1]), db[3]).create_unit(
        OrgUnitCreate(code="PLANT", name="Plant Operations", kind="bu")
    )
    adapter.value = replace(
        adapter.value,
        target=AuthorizationTarget(ScopeType.BU, unit.id, "requisition", adapter.document),
    )
    row = svc(db).create(
        WorkflowCreate(
            document_type="requisition",
            name="Capital requests",
            scope_type="bu",
            scope_id=unit.id,
        )
    )
    svc(db).create_version(
        row.id,
        WorkflowVersionWrite(
            graph={
                "nodes": [
                    {"id": "start", "kind": "start"},
                    {
                        "id": "one",
                        "kind": "approval",
                        "approvers": {"type": "permission"},
                        "mode": "any",
                    },
                    {"id": "end", "kind": "end"},
                ],
                "edges": [{"from": "start", "to": "one"}, {"from": "one", "to": "end"}],
            }
        ),
    )
    svc(db).publish(row.id, 1)
    instance = begin(setup)

    result = explain(db[0], Scope(db[1]), instance.id)
    assert result.workflow.bound_scope == f"bu:{unit.id}"
    assert result.why_this_workflow == (
        "Published version 1 bound to Plant Operations (business unit) "
        "— nearer than the organization-level workflow."
    )


def test_explanation_foreign_tenant_admin_and_concealment(setup):
    db, adapter, *_ = setup
    adapter.value = replace(
        adapter.value,
        target=AuthorizationTarget(ScopeType.ORG, db[1], "requisition", adapter.document),
    )
    row = definition(db)
    draft(
        db,
        row,
        {
            "nodes": [
                {"id": "start", "kind": "start"},
                {
                    "id": "one",
                    "kind": "approval",
                    "approvers": {"type": "permission"},
                    "mode": "any",
                },
                {"id": "end", "kind": "end"},
            ],
            "edges": [{"from": "start", "to": "one"}, {"from": "one", "to": "end"}],
        },
    )
    svc(db).publish(row.id, 1)
    instance = begin(setup)
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])

    async def run():
        async with client(app) as browser:
            await login(browser)
            for (grant_id,) in (
                db[0]
                .execute(
                    "SELECT id FROM user_role WHERE org_id=%s AND user_id=%s",
                    (db[1], db[3]),
                )
                .fetchall()
            ):
                auth.revoke_role(grant_id)
            admin_role = auth.create_role("explanation-admin", "Explanation administrator")
            auth.grant_permission(admin_role.id, "approval.admin")
            admin_grant = auth.grant_role(
                db[3], admin_role.id, AuthorizationTarget.organization(db[1])
            )
            path = f"/api/v1/approvals/instances/{instance.id}/explanation"
            assert (await browser.get(path)).status_code == 200
            auth.revoke_role(admin_grant.id)
            concealed = await browser.get(path)
            assert concealed.status_code == 404
        async with client(app) as browser:
            await login(browser, "second@example.test")
            foreign = await browser.get(path)
            assert foreign.status_code == 404
            foreign_body = foreign.json()
            concealed_body = concealed.json()
            foreign_body.pop("correlation_id")
            concealed_body.pop("correlation_id")
            assert foreign_body == concealed_body

    asyncio.run(run())
