"""Global immutable ECB rates; Decimal cross rates and exact business dates."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal, cast
from xml.etree import ElementTree

import psycopg

from flo.kernel.errors import ErrorCode, ProblemError, ProblemFieldError
from flo.kernel.money import currency_exponent, quantize
from flo.modules.org.schemas import FxIngestReport, FxLastRun, FxRateRead, FxStatus

_logger = logging.getLogger(__name__)

MAX_BYTES = 1024 * 1024
SOURCE = "ECB"
CUBE = "{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}Cube"
Fetcher = Callable[[str], bytes]
ErrorClass = Literal["network", "http_status", "too_large", "doctype", "parse", "internal"]


class FeedFailure(ValueError):
    def __init__(self, error_class: ErrorClass) -> None:
        self.error_class = error_class
        super().__init__(error_class)


class FxRateMissing(ProblemError):
    def __init__(self, base: str, quote: str, on_date: date) -> None:
        super().__init__(
            ErrorCode.VALIDATION_FAILED,
            detail=(
                f"No ECB rate for {base} to {quote} on {on_date}. "
                "Rates exist for business days after the first ingest. Choose a published "
                "date, or ask an administrator to check the FX feed status."
            ),
            checks={"problem": "fx_rate_missing"},
        )


def validate_currency(currency: str, field: str) -> None:
    try:
        currency_exponent(currency)
    except ValueError:
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED,
            detail="Unknown currency. Choose a bundled ISO 4217 currency code.",
            errors=(ProblemFieldError(field=field, message="Unknown currency code."),),
            checks={"problem": "unknown_currency"},
        ) from None


def assert_single_currency(*currencies: str) -> None:
    if len(set(currencies)) > 1:
        raise ProblemError(
            ErrorCode.VALIDATION_FAILED,
            detail=(
                "Currencies differ. Record an explicit exchange-rate policy "
                "before mixing currencies."
            ),
            checks={"problem": "currency_mismatch"},
        )


@dataclass(frozen=True)
class Conversion:
    amount: Decimal
    rate: Decimal
    source: str
    effective_date: date
    from_ccy: str
    to_ccy: str

    def as_record(self) -> dict[str, str]:
        return {
            "rate": str(self.rate),
            "source": self.source,
            "effective_date": self.effective_date.isoformat(),
            "from": self.from_ccy,
            "to": self.to_ccy,
        }


def parse(payload: bytes) -> tuple[date, list[tuple[str, Decimal]]]:
    if len(payload) > MAX_BYTES:
        raise FeedFailure("too_large")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise FeedFailure("parse") from None
    # BOM-less UTF-16/32 ASCII can decode as UTF-8 but contains forbidden XML NULs.
    if "\x00" in text:
        raise FeedFailure("parse")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise FeedFailure("doctype")
    try:
        document = ElementTree.fromstring(payload)  # noqa: S314 - declarations rejected above
        days = document.findall(f"{CUBE}/{CUBE}")
        if len(days) != 1:
            raise ValueError
        effective = date.fromisoformat(days[0].attrib["time"])
        rates: list[tuple[str, Decimal]] = []
        for item in days[0]:
            currency = item.attrib["currency"]
            rate = Decimal(item.attrib["rate"])
            if (
                item.tag != CUBE
                or len(currency) != 3
                or not all("A" <= c <= "Z" for c in currency)
                or currency == "EUR"
                or not rate.is_finite()
                or rate <= 0
            ):
                raise ValueError
            stored = rate.quantize(Decimal("0.00000001"))
            if stored <= 0 or stored >= Decimal("10000000000"):
                raise ValueError
            rates.append((currency, stored))
        if not rates or len({c for c, _ in rates}) != len(rates):
            raise ValueError
        return effective, rates
    except (ElementTree.ParseError, ValueError, KeyError, InvalidOperation):
        raise FeedFailure("parse") from None


def utc_now() -> datetime:
    return datetime.now(UTC)


def ingest(
    connection: psycopg.Connection[tuple[object, ...]],
    url: str,
    *,
    fetcher: Fetcher,
    now: Callable[[], datetime] = utc_now,
) -> FxIngestReport:
    started = now()
    count = 0
    failure: ErrorClass | None = None
    try:
        effective, rates = parse(fetcher(url))
        with connection.transaction():
            for currency, rate in rates:
                row = connection.execute(
                    """INSERT INTO fx_rate
                    (base, quote, rate, effective_date, source, fetched_at)
                    VALUES ('EUR', %s, %s, %s, %s, %s)
                    ON CONFLICT (base, quote, effective_date, source) DO NOTHING RETURNING quote""",
                    (currency, rate, effective, SOURCE, started),
                ).fetchone()
                count += int(row is not None)
    except FeedFailure as error:
        failure = error.error_class
    except OSError:
        failure = "network"
    except Exception:
        failure = "internal"
        count = 0
    newest_row = connection.execute("SELECT max(effective_date) FROM fx_rate").fetchone()
    newest = cast(date | None, newest_row[0]) if newest_row else None
    with connection.transaction():
        connection.execute(
            """INSERT INTO fx_ingest_run
            (started_at, finished_at, status, rows_inserted, newest_effective_date, error_class)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (started, now(), "failed" if failure else "ok", count, newest, failure),
        )
    if failure:
        _logger.warning("fx ingest failed error_class=%s", failure)
        raise FeedFailure(failure)
    _logger.info("fx ingest ok rows_inserted=%d newest_effective_date=%s", count, newest)
    return FxIngestReport(status="ok", rows_inserted=count, newest_effective_date=newest)


def rate_for(
    connection: psycopg.Connection[tuple[object, ...]], base: str, quote: str, on_date: date
) -> FxRateRead:
    validate_currency(base, "base")
    validate_currency(quote, "quote")
    if base == quote:
        return FxRateRead(
            base=base, quote=quote, rate="1", source="identity", effective_date=on_date
        )
    effective = (
        on_date - timedelta(days=on_date.weekday() - 4) if on_date.weekday() >= 5 else on_date
    )
    legs: dict[str, Decimal] = {"EUR": Decimal(1)}
    rows = connection.execute(
        """SELECT quote, rate FROM fx_rate
        WHERE base = 'EUR' AND source = %s AND effective_date = %s
        AND quote IN (%s, %s)""",
        (SOURCE, effective, base, quote),
    ).fetchall()
    for currency, rate in rows:
        legs[cast(str, currency)] = cast(Decimal, rate)
    if base not in legs or quote not in legs:
        raise FxRateMissing(base, quote, on_date)
    return FxRateRead(
        base=base,
        quote=quote,
        rate=str(legs[quote] / legs[base]),
        source=SOURCE,
        effective_date=effective,
    )


def convert(
    connection: psycopg.Connection[tuple[object, ...]],
    amount: Decimal,
    from_ccy: str,
    to_ccy: str,
    on_date: date,
) -> Conversion:
    result = rate_for(connection, from_ccy, to_ccy, on_date)
    rate = Decimal(result.rate)
    return Conversion(
        quantize(amount * rate, to_ccy),
        rate,
        result.source,
        result.effective_date,
        from_ccy,
        to_ccy,
    )


def status(connection: psycopg.Connection[tuple[object, ...]]) -> FxStatus:
    row = connection.execute("""SELECT status, finished_at, rows_inserted, error_class
        FROM fx_ingest_run ORDER BY id DESC LIMIT 1""").fetchone()
    last = (
        None
        if row is None
        else FxLastRun.model_validate(
            dict(zip(("status", "finished_at", "rows_inserted", "error_class"), row, strict=True))
        )
    )
    newest = connection.execute("SELECT max(effective_date) FROM fx_rate").fetchone()
    return FxStatus(
        last_run=last, newest_rate_date=cast(date | None, newest[0]) if newest else None
    )
