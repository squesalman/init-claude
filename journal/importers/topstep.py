"""
TopstepX "Trades" CSV export -> two execution legs per row (docs/domain/topstep-import.md).

Pure: no DB, so it can be re-run over stored raw rows (ADR-0006). Never calls float().
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from itertools import islice

HEADER = [
    "Id", "ContractName", "EnteredAt", "ExitedAt", "EntryPrice", "ExitPrice", "Fees", "PnL",
    "Size", "Type", "TradeDay", "TradeDuration", "Commissions",
]
# Verified against a real export (topstep-import.md section 2). Unknown roots fail the row.
MULTIPLIERS = {"CL": Decimal("1000"), "MCL": Decimal("100")}
CURRENCY = "USD"
# Sanity cap, not a product limit: ADR-0006's revisit trigger is about 2,000 lines per file.
MAX_DATA_ROWS = 5_000
_MAX_EXTRA_CELLS = 5  # the full bytes are kept in ImportBatch.raw_file

FILE_NOT_RECOGNISED = (
    "This file does not look like a TopstepX export. Check that you exported trades as CSV."
)
WRONG_COLUMN_COUNT = "This row does not have the 13 TopstepX columns, so it was left out."
PNL_MISMATCH = "The P&L in the file does not match its prices, so this row was left out."
FEES_TOO_PRECISE = (
    "Fees plus commissions have more than 4 decimal places, so this row was left out."
)
EXIT_BEFORE_ENTRY = "The exit time is earlier than the entry time, so this row was left out."
TOO_MANY_ROWS = (
    f"This file has more than {MAX_DATA_ROWS:,} trades. Export a shorter date range and "
    "upload each part."
)

_NUMBER = re.compile(r"-?[0-9]{1,20}(?:\.[0-9]{1,20})?")
_ID = re.compile(r"\S{1,100}")  # leaves room for the ":entry"/":exit" suffix in 128 chars
_CONTRACT = re.compile(r"(.+)[A-Z][0-9]")  # root + month letter + 1-digit year
_TIMESTAMP = "%m/%d/%Y %H:%M:%S %z"  # month first, own offset per row (verified)
_PRICE_PLACES, _PRICE_DIGITS = 10, 20  # Execution.price / quantity: NUMERIC(20,10)
_FEE_PLACES, _FEE_DIGITS = 4, 19  # Execution.fees: NUMERIC(19,4)


class ImportFileError(Exception):
    """The whole file is rejected before anything is written. str() is user-facing."""


@dataclass(frozen=True)
class ParsedRow:
    line_number: int
    raw: dict  # column -> original string, exactly as read
    legs: tuple = ()  # (entry, exit) Execution field dicts when the row is readable
    error: str = ""  # user-facing reason when it is not


class _RowError(Exception):
    pass


def parse(file_bytes: bytes) -> list[ParsedRow]:
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFileError(FILE_NOT_RECOGNISED) from None
    if "\x00" in text:  # Postgres JSONB cannot store it
        raise ImportFileError(FILE_NOT_RECOGNISED)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        # Header plus one row over the cap is enough to know; never read the rest.
        lines = list(islice(((reader.line_num, c) for c in reader if c), MAX_DATA_ROWS + 2))
    except csv.Error:
        raise ImportFileError(FILE_NOT_RECOGNISED) from None
    # A header-only file is a file-level problem too (design import-account-label 8.10).
    if len(lines) < 2 or lines[0][1] != HEADER:
        raise ImportFileError(FILE_NOT_RECOGNISED)
    if len(lines) > MAX_DATA_ROWS + 1:
        raise ImportFileError(TOO_MANY_ROWS)
    return [_parse_row(line_number, cells) for line_number, cells in lines[1:]]


def _parse_row(line_number: int, cells: list[str]) -> ParsedRow:
    raw = dict(zip(HEADER, cells, strict=False))
    if len(cells) > len(HEADER):
        extra = cells[len(HEADER):]
        raw["_extra_count"] = str(len(extra))
        raw["_extra"] = extra[:_MAX_EXTRA_CELLS]
    if len(cells) != len(HEADER):
        return ParsedRow(line_number, raw, error=WRONG_COLUMN_COUNT)
    try:
        return ParsedRow(line_number, raw, legs=_legs(raw))
    except _RowError as exc:
        return ParsedRow(line_number, raw, error=str(exc))


def _unreadable(column: str) -> _RowError:
    return _RowError(f"The {column} value could not be read, so this row was left out.")


def _number(raw: dict, column: str) -> Decimal:
    if not _NUMBER.fullmatch(raw[column]):  # rejects NaN, Infinity, "1_000", " 1", "1e3"
        raise _unreadable(column)
    return Decimal(raw[column])


def _fits(value: Decimal, places: int, digits: int) -> bool:
    return abs(value) < 10 ** (digits - places) and value == value.quantize(
        Decimal(1).scaleb(-places)
    )


def _price(raw: dict, column: str) -> Decimal:
    value = _number(raw, column)
    # Mirrors the execution_price_not_zero CHECK, so a bad row fails alone instead of
    # aborting the upload.
    if value == 0 or not _fits(value, _PRICE_PLACES, _PRICE_DIGITS):
        raise _unreadable(column)
    return value


def _timestamp(raw: dict, column: str) -> datetime:
    try:
        return datetime.strptime(raw[column], _TIMESTAMP).astimezone(UTC)
    except (ValueError, OverflowError):  # OverflowError: year 1 / 9999 shifted past UTC
        raise _unreadable(column) from None


def _legs(raw: dict) -> tuple[dict, dict]:
    trade_id = raw["Id"]
    if not _ID.fullmatch(trade_id):
        raise _unreadable("Id")
    side_sign = {"Long": 1, "Short": -1}.get(raw["Type"])
    if side_sign is None:
        raise _unreadable("Type")
    contract = _CONTRACT.fullmatch(raw["ContractName"])
    if not contract or contract[1] not in MULTIPLIERS:
        root = (contract[1] if contract else raw["ContractName"])[:16]  # never echo a huge cell
        raise _RowError(f"The contract {root} is not supported yet, so this row was left out.")
    multiplier = MULTIPLIERS[contract[1]]

    entry_price, exit_price = _price(raw, "EntryPrice"), _price(raw, "ExitPrice")
    size = _number(raw, "Size")
    if size <= 0 or size != size.to_integral_value() or not _fits(size, 0, 10):
        raise _unreadable("Size")
    fees, commissions, pnl = (_number(raw, c) for c in ("Fees", "Commissions", "PnL"))
    entered_at, exited_at = _timestamp(raw, "EnteredAt"), _timestamp(raw, "ExitedAt")
    if exited_at < entered_at:  # equal is fine (user ruling)
        raise _RowError(EXIT_BEFORE_ENTRY)

    # Exact arithmetic: every operand has at most 40 digits (_NUMBER), so 60 never rounds.
    with localcontext(prec=60):
        if (exit_price - entry_price) * size * multiplier * side_sign != pnl:
            raise _RowError(PNL_MISMATCH)
        total_fees = fees + commissions
    # All costs sit on the exit leg (topstep-import.md section 5). Quantized once; if that
    # would change the amount, the row fails instead of rounding silently.
    exit_fee = total_fees.quantize(Decimal(1).scaleb(-_FEE_PLACES), rounding=ROUND_HALF_EVEN)
    if exit_fee != total_fees or not _fits(exit_fee, _FEE_PLACES, _FEE_DIGITS):
        raise _RowError(FEES_TOO_PRECISE)

    entry_side, exit_side = ("buy", "sell") if side_sign == 1 else ("sell", "buy")
    common = {
        "broker_trade_id": trade_id, "symbol": raw["ContractName"], "quantity": size,
        "contract_multiplier": multiplier, "currency": CURRENCY,
    }
    return (
        {"broker_execution_id": f"{trade_id}:entry", "side": entry_side, "price": entry_price,
         "fees": Decimal("0"), "executed_at": entered_at, **common},
        {"broker_execution_id": f"{trade_id}:exit", "side": exit_side, "price": exit_price,
         "fees": exit_fee, "executed_at": exited_at, **common},
    )
