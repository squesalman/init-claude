"""
Stat cards (docs/product/features/import-and-list.md section 3, pnl-and-matching.md sections 2
and 3). Closed trades only, one group per currency, no FX (ADR-0003 section 5). Decimal only.
R is the one implementation for the stat cards, the journal page status line and the form's
stop check (ADR-0007 section 4). Pure: no DB.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

# Reason codes, exactly the domain strings (pnl-and-matching.md section 3).
TRADE_OPEN = "trade_open"
RISK_NOT_POSITIVE = "risk_not_positive"
RISK_CURRENCY_MISMATCH = "risk_currency_mismatch"
STOP_NOT_A_RISK = "stop_not_a_risk"
STOP_PRICE_MULTI_LEG = "stop_price_multi_leg"
NO_RISK_INPUT = "no_risk_input"

_R_UNIT = Decimal("0.0001")
_AVG_R_UNIT = Decimal("0.01")


@dataclass(frozen=True)
class CurrencyStats:
    win_rate: Decimal | None  # percent, 2 dp; None when win_rate_n == 0
    win_rate_n: int  # wins + losses
    breakeven_count: int
    total_pnl: Decimal
    total_n: int  # closed trades, breakeven included
    avg_r: Decimal | None  # 2 dp; None when avg_r_n == 0
    avg_r_n: int  # closed trades with a non-null R
    avg_r_left_out: int  # closed trades with a null R, any reason


def stop_on_loss_side(trade, stop_price) -> bool:
    """Single-entry only: (entry - stop) * side_sign > 0."""
    sign = 1 if trade.direction == "long" else -1
    return (trade.avg_entry_price - stop_price) * sign > 0


def r_multiple(trade, entry) -> tuple[Decimal | None, str | None]:
    """(R at 4 dp HALF_EVEN, None) or (None, reason). Evaluation order per section 3: planned
    risk wins with no fallback to the stop; the stop needs a single-entry trade."""
    if trade.is_open:
        return None, TRADE_OPEN
    planned = entry.planned_risk_amount if entry is not None else None
    stop = entry.stop_price if entry is not None else None
    if planned is not None:
        if planned <= 0:
            return None, RISK_NOT_POSITIVE
        if entry.risk_currency != trade.currency:
            return None, RISK_CURRENCY_MISMATCH
        risk = planned
    elif stop is not None:
        if trade.entry_lot_count > 1:
            return None, STOP_PRICE_MULTI_LEG
        if not stop_on_loss_side(trade, stop):
            return None, STOP_NOT_A_RISK
        risk = abs(trade.avg_entry_price - stop) * trade.quantity * trade.multiplier
    else:
        return None, NO_RISK_INPUT
    return (trade.net_pnl / risk).quantize(_R_UNIT, rounding=ROUND_HALF_EVEN), None


def compute_stats(trades, entries_by_opening_id) -> dict[str, CurrencyStats]:
    closed_by_currency = defaultdict(list)
    for trade in trades:
        if not trade.is_open:
            r, _ = r_multiple(trade, entries_by_opening_id.get(trade.opening_execution_id))
            closed_by_currency[trade.currency].append((trade.net_pnl, r))
    return {currency: _stats(rows) for currency, rows in closed_by_currency.items()}


def _stats(rows: list[tuple[Decimal, Decimal | None]]) -> CurrencyStats:
    nets = [net for net, _ in rows]
    rs = [r for _, r in rows if r is not None]
    # BREAKEVEN_ELIGIBLE_EXCLUDED (section 2): net_pnl == 0 exactly is neither win nor loss.
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    decided = wins + losses
    win_rate = (
        (Decimal(wins) * 100 / decided).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        if decided
        else None
    )
    # Mean of the stored 4 dp values, quantized once (section 3, vector 23).
    avg_r = (sum(rs) / len(rs)).quantize(_AVG_R_UNIT, rounding=ROUND_HALF_EVEN) if rs else None
    return CurrencyStats(
        win_rate=win_rate,
        win_rate_n=decided,
        breakeven_count=len(nets) - decided,
        total_pnl=sum(nets, Decimal("0.00")),
        total_n=len(nets),
        avg_r=avg_r,
        avg_r_n=len(rs),
        avg_r_left_out=len(nets) - len(rs),
    )
