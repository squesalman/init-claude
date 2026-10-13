"""
journal/matching.py derive_trades(): docs/domain/pnl-and-matching.md section 1 and
section 5 vectors 1-4, plus ADR-0004 section 3 buckets (T4 and its NULL-bucket companion).
Pure: executions are plain objects, no DB.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from journal.matching import derive_trades

T0 = datetime(2026, 1, 1, 14, 30, tzinfo=UTC)


def ex(id, side, qty, price, fee="0", minutes=0, *, trade_id=None, label="", symbol="X",
       multiplier="1", currency="USD", broker="topstep"):
    return SimpleNamespace(
        id=id, side=side, quantity=Decimal(qty), price=Decimal(price), fees=Decimal(fee),
        executed_at=T0 + timedelta(minutes=minutes), broker_trade_id=trade_id,
        broker_account_label=label, symbol=symbol, contract_multiplier=Decimal(multiplier),
        currency=currency, broker=broker,
    )


def test_vector_1_single_entry_single_exit():
    [trade] = derive_trades([
        ex(1, "buy", 10, "100.00", "1.00", 0),
        ex(2, "sell", 10, "105.00", "1.00", 30),
    ])

    assert trade.direction == "long"
    assert trade.quantity == 10
    assert (trade.avg_entry_price, trade.avg_exit_price) == (Decimal("100.00"), Decimal("105.00"))
    assert (trade.gross_pnl, trade.fees, trade.net_pnl) == (
        Decimal("50.00"), Decimal("2.00"), Decimal("48.00"),
    )
    assert not trade.is_open
    assert (trade.opening_execution_id, trade.execution_ids) == (1, (1, 2))
    assert (trade.opened_at, trade.closed_at) == (T0, T0 + timedelta(minutes=30))
    assert (trade.currency, trade.account_label, trade.symbol) == ("USD", "", "X")


def test_vector_2_scale_in_and_two_partial_closes_is_one_trade():
    [trade] = derive_trades([
        ex(1, "buy", 100, "10.00", "1.00", 0),
        ex(2, "buy", 50, "10.20", "0.50", 1),
        ex(3, "sell", 80, "10.50", "0.80", 2),
        ex(4, "sell", 70, "10.60", "0.70", 3),
    ])

    assert (trade.direction, trade.quantity, trade.is_open) == ("long", 150, False)
    assert trade.net_pnl == Decimal("69.00")
    assert trade.gross_pnl == Decimal("72.00")
    assert trade.fees == Decimal("3.00")
    assert trade.execution_ids == (1, 2, 3, 4)


def test_vector_3_flip_in_one_execution_makes_two_trades():
    closed, opened = derive_trades([
        ex(1, "buy", 10, "100.00", "0.00", 0),
        ex(2, "sell", 15, "105.00", "0.00", 1),
    ])

    assert (closed.direction, closed.quantity) == ("long", 10)
    assert (closed.avg_entry_price, closed.avg_exit_price) == (Decimal("100.00"), Decimal("105.00"))
    assert closed.net_pnl == Decimal("50.00")
    assert closed.closed_at == T0 + timedelta(minutes=1)

    assert (opened.direction, opened.quantity, opened.is_open) == ("short", 5, True)
    assert opened.avg_entry_price == Decimal("105.00")
    assert opened.avg_exit_price is None and opened.net_pnl is None
    assert opened.opened_at == T0 + timedelta(minutes=1)
    assert opened.opening_execution_id == 2


def test_flip_splits_the_flipping_fill_fee_pro_rata():
    closed, opened = derive_trades([
        ex(1, "buy", 10, "100.00", "0.00", 0),
        ex(2, "sell", 15, "105.00", "1.50", 1),
        ex(3, "buy", 5, "104.00", "0.50", 2),
    ])

    assert closed.fees == Decimal("1.00")  # 10 of the 15 units
    assert opened.fees == Decimal("1.00")  # 0.50 left on the lot + 0.50 closing
    assert opened.net_pnl == Decimal("4.00")  # (105 - 104) * 5 - 1.00


def test_vector_4_open_trade_has_no_exit_and_no_net():
    [trade] = derive_trades([ex(1, "buy", 5, "20.00", "0.50", 0)])

    assert (trade.direction, trade.quantity, trade.is_open) == ("long", 5, True)
    assert trade.avg_exit_price is None and trade.closed_at is None
    assert trade.net_pnl is None and trade.gross_pnl is None


def test_partially_closed_trade_is_still_open():
    [trade] = derive_trades([ex(1, "buy", 5, "20.00", "0", 0), ex(2, "sell", 2, "21.00", "0", 1)])

    assert trade.is_open and trade.net_pnl is None
    assert trade.execution_ids == (1, 2)


def _t4_executions(trade_ids):
    inner, outer = trade_ids
    return [
        ex(1, "buy", 1, "80.20", "0", 10, trade_id=inner, symbol="CLZ6", multiplier="1000"),
        ex(2, "sell", 1, "80.10", "3.00", 20, trade_id=inner, symbol="CLZ6", multiplier="1000"),
        ex(3, "buy", 1, "80.00", "0", 0, trade_id=outer, symbol="CLZ6", multiplier="1000"),
        ex(4, "sell", 1, "80.50", "3.00", 30, trade_id=outer, symbol="CLZ6", multiplier="1000"),
    ]


def test_t4_broker_trade_id_keeps_nested_rows_as_two_trades():
    trades = derive_trades(_t4_executions(("9000000010", "9000000011")))

    assert len(trades) == 2
    outer, inner = trades  # ordered by opened_at
    assert outer.opening_execution_id == 3
    assert (outer.gross_pnl, outer.fees, outer.net_pnl) == (
        Decimal("500.00"), Decimal("3.00"), Decimal("497.00"),
    )
    assert inner.opening_execution_id == 1
    assert (inner.gross_pnl, inner.fees, inner.net_pnl) == (
        Decimal("-100.00"), Decimal("3.00"), Decimal("-103.00"),
    )


def test_t4_companion_null_broker_trade_id_merges_into_one_fifo_trade():
    [trade] = derive_trades(_t4_executions((None, None)))

    assert (trade.quantity, trade.opening_execution_id) == (2, 3)
    assert trade.gross_pnl == Decimal("400.00")
    assert trade.net_pnl == Decimal("394.00")


def test_buckets_split_by_account_label_and_symbol():
    trades = derive_trades([
        ex(1, "buy", 1, "10", minutes=0, label="A"),
        ex(2, "sell", 1, "11", minutes=1, label="B"),
        ex(3, "buy", 1, "10", minutes=2, symbol="Y"),
    ])

    assert [(t.account_label, t.symbol, t.is_open) for t in trades] == [
        ("A", "X", True), ("B", "X", True), ("", "Y", True),
    ]


def test_same_timestamp_fills_keep_input_order():
    # Input order is the broker's order; sorting by time must not reorder ties.
    [trade] = derive_trades([ex(1, "buy", 1, "10", minutes=0), ex(2, "sell", 1, "11", minutes=0)])

    assert (trade.direction, trade.net_pnl) == ("long", Decimal("1.00"))


def test_net_pnl_rounds_once_half_even_to_cents():
    # Fee 0.01 split in three: never rounded per lot, only the trade total.
    trades = derive_trades([
        ex(1, "buy", 3, "10", "0.01", 0),
        ex(2, "sell", 1, "10.005", "0", 1),
        ex(3, "sell", 2, "10", "0", 2),
    ])

    [trade] = trades
    assert trade.gross_pnl == Decimal("0.00")  # 0.005 -> half-even to 0.00
    assert trade.fees == Decimal("0.01")
    assert trade.net_pnl == Decimal("0.00")  # 0.005 - 0.01 = -0.005 -> -0.00


# --- ADR-0007 section 3: entry_lot_count and multiplier (domain section 3 Terms) -----------


def test_entry_lot_count_is_1_for_single_entry_vectors_1_and_18():
    [v1] = derive_trades(
        [ex(1, "buy", 10, "100.00", "1.00", 0), ex(2, "sell", 10, "105.00", "1.00", 1)]
    )
    [v18] = derive_trades([  # one entry lot, two partial exits
        ex(1, "buy", 100, "50.00", "2.00", 0),
        ex(2, "sell", 40, "51.00", "0", 1),
        ex(3, "sell", 60, "50.60", "0", 2),
    ])

    assert (v1.entry_lot_count, v18.entry_lot_count) == (1, 1)
    assert v18.avg_entry_price == Decimal("50.00")


def test_entry_lot_count_is_2_for_the_vector_2_scale_in():
    [trade] = derive_trades([
        ex(1, "buy", 100, "10.00", "1.00", 0),
        ex(2, "buy", 50, "10.20", "0.50", 1),
        ex(3, "sell", 80, "10.50", "0.80", 2),
        ex(4, "sell", 70, "10.60", "0.70", 3),
    ])

    assert trade.entry_lot_count == 2


def test_vector_3_flip_leftover_is_one_lot_of_the_new_trade():
    closed, opened = derive_trades(
        [ex(1, "buy", 10, "100.00"), ex(2, "sell", 15, "105.00", minutes=1)]
    )

    assert (closed.entry_lot_count, opened.entry_lot_count) == (1, 1)


def test_multiplier_comes_from_the_opening_execution():
    [trade] = derive_trades([
        ex(1, "buy", 2, "80.00", multiplier="1000"),
        ex(2, "sell", 2, "80.15", minutes=1, multiplier="1000"),
    ])

    assert trade.multiplier == Decimal("1000")


# --- ADR-0008 section 1: broker joins the bucket key; manual vectors 30, 32, 33 -----------------

K_A, K_B = "manual:" + "a" * 32, "manual:" + "b" * 32


def test_same_key_string_on_two_brokers_never_pairs():
    # ADR-0008 option B's threat: a hand-crafted CSV whose Id equals a manual key.
    trades = derive_trades([
        ex(1, "buy", 1, "100.00", minutes=0, trade_id=K_A, broker="manual"),
        ex(2, "sell", 1, "105.00", minutes=1, trade_id=K_A, broker="topstep"),
    ])

    assert [(t.opening_execution_id, t.is_open, t.direction) for t in trades] == [
        (1, True, "long"), (2, True, "short"),
    ]


def test_null_keys_share_one_bucket_per_broker_only():
    trades = derive_trades([
        ex(1, "buy", 1, "100.00", minutes=0, broker="manual"),
        ex(2, "sell", 1, "105.00", minutes=1, broker="other"),
    ])

    assert len(trades) == 2 and all(t.is_open for t in trades)


def test_vector_30_two_open_manual_longs_with_own_keys_stay_two_trades():
    fills = [
        ex(1, "buy", 10, "100.00", minutes=0, trade_id=K_A, symbol="AAPL", broker="manual"),
        ex(2, "buy", 5, "101.00", minutes=30, trade_id=K_B, symbol="AAPL", broker="manual"),
    ]
    a, b = derive_trades(fills)
    assert (a.opening_execution_id, a.is_open, a.quantity, a.entry_lot_count) == (1, True, 10, 1)
    assert (b.opening_execution_id, b.is_open, b.quantity, b.entry_lot_count) == (2, True, 5, 1)

    fills.append(ex(3, "sell", 5, "103.00", "1.00", 90, trade_id=K_B, symbol="AAPL",
                    broker="manual"))
    a, b = derive_trades(fills)
    assert (a.opening_execution_id, a.is_open, a.quantity) == (1, True, 10)
    assert (b.opening_execution_id, b.is_open) == (2, False)
    assert (b.gross_pnl, b.fees, b.net_pnl) == (Decimal("10.00"), Decimal("1.00"), Decimal("9.00"))


def test_vector_30_companion_null_keys_would_merge_the_two_trades():
    [trade] = derive_trades([
        ex(1, "buy", 10, "100.00", minutes=0, symbol="AAPL", broker="manual"),
        ex(2, "buy", 5, "101.00", minutes=30, symbol="AAPL", broker="manual"),
        ex(3, "sell", 5, "103.00", "1.00", 90, symbol="AAPL", broker="manual"),
    ])

    assert (trade.is_open, trade.quantity, trade.entry_lot_count, trade.net_pnl) == (
        True, 15, 2, None,
    )


def test_vector_32a_equal_times_keep_the_entry_as_opener():
    [trade] = derive_trades([
        ex(1, "buy", 10, "100.00", minutes=0, trade_id=K_A, broker="manual"),
        ex(2, "sell", 10, "100.50", minutes=0, trade_id=K_A, broker="manual"),
    ])

    assert (trade.direction, trade.opening_execution_id, trade.gross_pnl, trade.is_open) == (
        "long", 1, Decimal("5.00"), False,
    )


def test_vector_32b_an_exit_before_the_entry_would_flip_the_opener():
    # Why the forms reject it: the exit sorts first, opens a short, and the trade id moves.
    [trade] = derive_trades([
        ex(1, "buy", 10, "100.00", minutes=1, trade_id=K_A, broker="manual"),
        ex(2, "sell", 10, "105.00", minutes=0, trade_id=K_A, broker="manual"),
    ])

    assert (trade.direction, trade.opening_execution_id, trade.gross_pnl) == (
        "short", 2, Decimal("50.00"),
    )


def test_vector_33a_a_one_leg_entry_qty_edit_leaves_the_trade_open():
    [trade] = derive_trades([
        ex(1, "buy", 20, "100.00", minutes=0, trade_id=K_A, broker="manual"),
        ex(2, "sell", 10, "105.00", minutes=1, trade_id=K_A, broker="manual"),
    ])

    assert (trade.is_open, trade.quantity, trade.net_pnl) == (True, 20, None)


def test_vector_33b_a_one_leg_exit_qty_edit_flips_into_a_second_trade():
    closed, flipped = derive_trades([
        ex(1, "buy", 10, "100.00", minutes=0, trade_id=K_A, broker="manual"),
        ex(2, "sell", 15, "105.00", minutes=1, trade_id=K_A, broker="manual"),
    ])

    assert (closed.is_open, closed.net_pnl) == (False, Decimal("50.00"))
    assert (flipped.is_open, flipped.direction, flipped.quantity, flipped.avg_entry_price) == (
        True, "short", 5, Decimal("105.00"),
    )
