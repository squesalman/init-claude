"""
Executions -> trades, computed on read (ADR-0003 section 5, docs/domain/pnl-and-matching.md
section 1). FIFO per (account label, symbol, broker_trade_id) bucket (ADR-0004 section 3).

Pure over attributes: works on Execution rows or any object with the same fields, no DB.
"""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

# ponytail: every currency rounds to 2 dp (only USD exists today); add an ISO 4217
# minor-unit table when a non-2-dp currency (JPY) is imported.
_MINOR_UNIT = Decimal("0.01")
_PRICE_UNIT = Decimal("0.0000000001")  # Execution.price scale, NUMERIC(20,10)


@dataclass(frozen=True)
class Trade:
    opening_execution_id: int
    account_label: str
    symbol: str
    direction: str  # "long" | "short"
    quantity: Decimal  # total entry quantity
    avg_entry_price: Decimal
    avg_exit_price: Decimal | None
    opened_at: datetime
    closed_at: datetime | None
    gross_pnl: Decimal | None  # None while open: open trades have no P&L (section 1)
    fees: Decimal | None
    net_pnl: Decimal | None
    currency: str
    is_open: bool
    execution_ids: tuple


def derive_trades(executions) -> list[Trade]:
    """
    Callers pass executions in broker order (e.g. order_by("executed_at", "id"): id follows
    file order). The sort below is stable, so same-timestamp fills keep that order.
    Returns trades ordered by opened_at.
    """
    buckets = defaultdict(list)
    for execution in sorted(executions, key=lambda e: e.executed_at):
        key = (execution.broker_account_label, execution.symbol, execution.broker_trade_id)
        buckets[key].append(execution)
    trades = [trade for bucket in buckets.values() for trade in _fifo(bucket)]
    return sorted(trades, key=lambda t: t.opened_at)


class _Lot:
    def __init__(self, execution, qty, fee):
        self.price, self.multiplier = execution.price, execution.contract_multiplier
        self.qty, self.fee = qty, fee


class _Draft:
    def __init__(self, execution, sign):
        self.opening, self.sign = execution, sign
        self.execution_ids = []
        self.entry_qty = self.entry_value = self.exit_qty = self.exit_value = Decimal(0)
        self.gross = self.fees = Decimal(0)

    def trade(self, closed_at=None) -> Trade:
        is_open = closed_at is None
        first = self.opening
        return Trade(
            opening_execution_id=first.id,
            account_label=first.broker_account_label,
            symbol=first.symbol,
            direction="long" if self.sign == 1 else "short",
            quantity=self.entry_qty,
            avg_entry_price=_price(self.entry_value / self.entry_qty),
            avg_exit_price=None if is_open else _price(self.exit_value / self.exit_qty),
            opened_at=first.executed_at,
            closed_at=closed_at,
            gross_pnl=None if is_open else _money(self.gross),
            fees=None if is_open else _money(self.fees),
            net_pnl=None if is_open else _money(self.gross - self.fees),  # rounded once
            currency=first.currency,
            is_open=is_open,
            execution_ids=tuple(self.execution_ids),
        )


def _fifo(executions) -> list[Trade]:
    trades, lots, draft = [], deque(), None
    for execution in executions:
        sign = 1 if execution.side == "buy" else -1
        qty, fee = execution.quantity, execution.fees

        if draft is not None and sign != draft.sign:  # close, oldest lot first
            draft.execution_ids.append(execution.id)
            while qty and lots:
                lot = lots[0]
                matched = min(lot.qty, qty)
                lot_fee, exit_fee = _share(lot.fee, lot.qty, matched), _share(fee, qty, matched)
                draft.gross += (execution.price - lot.price) * matched * draft.sign * lot.multiplier
                draft.fees += lot_fee + exit_fee
                draft.exit_qty += matched
                draft.exit_value += execution.price * matched
                lot.qty, lot.fee = lot.qty - matched, lot.fee - lot_fee
                qty, fee = qty - matched, fee - exit_fee
                if not lot.qty:
                    lots.popleft()
            if not lots:
                trades.append(draft.trade(closed_at=execution.executed_at))
                draft = None

        if qty:  # open, scale in, or the leftover of a flip opens the next trade
            if draft is None:
                draft = _Draft(execution, sign)
            draft.execution_ids.append(execution.id)
            lots.append(_Lot(execution, qty, fee))
            draft.entry_qty += qty
            draft.entry_value += execution.price * qty

    if draft is not None:
        trades.append(draft.trade())
    return trades


def _share(fee: Decimal, qty: Decimal, matched: Decimal) -> Decimal:
    """Pro-rata fee at full precision; the last unit takes the remainder, so nothing is lost."""
    return fee if matched == qty else fee * matched / qty


def _money(value: Decimal) -> Decimal:
    rounded = value.quantize(_MINOR_UNIT, rounding=ROUND_HALF_EVEN)
    return rounded.copy_abs() if rounded.is_zero() else rounded  # no "-0.00"


def _price(value: Decimal) -> Decimal:
    return value.quantize(_PRICE_UNIT, rounding=ROUND_HALF_EVEN)
