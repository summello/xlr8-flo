"""Requisition expiry: a per-organization setting and a job that releases exactly once (E09-S07)."""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest

from flo.api.health import app
from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.tenancy.context import Scope
from flo.modules.org.schemas import SettingPut
from flo.modules.org.service import OrgService
from tests.requisitions.test_approval_effects import approve, drift, linked
from tests.requisitions.test_approval_effects import state as state
from tests.requisitions.test_header import bootstrap_db as bootstrap_db
from tests.requisitions.test_header import empty_database as empty_database
from tests.requisitions.test_header import header_db as header_db
from tests.requisitions.test_header import project, unit
from tests.requisitions.test_lifecycle import lifecycle_db as lifecycle_db
from tests.requisitions.test_lifecycle import sql
from tests.requisitions.test_lines import lines_db as lines_db
from tests.requisitions.test_submission_check import change

NIL = UUID(int=0)
PATH = "/internal/jobs/requisition-expiry"


def set_days(db, value, unit_id=None):
    OrgService(db[0], Scope(db[1]), db[3]).set_setting(
        "requisition.expiry_days", SettingPut(value=value, unit_id=unit_id)
    )


def approved_aged(db, days, p=None):
    p = p or project(db, unit(db, uuid4().hex))
    row = linked(db, p, price="60")
    approve(db, row)
    change(db, "requisition", row.id, f"decided_at=now() - interval '{days} days'")
    return p, row


def run_job(key=None):
    pair = "cron-csrf-pair-0123456789abcdef"
    headers = {
        "Idempotency-Key": key or uuid4().hex,
        "Cookie": f"flo_csrf={pair}",
        "X-CSRF-Token": pair,
    }

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            return await client.post(PATH, headers=headers)

    return asyncio.run(send())


def snapshot(db):
    tables = [
        r[0]
        for r in db[0]
        .execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
            "AND table_type='BASE TABLE' AND table_name NOT LIKE '%idempotency%' "
            "AND table_name NOT LIKE '%reconcile%' ORDER BY 1"
        )
        .fetchall()
    ]
    return {
        t: db[0].execute(f'SELECT "{t}"::text FROM "{t}" ORDER BY 1').fetchall() for t in tables
    }


def test_expiry_boundary_releases_once_and_abandons_sub_project_as_system(header_db_job):
    db = header_db_job
    set_days(db, 30)
    p, old = approved_aged(db, 31)
    _, edge = approved_aged(db, 30)
    done = run_job()
    assert done.status_code == 200, done.text
    assert done.json()["expired"] == 1 and done.json()["organizations"] >= 1
    assert (
        state(db, old.id).status == "expired"
        and state(db, edge.id).status == "sourcing_in_progress"
    )
    assert sql(
        db,
        "SELECT amount,reason FROM ledger_entry WHERE source_id=%s AND entry_type='release'",
        (old.id,),
    ) == [(Decimal(-60), "expired")]
    key = sql(db, "SELECT idempotency_key FROM ledger_entry WHERE entry_type='release'")
    assert key == [(f"req:{old.id}:release:{state(db, old.id).reservation.funding_round}:expired",)]
    event = sql(
        db,
        "SELECT e.actor_id,e.system_actor,e.reason FROM project_status_event e JOIN project s "
        "ON s.id=e.project_id WHERE s.generated_by_requisition_id=%s",
        (old.id,),
    )
    assert event == [(None, "requisition-expiry", f"Abandoned because {old.number} was expired")]
    assert sql(
        db,
        "SELECT actor_id FROM requisition_status_event "
        "WHERE requisition_id=%s AND to_status='expired'",
        (old.id,),
    ) == [(NIL,)]
    assert sql(
        db,
        "SELECT actor_kind FROM audit_log WHERE target_id=%s AND action='requisition.transition' "
        "ORDER BY id DESC LIMIT 1",
        (old.id,),
    ) == [("job",)]
    assert drift(db) == []


def test_expiry_job_twice_changes_nothing_with_same_or_different_keys(header_db_job):
    db = header_db_job
    set_days(db, 30)
    approved_aged(db, 45)
    assert run_job("same").json()["expired"] == 1
    before = snapshot(db)
    assert run_job("same").json()["expired"] == 0
    assert run_job().json()["expired"] == 0
    assert snapshot(db) == before


def test_unset_setting_never_expires(header_db_job):
    db = header_db_job
    _, row = approved_aged(db, 4000)
    before = snapshot(db)
    assert run_job().json()["expired"] == 0
    assert snapshot(db) == before and state(db, row.id).status == "sourcing_in_progress"


def test_unit_override_beats_organization_value(header_db_job):
    db = header_db_job
    bu = unit(db, uuid4().hex)
    other = unit(db, uuid4().hex)
    set_days(db, 30)
    set_days(db, 10, bu.id)
    _, short = approved_aged(db, 15, project(db, bu))
    _, long = approved_aged(db, 15, project(db, other))
    assert run_job().json()["expired"] == 1
    assert state(db, short.id).status == "expired"
    assert state(db, long.id).status == "sourcing_in_progress"


def test_sourcing_started_requisition_does_not_expire(header_db_job, monkeypatch):
    from flo.modules.requisitions import lifecycle

    db = header_db_job
    set_days(db, 30)
    _, row = approved_aged(db, 40)
    monkeypatch.setattr(lifecycle, "_sourcing_probes", [lambda conn, scope, id: True])
    assert run_job().json()["expired"] == 0
    assert state(db, row.id).status == "sourcing_in_progress"


def test_one_failing_requisition_does_not_stop_the_rest(header_db_job, monkeypatch):
    from flo.modules.requisitions import lifecycle

    db = header_db_job
    set_days(db, 30)
    _, bad = approved_aged(db, 40)
    _, good = approved_aged(db, 41)
    real = lifecycle.release_live

    def failing(conn, scope, row, reason, *, actor_id):
        if row["id"] == bad.id:
            raise RuntimeError("planted failure")
        return real(conn, scope, row, reason, actor_id=actor_id)

    monkeypatch.setattr(lifecycle, "release_live", failing)
    assert run_job().json()["expired"] == 1
    assert state(db, bad.id).status == "sourcing_in_progress"
    assert state(db, good.id).status == "expired"
    assert drift(db) == []


@pytest.mark.parametrize("value", [0, 3651, "30", True, 1.5, [1]])
def test_expiry_setting_rejects_invalid_values(header_db_job, value):
    with pytest.raises(ProblemError) as error:
        set_days(header_db_job, value)
    assert error.value.code == ErrorCode.VALIDATION_FAILED


def test_expiry_setting_accepts_range_and_null_clears(header_db_job):
    db = header_db_job
    for value in (1, 3650):
        set_days(db, value)
    set_days(db, None)
    service = OrgService(db[0], Scope(db[1]), db[3])
    root = service.effective_setting(unit(db, uuid4().hex).id, "requisition.expiry_days")
    assert root.value is None


def test_internal_expiry_rejects_missing_key_and_origin(
    header_db_job,
):
    from flo.api.origin_auth import require_origin_secret

    db = header_db_job
    set_days(db, 30)
    _, row = approved_aged(db, 40)

    async def send(headers):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            return await client.post(PATH, headers=headers)

    pair = "cron-csrf-pair-0123456789abcdef"
    csrf = {"Cookie": f"flo_csrf={pair}", "X-CSRF-Token": pair}
    assert asyncio.run(send(csrf)).status_code == 400
    app.dependency_overrides.pop(require_origin_secret)
    app.middleware_stack = None
    keyed = csrf | {"Idempotency-Key": uuid4().hex, "X-FLO-Origin-Secret": "incorrect"}
    assert asyncio.run(send(keyed)).status_code == 404
    assert state(db, row.id).status == "sourcing_in_progress"


@pytest.fixture
def header_db_job(lifecycle_db, monkeypatch):
    """lifecycle_db plus the origin-secret override the cron route needs."""
    from flo.api.internal import get_internal_settings
    from flo.api.origin_auth import require_origin_secret
    from flo.kernel.config import Settings
    from tests.requisitions.test_header import connection_url

    db, _ = lifecycle_db
    settings = Settings(database_url=connection_url(db[0]))
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[require_origin_secret] = lambda: None
    app.dependency_overrides[get_internal_settings] = lambda: settings
    app.middleware_stack = None
    yield db
    app.dependency_overrides.clear()
    app.dependency_overrides.update(overrides)
    app.middleware_stack = None


def test_expiry_age_is_counted_in_utc_whatever_the_session_timezone(header_db_job):
    from flo.modules.requisitions.service import expire_org

    db = header_db_job
    set_days(db, 30)
    _, row = approved_aged(db, 31)
    # 20:00 UTC on the 31st day back is already the next calendar day in Kolkata.
    change(
        db,
        "requisition",
        row.id,
        "decided_at=date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' "
        "- interval '31 days' + interval '20 hours'",
    )
    db[0].execute("SET TIME ZONE 'Asia/Kolkata'")
    try:
        assert expire_org(db[0], Scope(db[1])) == 1
    finally:
        db[0].execute("RESET TIME ZONE")
    assert state(db, row.id).status == "expired"
