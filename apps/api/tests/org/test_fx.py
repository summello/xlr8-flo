"""FX-001..004 / FIN-003: real Postgres rates, guards, tenancy and conversion."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError

import httpx
import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from pydantic import ValidationError

from flo.api import internal
from flo.api.fx import router as fx_router
from flo.api.health import app as production_app
from flo.api.internal import get_fx_fetcher, get_internal_settings
from flo.api.org import router as org_router
from flo.api.origin_auth import get_origin_settings
from flo.kernel.config import Settings
from flo.kernel.errors import ProblemError
from flo.kernel.logging import correlation_context
from flo.kernel.tenancy.context import Scope
from flo.modules.org import fx
from flo.modules.org.schemas import BaseCurrencyPut
from flo.modules.org.service import OrgService, assert_single_currency, convert
from tests.authz.conftest import ROOT, load_migration
from tests.authz.test_effective_access import build_app, grant_permissions
from tests.authz.test_resolver import insert_identity

MIGRATION = load_migration(ROOT / "migrations/20261008_0028_fx.py", "fx_test_migration")
PAYLOAD = (Path(__file__).parents[1] / "fixtures/ecb_daily.xml").read_bytes()
FRIDAY = date(2026, 10, 2)
NOW = datetime(2026, 10, 2, 16, 30, tzinfo=UTC)


@pytest.fixture
def fx_db(org_database):
    MIGRATION.upgrade(org_database.connection)
    try:
        yield org_database
    finally:
        MIGRATION.downgrade(org_database.connection)


def ingest(db, payload=PAYLOAD):
    return fx.ingest(
        db.connection, "https://feed.test/rates", fetcher=lambda _: payload, now=lambda: NOW
    )


def snapshot(db):
    return db.connection.execute("SELECT * FROM fx_rate ORDER BY quote").fetchall()


def test_ingest_idempotent_stores_all_rates_and_clock(fx_db):
    db = fx_db
    assert fx.status(db.connection).model_dump() == {"last_run": None, "newest_rate_date": None}
    assert ingest(db).rows_inserted == 5
    before = snapshot(db)
    assert len(before) == 5
    assert all(row[0] == "EUR" and row[-1] == NOW for row in before)
    assert ingest(db).rows_inserted == 0
    assert snapshot(db) == before
    # Reference data is wider than the bundled conversion exponent table.
    assert ingest(db, PAYLOAD.replace(b"CAD", b"ZZZ")).rows_inserted == 1
    status = fx.status(db.connection)
    assert status.newest_rate_date == FRIDAY
    assert status.last_run.rows_inserted == 1


@pytest.mark.parametrize(
    "amount,base,quote,expected",
    [
        ("1000.00", "USD", "GBP", "764.75"),
        ("1000.00", "GBP", "USD", "1307.61"),
        ("1000.00", "EUR", "USD", "1091.20"),
        ("1000.00", "USD", "EUR", "916.42"),
        ("1000.00", "USD", "JPY", "148873"),
        ("1000", "JPY", "USD", "6.72"),
        ("250.55", "CAD", "CHF", "158.71"),
    ],
)
def test_golden_cross_rates_round_once(fx_db, amount, base, quote, expected):
    ingest(fx_db)
    before = snapshot(fx_db)
    result = convert(fx_db.connection, Decimal(amount), base, quote, FRIDAY)
    legs = {
        "EUR": Decimal(1),
        "USD": Decimal("1.0912"),
        "GBP": Decimal("0.8345"),
        "JPY": Decimal("162.45"),
        "CAD": Decimal("1.4871"),
        "CHF": Decimal("0.9420"),
    }
    assert result.amount == Decimal(expected)
    assert result.rate == legs[quote] / legs[base]
    assert result.as_record() == {
        "rate": str(result.rate),
        "source": "ECB",
        "effective_date": "2026-10-02",
        "from": base,
        "to": quote,
    }
    assert snapshot(fx_db) == before


def test_dates_exact_weekend_and_missing_monday(fx_db):
    ingest(fx_db)
    for day in (2, 3, 4):
        assert (
            convert(fx_db.connection, Decimal(1), "USD", "GBP", date(2026, 10, day)).effective_date
            == FRIDAY
        )
    for day in (5, 10):
        with pytest.raises(fx.FxRateMissing) as error:
            convert(fx_db.connection, Decimal(1), "USD", "GBP", date(2026, 10, day))
        assert "Choose a published date" in error.value.detail
    with pytest.raises(fx.FxRateMissing):
        convert(fx_db.connection, Decimal(1), "AUD", "GBP", FRIDAY)
    # Both legs must exist on the same day.
    ingest(
        fx_db,
        PAYLOAD.replace(b"2026-10-02", b"2026-10-05").replace(
            b"<Cube currency='GBP' rate='0.8345'/>", b""
        ),
    )
    with pytest.raises(fx.FxRateMissing):
        convert(fx_db.connection, Decimal(1), "USD", "GBP", date(2026, 10, 5))


def test_identity_unknown_and_mixed_currency(fx_db):
    result = convert(fx_db.connection, Decimal("1.235"), "USD", "USD", FRIDAY)
    assert result.amount == Decimal("1.24")
    assert result.rate == Decimal(1) and result.source == "identity"
    assert result.effective_date == FRIDAY
    with pytest.raises(ProblemError) as error:
        convert(fx_db.connection, Decimal(1), "ZZZ", "USD", FRIDAY)
    assert error.value.checks["problem"] == "unknown_currency"
    assert_single_currency("USD", "USD")
    with pytest.raises(ProblemError) as error:
        assert_single_currency("USD", "GBP")
    assert error.value.checks["problem"] == "currency_mismatch"


@pytest.mark.parametrize("declaration", [b"<!DOCTYPE x>", b"<!eNtItY x 'y'>"])
def test_declarations_rejected_before_parser(fx_db, monkeypatch, declaration):
    parser = MagicMock(side_effect=AssertionError("parser must not run"))
    monkeypatch.setattr(fx.ElementTree, "fromstring", parser)
    with pytest.raises(fx.FeedFailure, match="doctype"):
        ingest(fx_db, declaration + PAYLOAD)
    parser.assert_not_called()
    assert fx.status(fx_db.connection).last_run.error_class == "doctype"
    assert snapshot(fx_db) == []


@pytest.mark.parametrize("value", [b"NaN", b"Infinity", b"0", b"-1", b"no", b"0.000000001"])
def test_malformed_rate_fails_whole_run(fx_db, value):
    with pytest.raises(fx.FeedFailure, match="parse"):
        ingest(fx_db, PAYLOAD.replace(b"1.4871", value))
    assert snapshot(fx_db) == []
    assert fx.status(fx_db.connection).last_run.error_class == "parse"


def test_fetch_https_redirect_timeout_cap_and_classification(monkeypatch):
    with pytest.raises(ValidationError):
        Settings(fx_feed_url="http://feed.test")
    with pytest.raises(fx.FeedFailure, match="network"):
        internal.fetch_fx("http://feed.test")
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.geturl.return_value = "https://feed.test/rates"
    response.read.return_value = PAYLOAD
    opener = MagicMock(return_value=response)
    monkeypatch.setattr(internal.urllib.request, "urlopen", opener)
    assert internal.fetch_fx("https://feed.test/rates") == PAYLOAD
    assert opener.call_args.kwargs == {"timeout": 10}
    response.read.assert_called_with(fx.MAX_BYTES + 1)
    response.read.return_value = b"x" * (fx.MAX_BYTES + 1)
    with pytest.raises(fx.FeedFailure, match="too_large"):
        internal.fetch_fx("https://feed.test/rates")
    response.geturl.return_value = "http://feed.test/rates"
    with pytest.raises(fx.FeedFailure, match="network"):
        internal.fetch_fx("https://feed.test/rates")
    for failure, label in [
        (URLError("offline"), "network"),
        (HTTPError("https://feed.test", 503, "down", {}, None), "http_status"),
    ]:
        opener.side_effect = failure
        with pytest.raises(fx.FeedFailure, match=label):
            internal.fetch_fx("https://feed.test/rates")


def test_oversized_payload_records_failed_run(fx_db):
    with pytest.raises(fx.FeedFailure, match="too_large"):
        ingest(fx_db, b"x" * (fx.MAX_BYTES + 1))
    assert fx.status(fx_db.connection).last_run.error_class == "too_large"
    assert snapshot(fx_db) == []


def assert_append_only(db):
    for query in ("UPDATE fx_rate SET rate = 2", "DELETE FROM fx_rate", "TRUNCATE fx_rate"):
        with pytest.raises(psycopg.Error, match="append.only"):
            with db.connection.transaction():
                db.connection.execute(query)


def test_append_only_and_trigger_removal_plant(fx_db):
    ingest(fx_db)
    assert_append_only(fx_db)
    fx_db.connection.execute("DROP TRIGGER fx_rate_append_only ON fx_rate")
    try:
        with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
            assert_append_only(fx_db)
    finally:
        fx_db.connection.execute("""CREATE TRIGGER fx_rate_append_only BEFORE UPDATE OR DELETE
            ON fx_rate FOR EACH ROW EXECUTE FUNCTION raise_append_only()""")


def test_doctype_guard_removal_plant(monkeypatch):
    source = Path(fx.__file__).read_text()
    start = source.index('    if "<!DOCTYPE"')
    end = source.index("    try:", start)
    namespace = dict(vars(fx))
    exec(compile(source[:start] + source[end:], str(fx.__file__), "exec"), namespace)
    parser = MagicMock(return_value=fx.ElementTree.fromstring(PAYLOAD))
    monkeypatch.setattr(fx.ElementTree, "fromstring", parser)
    namespace["parse"](b"<!DOCTYPE x>" + PAYLOAD)
    with pytest.raises(AssertionError):
        parser.assert_not_called()


ENCODED_ENTITY_XML = (
    '<?xml version="1.0" encoding="UTF-16"?>'
    '<!DOCTYPE x [<!ENTITY e "expanded">]><x>&e;</x>'
)


def assert_encoding_rejected(parse, parser, payload):
    with pytest.raises(fx.FeedFailure, match="parse"):
        parse(payload)
    parser.assert_not_called()


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32"])
def test_non_utf8_declarations_rejected_before_parser(monkeypatch, encoding):
    parser = MagicMock(side_effect=AssertionError("parser must not run"))
    monkeypatch.setattr(fx.ElementTree, "fromstring", parser)
    assert_encoding_rejected(fx.parse, parser, ENCODED_ENTITY_XML.encode(encoding))


@pytest.mark.parametrize("payload", [b"\xff", b"\xfe\xff", b"\x00\x00\xfe\xff"])
def test_undecodable_payload_rejected_before_parser(monkeypatch, payload):
    parser = MagicMock(side_effect=AssertionError("parser must not run"))
    monkeypatch.setattr(fx.ElementTree, "fromstring", parser)
    assert_encoding_rejected(fx.parse, parser, payload)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
def test_utf8_guard_removal_plant(monkeypatch, encoding):
    source = Path(fx.__file__).read_text()
    start = source.index('    try:\n        text = payload.decode')
    end = source.index('    if "<!DOCTYPE"', start)
    namespace = dict(vars(fx))
    # Without strict decoding, the declaration scan sees interleaved NUL bytes.
    mutated = source[:start] + '    text = payload.decode("latin-1")\n' + source[end:]
    exec(compile(mutated, str(fx.__file__), "exec"), namespace)
    parser = MagicMock(return_value=fx.ElementTree.fromstring(PAYLOAD))
    monkeypatch.setattr(fx.ElementTree, "fromstring", parser)
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        assert_encoding_rejected(namespace["parse"], parser, ENCODED_ENTITY_XML.encode(encoding))
    parser.assert_called_once()


def test_latest_prior_lookup_plant_is_caught(fx_db):
    ingest(fx_db)
    source = (
        Path(fx.__file__).read_text().replace("AND effective_date = %s", "AND effective_date <= %s")
    )
    namespace = dict(vars(fx))
    exec(compile(source, str(fx.__file__), "exec"), namespace)
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        with pytest.raises(fx.FxRateMissing):
            namespace["rate_for"](fx_db.connection, "USD", "GBP", date(2026, 10, 5))


def request(db, method, path, body=None, viewer=None, headers=None):
    app = build_app(db, viewer or db.actor_id)
    app.include_router(fx_router)
    app.include_router(org_router)

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            return await client.request(method, path, json=body, headers=headers)

    return asyncio.run(send())


def test_fx_reads_foreign_org_inputs_do_not_change_reference_data(fx_db):
    db = fx_db
    grant_permissions(db, db.actor_id, "fx.read")
    ingest(db)
    for path in ("/api/v1/fx/rates?base=USD&quote=GBP&on=2026-10-02", "/api/v1/fx/status"):
        expected = request(db, "GET", path).json()
        result = request(
            db,
            "GET",
            path + ("&" if "?" in path else "?") + f"org_id={db.org_b}",
            {"org_id": str(db.org_b)},
            headers={"X-Org-ID": str(db.org_b)},
        )
        assert result.status_code == 200
        assert result.json() == expected
    viewer = insert_identity(db, "fx-no-grant")
    for path in ("/api/v1/fx/rates?base=USD&quote=GBP&on=2026-10-02", "/api/v1/fx/status"):
        assert request(db, "GET", path, viewer=viewer).status_code == 403
    denied = insert_identity(db, "fx-wrong-permission")
    grant_permissions(db, denied, "org.unit.read")
    assert request(db, "GET", "/api/v1/fx/status", viewer=denied).status_code == 403


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?base=usd&quote=GBP&on=2026-10-02",
        "?base=USD&quote=GBP&on=invalid",
        "?base=USD&quote=GBP&on=2026-10-05",
        "?base=ZZZ&quote=GBP&on=2026-10-02",
    ],
)
def test_rate_request_validation(fx_db, query):
    grant_permissions(fx_db, fx_db.actor_id, "fx.read")
    ingest(fx_db)
    assert request(fx_db, "GET", "/api/v1/fx/rates" + query).status_code == 422


def test_base_currency_set_repeat_lock_and_foreign_org(fx_db):
    db = fx_db
    grant_permissions(db, db.actor_id, "org.setting.manage")
    path = "/api/v1/org/base-currency"
    assert request(db, "PUT", path, {"currency": "ZZZ"}).status_code == 422
    assert request(db, "PUT", path, {"currency": "EUR", "org_id": str(db.org_b)}).status_code == 422
    assert request(db, "PUT", path, {"currency": "EUR"}).json() == {"currency": "EUR"}
    assert request(db, "PUT", path, {"currency": "EUR"}).status_code == 200
    denied = request(db, "PUT", path, {"currency": "USD"})
    assert (
        denied.status_code == 409 and denied.json()["checks"]["problem"] == "base_currency_locked"
    )
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action='org.base_currency.set'"
    ).fetchone() == (1,)
    # An org B administrator only changes org B, even with org A in the request header/query.
    other = replace(db, org_a=db.org_b, org_b=db.org_a)
    grant_permissions(other, db.actor_id, "org.setting.manage")
    assert (
        request(
            other,
            "PUT",
            path + f"?org_id={db.org_a}",
            {"currency": "GBP"},
            headers={"X-Org-ID": str(db.org_a)},
        ).status_code
        == 200
    )
    assert db.connection.execute(
        "SELECT base_currency FROM organization WHERE id=%s", (db.org_a,)
    ).fetchone() == ("EUR",)
    assert db.connection.execute(
        "SELECT base_currency FROM organization WHERE id=%s", (db.org_b,)
    ).fetchone() == ("GBP",)
    viewer = insert_identity(db, "base-denied")
    assert request(db, "PUT", path, {"currency": "EUR"}, viewer=viewer).status_code == 403


def test_base_currency_concurrent_set_serializes(fx_db):
    db = fx_db

    def set_currency(currency):
        with psycopg.connect(
            make_conninfo(db.connection.info.dsn, password=db.connection.info.password),
            autocommit=True,
        ) as connection:
            with correlation_context("fx-concurrency"):
                try:
                    OrgService(connection, Scope(db.org_a), db.actor_id).set_base_currency(
                        BaseCurrencyPut(currency=currency)
                    )
                    return "ok"
                except ProblemError as error:
                    return error.checks["problem"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(set_currency, ("EUR", "USD")))
    assert sorted(results) == ["base_currency_locked", "ok"]
    assert db.connection.execute(
        "SELECT count(*) FROM audit_log WHERE action='org.base_currency.set'"
    ).fetchone() == (1,)


def test_migration_up_down_preserves_org_data(org_database):
    db = org_database
    before = db.connection.execute("SELECT * FROM organization ORDER BY id").fetchall()
    MIGRATION.upgrade(db.connection)
    assert db.connection.execute("SELECT code FROM permission WHERE code='fx.read'").fetchone() == (
        "fx.read",
    )
    db.connection.execute(
        "INSERT INTO fx_rate (quote, rate, effective_date, fetched_at) VALUES ('AUD', 2, %s, %s)",
        (FRIDAY, NOW),
    )
    assert db.connection.execute("SELECT base, source FROM fx_rate").fetchone() == ("EUR", "ECB")
    ingest(db)
    MIGRATION.downgrade(db.connection)
    assert db.connection.execute(
        "SELECT to_regclass('fx_rate'), to_regclass('fx_ingest_run')"
    ).fetchone() == (None, None)
    assert db.connection.execute("SELECT * FROM organization ORDER BY id").fetchall() == before


@pytest.mark.parametrize("failure_kind", ["network", "internal"])
def test_internal_fx_production_stack_repeats_failures_and_guards(fx_db, failure_kind):
    db = fx_db
    # Test-only origin credential, never a production secret.
    settings = Settings(
        origin_shared_secret="fx-test-origin-credential-at-least-32-bytes",
        database_url=make_conninfo(db.connection.info.dsn, password=db.connection.info.password),
    )
    overrides = dict(production_app.dependency_overrides)
    production_app.dependency_overrides[get_origin_settings] = lambda: settings
    production_app.dependency_overrides[get_internal_settings] = lambda: settings
    production_app.dependency_overrides[get_fx_fetcher] = lambda: lambda _: PAYLOAD
    pair = "cron-csrf-pair-0123456789abcdef"
    cron = {
        "X-FLO-Origin-Secret": settings.origin_shared_secret.get_secret_value(),
        "Idempotency-Key": "fx-ingest-repeat",
        "Cookie": f"flo_csrf={pair}",
        "X-CSRF-Token": pair,
    }

    async def send(path="/internal/jobs/fx-ingest", method="POST", headers=cron):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=production_app), base_url="https://test"
        ) as client:
            return await client.request(method, path, headers=headers)

    try:
        first = asyncio.run(send())
        assert first.status_code == 200, first.text
        assert first.json() == {
            "status": "ok",
            "rows_inserted": 5,
            "newest_effective_date": "2026-10-02",
        }
        before = snapshot(db)
        assert asyncio.run(send()).json()["rows_inserted"] == 0
        assert (
            asyncio.run(send(headers=cron | {"Idempotency-Key": "another-key"})).json()[
                "rows_inserted"
            ]
            == 0
        )
        assert snapshot(db) == before
        assert (
            asyncio.run(
                send(headers={k: v for k, v in cron.items() if k not in {"Cookie", "X-CSRF-Token"}})
            ).status_code
            == 403
        )
        assert (
            asyncio.run(
                send(headers={k: v for k, v in cron.items() if k != "Idempotency-Key"})
            ).status_code
            == 400
        )
        assert (
            asyncio.run(
                send(headers={k: v for k, v in cron.items() if k != "X-FLO-Origin-Secret"})
            ).status_code
            == 404
        )

        def unavailable(_):
            if failure_kind == "internal":
                raise RuntimeError("unexpected fetch failure")
            raise URLError("offline")

        production_app.dependency_overrides[get_fx_fetcher] = lambda: unavailable
        failed = asyncio.run(send())
        assert failed.status_code == 503
        assert failed.json()["checks"]["problem"] == "fx_feed_unavailable"
        assert fx.status(db.connection).last_run.error_class == failure_kind
        assert db.connection.execute(
            "SELECT count(*) FROM fx_ingest_run WHERE status='failed'"
        ).fetchone() == (1,)
        assert snapshot(db) == before
        for path in (
            "/api/v1/fx/rates?base=USD&quote=GBP&on=2026-10-02",
            "/api/v1/fx/status",
            "/api/v1/org/base-currency",
        ):
            assert (
                asyncio.run(send(path, "PUT" if "base-currency" in path else "GET")).status_code
                == 401
            )
    finally:
        production_app.dependency_overrides.clear()
        production_app.dependency_overrides.update(overrides)


def test_internal_failure_catch_removal_plant(fx_db):
    def unavailable(_):
        raise RuntimeError("unexpected fetch failure")

    def assert_recorded(ingest_function):
        before = snapshot(fx_db)
        with pytest.raises(fx.FeedFailure, match="internal"):
            ingest_function(fx_db.connection, "https://feed.test/rates", fetcher=unavailable)
        assert fx_db.connection.execute(
            "SELECT status, rows_inserted, error_class FROM fx_ingest_run"
        ).fetchall() == [("failed", 0, "internal")]
        assert snapshot(fx_db) == before

    assert_recorded(fx.ingest)
    source = Path(fx.__file__).read_text()
    start = source.index("    except Exception:")
    end = source.index("    newest_row", start)
    namespace = dict(vars(fx))
    exec(compile(source[:start] + source[end:], str(fx.__file__), "exec"), namespace)
    with pytest.raises(RuntimeError, match="unexpected fetch failure"):
        assert_recorded(namespace["ingest"])
