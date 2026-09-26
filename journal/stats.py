"""
Stat cards (docs/product/features/import-and-list.md section 3, pnl-and-matching.md section 2).
Closed trades only, one group per currency, no FX (ADR-0003 section 5). Decimal only.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal


@dataclass(frozen=True)
class CurrencyStats:
    win_rate: Decimal | None  # percent, 2 dp; None when win_rate_n == 0
    win_rate_n: int  # wins + losses
    breakeven_count: int
    total_pnl: Decimal
    total_n: int  # closed trades, breakeven included
    # Stub until journaling adds R inputs (pnl-and-matching.md section 3).
    avg_r: Decimal | None = None
    avg_r_n: int = 0


def compute_stats(trades) -> dict[str, CurrencyStats]:
    net_by_currency = defaultdict(list)
    for trade in trades:
        if not trade.is_open:
            net_by_currency[trade.currency].append(trade.net_pnl)
    return {currency: _stats(nets) for currency, nets in net_by_currency.items()}


def _stats(nets: list[Decimal]) -> CurrencyStats:
    # BREAKEVEN_ELIGIBLE_EXCLUDED (section 2): net_pnl == 0 exactly is neither win nor loss.
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    decided = wins + losses
    win_rate = (
        (Decimal(wins) * 100 / decided).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        if decided
        else None
    )
    return CurrencyStats(
        win_rate=win_rate,
        win_rate_n=decided,
        breakeven_count=len(nets) - decided,
        total_pnl=sum(nets, Decimal("0.00")),
        total_n=len(nets),
    )
