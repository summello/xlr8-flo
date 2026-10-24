"""APR-004/019 and USR-007 against real Postgres and production middleware."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals import conditions
from flo.modules.approvals.schemas import PermissionApprovers, RoleApprovers, UserApprovers
from flo.modules.approvals.service import eligible_approvers, register_document_type
from flo.modules.identity.service import (
    AuthorizationResolver,
    AuthorizationTarget,
    IdentityAuthorizationService,
    ScopeType,
    role_code,
)
from flo.modules.org.schemas import OrgUnitCreate, SettingPut
from flo.modules.org.service import OrgService
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.isolation.test_real_sessions import client, headers, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db


class Adapter:
    document_type = "requisition"
    approve_permission = "requisition.approve"

    def fields(self):
        return {"amount": "money"}


@pytest.fixture
def eligibility_tree(workflow_db, monkeypatch):
    db = workflow_db
    conn, org, foreign_org, actor = db
    scope = Scope(org)
    monkeypatch.setattr(conditions, "_adapters", {})
    register_document_type(Adapter())
    org_service = OrgService(conn, scope, actor)
    a = org_service.create_unit(OrgUnitCreate(code="A", name="A", kind="bu"))
    a1 = org_service.create_unit(OrgUnitCreate(code="A1", name="A1", kind="ou", parent_id=a.id))
    b = org_service.create_unit(OrgUnitCreate(code="B", name="B", kind="bu"))
    targets = {"org": AuthorizationTarget.organization(org)} | {
        key: AuthorizationTarget(ScopeType.BU, unit.id)
        for key, unit in (("a", a), ("a1", a1), ("b", b))
    }
    auth = IdentityAuthorizationService(conn, scope, actor)
    roles = auth.seed_baseline_roles()
    projects = [uuid4() for _ in range(3)]
    auth.register_scope(ScopeType.PROJECT, projects[0], ScopeType.BU, a1.id)
    for index in (1, 2):
        auth.register_scope(
            ScopeType.PROJECT,
            projects[index],
            ScopeType.PROJECT,
            projects[index - 1],
            roll_down=index != 2,
        )
    targets |= {
        f"p{i}": AuthorizationTarget(ScopeType.PROJECT, id) for i, id in enumerate(projects)
    }
    users = {}
    for label in (
        "org",
        "bu",
        "project",
        "child",
        "role_only",
        "both",
        "inactive",
        "foreign",
        "none",
        "future",
    ):
        id = IdentityId(uuid4())
        conn.execute(
            "INSERT INTO identity(id,email,password_hash) VALUES(%s,%s,%s)",
            (id, label + "@example.test", "$argon2id$test-placeholder"),
        )
        users[label] = id
        if label != "none":
            conn.execute(
                "INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)",
                (id, foreign_org if label == "foreign" else org),
            )
    for label, target in (
        ("org", "org"),
        ("bu", "a"),
        ("child", "a1"),
        ("project", "p0"),
        ("both", "a"),
        ("inactive", "org"),
    ):
        auth.grant_role(users[label], roles[role_code("approver")].id, targets[target])
    for label in ("role_only", "both"):
        auth.grant_role(users[label], roles[role_code("requestor")].id, targets["a"])
    auth.grant_role(
        users["future"],
        roles[role_code("approver")].id,
        targets["org"],
        effective_from=datetime.now(UTC) + timedelta(days=1),
    )
    other = IdentityAuthorizationService(conn, Scope(foreign_org), actor)
    other_roles = other.seed_baseline_roles()
    other.grant_role(
        users["foreign"],
        other_roles[role_code("approver")].id,
        AuthorizationTarget.organization(foreign_org),
    )
    conn.execute(
        "UPDATE identity SET status='deactivated',deactivated_at=now() WHERE id=%s",
        (users["inactive"],),
    )
    return db, targets, users


def eligible(tree, rule=None, target="a", requester=None):
    db, targets, users = tree
    conn, org, _, _ = db
    scope = Scope(org)
    with tenant_transaction(conn, scope):
        return eligible_approvers(
            conn,
            scope,
            "requisition",
            rule or PermissionApprovers(type="permission"),
            targets[target],
            requester or uuid4(),
            AuthorizationResolver(conn, scope),
        )


def test_permission_eligibility_scope_tree_and_active_tenant(eligibility_tree):
    tree = eligibility_tree
    db, targets, users = tree
    expected = {
        "a": {"org", "bu", "both"},
        "a1": {"org", "child"},
        "b": {"org"},
        "p0": {"org", "child", "project"},
        "p1": {"org", "child", "project"},
        "p2": {"org", "child"},
    }
    for target, labels in expected.items():
        result = eligible(tree, target=target)
        assert {row.user_id for row in result.members} == {users[label] for label in labels} | {
            db[3]
        }
        assert all(
            row.basis
            == (
                f"permission:requisition.approve@{targets[target].scope_type.value}:"
                f"{targets[target].scope_id}"
            )
            for row in result.members
        )
    assert not {users[label] for label in ("inactive", "foreign", "none", "future")} & {
        row.user_id for row in eligible(tree).members
    }


def test_role_without_permission_is_not_eligible(eligibility_tree):
    _, _, users = eligibility_tree
    result = eligible(eligibility_tree, RoleApprovers(type="role", role_code="requestor"))
    assert [row.user_id for row in result.members] == [users["both"]]
    assert result.members[0].basis.startswith("role:requestor@bu:")


def test_users_rule_excludes_out_of_scope_and_hides_inactive_foreign(eligibility_tree):
    _, _, users = eligibility_tree
    result = eligible(
        eligibility_tree, UserApprovers(type="users", user_ids=list(users.values())), target="b"
    )
    assert [row.user_id for row in result.members] == [users["org"]]
    assert {row.user_id for row in result.excluded} == {
        users[label] for label in ("bu", "child", "project", "role_only", "both", "future")
    }
    assert {row.reason for row in result.excluded} == {"not_eligible_in_scope"}


@pytest.mark.parametrize("rule_kind", ["permission", "role", "users"])
def test_requester_separation_of_duty_and_nearest_override(eligibility_tree, rule_kind):
    db, targets, users = eligibility_tree
    rule = {
        "permission": PermissionApprovers(type="permission"),
        "role": RoleApprovers(type="role", role_code="requestor"),
        "users": UserApprovers(type="users", user_ids=[users["both"]]),
    }[rule_kind]
    result = eligible(eligibility_tree, rule, requester=users["both"])
    assert users["both"] not in {row.user_id for row in result.members}
    assert [(row.user_id, row.reason) for row in result.excluded] == [
        (users["both"], "separation_of_duty")
    ]
    org = OrgService(db[0], Scope(db[1]), db[3])
    with pytest.raises(ProblemError):
        org.set_setting(
            "approval.separation_of_duty", SettingPut(unit_id=targets["a"].scope_id, value="false")
        )
    org.set_setting(
        "approval.separation_of_duty", SettingPut(unit_id=targets["a"].scope_id, value=False)
    )
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    roles = auth.seed_baseline_roles()
    for code in ("approver", "requestor"):
        auth.grant_role(users["both"], roles[role_code(code)].id, targets["a1"])
    for target in ("a1", "p2"):
        assert users["both"] in {
            row.user_id for row in eligible(eligibility_tree, rule, target, users["both"]).members
        }
    org.set_setting(
        "approval.separation_of_duty", SettingPut(unit_id=targets["a1"].scope_id, value=True)
    )
    assert users["both"] not in {
        row.user_id
        for row in eligible(eligibility_tree, rule, target="a1", requester=users["both"]).members
    }


@pytest.mark.parametrize("rule_kind", ["permission", "role", "users"])
def test_requester_separation_of_duty_at_organization(eligibility_tree, rule_kind):
    db, _, users = eligibility_tree
    requester = users["org"]
    rule = {
        "permission": PermissionApprovers(type="permission"),
        "role": RoleApprovers(type="role", role_code="approver"),
        "users": UserApprovers(type="users", user_ids=[requester]),
    }[rule_kind]
    result = eligible(eligibility_tree, rule, target="org", requester=requester)
    assert requester not in {row.user_id for row in result.members}
    assert [(row.user_id, row.reason) for row in result.excluded] == [
        (requester, "separation_of_duty")
    ]
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(
        "approval.separation_of_duty", SettingPut(unit_id=None, value=False)
    )
    result = eligible(eligibility_tree, rule, target="org", requester=requester)
    assert requester in {row.user_id for row in result.members}
    assert not result.excluded


def test_eligibility_is_live_after_permission_revocation(eligibility_tree):
    db, _, users = eligibility_tree
    assert users["bu"] in {row.user_id for row in eligible(eligibility_tree).members}
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    assignment = (
        db[0].execute("SELECT id FROM user_role WHERE user_id=%s", (users["bu"],)).fetchone()[0]
    )
    auth.revoke_role(assignment)
    assert users["bu"] not in {row.user_id for row in eligible(eligibility_tree).members}


def test_publish_rejects_users_without_document_permission(eligibility_tree):
    db, targets, users = eligibility_tree
    row = definition(db)
    graph = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {
                "id": "approve",
                "kind": "approval",
                "mode": "any",
                "approvers": {"type": "users", "user_ids": [str(users["role_only"])]},
            },
            {"id": "end", "kind": "end"},
        ],
        "edges": [{"from": "start", "to": "approve"}, {"from": "approve", "to": "end"}],
    }
    draft(db, row, graph)
    from flo.modules.approvals.service import validate_graph

    report = validate_graph(
        db[0],
        Scope(db[1]),
        "requisition",
        graph,
        target=targets["org"],
        resolver=AuthorizationResolver(db[0], Scope(db[1])),
    )
    assert [issue.code for issue in report.errors] == ["missing_approvers"]

    async def validate():
        async with client(app) as browser:
            await login(browser)
            response = await browser.get(
                f"/api/v1/workflows/{row.id}/versions/1/validation",
                headers=headers(browser),
            )
            assert response.status_code == 200, response.text
            assert response.json() == report.model_dump(mode="json", exclude_none=True)

    asyncio.run(validate())
    with pytest.raises(ProblemError) as caught:
        svc(db).publish(row.id, 1)
    assert caught.value.checks["problem"] == "workflow_invalid"
    assert svc(db).get_version(row.id, 1).status == "draft"
    graph["nodes"][1]["approvers"]["user_ids"] = [str(users["org"])]
    from flo.modules.approvals.schemas import WorkflowVersionWrite

    svc(db).update_version(row.id, 1, WorkflowVersionWrite(graph=graph), "1")
    assert svc(db).publish(row.id, 1).status == "published"


def test_picker_foreign_scope_and_authorization(eligibility_tree):
    db, targets, users = eligibility_tree

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            path = "/api/v1/approvals/eligible-approvers"

            async def pick(scope_type, scope_id, **extra):
                return await browser.get(
                    path,
                    params={
                        "document_type": "requisition",
                        "scope_type": scope_type,
                        "scope_id": str(scope_id),
                    }
                    | extra,
                    headers=headers(browser),
                )

            for scope_type, id in (("org", db[2]), ("org", uuid4()), ("bu", uuid4())):
                assert (await pick(scope_type, id)).status_code == 404
            foreign = OrgService(db[0], Scope(db[2]), db[3]).create_unit(
                OrgUnitCreate(code="FOREIGN", name="Foreign", kind="bu")
            )
            assert (await pick("bu", foreign.id)).status_code == 404
            result = await pick("bu", targets["a1"].scope_id)
            assert result.status_code == 200, result.text
            # The bootstrap administrator also holds all approval permissions.
            assert {row["user_id"] for row in result.json()["rows"]} == {
                str(users[label]) for label in ("org", "child")
            } | {str(db[3])}
            assert (await pick("bu", targets["a"].scope_id, q="BU@")).json()["rows"] == [
                {"user_id": str(users["bu"]), "email": "bu@example.test", "name": None}
            ]
            assert (await pick("bu", targets["b"].scope_id, q="bu")).json()["rows"] == []
            auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
            admin_role = (
                db[0]
                .execute(
                    "SELECT id FROM role WHERE org_id=%s AND code='organization-administrator'",
                    (db[1],),
                )
                .fetchone()[0]
            )
            auth.revoke_permission(admin_role, "workflow.manage")
            assert (await pick("org", db[1])).status_code == 403
            assert (await pick("bu", targets["a"].scope_id)).status_code == 403
            assignment = (
                db[0]
                .execute("SELECT id FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
                .fetchone()[0]
            )
            auth.revoke_role(assignment)
            assert (await pick("bu", targets["a"].scope_id)).status_code == 404

    asyncio.run(scenario())


def test_parent_bu_grant_does_not_cover_child_unit(eligibility_tree):
    db, targets, users = eligibility_tree
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    project = uuid4()
    auth.register_scope(ScopeType.PROJECT, project, ScopeType.BU, targets["a"].scope_id)
    with tenant_transaction(db[0], Scope(db[1])):
        resolver = AuthorizationResolver(db[0], Scope(db[1]))
        assert users["bu"] in resolver.holders("requisition.approve", targets["a"])
        assert users["bu"] in resolver.holders(
            "requisition.approve", AuthorizationTarget(ScopeType.PROJECT, project)
        )
        assert users["bu"] not in resolver.holders("requisition.approve", targets["a1"])
        assert users["bu"] not in resolver.holders("requisition.approve", targets["b"])
        assert users["child"] in resolver.holders("requisition.approve", targets["a1"])
        assert users["org"] in resolver.holders("requisition.approve", targets["a1"])


def test_picker_limits_rows_and_treats_query_as_literal_prefix(eligibility_tree):
    from flo.modules.approvals.service import approver_picker

    db, targets, users = eligibility_tree
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = auth.seed_baseline_roles()[role_code("approver")]
    for i in range(55):
        user = IdentityId(uuid4())
        db[0].execute(
            "INSERT INTO identity(id,email,password_hash) VALUES(%s,%s,%s)",
            (user, f"bulk{i:02d}@example.test", "$argon2id$test-placeholder"),
        )
        auth.grant_role(user, role.id, targets["a"])
    with tenant_transaction(db[0], Scope(db[1])):
        resolver = AuthorizationResolver(db[0], Scope(db[1]))
        page = approver_picker(db[0], Scope(db[1]), "requisition", targets["a"], "BULK", resolver)
        assert len(page.rows) == 50
        assert [row.email for row in page.rows] == [f"bulk{i:02d}@example.test" for i in range(50)]
        assert not approver_picker(
            db[0], Scope(db[1]), "requisition", targets["a"], "%", resolver
        ).rows


def test_role_eligibility_uses_injected_resolver(eligibility_tree):
    db, targets, users = eligibility_tree

    class Resolver:
        def holders(self, permission, target):
            assert permission == "requisition.approve"
            assert target == targets["a"]
            return [users["both"]]

        def role_holders(self, role_code, target):
            assert role_code == "requestor"
            assert target == targets["a"]
            return []

    result = eligible_approvers(
        db[0],
        Scope(db[1]),
        "requisition",
        RoleApprovers(type="role", role_code="requestor"),
        targets["a"],
        uuid4(),
        Resolver(),
    )
    assert not result.members


def test_validation_and_publish_use_injected_resolver(eligibility_tree):
    from flo.modules.approvals.service import WorkflowService

    db, targets, users = eligibility_tree

    class Resolver:
        def holders(self, permission, target):
            assert permission == "requisition.approve"
            assert target == targets["org"]
            return []

    row = definition(db)
    graph = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {
                "id": "approve",
                "kind": "approval",
                "mode": "any",
                "approvers": {"type": "users", "user_ids": [str(users["org"])]},
            },
            {"id": "end", "kind": "end"},
        ],
        "edges": [{"from": "start", "to": "approve"}, {"from": "approve", "to": "end"}],
    }
    draft(db, row, graph)
    service = WorkflowService(db[0], Scope(db[1]), db[3], resolver=Resolver())
    assert [issue.code for issue in service.validation(row.id, 1).errors] == ["missing_approvers"]
    with pytest.raises(ProblemError) as caught:
        service.publish(row.id, 1)
    assert caught.value.checks["problem"] == "workflow_invalid"


def test_unregistered_document_adapter_has_actionable_error(monkeypatch):
    monkeypatch.setattr(conditions, "_adapters", {})
    with pytest.raises(ProblemError) as caught:
        conditions.approve_permission("vendor")
    assert caught.value.errors[0].field == "/document_type"
    assert "Approval integration for vendor is not available yet" in caught.value.errors[0].message
    assert "Contact your operator" in caught.value.errors[0].message
