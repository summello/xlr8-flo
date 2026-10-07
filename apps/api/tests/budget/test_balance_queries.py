"""BUD-009/011, ACC-003: fiscal totals, reconciliation and bounded drill-down."""

import asyncio
from datetime import date
from decimal import Decimal

import httpx
import pytest

from flo.kernel.tenancy.context import Scope
from flo.modules.budget.models import LedgerType
from flo.modules.budget.service import balance_query
from flo.modules.identity.models import AuthorizationTarget, ScopeType
from tests.authz.conftest import authorization_database as authorization_database
from tests.authz.test_effective_access import grant_permissions
from tests.authz.test_resolver import insert_identity, service_for
from tests.budget.test_allocation import allocation_db as allocation_db
from tests.budget.test_allocation import app, project
from tests.budget.test_post_entry import post
from tests.budget.test_post_entry import posting_db as posting_db
from tests.isolation.conftest import tenant_database as tenant_database
from tests.org.conftest import correlation as correlation
from tests.org.conftest import org_database as org_database
from tests.projects.conftest import body, service, unit
from tests.projects.conftest import project_db as project_db


@pytest.fixture
def query_db(allocation_db):
    grant_permissions(allocation_db, allocation_db.actor_id, "ledger.read")
    return allocation_db


def get(db, row, path="balance", params=None, viewer=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app(db, viewer)), base_url="https://testserver"
        ) as client:
            return await client.get(f"/api/v1/projects/{row.id}/{path}", params=params or {})

    return asyncio.run(send())


def seed(db, row):
    db.connection.execute(
        "INSERT INTO fiscal_calendar(org_id, start_month) VALUES (%s, 4)", (db.org_a,)
    )
    for day, kind, amount in [
        ("2025-04-01", "allocation", "100"),
        ("2026-03-31", "reservation", "10"),
        ("2026-04-01", "allocation", "200"),
        ("2026-05-10", "commitment", "20"),
        ("2026-07-01", "reservation", "30"),
        ("2026-08-01", "actual", "40"),
        ("2026-08-20", "adjustment", "50"),
        ("2027-03-31", "allocation", "70"),
    ]:
        post(
            db,
            row,
            effective_date=date.fromisoformat(day),
            entry_type=LedgerType(kind),
            amount=Decimal(amount),
            reason="Golden table posting",
        )


@pytest.mark.parametrize(
    "period,expected,start,end",
    [
        ("mtd", (50, 0, 0, 40), "2026-08-01", "2026-08-26"),
        ("qtd", (50, 30, 0, 40), "2026-07-01", "2026-08-26"),
        ("ytd", (250, 30, 20, 40), "2026-04-01", "2026-08-26"),
        ("fiscal_year", (320, 30, 20, 40), "2026-04-01", "2027-03-31"),
        ("life", (350, 40, 20, 40), None, "2026-08-26"),
        ("range", (200, 30, 20, 0), "2026-04-01", "2026-07-31"),
    ],
)
def test_fiscal_golden_table(query_db, period, expected, start, end):
    db = query_db
    row = project(db)
    seed(db, row)
    params = {"period": period, "as_of": "2026-08-26"}
    if period == "range":
        params.update({"from": start, "to": end})
    result = get(db, row, params=params)
    assert result.status_code == 200, result.text
    data = result.json()
    assert tuple(data[key] for key in ("allocated", "reserved", "committed", "actual")) == tuple(
        f"{value}.0000" for value in expected
    )
    assert data["available"] == data["variance"] == f"{expected[0] - sum(expected[1:])}.0000"
    assert data["range"] == {"from": start, "to": end}
    assert data["reconciles"] is None


def test_current_life_reconciles_and_planted_drift_fails(query_db):
    db = query_db
    row = project(db)
    seed(db, row)
    data = get(db, row, "balance/reconcile").json()
    assert data["balance"]["allocated"] == "420.0000"
    assert data["balance"]["consumption_pct"] == "0.24"
    assert data["balance"]["reconciles"] is True
    assert set(data["difference_by_bucket"].values()) == {"0.0000"}
    db.connection.execute(
        "INSERT INTO ledger_entry(org_id,bu_id,project_id,entry_type,bucket,amount,currency,"
        "source_type,department_code,ledger_account_code,effective_date,actor_id) "
        "SELECT org_id,bu_id,project_id,'allocation','allocated',7,currency,"
        "'system',department_code,ledger_account_code,effective_date,actor_id "
        "FROM ledger_entry ORDER BY id LIMIT 1"
    )
    drift = get(db, row, "balance/reconcile").json()
    assert drift["difference_by_bucket"]["allocated"] == "-7.0000"
    assert drift["balance"]["reconciles"] is False
    assert get(db, row).json()["reconciles"] is False
    assert drift["entries_total_by_bucket"]["allocated"] == "427.0000"


def test_zero_allocation_and_half_up_ratio(query_db):
    db = query_db
    row = project(db)
    assert get(db, row).json()["consumption_pct"] is None
    post(db, row, amount=Decimal("200"))
    post(db, row, entry_type=LedgerType.RESERVATION, amount=Decimal("1"))
    assert get(db, row).json()["consumption_pct"] == "0.01"


@pytest.mark.parametrize(
    "params",
    [
        {"period": "range", "from": "2026-01-02", "to": "2026-01-01"},
        {"period": "range", "from": "2000-01-01", "to": "2021-01-01"},
        {"period": "range"},
        {"period": "invalid"},
        {"as_of": "invalid"},
    ],
)
def test_bad_period_range_rejected(query_db, params):
    result = get(query_db, project(query_db), params=params)
    assert result.status_code == 422, result.text


@pytest.mark.parametrize(
    "params", [{"period": "mtd"}, {"as_of": "2026-08-26"}, {"period": "range"}]
)
def test_reconciliation_guard(query_db, params):
    result = get(query_db, project(query_db), "balance/reconcile", params)
    assert result.status_code == 422
    assert result.json()["checks"]["problem"] == "period_not_reconcilable"


def test_ledger_pages_2000_entries_without_skips(query_db):
    db = query_db
    row = project(db)
    first = post(db, row)
    db.connection.execute(
        "INSERT INTO ledger_entry(org_id,bu_id,project_id,entry_type,bucket,amount,currency,"
        "source_type,department_code,ledger_account_code,effective_date,actor_id,reason) "
        "SELECT org_id,bu_id,project_id,entry_type,bucket,amount,currency,"
        "source_type,department_code,ledger_account_code,"
        "effective_date + (n %% 3),actor_id,'page test' FROM ledger_entry "
        "CROSS JOIN generate_series(1,1999) n WHERE id=%s",
        (first.id,),
    )
    expected = [
        row[0]
        for row in db.connection.execute(
            "SELECT id FROM ledger_entry ORDER BY effective_date,id"
        ).fetchall()
    ]
    ids, cursor = [], None
    while True:
        result = get(db, row, "ledger", {"page_size": 50, **({"cursor": cursor} if cursor else {})})
        assert result.status_code == 200, result.text
        data = result.json()
        assert len(data["entries"]) == 50
        ids.extend(entry["id"] for entry in data["entries"])
        cursor = data["next_cursor"]
        if cursor is None:
            break
    assert ids == expected
    assert len(set(ids)) == 2000
    assert {
        "source_type",
        "source_id",
        "reverses_entry_id",
        "releases_entry_id",
        "actor_id",
        "reason",
    } <= data["entries"][0].keys()
    filtered = get(db, row, "ledger", {"bucket": "reserved"}).json()
    assert filtered["entries"] == []
    filtered = get(
        db, row, "ledger", {"from": "2026-08-27", "to": "2026-08-27", "entry_type": "allocation"}
    ).json()
    assert all(entry["effective_date"] == "2026-08-27" for entry in filtered["entries"])


@pytest.mark.parametrize(
    "params",
    [
        {"page_size": 51},
        {"page_size": 0},
        {"cursor": "bad"},
        {"cursor": "2026-08-26:0"},
        {"cursor": "2026-08-26:9223372036854775808"},
        {"bucket": "bad"},
        {"entry_type": "bad"},
        {"from": "2026-08-27", "to": "2026-08-26"},
    ],
)
def test_ledger_guards(query_db, params):
    assert get(query_db, project(query_db), "ledger", params).status_code == 422


def test_foreign_balance(query_db):
    db = query_db
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    assert get(db, foreign).status_code == 404


def test_foreign_reconcile(query_db):
    db = query_db
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    assert get(db, foreign, "balance/reconcile").status_code == 404


def test_foreign_ledger(query_db):
    db = query_db
    foreign = service(db, db.org_b).create(body(unit(db, org=db.org_b)))
    assert get(db, foreign, "ledger").status_code == 404


@pytest.mark.parametrize("path", ["balance", "balance/reconcile", "ledger"])
def test_read_authorization_guards(query_db, path):
    db = query_db
    row = project(db)
    viewer = insert_identity(db, "ledger-viewer")
    assert get(db, row, path, viewer=viewer).status_code == 404
    with service_for(db, Scope(db.org_a), "read-grant") as identity:
        role = identity.create_role("read", "Read")
        identity.grant_permission(role.id, "project.read")
        identity.grant_role(viewer, role.id, AuthorizationTarget(ScopeType.PROJECT, row.id))
    assert get(db, row, path, viewer=viewer).status_code == 403
    with service_for(db, Scope(db.org_a), "ledger-grant") as identity:
        identity.grant_permission(role.id, "ledger.read")
    assert get(db, row, path, viewer=viewer).status_code == 200


def test_openapi_money_strings(query_db):
    schemas = app(query_db).openapi()["components"]["schemas"]
    balance = schemas["BalanceQueryRead"]["properties"]
    for key in ("allocated", "reserved", "committed", "actual", "available", "variance"):
        assert balance[key]["type"] == "string"
    assert schemas["LedgerEntryRead"]["properties"]["amount"]["type"] == "string"


def test_service_historical_life_does_not_compare_rollup(query_db):
    db = query_db
    row = project(db)
    seed(db, row)
    balance = balance_query(db.connection, Scope(db.org_a), row.id, as_of=date(2026, 3, 31))
    assert balance.allocated == "100.0000"
    assert balance.reconciles is None


def test_read_snapshot_during_uncommitted_post(query_db):
    from flo.kernel.logging import correlation_context
    from flo.modules.budget.service import post_entry
    from tests.budget.test_concurrency import connect
    from tests.budget.test_post_entry import payload

    db = query_db
    row = project(db)
    post(db, row)
    with connect(db) as writer, correlation_context("concurrent-balance-read"):
        with writer.transaction():
            post_entry(writer, Scope(db.org_a), **payload(db, row, amount=Decimal("25")))
            before = get(db, row, "balance/reconcile").json()
            assert before["balance"]["allocated"] == "100.0000"
            assert before["balance"]["reconciles"] is True
        after = get(db, row, "balance/reconcile").json()
        assert after["balance"]["allocated"] == "125.0000"
        assert after["balance"]["reconciles"] is True


@pytest.mark.parametrize(
    "start,end",
    [
        ("2000-02-29", "2020-02-29"),
        ("2026-01-01", "2046-01-01"),
    ],
)
def test_twenty_year_boundary_allowed(query_db, start, end):
    result = get(query_db, project(query_db), params={"period": "range", "from": start, "to": end})
    assert result.status_code == 200, result.text
    assert result.json()["range"] == {"from": start, "to": end}


def test_mixed_sequence_and_lineage_drilldown(query_db):
    from flo.modules.budget.service import record_actual
    from tests.budget.test_postings import consume, reverse
    from tests.budget.test_reservation_release import release, reservation

    db = query_db
    row = project(db)
    post(db, row, amount=Decimal("500"))
    held = reservation(db, row, Decimal("100"))
    released = release(db, held, amount=Decimal("20"))
    committed = consume(db, row, amount=Decimal("80"), from_reservation_entry_id=held.id)
    actual = consume(
        db, row, function=record_actual, amount=Decimal("30"), from_commitment_entry_id=committed.id
    )
    reversal = reverse(db, actual)
    result = get(db, row, "balance/reconcile")
    assert result.status_code == 200, result.text
    data = result.json()
    assert set(data["difference_by_bucket"].values()) == {"0.0000"}
    entries = get(db, row, "ledger").json()["entries"]
    assert (
        next(entry for entry in entries if entry["id"] == released.id)["releases_entry_id"]
        == held.id
    )
    assert (
        next(entry for entry in entries if entry["id"] == reversal.id)["reverses_entry_id"]
        == actual.id
    )
    for bucket in ("allocated", "reserved", "committed", "actual"):
        total = sum(
            (Decimal(entry["amount"]) for entry in entries if entry["bucket"] == bucket), Decimal(0)
        )
        assert format(total, ".4f") == data["balance"][bucket]


@pytest.mark.parametrize(
    "allocated,consumed,expected",
    [
        ("0", "0", None),
        ("200", "1", "0.01"),
        ("3", "1", "0.33"),
        ("200", "-1", "-0.01"),
        ("100", "150", "1.50"),
    ],
)
def test_decimal_consumption_ratio(allocated, consumed, expected):
    from flo.modules.budget.queries import consumption_ratio

    assert consumption_ratio(Decimal(allocated), Decimal(consumed)) == expected
