"""PO line figures: price, tax and freight kept apart, one rounding rule (E13-S02, FIN-009).

Every figure is quantized per line in the PO currency; document totals are sums of rounded lines.
"""

from dataclasses import dataclass
from decimal import Decimal

from flo.kernel.money import Money, quantize


@dataclass(frozen=True)
class Figures:
    price: Decimal
    tax: Decimal
    freight: Decimal

    @property
    def line_total(self) -> Decimal:
        return self.price + self.tax + self.freight

    def budget(self, *, tax_on: bool, freight_on: bool) -> Decimal:
        return self.price + (self.tax if tax_on else 0) + (self.freight if freight_on else 0)


def landed(
    unit_price: Decimal,
    qty: Decimal,
    quoted_tax: Decimal,
    quoted_freight: Decimal,
    quoted_qty: Decimal,
    currency: str,
) -> Figures:
    """The line at `qty` of a quote: tax and freight follow qty / quoted qty, as the award does.

    `line_total` is one quantize of the exact sum (the award's landed line); freight is the
    residual after the quantized price and tax. Where that residual is negative (price and tax
    each rounded up), freight is 0 and tax absorbs the rest, so the total still matches.
    """
    price_exact = unit_price * qty
    tax_exact = quoted_tax * qty / quoted_qty
    total = quantize(price_exact + tax_exact + quoted_freight * qty / quoted_qty, currency)
    price = quantize(price_exact, currency)
    tax = quantize(tax_exact, currency)
    freight = total - price - tax
    if freight < 0:
        tax, freight = total - price, Decimal(0)
    return Figures(price, tax, quantize(freight, currency))


def spread(document_freight: Decimal, exact_prices: list[Decimal], currency: str) -> list[Decimal]:
    """Share the document freight pro rata by exact line price, in whole minor units.

    The remainder goes to the largest lines (ties: the lowest index, i.e. `line_no`); all-zero
    prices split equally. Returned in the input order.
    """
    ratios = [int(p.scaleb(8)) for p in exact_prices]
    if not any(ratios):
        ratios = [1] * len(ratios)
    order = sorted(range(len(ratios)), key=lambda i: (-ratios[i], i))
    parts = Money(quantize(document_freight, currency), currency).allocate(
        [ratios[i] for i in order]
    )
    out = [Decimal(0)] * len(ratios)
    for i, part in zip(order, parts, strict=True):
        out[i] = part.amount
    return out
