"""
journal/importers/topstep.py parse(): docs/domain/topstep-import.md sections 1-6 and
ADR-0006 (parse step). Pure, no DB. All rows are synthetic.
"""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from journal.importers.topstep import HEADER, ImportFileError, parse
from journal.matching import derive_trades

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "topstep_synthetic.csv"

T1 = {
    "Id": "9000000001", "ContractName": "CLZ6",
    "EnteredAt": "12/19/2026 09:00:00 +00:00", "ExitedAt": "12/19/2026 09:05:00 +00:00",
    "EntryPrice": "80.00", "ExitPrice": "80.15", "Fees": "6.04", "PnL": "300.00",
    "Size": "2", "Type": "Long", "Commissions": "2.00",
}
T2 = {
    "Id": "9000000002", "ContractName": "MCLZ6",
    "EnteredAt": "12/19/2026 10:00:00 +00:00", "ExitedAt": "12/19/2026 10:02:00 +00:00",
    "EntryPrice": "80.00", "ExitPrice": "79.80", "Fees": "1.02", "PnL": "20.00",
    "Size": "1", "Type": "Short", "Commissions": "0.50",
}
T4 = [
    {"Id": "9000000010", "ContractName": "CLZ6", "Type": "Long", "Size": "1",
     "EnteredAt": "12/19/2026 09:10:00 +00:00", "ExitedAt": "12/19/2026 09:20:00 +00:00",
     "EntryPrice": "80.20", "ExitPrice": "80.10", "PnL": "-100.00", "Fees": "2.00",
     "Commissions": "1.00"},
    {"Id": "9000000011", "ContractName": "CLZ6", "Type": "Long", "Size": "1",
     "EnteredAt": "12/19/2026 09:00:00 +00:00", "ExitedAt": "12/19/2026 09:30:00 +00:00",
     "EntryPrice": "80.00", "ExitPrice": "80.50", "PnL": "500.00", "Fees": "2.00",
     "Commissions": "1.00"},
]
_FILLER = {"TradeDay": "12/19/2026 00:00:00 -06:00", "TradeDuration": "00:05:00.0000000"}


def csv_bytes(*rows, header=HEADER, bom=True, newline="\r\n"):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator=newline)
    writer.writerow(header)
    for row in rows:
        full = {**_FILLER, **row}
        writer.writerow([full.get(col, "") for col in header])
    return (b"\xef\xbb\xbf" if bom else b"") + buf.getvalue().encode("utf-8")


def as_executions(parsed):
    """Parsed legs as plain objects, the shape derive_trades() reads."""
    legs = [leg for row in parsed for leg in row.legs]
    return [SimpleNamespace(id=i, broker_account_label="", **leg) for i, leg in enumerate(legs)]


def utc(*args):
    return datetime(*args, tzinfo=UTC)


# --- Vectors ------------------------------------------------------------------------------


def test_t1_long_cl_synthesizes_two_legs_and_one_trade():
    [row] = parse(csv_bytes(T1))

    assert row.error == ""
    entry, exit_ = row.legs
    assert entry == {
        "broker_execution_id": "9000000001:entry", "broker_trade_id": "9000000001",
        "symbol": "CLZ6", "side": "buy", "quantity": Decimal("2"), "price": Decimal("80.00"),
        "contract_multiplier": Decimal("1000"), "fees": Decimal("0"), "currency": "USD",
        "executed_at": utc(2026, 12, 19, 9, 0),
    }
    assert exit_["broker_execution_id"] == "9000000001:exit"
    assert exit_["broker_trade_id"] == "9000000001"
    assert exit_["side"] == "sell"
    assert exit_["price"] == Decimal("80.15")
    assert exit_["fees"] == Decimal("8.04")
    assert exit_["executed_at"] == utc(2026, 12, 19, 9, 5)

    [trade] = derive_trades(as_executions([row]))
    assert (trade.direction, trade.quantity, trade.is_open) == ("long", 2, False)
    assert (trade.avg_entry_price, trade.avg_exit_price) == (Decimal("80.00"), Decimal("80.15"))
    assert (trade.gross_pnl, trade.fees, trade.net_pnl) == (
        Decimal("300.00"), Decimal("8.04"), Decimal("291.96"),
    )


def test_t2_short_mcl_uses_multiplier_100():
    [row] = parse(csv_bytes(T2))

    entry, exit_ = row.legs
    assert (entry["side"], exit_["side"]) == ("sell", "buy")
    assert entry["contract_multiplier"] == Decimal("100")
    assert exit_["fees"] == Decimal("1.52")

    [trade] = derive_trades(as_executions([row]))
    assert (trade.direction, trade.quantity) == ("short", 1)
    assert (trade.gross_pnl, trade.fees, trade.net_pnl) == (
        Decimal("20.00"), Decimal("1.52"), Decimal("18.48"),
    )


def test_t3_unknown_root_fails_the_row_never_defaults_to_1():
    [row] = parse(csv_bytes({**T1, "Id": "9000000003", "ContractName": "ZZZ99Z6"}))

    assert row.legs == ()
    assert "ZZZ99" in row.error


def test_t4_nested_rows_stay_two_trades():
    trades = derive_trades(as_executions(parse(csv_bytes(*T4))))

    assert len(trades) == 2
    outer, inner = trades
    assert outer.gross_pnl == Decimal("500.00") and outer.net_pnl == Decimal("497.00")
    assert inner.gross_pnl == Decimal("-100.00") and inner.net_pnl == Decimal("-103.00")
    assert outer.fees == inner.fees == Decimal("3.00")


# --- File-level checks: raise, nothing parsed -----------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        b"",  # empty
        b"\xef\xbb\xbf",  # BOM only
        csv_bytes(),  # header only (design 8.10: file-level message)
        csv_bytes(T1, header=[*HEADER[:-1], "Commission"]),  # wrong header
        b"Id,Contract\r\n1,2\r\n",
        "﻿Id;ContractName".encode(),
        b"\xff\xfe" + csv_bytes(T1)[3:],  # not UTF-8
        csv_bytes(T1).replace(b"CLZ6", b"CL\x00Z6"),  # NUL byte
    ],
)
def test_unrecognised_file_raises(content):
    with pytest.raises(ImportFileError):
        parse(content)


def test_bom_crlf_and_plain_lf_files_both_parse():
    assert parse(csv_bytes(T1))[0].error == ""
    assert parse(csv_bytes(T1, bom=False, newline="\n"))[0].error == ""


# --- Row-level checks: that row fails, the rest still parse ----------------------------------


@pytest.mark.parametrize(
    "override, column",
    [
        ({"EntryPrice": "80.0O"}, "EntryPrice"),
        ({"ExitPrice": "NaN"}, "ExitPrice"),
        ({"Fees": "1,5"}, "Fees"),
        ({"PnL": "Infinity"}, "PnL"),
        ({"Size": "0"}, "Size"),
        ({"Size": "1.5"}, "Size"),
        ({"Size": " 2"}, "Size"),
        ({"Commissions": ""}, "Commissions"),
        ({"EntryPrice": "0", "PnL": "160300.00"}, "EntryPrice"),
        ({"EntryPrice": "80.00000000001"}, "EntryPrice"),  # 11 dp does not fit the column
        ({"EnteredAt": "2026-12-19 09:00:00 +00:00"}, "EnteredAt"),
        ({"ExitedAt": "13/19/2026 09:05:00 +00:00"}, "ExitedAt"),
        ({"ExitedAt": "12/19/2026 09:05:00"}, "ExitedAt"),  # no offset
        ({"Type": "long"}, "Type"),
        ({"Id": ""}, "Id"),
        ({"Id": "90 01"}, "Id"),
    ],
)
def test_unreadable_value_fails_only_that_row(override, column):
    bad, good = parse(csv_bytes({**T1, **override}, T2))

    assert bad.legs == ()
    assert column in bad.error
    assert good.error == "" and len(good.legs) == 2


def test_pnl_mismatch_fails_the_row():
    [row] = parse(csv_bytes({**T1, "PnL": "299.99"}))

    assert row.legs == ()
    assert "P&L" in row.error


def test_pnl_cross_check_is_exact_decimal_equality_not_string_equality():
    [row] = parse(csv_bytes({**T1, "PnL": "300"}))

    assert row.error == ""


def test_fees_that_do_not_fit_4dp_fail_instead_of_rounding():
    [row] = parse(csv_bytes({**T1, "Fees": "6.04005", "Commissions": "2.00000"}))
    assert row.legs == ()
    assert "fee" in row.error.lower()

    [ok] = parse(csv_bytes({**T1, "Fees": "6.04005", "Commissions": "2.00005"}))
    assert ok.legs[1]["fees"] == Decimal("8.0401")


def test_wrong_column_count_fails_the_row_and_keeps_every_cell():
    extra = b"9000000008,CLZ6,a,b,c,d,e,f,g,h,i,j,k,surplus\r\n"
    short = b"9000000009,CLZ6\r\n"
    content = csv_bytes(T1, T2).replace(b"\r\n9000000002", b"\r\n" + extra + short + b"9000000002")
    t1, too_many, too_few, t2 = parse(content)

    assert t1.error == "" and t2.error == ""
    assert too_many.legs == () and too_many.error
    assert too_many.raw["_extra"] == ["surplus"]
    assert too_many.raw["_extra_count"] == "1"
    assert too_few.legs == () and too_few.error
    assert too_few.raw == {"Id": "9000000009", "ContractName": "CLZ6"}


def test_row_uses_its_own_offset_for_utc():
    [row] = parse(csv_bytes({
        **T1, "EnteredAt": "09/03/2026 21:30:00 +08:00", "ExitedAt": "09/03/2026 21:35:00 +08:00",
    }))

    # Month first: 09/03 is 3 September.
    assert row.legs[0]["executed_at"] == utc(2026, 9, 3, 13, 30)
    assert row.legs[1]["executed_at"] == utc(2026, 9, 3, 13, 35)


def test_raw_keeps_every_value_as_the_original_string():
    [row] = parse(csv_bytes({**T1, "Fees": "6.04000"}))

    assert row.raw == {**_FILLER, **{k: v for k, v in T1.items()}, "Fees": "6.04000"}
    assert all(isinstance(v, str) for v in row.raw.values())


def test_line_numbers_are_file_lines_and_blank_lines_are_skipped():
    content = csv_bytes(T1, T2).replace(b"\r\n9000000002", b"\r\n\r\n9000000002")
    rows = parse(content)

    assert [r.line_number for r in rows] == [2, 4]


def test_synthetic_fixture_row_outcomes():
    rows = parse(FIXTURE.read_bytes())

    assert [bool(r.error) for r in rows] == [False, False, True, False, False, False, True, False]
    breakeven = rows[5]
    assert breakeven.legs[0]["executed_at"] == utc(2026, 12, 19, 12, 0)


# --- Review fix batch: caps, overflow, reversed times -------------------------------------


def test_more_data_rows_than_the_cap_rejects_the_file():
    from journal.importers.topstep import MAX_DATA_ROWS

    assert len(parse(csv_bytes(*[T1] * MAX_DATA_ROWS))) == MAX_DATA_ROWS
    with pytest.raises(ImportFileError):
        parse(csv_bytes(*[T1] * (MAX_DATA_ROWS + 1)))


def test_extra_cells_are_counted_and_only_the_first_few_kept():
    content = csv_bytes(T1).replace(b"2.00\r\n", b"2.00" + b",x" * 10_000 + b"\r\n")
    [row] = parse(content)

    assert row.legs == ()
    assert row.raw["_extra_count"] == "10000"
    assert 0 < len(row.raw["_extra"]) <= 5


def test_unsupported_contract_message_does_not_echo_a_huge_name():
    [row] = parse(csv_bytes({**T1, "ContractName": "Q" * 100_000 + "Z6"}))

    assert row.legs == ()
    assert len(row.error) < 200


@pytest.mark.parametrize("side", ["Long", "Short"])
def test_exit_before_entry_fails_only_that_row(side):
    reversed_times = {
        **T1, "Type": side, "PnL": "300.00" if side == "Long" else "-300.00",
        "EnteredAt": "12/19/2026 09:05:00 +00:00", "ExitedAt": "12/19/2026 09:00:00 +00:00",
    }
    bad, good = parse(csv_bytes(reversed_times, T2))

    assert bad.legs == () and bad.error
    assert "09:0" not in bad.error
    assert "error" not in bad.error.lower() and "invalid" not in bad.error.lower()
    assert good.error == ""


def test_exit_at_the_same_second_as_entry_is_allowed():
    [row] = parse(csv_bytes({**T1, "ExitedAt": T1["EnteredAt"]}))

    assert row.error == ""
