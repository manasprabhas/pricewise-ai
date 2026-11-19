"""Money policy: every stored amount is an integer number of cents.

Rounding policy (explicit, used everywhere):
- Amounts read from receipts are converted with Decimal from their text, then rounded HALF_UP to the cent.
- A computed line amount (weight x price per lb, count x price each) is rounded HALF_UP to the cent, the way
  store registers print it.
- Medians and averages of prices are computed in Decimal and rounded HALF_UP to the cent.
- Sums are exact integer additions. No float is ever summed for money.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from statistics import median as _median

CENT = Decimal("0.01")


def to_decimal(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    if x is None:
        return Decimal(0)
    if isinstance(x, float):
        if x != x:
            raise ValueError("Not a number (NaN) can't be an amount of money.")
        x = repr(float(x))             # 3.48 -> "3.48", never the binary expansion (float() unwraps numpy)
    elif hasattr(x, "item") and not isinstance(x, str):
        x = x.item()                   # numpy int/float scalars
        return to_decimal(x)
    return Decimal(str(x).replace("$", "").replace(",", "").strip() or "0")


def cents(x) -> int:
    """Dollars (str, float, Decimal) -> integer cents, HALF_UP."""
    return int((to_decimal(x).quantize(CENT, rounding=ROUND_HALF_UP) * 100).to_integral_value())


def dollars(c: int | None) -> float | None:
    """Integer cents -> float dollars, only for display or legacy columns (always exact to the cent)."""
    return None if c is None else round(c / 100, 2)


def line_cents(qty, unit_price) -> int:
    """qty x price, rounded HALF_UP to the cent like a register: 2.31 lb x $0.58 = $1.3398 -> 134."""
    return cents(to_decimal(qty) * to_decimal(unit_price))


def median_cents(values: list[int]) -> int:
    """Median of cent amounts; an even count averages the middle two and rounds HALF_UP."""
    m = Decimal(str(_median(values)))
    return int(m.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fmt(c: int | None, signed: bool = False) -> str:
    if c is None:
        return "—"
    sign = "−" if c < 0 else ("+" if signed and c > 0 else "")
    return f"{sign}${abs(c) / 100:,.2f}"


def packages_needed(amount: float, package_size: float) -> int:
    """Whole packages needed to cover `amount` with packages of `package_size` (same base unit).
    1e-9 tolerance so 3 x 0.333...3 doesn't become 4 packages."""
    import math
    if package_size <= 0:
        raise ValueError("Package size must be positive")
    if amount <= 0:
        return 0
    return math.ceil(amount / package_size - 1e-9)
