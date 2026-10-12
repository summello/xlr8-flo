"""Daily digests and escalation: one email per person per day, no amounts, delegate-aware."""

import json
from datetime import timedelta
from uuid import uuid4

from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from flo.modules.approvals.reminders import run_org
from flo.modules.approvals.schemas import DecisionWrite
from flo.modules.approvals.service import decide
from tests.approvals.test_delegation import begin, delegate, publish, users_graph
from tests.approvals.test_delegation import setup as setup  # noqa: F401  (fixture)
from tests.approvals.test_delegation import workflow_db as workflow_db  # noqa: F401
from tests.kernel.test_migrate import empty_database as empty_database  # noqa: F401
from tests.org.test_bootstrap import bootstrap_db as bootstrap_db  # noqa: F401


def digests(db):
    with tenant_transaction(db[0], Scope(db[1])):
        return (
            db[0]
            .execute(
                "SELECT payload FROM outbox WHERE payload->>'template'='approval-digest-v1' "
                "ORDER BY created_at,id"
            )
            .fetchall()
        )


def created_at(db):
    with tenant_transaction(db[0], Scope(db[1])):
        return db[0].execute("SELECT created_at FROM approval_task").fetchone()[0]


def test_one_digest_per_person_per_day_without_amounts(setup):
    db, adapter, users, *_ = setup
    publish(setup, users_graph(users[0], users[1], due_days=2))
    begin(setup)
    first_document = adapter.document
    snapshot = adapter.snapshot
    adapter.document = uuid4()
    second_document = adapter.document
    adapter.snapshot = lambda conn, scope, id: (
        adapter.value
        if scope.org_id == db[1] and id in (first_document, second_document)
        else snapshot(conn, scope, id)
    )
    begin(setup)
    soon = created_at(db) + timedelta(days=1, hours=12)  # due within 24 hours
    first = run_org(db[0], Scope(db[1]), now=soon)
    second = run_org(db[0], Scope(db[1]), now=soon + timedelta(hours=1))  # same day
    assert (first.digests, second.digests) == (2, 0)
    rows = digests(db)
    assert len(rows) == 2
    assert all(len(r[0]["context"]["items"]) == 2 for r in rows)
    text = json.dumps([r[0] for r in rows])
    assert "300000" not in text and "amount" not in text
    assert {r[0]["to"] for r in rows} == {"a@example.test", "b@example.test"}


def test_digest_goes_to_the_delegate_during_an_active_delegation(setup):
    db, adapter, users, *_ = setup
    owner, other = users[0], users[1]
    publish(setup, users_graph(owner, due_days=1))
    begin(setup)
    now = created_at(db) + timedelta(hours=18)
    delegate(setup, owner, users[2], start=now - timedelta(days=1), now=now)
    run_org(db[0], Scope(db[1]), now=now)
    recipients = {r[0]["to"] for r in digests(db)}
    assert "c@example.test" in recipients and other  # the delegate is told


def test_escalation_adds_members_without_rewriting_attribution(setup):
    db, adapter, users, *_ = setup
    owner, other, escalator = users
    publish(setup, users_graph(owner, escalate=[escalator]))
    instance = begin(setup)
    task = instance.tasks[-1].id
    before = run_org(db[0], Scope(db[1]), now=created_at(db) + timedelta(hours=2))
    assert before.escalated == 0
    late = run_org(db[0], Scope(db[1]), now=created_at(db) + timedelta(days=2))
    assert late.escalated == 1
    result = decide(
        db[0],
        Scope(db[1]),
        task_id=task,
        actor_id=escalator,
        body=DecisionWrite(outcome="approve", document_version=adapter.value.version),
    )
    assert result.decision.authority == "member" and result.instance.status == "approved"


def test_new_due_task_after_digest_does_not_resend_or_crash(setup):
    from uuid import uuid4

    db, adapter, users, *_ = setup
    publish(setup, users_graph(users[0], due_days=1))
    begin(setup)
    now = created_at(db) + timedelta(hours=1)
    assert run_org(db[0], Scope(db[1]), now=now).digests == 1
    first_document = adapter.document
    snapshot = adapter.snapshot
    adapter.document = uuid4()
    second_document = adapter.document
    adapter.snapshot = lambda conn, scope, id: (
        adapter.value
        if scope.org_id == db[1] and id in (first_document, second_document)
        else snapshot(conn, scope, id)
    )
    begin(setup)
    assert run_org(db[0], Scope(db[1]), now=now + timedelta(hours=1)).digests == 0
    assert len(digests(db)) == 1
    with tenant_transaction(db[0], Scope(db[1])):
        assert (
            db[0].execute("SELECT reminded_on FROM approval_task").fetchall() == [(now.date(),)] * 2
        )


def test_internal_reminder_trigger_origin_key_and_nonreplay(setup, monkeypatch):
    import asyncio

    from flo.api.health import app
    from flo.api.origin_auth import require_origin_secret
    from tests.isolation.test_real_sessions import client, headers

    db = setup[0]
    app.dependency_overrides.pop(require_origin_secret)
    token = "A" * 32
    monkeypatch.setenv("ORIGIN_SHARED_SECRET", token)

    async def scenario():
        async with client(app) as browser:
            # Use the production CSRF protocol even on internal routes.
            await browser.get(
                "/api/v1/auth/sessions", headers={"X-FLO-Origin-Secret": token}
            )
            path = "/internal/jobs/approval-reminders"
            for secret in (None, "incorrect"):
                h = headers(browser, uuid4().hex)
                if secret:
                    h["X-FLO-Origin-Secret"] = secret
                response = await browser.post(path, headers=h)
                assert response.status_code == 404
            h = headers(browser) | {"X-FLO-Origin-Secret": token}
            h.pop("Idempotency-Key")
            response = await browser.post(path, headers=h)
            assert response.status_code == 400
            assert db[0].execute("SELECT count(*) FROM job").fetchone() == (0,)
            key = uuid4().hex
            for replay_key in (key, key, uuid4().hex):
                response = await browser.post(
                    path,
                    headers=headers(browser, replay_key) | {"X-FLO-Origin-Secret": token},
                )
                assert response.status_code == 200, response.text
                assert response.json() == {"organizations": 2, "digests": 0, "escalated": 0}
            rows = (
                db[0]
                .execute(
                    "SELECT org_id,kind,count(*) FROM job GROUP BY org_id,kind ORDER BY org_id"
                )
                .fetchall()
            )
            assert rows == [(org, "approval.reminders", 3) for org in sorted((db[1], db[2]))]
            assert digests(db) == []  # endpoint only queues tenant work

    asyncio.run(scenario())


def test_jobs_tick_runs_registered_reminder_handler(setup, monkeypatch):
    from flo.api import document_types, internal
    from flo.kernel.config import Settings
    from flo.kernel.outbox.dispatcher import OutboxSummary
    from flo.modules.approvals.reminders import run_all

    db, adapter, users, *_ = setup
    publish(setup, users_graph(users[0], due_days=1))
    begin(setup)
    with tenant_transaction(db[0], Scope(db[1])):
        db[0].execute("UPDATE approval_task SET due_at=now()-interval '1 hour'")
    run_all(db[0])
    monkeypatch.setattr(document_types, "register_document_types", lambda: None)
    monkeypatch.setattr(internal, "create_email_sender", lambda settings: object())
    monkeypatch.setattr(internal.OutboxDispatcher, "run", lambda self, budget: OutboxSummary())
    report = internal._run_jobs_tick(Settings())
    assert report.jobs.done == 2 and report.jobs.dead == 0
    assert len(digests(db)) == 1


def test_reminder_runs_serialize_daily_recipient_check(setup, monkeypatch):
    import psycopg

    from flo.kernel.outbox.store import OutboxStore
    from tests.org.test_bootstrap import connection_url

    db, adapter, users, *_ = setup
    publish(setup, users_graph(users[0], due_days=1))
    begin(setup)
    original = OutboxStore.add_email
    checked = []

    def competing_run(store, **kwargs):
        with psycopg.connect(connection_url(db[0]), autocommit=True) as other:
            with other.transaction():
                assert other.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s::text, 8))",
                    (db[1],),
                ).fetchone() == (False,)
                checked.append(True)
        return original(store, **kwargs)

    monkeypatch.setattr(OutboxStore, "add_email", competing_run)
    assert run_org(db[0], Scope(db[1]), now=created_at(db) + timedelta(hours=1)).digests == 1
    assert checked == [True]
