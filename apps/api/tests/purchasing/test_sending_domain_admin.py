"""Sending domain status, check request and suppression list for administrators (E13-S21)."""

import asyncio
from uuid import uuid4

import psycopg
import pytest

from flo.api import internal
from flo.api.auth import get_auth_settings
from flo.api.health import app
from flo.kernel.config import Settings
from flo.kernel.email import DOMAIN_CHECK_JOB
from flo.kernel.jobs import JobQueue
from flo.kernel.logging import correlation_context
from flo.kernel.ports.email import DomainStatus, TransientEmailError
from flo.kernel.tenancy.context import Scope
from flo.kernel.tenancy.rls import tenant_transaction
from tests.isolation.test_real_sessions import client, headers, login
from tests.purchasing.test_deliverability import (
    bootstrap_db as bootstrap_db,
)
from tests.purchasing.test_deliverability import (
    empty_database as empty_database,
)
from tests.purchasing.test_deliverability import (
    header_db as header_db,
)
from tests.purchasing.test_deliverability import (
    lifecycle_db as lifecycle_db,
)
from tests.purchasing.test_deliverability import (
    lines_db as lines_db,
)
from tests.purchasing.test_deliverability import (
    sql,
)
from tests.purchasing.test_deliverability import (
    stack as stack,
)
from tests.vendors.test_vendors import regrant

BASE = "/api/v1/admin/sending-domain"
LIST = "/api/v1/admin/email-suppressions"


def url(settings):
    return settings.database_url.get_secret_value()


def store(settings, spf, dkim, detail="", age="0 seconds"):
    with psycopg.connect(url(settings)) as conn:
        conn.execute("DELETE FROM email_domain_status")
        conn.execute(
            "INSERT INTO email_domain_status (spf, dkim, detail, checked_at) "
            "VALUES (%s, %s, %s, now() - %s::interval)",
            (spf, dkim, detail, age),
        )


def suppress(db, org, email, reason="bounced", at="0 seconds"):
    with tenant_transaction(db[0], Scope(org)):
        db[0].execute(
            "INSERT INTO email_suppression (id, org_id, email_normalised, reason, created_at) "
            "VALUES (%s, %s, %s, %s, now() - %s::interval)",
            (uuid4(), org, email, reason, at),
        )


def run(db, calls):
    """Log in as the organization administrator and run `calls(browser)`."""

    async def scenario():
        async with client(app) as browser:
            await login(browser)
            regrant(db, ["org.setting.manage"])
            return await calls(browser)

    return asyncio.run(scenario())


@pytest.fixture
def resend_settings(stack):
    settings = Settings(email_provider="resend")
    app.dependency_overrides[get_auth_settings] = lambda: settings
    yield stack[1]
    app.dependency_overrides.pop(get_auth_settings, None)


def test_sending_domain_read_follows_the_stored_check(stack, resend_settings):
    db, settings = stack[0], resend_settings

    async def read(browser):
        first = (await browser.get(BASE)).json()
        store(settings, "pass", "fail", "DKIM TXT k: failed")
        failing = (await browser.get(BASE)).json()
        store(settings, "pass", "pass")
        passing = (await browser.get(BASE)).json()
        regrant(db, ["rfq.view"])
        return first, failing, passing, (await browser.get(BASE)).status_code

    first, failing, passing, denied = run(db, read)
    assert first["spf"] is None and first["checked_at"] is None
    assert first["detail"] == "" and first["po_email_allowed"] is False  # resend: held
    assert (failing["dkim"], failing["detail"], failing["po_email_allowed"]) == (
        "fail",
        "DKIM TXT k: failed",
        False,
    )
    assert passing["po_email_allowed"] is True and passing["check_pending"] is False
    assert denied == 403


def test_sending_domain_before_any_check_is_allowed_under_smtp(stack):
    async def read(browser):
        return (await browser.get(BASE)).json()

    body = run(stack[0], read)
    assert (body["spf"], body["dkim"], body["po_email_allowed"]) == (None, None, True)


def test_check_request_enqueues_once_and_is_rate_limited(stack):
    db, settings = stack

    async def calls(browser):
        post = lambda key: browser.post(BASE + "/check", headers=headers(browser, key))  # noqa: E731
        first, second = await post("a"), await post("b")
        no_key = await browser.post(BASE + "/check", headers={})
        store(settings, "pass", "pass")
        recent = await post("c")
        regrant(db, ["rfq.view"])
        return first, second, no_key, recent, await post("d")

    first, second, no_key, recent, denied = run(db, calls)
    assert first.status_code == 202 and first.json()["check_pending"] is True
    assert second.status_code == 202 and second.json()["check_pending"] is True
    assert sql(db, "SELECT kind, max_attempts FROM job WHERE org_id = %s", (db[1],)) == [
        ("email-domain-check", 1)
    ]
    assert no_key.status_code in (400, 403)
    assert recent.status_code == 429
    assert recent.json()["checks"]["problem"] == "domain_check_recent"
    assert sql(db, "SELECT count(*) FROM job WHERE org_id = %s", (db[1],)) == [(1,)]
    assert denied.status_code == 403


def test_check_request_without_csrf_is_refused(stack):
    async def calls(browser):
        return await browser.post(BASE + "/check", headers={"Idempotency-Key": "x"})

    assert run(stack[0], calls).status_code == 403


class Fake:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0

    def domain_status(self):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def queue_check(db):
    with correlation_context("check"), tenant_transaction(db[0], Scope(db[1])):
        JobQueue(db[0], Scope(db[1])).enqueue(DOMAIN_CHECK_JOB, {}, max_attempts=1)


def rows(settings):
    with psycopg.connect(url(settings)) as conn:
        return conn.execute("SELECT spf, dkim, detail FROM email_domain_status").fetchall()


def test_job_stores_one_row_and_skips_the_provider_when_recent(stack, monkeypatch):
    db, settings = stack
    fake = Fake(DomainStatus("unknown", "unknown", "Local mail catcher"))
    monkeypatch.setattr(internal, "create_domain_checker", lambda s: fake)
    queue_check(db)
    internal._run_jobs_tick(settings)
    assert rows(settings) == [("unknown", "unknown", "Local mail catcher")]
    assert sql(db, "SELECT state FROM job WHERE kind = 'email-domain-check'") == [("done",)]
    # a second organization asking inside the minute makes no provider call
    queue_check(db)
    internal._run_jobs_tick(settings)
    assert fake.calls == 1 and len(rows(settings)) == 1
    assert sql(db, "SELECT state FROM job WHERE kind = 'email-domain-check'") == [
        ("done",),
        ("done",),
    ]


def test_job_leaves_the_last_status_when_the_provider_is_down(stack, monkeypatch):
    db, settings = stack
    monkeypatch.setattr(
        internal, "create_domain_checker", lambda s: Fake(TransientEmailError("later"))
    )
    queue_check(db)
    internal._run_jobs_tick(settings)
    assert rows(settings) == []
    assert sql(db, "SELECT state, last_error FROM job WHERE kind = 'email-domain-check'") == [
        ("dead", "email domain check unavailable")
    ]


def test_suppression_list_paging_prefix_and_permission(stack):
    db = stack[0]
    for index in range(51):
        suppress(db, db[1], f"u{index:02d}@x.com", at=f"{index} minutes")
    suppress(db, db[1], "abc@x.com", at="100 minutes")
    suppress(db, db[1], "xab@x.com", at="101 minutes")
    suppress(db, db[1], "ab@x.com", "complained", at="102 minutes")

    async def calls(browser):
        page = (await browser.get(LIST)).json()
        more = (await browser.get(LIST, params={"cursor": page["next_cursor"]})).json()
        prefix = (await browser.get(LIST, params={"q": " AB "})).json()
        escaped = (await browser.get(LIST, params={"q": "a_"})).json()
        bad = await browser.get(LIST, params={"cursor": "garbled"})
        regrant(db, ["rfq.view"])
        return page, more, prefix, escaped, bad, await browser.get(LIST)

    page, more, prefix, escaped, bad, denied = run(db, calls)
    assert len(page["rows"]) == 50 and page["next_cursor"]
    assert page["rows"][0]["email"] == "u00@x.com"
    assert [r["email"] for r in more["rows"]][:1] == ["u50@x.com"]
    assert more["next_cursor"] is None and len(more["rows"]) == 4
    assert sorted(r["email"] for r in prefix["rows"]) == ["ab@x.com", "abc@x.com"]
    assert escaped["rows"] == []
    assert bad.status_code == 422 and bad.json()["checks"]["problem"] == "invalid_cursor"
    assert denied.status_code == 403


def test_suppression_list_hides_other_tenants(stack):
    db = stack[0]
    other = db[2]  # the second tenant the fixture seeds
    suppress(db, other, "theirs@x.com")
    suppress(db, db[1], "mine@x.com")

    async def calls(browser):
        return (await browser.get(LIST)).json()

    assert [r["email"] for r in run(db, calls)["rows"]] == ["mine@x.com"]


def test_sending_domain_foreign_tenant_is_concealed(stack):
    """Named for test_route_coverage. The status row is global (no tenant column); the check job
    carries the caller's org_id, and another organization's jobs never mark it pending."""
    db = stack[0]
    with correlation_context("foreign"), tenant_transaction(db[0], Scope(db[2])):
        JobQueue(db[0], Scope(db[2])).enqueue(DOMAIN_CHECK_JOB, {}, max_attempts=1)

    async def read(browser):
        return (await browser.get(BASE)).json()

    assert run(db, read)["check_pending"] is False
