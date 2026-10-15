"""
Display-only formatting for /trades/ (docs/design/auth-and-trades-list.md 5.1, 6.2).
Values arrive already computed and rounded (matching.py, stats.py); nothing here feeds back
into a calculation. Decimal format specs are exact, no float at any step.
"""

from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from django.utils import dateformat, timezone

_TEN_DP = Decimal("0.0000000001")  # the stored precision (NUMERIC(20,10))


def money(value: Decimal) -> str:
    """USD: explicit sign, $, thousands separator, 2 dp. Zero (incl. -0) has no sign."""
    text = f"${abs(value):,.2f}"
    if text == "$0.00":
        return text
    return ("-" if value < 0 else "+") + text


def opened(when) -> str:
    """Design 5.1 column 1: "Sep 26, 2:31 PM" in the active zone, with the year when it is
    not this year."""
    local = timezone.localtime(when)
    this_year = local.year == timezone.localtime().year
    return dateformat.format(local, "M j, g:i A" if this_year else "M j, Y, g:i A")


def opened_with_year(when) -> str:
    """"Mar 2, 2026, 9:30 AM" in the active zone, the year always shown (manual-entry design
    12: EXIT_BEFORE_ENTRY's {entry}, where a wrong-year typo is a likely cause)."""
    return dateformat.format(timezone.localtime(when), "M j, Y, g:i A")


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
    with localcontext(prec=60):  # 20 whole digits + 10 dp is past the default prec 28
        whole, frac = f"{value.quantize(_TEN_DP, rounding=ROUND_HALF_EVEN):,.10f}".split(".")
    frac = frac.rstrip("0").ljust(min_dp, "0")
    return f"{whole}.{frac}" if frac else whole


def price(value: Decimal) -> str:
    return _decimal(value, 2)


def quantity(value: Decimal) -> str:
    return _decimal(value, 0)
