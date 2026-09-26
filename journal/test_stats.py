"""
journal/stats.py: docs/domain/pnl-and-matching.md section 2 (vector 5) and
docs/product/features/import-and-list.md section 3. Closed trades only, grouped by currency.
"""

from decimal import Decimal
from types import SimpleNamespace

from journal.stats import compute_stats


def closed(net, currency="USD"):
    return SimpleNamespace(is_open=False, net_pnl=Decimal(net), currency=currency)


def open_trade(currency="USD"):
    return SimpleNamespace(is_open=True, net_pnl=None, currency=currency)


def test_vector_5_breakeven_excluded_from_win_rate_but_counted_in_total():
    trades = [closed(p) for p in ["50.00", "-30.00", "0.00", "10.00", "-10.00", "0.00", "5.00"]]

    usd = compute_stats(trades)["USD"]

    assert (usd.win_rate, usd.win_rate_n, usd.breakeven_count) == (Decimal("60.00"), 5, 2)
    assert (usd.total_pnl, usd.total_n) == (Decimal("25.00"), 7)


def test_all_breakeven_gives_no_win_rate_and_n_0():
    usd = compute_stats([closed("0.00"), closed("0")])["USD"]

    assert (usd.win_rate, usd.win_rate_n, usd.breakeven_count) == (None, 0, 2)
    assert (usd.total_pnl, usd.total_n) == (Decimal("0.00"), 2)


def test_open_trades_are_excluded_from_every_stat():
    assert compute_stats([open_trade(), open_trade()]) == {}

    usd = compute_stats([open_trade(), closed("-12.50")])["USD"]
    assert (usd.win_rate, usd.win_rate_n) == (Decimal("0.00"), 1)
    assert (usd.total_pnl, usd.total_n) == (Decimal("-12.50"), 1)


def test_no_trades_gives_no_groups():
    assert compute_stats([]) == {}


def test_win_rate_quantized_once_half_even():
    usd = compute_stats([closed("1"), closed("1"), closed("-1")])["USD"]  # 66.666...

    assert usd.win_rate == Decimal("66.67")


def test_grouped_by_currency_without_conversion():
    stats = compute_stats([closed("10.00"), closed("-5.00", "EUR"), closed("2.00")])

    assert (stats["USD"].total_pnl, stats["USD"].total_n) == (Decimal("12.00"), 2)
    assert (stats["EUR"].total_pnl, stats["EUR"].win_rate) == (Decimal("-5.00"), Decimal("0.00"))


def test_avg_r_is_a_stub_until_journaling():
    usd = compute_stats([closed("10.00")])["USD"]

    assert (usd.avg_r, usd.avg_r_n) == (None, 0)
