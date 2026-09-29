"""
Display-only formatting for /trades/ (docs/design/auth-and-trades-list.md 5.1, 6.2).
Values arrive already computed and rounded (matching.py, stats.py); nothing here feeds back
into a calculation. Decimal format specs are exact, no float at any step.
"""

from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal

_TEN_DP = Decimal("0.0000000001")  # the stored precision (NUMERIC(20,10))


def money(value: Decimal) -> str:
    """USD: explicit sign, $, thousands separator, 2 dp. Zero (incl. -0) has no sign."""
    text = f"${abs(value):,.2f}"
    if text == "$0.00":
        return text
    return ("-" if value < 0 else "+") + text


def duration(delta: timedelta) -> str:
    """45s, 12m, 1h 05m, 2d 3h. Floors to the unit shown."""
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes, hours, days = seconds // 60, seconds // 3600, seconds // 86400
    if seconds < 3600:
        return f"{minutes}m"
    if seconds < 86400:
        return f"{hours}h {minutes % 60:02d}m"
    return f"{days}d {hours % 24}h"


def _decimal(value: Decimal, min_dp: int) -> str:
    """Up to the stored 10 dp, thousands separator, trailing zeros trimmed down to min_dp."""
    whole, frac = f"{value.quantize(_TEN_DP, rounding=ROUND_HALF_EVEN):,.10f}".split(".")
    frac = frac.rstrip("0").ljust(min_dp, "0")
    return f"{whole}.{frac}" if frac else whole


def price(value: Decimal) -> str:
    return _decimal(value, 2)


def quantity(value: Decimal) -> str:
    return _decimal(value, 0)
