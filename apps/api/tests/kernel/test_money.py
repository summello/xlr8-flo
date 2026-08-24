from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import Integer, create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.schema import CreateTable

from flo.kernel.db.types import CurrencyCode, MoneyAmount, money_composite
from flo.kernel.money import CurrencyMismatch, Money, quantize


class Base(DeclarativeBase):
    pass


class PricedRecord(Base):
    __tablename__ = "e03_s01_money_round_trip"
    __table_args__ = {"prefixes": ["TEMPORARY"]}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    price_amount: Mapped[Decimal] = mapped_column(MoneyAmount(), nullable=False)
    price_currency: Mapped[str] = mapped_column(CurrencyCode(), nullable=False)
    price: Mapped[Money] = money_composite("price_amount", "price_currency")


def test_money_requires_decimal_and_valid_uppercase_currency() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Money(10.50, "USD")  # type: ignore[arg-type]

    assert Money(Decimal("10.50"), "USD") == Money(Decimal("10.50"), "USD")

    with pytest.raises(ValueError, match="uppercase"):
        Money(Decimal("10.50"), "usd")
    with pytest.raises(ValueError, match="unsupported"):
        Money(Decimal("10.50"), "ZZZ")


def test_arithmetic_never_implicitly_mixes_currencies() -> None:
    with pytest.raises(CurrencyMismatch):
        Money(Decimal("1.00"), "USD") + Money(Decimal("1.00"), "EUR")
    with pytest.raises(CurrencyMismatch):
        Money(Decimal("1.00"), "USD") - Money(Decimal("1.00"), "EUR")


def test_decimal_multiplication_is_exact() -> None:
    assert Money(Decimal("0.1"), "USD") * 3 == Money(Decimal("0.3"), "USD")


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        ("2.5", "JPY", "3"),
        ("3.5", "JPY", "4"),
        ("-2.5", "JPY", "-3"),
        ("123.6", "JPY", "124"),
        ("1.2345", "BHD", "1.235"),
        ("1.235", "USD", "1.24"),
    ],
)
def test_quantize_uses_currency_exponent_and_half_up(
    amount: str,
    currency: str,
    expected: str,
) -> None:
    assert quantize(Decimal(amount), currency) == Decimal(expected)


def test_allocate_preserves_the_amount_and_distributes_remainder_earliest() -> None:
    allocations = Money(Decimal("100.00"), "USD").allocate([1, 1, 1])

    assert allocations == [
        Money(Decimal("33.34"), "USD"),
        Money(Decimal("33.33"), "USD"),
        Money(Decimal("33.33"), "USD"),
    ]
    assert sum((part.amount for part in allocations), Decimal(0)) == Decimal("100.00")


def test_document_total_is_sum_of_quantized_lines() -> None:
    raw_lines = [Decimal(1) / Decimal(3) for _ in range(37)]
    printed_lines = [quantize(line, "USD") for line in raw_lines]

    document_total = sum(printed_lines, Decimal(0))

    assert document_total == Decimal("12.21")
    assert document_total != quantize(sum(raw_lines, Decimal(0)), "USD")


def test_money_mapping_generates_numeric_18_4_and_paired_currency_columns() -> None:
    ddl = str(CreateTable(PricedRecord.__table__).compile(dialect=postgresql.dialect()))

    assert "price_amount NUMERIC(18, 4) NOT NULL" in ddl
    assert "price_currency CHAR(3) NOT NULL" in ddl
    assert set(PricedRecord.price.property.attrs) == {"price_amount", "price_currency"}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for the real-Postgres round-trip")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def test_money_round_trip_through_postgres_preserves_scale_and_currency() -> None:
    engine = create_engine(_database_url())
    with engine.connect() as connection, connection.begin():
        PricedRecord.__table__.create(connection)
        with Session(connection) as session:
            record = PricedRecord(price=Money(Decimal("123.4567"), "BHD"))
            session.add(record)
            session.flush()
            session.expire(record)

            loaded = session.scalar(select(PricedRecord).where(PricedRecord.id == record.id))

            assert loaded is not None
            assert loaded.price == Money(Decimal("123.4567"), "BHD")
            assert loaded.price.amount.as_tuple().exponent == -4
