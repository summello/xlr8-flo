"""APR-021 draft preview uses production routing without a synthetic requester."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from flo.api.health import app
from flo.kernel.errors import ProblemError
from flo.kernel.money import Money
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.preview import describe_branch
from flo.modules.approvals.schemas import parse_condition
from tests.approvals.test_engine import begin, graph, published
from tests.approvals.test_engine import setup as setup
from tests.approvals.test_workflow_definitions import definition, draft, svc
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db


def preview(setup, row, version=1, amount="300000"):
    return svc(setup[0]).route_preview(row.id, version, amount, "USD", None)


def test_shared_walk_start_and_route_preview_default_boundary_and_traces(setup):
    row, _ = published(setup, conditional=True)
    result = preview(setup, row)
    started = begin(setup)
    assert [s.node_id for s in result.steps] == [s.node_id for s in started.route]
    assert result.steps[0].reached_via == "Estimate is over 200,000.00 USD"
    # Reverse the named branch's predicate while keeping the same later approval.
    db = setup[0]
    g = graph(conditional=True)
    g["edges"][2]["to"] = "one"
    d = draft(db, row, g)
    above = preview(setup, row, d.version)
    below = preview(setup, row, d.version, "100000")
    assert above.steps[0].node_id == below.steps[0].node_id
    assert below.steps[0].reached_via == "No other condition matched"
    assert above.steps[0].reached_via != below.steps[0].reached_via
    assert above.path_edge_indices == [0, 1, 3]
    assert below.path_edge_indices == [0, 2, 3]
    db[0].execute("UPDATE approval_instance SET status='withdrawn' WHERE id=%s", (started.id,))
    setup[1].value = replace(setup[1].value, attributes={"amount": Money(Decimal("100000"), "USD")})
    real_default = begin(setup)
    assert preview(setup, row, amount="100000").steps == []
    assert real_default.route == []


def test_draft_preview_uses_requested_version_and_writes_nothing(setup):
    row, _ = published(setup)
    g = graph()
    g["nodes"][1]["name"] = "Draft Finance"
    g["nodes"][1]["id"] = "draft-step"
    g["edges"] = [{"from": "start", "to": "draft-step"}, {"from": "draft-step", "to": "end"}]
    d = draft(setup[0], row, g)
    before = [
        setup[0][0].execute(f"SELECT count(*) FROM {table}").fetchone()
        for table in ("approval_instance", "approval_task", "audit_log")
    ]
    result = preview(setup, row, d.version)
    assert result.workflow_version_id == d.id
    assert result.steps[0].label == "Draft Finance" and result.steps[0].reached_via is None
    after = [
        setup[0][0].execute(f"SELECT count(*) FROM {table}").fetchone()
        for table in ("approval_instance", "approval_task", "audit_log")
    ]
    assert before == after


def test_requester_excluded_by_start_present_in_admin_preview_scope_exclusion_unchanged(setup):
    db, adapter, users, *_ = setup
    adapter.value = replace(adapter.value, requester_id=users[0])
    g = graph()
    g["nodes"][1]["approvers"] = {"type": "users", "user_ids": [str(users[0])]}
    row = definition(db)
    draft(db, row, g)
    svc(db).publish(row.id, 1)
    result = preview(setup, row)
    assert result.separation_of_duty_applied is False
    from flo.modules.approvals.eligibility import eligible_approvers
    from flo.modules.approvals.schemas import UserApprovers

    default_members = eligible_approvers(
        db[0],
        Scope(db[1]),
        "requisition",
        UserApprovers(type="users", user_ids=[users[0]]),
        adapter.value.target,
        requester_id=None,
        resolver=svc(db).resolver,
    )
    assert [member.user_id for member in default_members.members] == [users[0]]
    assert result.steps[0].approver_emails == ["a@example.test"] and not result.steps[0].excluded
    with pytest.raises(ProblemError) as exc:
        begin(setup)
    assert exc.value.checks["problem"] == "no_eligible_approver"
    outsider = uuid4()
    db[0].execute(
        "INSERT INTO identity(id,email,password_hash) "
        "VALUES(%s,'outsider@example.test','$argon2id$test-placeholder')",
        (outsider,),
    )
    db[0].execute(
        "INSERT INTO identity_membership(identity_id,org_id) VALUES(%s,%s)", (outsider, db[1])
    )
    from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService

    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    auth.grant_role(
        outsider,
        auth.seed_baseline_roles()["requestor"].id,
        AuthorizationTarget.organization(db[1]),
    )
    g["nodes"][1]["approvers"]["user_ids"].append(str(outsider))
    d = draft(db, row, g)
    result = preview(setup, row, d.version)
    assert [(e.email, e.reason) for e in result.steps[0].excluded] == [
        ("outsider@example.test", "not_eligible_in_scope")
    ]


@pytest.mark.parametrize("amount", ["-1", "1e2", "1.00001", "", "123456789012345", "1."])
def test_bad_amount_is_field_error(setup, amount):
    row, _ = published(setup)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            r = await browser.get(
                f"/api/v1/workflows/{row.id}/versions/1/route-preview",
                params={"amount": amount, "currency": "USD"},
            )
            assert r.status_code == 422
            assert r.json()["errors"][0]["field"] == "/amount"

    asyncio.run(scenario())


def test_foreign_tenant_route_preview_and_scope_are_404(setup):
    db = setup[0]
    foreign = definition(db, db[2])
    own, _ = published(setup)
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService

    unit = OrgService(db[0], Scope(db[2]), db[3]).create_unit(
        OrgUnitCreate(code="FOREIGN", name="Foreign", kind="bu")
    )

    async def scenario():
        async with client(app) as browser:
            path = f"/api/v1/workflows/{foreign.id}/versions/1/route-preview"
            assert (
                await browser.get(path, params={"amount": "1", "currency": "USD"})
            ).status_code == 401
            await login(browser)
            assert (
                await browser.get(path, params={"amount": "1", "currency": "USD"})
            ).status_code == 404
            assert (
                await browser.get(
                    f"/api/v1/workflows/{own.id}/versions/1/route-preview",
                    params={"amount": "1", "currency": "USD", "bu_id": str(unit.id)},
                )
            ).status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "op,words",
    [
        ("eq", "is"),
        ("ne", "is not"),
        ("lt", "is below"),
        ("le", "is at most"),
        ("gt", "is over"),
        ("ge", "is at least"),
        ("in", "is one of"),
        ("not_in", "is not one of"),
    ],
)
def test_describe_branch_fixed_operator_words(op, words):
    value = ["A", "B"] if op in {"in", "not_in"} else {"amount": "50000", "currency": "USD"}
    assert (
        describe_branch(parse_condition({"field": "amount", "op": op, "value": value}), True)
        == f"Estimate {words} {'A, B' if isinstance(value, list) else '50,000.00 USD'}"
    )


def test_incomplete_and_cyclic_drafts_are_blocked_before_walking(setup, monkeypatch):
    from flo.modules.approvals import service

    row = definition(setup[0])
    g = graph(conditional=True)
    g["edges"][1]["to"] = "condition"
    draft(setup[0], row, g)

    def forbidden(*args, **kwargs):
        raise AssertionError("An invalid cyclic graph reached the walker")

    monkeypatch.setattr(service, "preview_route", forbidden)
    result = preview(setup, row)
    assert result.problems and any("Cycle" in p.message for p in result.problems)
    assert result.steps == [] and result.separation_of_duty_applied is False


def test_route_preview_authorization_for_every_baseline_role(setup):
    from flo.kernel.tenancy.rls import tenant_transaction
    from flo.modules.identity.models import BASELINE_ROLES
    from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService

    db = setup[0]
    row, _ = published(setup)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            path = f"/api/v1/workflows/{row.id}/versions/1/route-preview"
            for code, _ in BASELINE_ROLES:
                with tenant_transaction(db[0], Scope(db[1])):
                    db[0].execute(
                        "DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3])
                    )
                    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
                    auth.grant_role(
                        db[3],
                        auth.seed_baseline_roles()[code].id,
                        AuthorizationTarget.organization(db[1]),
                    )
                r = await browser.get(path, params={"amount": "1", "currency": "USD"})
                assert r.status_code == (
                    200 if code in {"auditor", "organization-administrator"} else 403
                ), (code, r.text)
            db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
            assert (
                await browser.get(path, params={"amount": "1", "currency": "USD"})
            ).status_code == 404

    asyncio.run(scenario())


def test_nearest_condition_and_group_words_come_from_shared_walk():
    from flo.modules.approvals.preview import walk
    from flo.modules.approvals.schemas import parse_graph

    g = graph(conditional=True)
    g["nodes"].insert(2, {"id": "second-condition", "kind": "condition", "name": "Second"})
    g["edges"][1]["to"] = "second-condition"
    g["edges"] += [
        {
            "from": "second-condition",
            "to": "one",
            "when": {
                "field": "amount",
                "op": "ge",
                "value": {"amount": "250000", "currency": "USD"},
            },
        },
        {"from": "second-condition", "to": "end", "default": True},
    ]
    route = []
    assert (
        walk(parse_graph(g), {"amount": Money(Decimal("300000"), "USD")}, route=route).node.id
        == "one"
    )
    assert route[-1]["reached_via"] == "Estimate is at least 250,000.00 USD"
    expression = {
        "all": [
            {"field": "amount", "op": "gt", "value": {"amount": "50000", "currency": "USD"}},
            {
                "any": [
                    {"field": "department", "op": "eq", "value": "Finance"},
                    {"field": "department", "op": "eq", "value": "Operations"},
                ]
            },
        ]
    }
    assert (
        describe_branch(parse_condition(expression), True)
        == "Estimate is over 50,000.00 USD and Department is Finance or Department is Operations"
    )


def test_invalid_currency_is_a_field_error(setup):
    row, _ = published(setup)
    with pytest.raises(ProblemError) as exc:
        svc(setup[0]).route_preview(row.id, 1, "10", "invalid", None)
    assert exc.value.errors[0].field == "/currency"


def test_preview_validates_the_selected_snapshot_even_if_the_draft_changes(setup, monkeypatch):
    from flo.modules.approvals import service
    from flo.modules.approvals.schemas import WorkflowVersionWrite

    db = setup[0]
    row = definition(db)
    cyclic = graph(conditional=True)
    cyclic["edges"][1]["to"] = "condition"
    draft(db, row, cyclic)
    subject = svc(db)
    read = subject.get_version

    def concurrent_edit(id, version):
        selected = read(id, version)
        svc(db).update_version(
            id, version, WorkflowVersionWrite(graph=graph()), str(selected.row_version)
        )
        return selected

    monkeypatch.setattr(subject, "get_version", concurrent_edit)
    monkeypatch.setattr(
        service,
        "preview_route",
        lambda *args, **kwargs: pytest.fail("The selected cyclic snapshot reached the walker"),
    )
    result = subject.route_preview(row.id, 1, "300000", "USD", None)
    assert any("Cycle" in problem.message for problem in result.problems)
    assert svc(db).get_version(row.id, 1).graph.nodes[1].kind == "approval"


def test_bu_administrator_cannot_preview_outside_workflow_scope(setup):
    from flo.modules.approvals.schemas import WorkflowCreate
    from flo.modules.identity.service import (
        AuthorizationTarget,
        IdentityAuthorizationService,
        ScopeType,
    )
    from flo.modules.org.schemas import OrgUnitCreate
    from flo.modules.org.service import OrgService

    db = setup[0]
    org = OrgService(db[0], Scope(db[1]), db[3])
    a = org.create_unit(OrgUnitCreate(code="A", name="A", kind="bu"))
    b = org.create_unit(OrgUnitCreate(code="B", name="B", kind="bu"))
    child = org.create_unit(OrgUnitCreate(code="CHILD", name="Child", kind="ou", parent_id=a.id))
    row = svc(db).create(
        WorkflowCreate(document_type="requisition", name="A Review", scope_type="bu", scope_id=a.id)
    )
    draft(db, row, graph())
    org_row, _ = published(setup)
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    db[0].execute("DELETE FROM user_role WHERE org_id=%s AND user_id=%s", (db[1], db[3]))
    auth.grant_role(
        db[3],
        auth.seed_baseline_roles()["organization-administrator"].id,
        AuthorizationTarget(ScopeType.BU, a.id),
    )

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            path = f"/api/v1/workflows/{row.id}/versions/1/route-preview"
            for unit in (a, child):
                response = await browser.get(
                    path, params={"amount": "1", "currency": "USD", "bu_id": str(unit.id)}
                )
                assert response.status_code == 200, response.text
            response = await browser.get(
                path, params={"amount": "1", "currency": "USD", "bu_id": str(b.id)}
            )
            assert response.status_code == 404

    asyncio.run(scenario())
    # An org-bound workflow still accepts any tenant unit.
    assert svc(db).route_preview(org_row.id, 1, "1", "USD", b.id).steps


def test_viewing_administrator_is_not_excluded_from_preview(setup):
    from flo.modules.identity.service import AuthorizationTarget, IdentityAuthorizationService

    db = setup[0]
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    auth.grant_role(
        db[3], auth.seed_baseline_roles()["approver"].id, AuthorizationTarget.organization(db[1])
    )
    g = graph()
    g["nodes"][1]["approvers"] = {"type": "users", "user_ids": [str(db[3])]}
    row = definition(db)
    draft(db, row, g)
    result = preview(setup, row)
    email = db[0].execute("SELECT email FROM identity WHERE id=%s", (db[3],)).fetchone()[0]
    assert result.steps[0].approver_emails == [email]
    assert result.steps[0].excluded == []
    assert result.separation_of_duty_applied is False


@pytest.mark.parametrize(
    "amount,currency,expected",
    [
        ("1.2345", "USD", "1.23 USD"),
        ("1.0050", "USD", "1.01 USD"),
        ("1234.5000", "JPY", "1,235 JPY"),
    ],
)
def test_branch_money_uses_kernel_currency_rounding(amount, currency, expected):
    condition = parse_condition(
        {"field": "amount", "op": "gt", "value": {"amount": amount, "currency": currency}}
    )
    assert describe_branch(condition, True) == f"Estimate is over {expected}"


@pytest.mark.parametrize(
    "op,expected", [("is_null", "Department is empty"), ("not_null", "Department is not empty")]
)
def test_null_branch_words_omit_value(op, expected):
    assert describe_branch(parse_condition({"field": "department", "op": op}), True) == expected


def test_eligibility_requires_explicit_requester_and_resolver(setup):
    from flo.modules.approvals.eligibility import eligible_approvers
    from flo.modules.approvals.schemas import UserApprovers

    db, adapter, users, *_ = setup
    args = (
        db[0],
        Scope(db[1]),
        "requisition",
        UserApprovers(type="users", user_ids=[users[0]]),
        adapter.value.target,
    )
    with pytest.raises(TypeError, match="requester_id"):
        eligible_approvers(*args, resolver=svc(db).resolver)
    with pytest.raises(TypeError, match="resolver"):
        eligible_approvers(*args, requester_id=None)
