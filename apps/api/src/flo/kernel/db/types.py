"""SQLAlchemy mappings for kernel value objects."""

from decimal import Decimal

from sqlalchemy import CHAR, Numeric
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Composite, composite
from sqlalchemy.types import TypeDecorator

from flo.kernel.money import Money, currency_exponent

MONEY_PRECISION = 18
MONEY_SCALE = 4
_STORAGE_QUANTUM = Decimal("0.0001")


class MoneyAmount(TypeDecorator[Decimal]):
    """Persist monetary amounts as fixed-scale ``NUMERIC(18,4)`` decimals."""

    impl = Numeric(precision=MONEY_PRECISION, scale=MONEY_SCALE, asdecimal=True)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Decimal | None:
        del dialect
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError("stored money amount must be Decimal")
        if not value.is_finite():
            raise ValueError("stored money amount must be finite")
        if value != value.quantize(_STORAGE_QUANTUM):
            raise ValueError("stored money amount cannot exceed four decimal places")
        return value

    def process_result_value(self, value: Decimal | None, dialect: Dialect) -> Decimal | None:
        del dialect
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError("database returned a non-decimal money amount")
        return value


class CurrencyCode(TypeDecorator[str]):
    """Persist validated ISO 4217 currency codes as exactly three characters."""

    impl = CHAR(3)
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        del dialect
        if value is None:
            return None
        currency_exponent(value)
        return value

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        del dialect
        if value is None:
            return None
        currency_exponent(value)
        return value


def money_composite(amount_attribute: str, currency_attribute: str) -> Composite[Money]:
    """Map a ``Money`` value to its inseparable amount and currency columns."""

    return composite(Money, amount_attribute, currency_attribute)
