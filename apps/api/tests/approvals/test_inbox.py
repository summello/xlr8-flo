"""APR-020: the four inbox views over live membership, paging, filters and server totals."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.identity import IdentityId
from flo.kernel.money import Money
from flo.kernel.tenancy.context import Scope
from flo.modules.approvals.inbox import inbox
from flo.modules.approvals.schemas import DecisionWrite, DocumentSummary
from flo.modules.approvals.service import decide
from tests.approvals.test_delegation import (
    NOW,
    begin_as,
    delegate,
    drop_roles,
    publish,
    users_graph,
)
from tests.approvals.test_engine import begin, resolver
from tests.approvals.test_engine import setup as setup  # noqa: F401  (fixture)
from tests.approvals.test_workflow_definitions import workflow_db as workflow_db  # noqa: F401
from tests.isolation.test_real_sessions import client, login
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401


def summarise(setup, amount="1250.5000", number="REQ-00001", title="Press"):
    db, adapter, *_ = setup
    calls = []

    def summaries(conn, scope, ids):
        calls.append(list(ids))
        return {
            i: DocumentSummary(
                number=number,
                title=title,
                amount=Money(Decimal(amount), "USD") if amount else None,
                requester_id=db[3],
                href="/requisitions/1",
                note="Needed by Friday",
            )
            for i in ids
        }

    adapter.summaries = summaries
    return calls


def view(setup, user, name, **kwargs):
    db = setup[0]
    return inbox(
        db[0],
        Scope(db[1]),
        user_id=user,
        view=name,
        resolver=resolver(db),
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


def test_member_sees_pending_and_overdue_with_server_total_and_one_summaries_call(setup):
    db, adapter, users, *_ = setup
    owner, other = users[0], users[1]
    publish(setup, users_graph(owner, due_days=1))
    calls = summarise(setup)
    begin(setup)
    pending = view(setup, owner, "pending")
    assert pending.total == 1 and pending.counts.pending == 1
    row = pending.rows[0]
    assert row.status_word == "Your Turn" and row.on_behalf_of_email is None
    assert row.number == "REQ-00001" and row.requester_note == "Needed by Friday"
    assert row.amount is not None and row.amount.amount == "1250.5000"
    assert [(a.amount, a.currency) for a in pending.total_amount] == [("1250.5000", "USD")]
    assert (row.step.index, row.step.of) == (1, 1)
    assert row.document_version == 1
    assert len(calls) == 1
    adapter.value = replace(adapter.value, version=2)  # a decision must name the current version
    assert view(setup, owner, "pending").rows[0].document_version == 2
    assert view(setup, other, "pending").total == 0
    late = view(setup, owner, "overdue", now=row.due_at + timedelta(days=3))
    assert late.total == 1 and late.rows[0].status_word == "Overdue"
    assert view(setup, owner, "overdue").total == 0
    assert view(setup, owner, "overdue", now=NOW + timedelta(days=30)).counts.overdue == 1


def test_delegate_is_covering_and_owner_sees_the_task_passed_on(setup):
    db, adapter, users, *_ = setup
    owner, other = users[0], users[1]
    publish(setup, users_graph(owner))
    summarise(setup)
    begin(setup)
    assert view(setup, other, "pending").total == 0
    delegate(setup, owner, other)
    covering = view(setup, other, "pending")
    assert covering.total == 1 and covering.rows[0].on_behalf_of_email == "a@example.test"
    mine = view(setup, other, "delegated").rows[0]
    assert mine.status_word == "Covering a@example.test"
    passed = view(setup, owner, "delegated").rows[0]
    assert passed.status_word == "With b@example.test" and passed.on_behalf_of_email is None
    assert view(setup, owner, "pending").total == 1  # the owner may still decide


def test_removed_member_leaves_pending_because_membership_is_live(setup):
    db, adapter, users, *_ = setup
    owner = users[0]
    publish(setup, users_graph(owner))
    summarise(setup)
    begin(setup)
    assert view(setup, owner, "pending").total == 1
    drop_roles(db, owner)
    assert view(setup, owner, "pending").total == 0


def test_completed_lists_decisions_as_self_or_on_behalf(setup):
    db, adapter, users, *_ = setup
    owner, other = users[0], users[1]
    publish(setup, users_graph(owner))
    summarise(setup)
    instance = begin(setup)
    delegate(setup, owner, other)
    decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[-1].id,
        actor_id=other,
        body=DecisionWrite(outcome="approve", document_version=adapter.value.version),
    )
    done = view(setup, other, "completed")
    assert done.total == 1
    row = done.rows[0]
    assert row.status_word == "Approved" and row.on_behalf_of_email == "a@example.test"
    assert row.decided_at is not None
    assert view(setup, other, "pending").total == 0
    assert view(setup, owner, "completed").total == 0


def test_filters_sort_paging_and_cursor_guard(setup):
    db, adapter, users, *_ = setup
    owner = users[0]
    publish(setup, users_graph(owner))
    summarise(setup, title="Press brake")
    begin(setup)
    assert view(setup, owner, "pending", q="req-0").total == 1
    assert view(setup, owner, "pending", q="press").total == 1
    email = db[0].execute("SELECT email FROM identity WHERE id=%s", (db[3],)).fetchone()[0]
    assert view(setup, owner, "pending", q=email[:4].upper()).total == 1  # requester prefix
    assert view(setup, owner, "pending", q="brake").total == 0
    assert view(setup, owner, "pending", document_type="po").total == 0
    assert view(setup, owner, "pending", sort="amount", direction="desc").total == 1
    with pytest.raises(ProblemError) as error:
        view(setup, owner, "pending", cursor="not-a-cursor")
    assert error.value.code == ErrorCode.VALIDATION_FAILED


def test_inbox_http_is_self_scoped_and_foreign_tenant_sees_nothing(setup):
    db, adapter, users, *_ = setup
    publish(setup, users_graph(users[0]))
    summarise(setup)
    begin(setup)
    # Another organization's scope never reaches this organization's tasks.
    foreign = inbox(
        db[0],
        Scope(db[2]),
        user_id=users[0],
        view="pending",
        resolver=resolver(db),
        now=NOW,
    )
    assert foreign.total == 0 and foreign.counts.pending == 0

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            ok = await browser.get("/api/v1/approvals/tasks?view=completed")
            assert ok.status_code == 200 and ok.json()["rows"] == []
            bad = await browser.get("/api/v1/approvals/tasks?view=nope")
            assert bad.status_code == 422
            assert (await browser.get("/api/v1/approvals/tasks?cursor=x")).status_code == 422

    asyncio.run(scenario())


def test_eligible_delegates_for_a_subset_must_hold_every_selected_type(setup):
    """APR-017: a request for two types lists only people who may approve both."""
    from flo.modules.approvals import conditions
    from flo.modules.identity.service import IdentityAuthorizationService
    from tests.approvals.test_engine import Adapter

    db, adapter, users, *_ = setup

    class Project(Adapter):
        document_type = "project"
        approve_permission = "project.approve"

    conditions.register_document_type(Project(db))
    auth = IdentityAuthorizationService(db[0], Scope(db[1]), db[3])
    role = auth.create_role("project-approver", "Project approver")
    auth.grant_permission(role.id, "project.approve")
    auth.grant_role(users[1], role.id, adapter.value.target)

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            found = {}
            for name, query in (
                ("both", "document_types=requisition&document_types=project"),
                ("only", "document_types=requisition"),
            ):
                result = await browser.get(
                    f"/api/v1/approvals/delegations/eligible-delegates?{query}"
                )
                assert result.status_code == 200, result.text
                found[name] = {r["email"] for r in result.json()["rows"]}
            return found

    found = asyncio.run(scenario())
    assert found["both"] == {"b@example.test"}
    assert {"a@example.test", "b@example.test", "c@example.test"} <= found["only"]


def test_decided_on_my_behalf_is_self_scoped_and_isolated(setup):
    """APR-017 / TEN-010: the owner sees what delegates decided for them, nobody else does."""
    from flo.modules.approvals.service import decided_on_behalf

    db, adapter, users, *_ = setup
    owner, delegate_user = db[3], users[0]
    adapter.value = replace(adapter.value, requester_id=IdentityId(users[2]))
    publish(setup, users_graph(owner))
    instance = begin_as(setup, users[2])
    delegate(setup, owner, delegate_user)
    decide(
        db[0],
        Scope(db[1]),
        task_id=instance.tasks[-1].id,
        actor_id=delegate_user,
        body=DecisionWrite(outcome="approve", document_version=adapter.value.version),
    )
    rows = decided_on_behalf(db[0], Scope(db[1]), owner)
    assert [(r.actor_email, r.on_behalf_of_email, r.outcome) for r in rows] == [
        (
            "a@example.test",
            db[0].execute("SELECT email FROM identity WHERE id=%s", (owner,)).fetchone()[0],
            "approve",
        )
    ]
    assert decided_on_behalf(db[0], Scope(db[1]), delegate_user) == []
    assert decided_on_behalf(db[0], Scope(db[2]), owner) == []

    async def scenario():
        async with client(app) as browser:
            assert (
                await browser.get("/api/v1/approvals/delegations/decided-on-my-behalf")
            ).status_code == 401
            await login(browser)
            mine = await browser.get("/api/v1/approvals/delegations/decided-on-my-behalf")
            assert mine.status_code == 200 and len(mine.json()) == 1
        async with client(app) as browser:
            await login(browser, "second@example.test")
            theirs = await browser.get("/api/v1/approvals/delegations/decided-on-my-behalf")
            assert theirs.status_code == 200 and theirs.json() == []

    asyncio.run(scenario())
