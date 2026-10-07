"""Fixed-precision money arithmetic and currency rounding policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from functools import cache
from importlib.resources import files


class CurrencyMismatch(ValueError):
    """Raised when an operation would mix two currencies implicitly."""

    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"cannot operate on {left} and {right}")
        self.left = left
        self.right = right


@cache
def _currency_exponents() -> dict[str, int]:
    resource = files("flo.kernel.data").joinpath("iso4217.json")
    data = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("bundled ISO 4217 data must be an object")
    if not all(
        isinstance(code, str)
        and len(code) == 3
        and code.isupper()
        and isinstance(exponent, int)
        and 0 <= exponent <= 4
        for code, exponent in data.items()
    ):
        raise RuntimeError("bundled ISO 4217 data is invalid")
    return data


def currency_exponent(currency: str) -> int:
    """Return the ISO 4217 minor-unit exponent for an uppercase currency code."""

    if not isinstance(currency, str):
        raise TypeError("currency must be a string")
    if len(currency) != 3 or not currency.isascii() or not currency.isupper():
        raise ValueError("currency must be a three-letter uppercase ISO 4217 code")
    try:
        return _currency_exponents()[currency]
    except KeyError as error:
        raise ValueError(f"unsupported ISO 4217 currency: {currency}") from error


def _require_decimal(amount: object) -> Decimal:
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be Decimal")
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    return amount


def quantize(amount: Decimal, currency: str) -> Decimal:
    """Round an amount to its ISO 4217 exponent using accounting HALF_UP."""

    decimal_amount = _require_decimal(amount)
    quantum = Decimal(1).scaleb(-currency_exponent(currency))
    return decimal_amount.quantize(quantum, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Money:
    """An immutable decimal amount inseparably paired with an ISO 4217 currency."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        _require_decimal(self.amount)
        currency_exponent(self.currency)

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(self.currency, other.currency)

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, qty: Decimal | int) -> Money:
        if isinstance(qty, bool) or not isinstance(qty, (Decimal, int)):
            return NotImplemented
        return Money(self.amount * qty, self.currency)

    def allocate(self, ratios: list[int]) -> list[Money]:
        """Split into minor units, giving indivisible remainder to earliest ratios."""

        if not ratios:
            raise ValueError("at least one allocation ratio is required")
        if any(isinstance(ratio, bool) or not isinstance(ratio, int) for ratio in ratios):
            raise TypeError("allocation ratios must be integers")
        if any(ratio < 0 for ratio in ratios) or not any(ratios):
            raise ValueError("allocation ratios must be non-negative with a positive total")
        if self.amount != quantize(self.amount, self.currency):
            raise ValueError("allocated amount must be expressed in whole currency minor units")

        total_ratio = sum(ratios)
        unit = Decimal(1).scaleb(-currency_exponent(self.currency))
        amounts = [
            (self.amount * ratio / total_ratio).quantize(unit, rounding=ROUND_DOWN)
            for ratio in ratios
        ]
        remainder_units = int((self.amount - sum(amounts, Decimal(0))) / unit)
        adjustment = unit if remainder_units > 0 else -unit
        eligible_indexes = [index for index, ratio in enumerate(ratios) if ratio]
        for index in eligible_indexes[: abs(remainder_units)]:
            amounts[index] += adjustment

        return [Money(amount, self.currency) for amount in amounts]
