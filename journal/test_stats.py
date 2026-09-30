"""
journal/stats.py: docs/domain/pnl-and-matching.md section 2 (vector 5), section 3 (R, vectors
6-23) and docs/product/features/import-and-list.md section 3. Closed trades only, grouped by
currency.
"""

from decimal import Decimal
from itertools import count
from types import SimpleNamespace

import pytest

from journal.stats import compute_stats, r_multiple, stop_on_loss_side

_ids = count(1)


def trade(net, currency="USD", direction="long", entry="50.00", qty="100", multiplier="1",
          lots=1):
    """A derived trade's shape (journal.matching.Trade), only the fields stats reads."""
    return SimpleNamespace(
        opening_execution_id=next(_ids), is_open=net is None,
        net_pnl=None if net is None else Decimal(net), currency=currency, direction=direction,
        avg_entry_price=Decimal(entry), quantity=Decimal(qty), multiplier=Decimal(multiplier),
        entry_lot_count=lots,
    )


def closed(net, currency="USD", **kw):
    return trade(net, currency, **kw)


def open_trade(currency="USD", **kw):
    return trade(None, currency, **kw)


def journal(stop=None, risk=None, currency=None):
    return SimpleNamespace(
        stop_price=None if stop is None else Decimal(stop),
        planned_risk_amount=None if risk is None else Decimal(risk),
        risk_currency=currency,
    )


def stats(trades, entries=None):
    return compute_stats(trades, entries or {})


def test_vector_5_breakeven_excluded_from_win_rate_but_counted_in_total():
    trades = [closed(p) for p in ["50.00", "-30.00", "0.00", "10.00", "-10.00", "0.00", "5.00"]]

    usd = stats(trades)["USD"]

    assert (usd.win_rate, usd.win_rate_n, usd.breakeven_count) == (Decimal("60.00"), 5, 2)
    assert (usd.total_pnl, usd.total_n) == (Decimal("25.00"), 7)


def test_all_breakeven_gives_no_win_rate_and_n_0():
    usd = stats([closed("0.00"), closed("0")])["USD"]

    assert (usd.win_rate, usd.win_rate_n, usd.breakeven_count) == (None, 0, 2)
    assert (usd.total_pnl, usd.total_n) == (Decimal("0.00"), 2)


def test_open_trades_are_excluded_from_every_stat():
    assert stats([open_trade(), open_trade()]) == {}

    usd = stats([open_trade(), closed("-12.50")])["USD"]
    assert (usd.win_rate, usd.win_rate_n) == (Decimal("0.00"), 1)
    assert (usd.total_pnl, usd.total_n) == (Decimal("-12.50"), 1)


def test_no_trades_gives_no_groups():
    assert stats([]) == {}


def test_win_rate_quantized_once_half_even():
    usd = stats([closed("1"), closed("1"), closed("-1")])["USD"]  # 66.666...

    assert usd.win_rate == Decimal("66.67")


def test_grouped_by_currency_without_conversion():
    groups = stats([closed("10.00"), closed("-5.00", "EUR"), closed("2.00")])

    assert (groups["USD"].total_pnl, groups["USD"].total_n) == (Decimal("12.00"), 2)
    assert (groups["EUR"].total_pnl, groups["EUR"].win_rate) == (Decimal("-5.00"), Decimal("0.00"))


# --- R-multiple: section 3, vectors 6-23 --------------------------------------------------------

V6 = dict(net="118.00")  # long 100 @ 50.00, exit 51.20, fees 2.00, USD
V10 = dict(net="69.00", entry="10.0666666667", qty="150", lots=2)  # scale-in, avg entry
V12 = dict(net="39.00", direction="short", entry="50.00", qty="20")
V13 = dict(net="291.96", entry="80.00", qty="2", multiplier="1000")


@pytest.mark.parametrize(
    "shape, entry, expected",
    [
        pytest.param(V6, journal(stop="49.50"), (Decimal("2.3600"), None), id="v6"),
        pytest.param(V6, journal(), (None, "no_risk_input"), id="v7"),
        pytest.param(V6, None, (None, "no_risk_input"), id="v7-no-entry"),
        pytest.param(V6, journal(risk="59.00", currency="USD"), (Decimal("2.0000"), None),
                     id="v8"),
        pytest.param(V6, journal(stop="49.50", risk="59.00", currency="USD"),
                     (Decimal("2.0000"), None), id="v9"),
        pytest.param(V10, journal(stop="9.80"), (None, "stop_price_multi_leg"), id="v10a"),
        pytest.param(V10, journal(risk="30.00", currency="USD"), (Decimal("2.3000"), None),
                     id="v10b"),
        pytest.param(V6, journal(stop="49.50", risk="59.00", currency="EUR"),
                     (None, "risk_currency_mismatch"), id="v11"),
        pytest.param(V12, journal(stop="50.50"), (Decimal("3.9000"), None), id="v12"),
        pytest.param(V13, journal(stop="79.90"), (Decimal("1.4598"), None), id="v13"),
        pytest.param(V13, journal(stop="80.00"), (None, "stop_not_a_risk"), id="v14-equal"),
        pytest.param(V13, journal(stop="80.10"), (None, "stop_not_a_risk"), id="v14-profit"),
        pytest.param(dict(net="-52.00"), journal(stop="49.50"), (Decimal("-1.0400"), None),
                     id="v15"),
        pytest.param(dict(net=None), journal(risk="100.00", currency="USD"),
                     (None, "trade_open"), id="v16"),
        pytest.param(dict(net=None), None, (None, "trade_open"), id="v16-no-entry"),
        pytest.param(dict(net="74.00"), journal(stop="49.50"), (Decimal("1.4800"), None),
                     id="v18"),
        pytest.param(V10, journal(stop="11.00"), (None, "stop_price_multi_leg"), id="v19"),
        pytest.param(V10, journal(stop="11.00", risk="30.00", currency="USD"),
                     (Decimal("2.3000"), None), id="v20"),
        pytest.param({**V6, "currency": "EUR"}, journal(risk="59.00", currency="USD"),
                     (None, "risk_currency_mismatch"), id="v22"),
        # Legacy rows only (the CHECK and the form block them): no fallback to the stop.
        pytest.param(V6, journal(stop="49.50", risk="0", currency="USD"),
                     (None, "risk_not_positive"), id="risk-zero-no-fallback"),
        pytest.param(V6, journal(risk="-5", currency="EUR"), (None, "risk_not_positive"),
                     id="risk-negative-before-mismatch"),
        pytest.param(dict(net="10.00", direction="short", entry="50.00"),
                     journal(stop="49.50"), (None, "stop_not_a_risk"), id="short-stop-below"),
    ],
)
def test_r_multiple_vectors(shape, entry, expected):
    assert r_multiple(trade(**shape), entry) == expected


def test_r_is_quantized_once_to_4dp_half_even():
    risk = journal(risk="1", currency="USD")
    assert r_multiple(closed("1.00"), journal(risk="3", currency="USD"))[0] == Decimal("0.3333")
    assert r_multiple(closed("0.00025"), risk)[0] == Decimal("0.0002")  # tie -> even
    assert r_multiple(closed("0.00035"), risk)[0] == Decimal("0.0004")


@pytest.mark.parametrize(
    "direction, stop, ok",
    [("long", "49.99", True), ("long", "50.00", False), ("long", "50.01", False),
     ("short", "50.01", True), ("short", "50.00", False), ("short", "49.99", False),
     ("long", "-1", True)],  # a negative stop is a valid price (ADR-0007 ruling 1)
)
def test_stop_on_loss_side(direction, stop, ok):
    assert stop_on_loss_side(trade("1", direction=direction), Decimal(stop)) is ok


def test_vector_17_avg_r_mean_of_stored_values():
    trades = [closed("118.00"), closed("5.00"), closed("-52.00"), closed("118.00")]
    entries = {
        trades[0].opening_execution_id: journal(stop="49.50"),  # 2.3600
        trades[2].opening_execution_id: journal(stop="49.50"),  # -1.0400
        trades[3].opening_execution_id: journal(risk="59.00", currency="USD"),  # 2.0000
    }

    usd = stats(trades, entries)["USD"]

    assert (usd.avg_r, usd.avg_r_n, usd.avg_r_left_out) == (Decimal("1.11"), 3, 1)


def _vector_23():
    trades = [closed("20.00"), closed("5.00"), closed("3.00"), closed("-4.00")]
    entries = {t.opening_execution_id: journal(risk="100.00", currency="USD") for t in trades[:2]}
    return trades, entries


def test_vector_23_half_even_tie_and_null_r_never_counted_as_zero():
    usd = stats(*_vector_23())["USD"]

    # 0.1250 -> 0.12 (ROUND_HALF_UP gives 0.13; counting T3/T4 as 0 gives 0.06)
    assert (usd.avg_r, usd.avg_r_n, usd.avg_r_left_out) == (Decimal("0.12"), 2, 2)


def test_vector_23_plus_an_open_trade_with_risk_changes_nothing():
    trades, entries = _vector_23()
    t5 = open_trade()
    entries[t5.opening_execution_id] = journal(risk="100.00", currency="USD")

    usd = stats([*trades, t5], entries)["USD"]

    assert (usd.avg_r, usd.avg_r_n, usd.avg_r_left_out) == (Decimal("0.12"), 2, 2)


def test_avg_r_none_when_n_is_0_and_every_closed_trade_is_left_out():
    usd = stats([closed("10.00"), closed("-3.00")])["USD"]

    assert (usd.avg_r, usd.avg_r_n, usd.avg_r_left_out) == (None, 0, 2)


def test_avg_r_per_currency_group_n_plus_left_out_is_closed_count():
    usd_t, eur_t, eur_u = closed("20.00"), closed("40.00", "EUR"), closed("1.00", "EUR")
    entries = {
        usd_t.opening_execution_id: journal(risk="10", currency="USD"),
        eur_t.opening_execution_id: journal(risk="20", currency="EUR"),
        eur_u.opening_execution_id: journal(risk="20", currency="USD"),  # mismatch: left out
    }

    groups = stats([usd_t, eur_t, eur_u, open_trade("EUR")], entries)

    usd, eur = groups["USD"], groups["EUR"]
    assert (usd.avg_r, usd.avg_r_n, usd.avg_r_left_out) == (Decimal("2.00"), 1, 0)
    assert (eur.avg_r, eur.avg_r_n, eur.avg_r_left_out) == (Decimal("2.00"), 1, 1)
    for g in groups.values():
        assert g.avg_r_n + g.avg_r_left_out == g.total_n
