"""
Topstep import write path, exactly ADR-0006 Decision 1: parse, one pre-fetch, classify
(ADR-0004 section 2), then per-row create() inside one transaction. Model instances are
passed, never *_id=, so the cross-tenant guard in UserOwned.save() costs no queries.
Also the batch delete, ADR-0005 section 3.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from journal.importers.topstep import ImportFileError, parse
from journal.matching import Trade, derive_trades
from journal.models import (
    MAX_RAW_FILE_BYTES,
    Execution,
    ImportBatch,
    JournalEntry,
    RawImportRow,
)

log = logging.getLogger(__name__)

BROKER = "topstep"
_COMPARED = ("symbol", "side", "quantity", "price", "executed_at")  # fees left out on purpose

CONFLICT = (
    "Id already imported with different contents. If this is a second account, fill in the "
    "Account field and re-upload."
)
FILE_TOO_LARGE = "This file is larger than 10 MB, so it was not imported."
NAME_NOT_STORABLE = (
    "The file name or Account name is too long or has characters that cannot be stored, "
    "so nothing was imported."
)
TRY_AGAIN = "We could not finish this import and nothing was saved. Try again in a moment."


class ImportRetryError(Exception):
    """A concurrent upload of the same rows won the race; nothing was saved. Retry is safe."""


def import_file(user, filename: str, file_bytes: bytes, account_label: str) -> ImportBatch:
    """Raises ImportFileError (file rejected) or ImportRetryError; both write nothing."""
    if len(file_bytes) > MAX_RAW_FILE_BYTES:
        raise ImportFileError(FILE_TOO_LARGE)
    label = account_label.strip()  # stored trimmed, case kept (ADR-0004 section 1)
    # Backstop for the upload form: column limits, and Postgres text cannot hold NUL.
    if len(label) > 64 or len(filename) > 255 or "\x00" in label + filename:
        raise ImportFileError(NAME_NOT_STORABLE)
    parsed = parse(file_bytes)
    try:
        with transaction.atomic():
            return _write(user, filename, file_bytes, label, parsed)
    except IntegrityError as exc:
        diag = getattr(exc.__cause__, "diag", None)
        if getattr(diag, "constraint_name", None) != "execution_broker_dedupe":
            raise
        raise ImportRetryError(TRY_AGAIN) from exc


def _existing_legs(user, label: str, leg_ids: list[str]) -> dict:
    rows = (
        Execution.objects.for_user(user)
        .filter(broker=BROKER, broker_account_label=label, broker_execution_id__in=leg_ids)
        .values_list("broker_execution_id", *_COMPARED)
    )
    return {row[0]: row[1:] for row in rows}


def _classify(row, seen: dict) -> tuple[str, str]:
    if row.error:
        return RawImportRow.STATUS_FAILED, row.error
    keys = [leg["broker_execution_id"] for leg in row.legs]
    wanted = [tuple(leg[f] for f in _COMPARED) for leg in row.legs]
    found = [seen.get(k) for k in keys]
    if found == [None] * len(keys):
        seen.update(zip(keys, wanted, strict=True))  # later rows in this file see these
        return RawImportRow.STATUS_IMPORTED, ""
    if found == wanted:
        return RawImportRow.STATUS_SKIPPED_DUPLICATE, ""
    return RawImportRow.STATUS_SKIPPED_CONFLICT, CONFLICT


def _write(user, filename, file_bytes, label, parsed) -> ImportBatch:
    leg_ids = [leg["broker_execution_id"] for row in parsed for leg in row.legs]
    seen = _existing_legs(user, label, leg_ids)
    outcomes = [_classify(row, seen) for row in parsed]
    statuses = [status for status, _ in outcomes]

    batch = ImportBatch.objects.create(
        user=user,
        broker=BROKER,
        filename=filename,
        file_sha256=hashlib.sha256(file_bytes).hexdigest(),
        raw_file=file_bytes,
        row_count=len(parsed),
        imported_count=statuses.count(RawImportRow.STATUS_IMPORTED),
        skipped_count=statuses.count(RawImportRow.STATUS_SKIPPED_DUPLICATE)
        + statuses.count(RawImportRow.STATUS_SKIPPED_CONFLICT),
        failed_count=statuses.count(RawImportRow.STATUS_FAILED),
    )
    for row, (status, error) in zip(parsed, outcomes, strict=True):
        raw_row = RawImportRow.objects.create(
            user=user, import_batch=batch, line_number=row.line_number, raw=row.raw,
            status=status, error=error,
        )
        if status == RawImportRow.STATUS_IMPORTED:
            for leg in row.legs:
                Execution.objects.create(
                    user=user, broker=BROKER, broker_account_label=label,
                    source=Execution.SOURCE_IMPORT, raw_import_row=raw_row, **leg,
                )
    return batch


class StaleConfirm(Exception):
    """A journal entry the user was not shown would go; nothing was deleted."""


@dataclass(frozen=True)
class DeleteResult:
    trade_count: int
    journal_count: int


def delete_import_batch(
    user, batch_id, confirmed_max_pk: int, confirmed_updated_at: datetime | None
) -> DeleteResult:
    """ADR-0005 section 3, amended by ADR-0007 section 7. Raises ImportBatch.DoesNotExist for
    a missing or someone else's id, StaleConfirm (all rolled back) if the batch has a journal
    entry the user was not shown, or one edited after it was shown. confirmed_max_pk and
    confirmed_updated_at are the newest entry pk and updated_at shown (0 and None = none) and
    must come from a source the caller trusts (the view's signed token), or an inflated value
    defeats this."""
    if confirmed_updated_at is None:
        confirmed_max_pk = 0  # fail safe: nothing counts as shown
    try:
        with transaction.atomic():
            batch = ImportBatch.objects.for_user(user).defer("raw_file").get(pk=batch_id)
            execs = Execution.objects.for_user(user).filter(raw_import_row__import_batch=batch)
            entries = JournalEntry.objects.for_user(user).filter(opening_execution__in=execs)
            # Delete only what was confirmed, then refuse if anything is left (follow-ups 29d):
            # a newer entry committed before exists() is seen here; one committed after it is
            # blocked by the RESTRICT FK on opening_execution when execs.delete() runs. An entry
            # edited after the confirm rendered (updated_at later than shown) is not deleted,
            # so the same exists() check raises StaleConfirm and the atomic rolls back
            # everything: nothing is deleted, as if it were checked before the delete.
            # ponytail: max pk misses (a) an UPDATE that re-points an older entry into this batch
            # (ADR-0003 manual correction, not built yet) and (b) a lower pk committing after a
            # higher one (Postgres sequences are non-transactional; concurrent same-user
            # inserts). Fix for both: sign a hash of the sorted shown pk set instead.
            confirmed = entries.filter(pk__lte=confirmed_max_pk)
            if confirmed_updated_at is not None:
                confirmed = confirmed.filter(updated_at__lte=confirmed_updated_at)
            n, _ = confirmed.delete()
            if entries.exists():
                raise StaleConfirm
            execs.delete()
            batch.delete()  # CASCADE raw rows; SET_NULL on execution is now a no-op
    except IntegrityError as exc:  # RestrictedError from execs.delete(), or the deferred FK
        # at commit: an entry arrived mid-delete and the atomic rolled back. Broad on purpose
        # (the commit-time error is not a RestrictedError), so log the class: never silent.
        log.warning("import delete refused as stale: %s", type(exc).__name__)  # class only
        raise StaleConfirm from exc
    return DeleteResult(trade_count=batch.imported_count, journal_count=n)


# --- Journaling (ADR-0007 section 6) ------------------------------------------------------------


def find_trade(user, execution_id) -> tuple[Execution, Trade] | None:
    """The trade this execution opened, or None for a missing id, another user's id, or a
    fill that opens no trade (a closing fill). Derives only the opener's (label, symbol):
    every matcher bucket sits inside that pair. Keep the order_by (follow-ups row 27)."""
    opening = Execution.objects.for_user(user).filter(pk=execution_id).first()
    if opening is None:
        return None
    executions = Execution.objects.for_user(user).filter(
        broker_account_label=opening.broker_account_label, symbol=opening.symbol
    ).order_by("executed_at", "id")
    for trade in derive_trades(executions):
        if trade.opening_execution_id == opening.pk:
            return opening, trade
    return None


def save_journal_entry(
    user, opening, trade, *, note, rules_followed, stop_price, planned_risk_amount
) -> JournalEntry | None:
    """Values come from a valid JournalEntryForm (its full_clean already ran). None = nothing
    to save: every input blank and no entry yet, so no row is created (AC 5). Otherwise one
    upsert: update_or_create is get_or_create (which re-reads the row after losing the UNIQUE
    race, so a second tab updates the first tab's row, AC 8) plus a row lock for the update.
    user= is explicit (for_user()'s filter is not carried into create()), and the opening
    instance is passed so the cross-tenant guard costs no query."""
    entries = JournalEntry.objects.for_user(user)
    blank = note == "" and (rules_followed, stop_price, planned_risk_amount) == (None, None, None)
    if blank and not entries.filter(opening_execution=opening).exists():
        return None
    fields = {
        "note": note,
        "stop_price": stop_price,
        "planned_risk_amount": planned_risk_amount,
        # Derived, never posted (domain section 3, vector 21); satisfies both currency CHECKs.
        "risk_currency": trade.currency if planned_risk_amount is not None else None,
    }
    if rules_followed is not None:  # a missing answer never un-answers an entry (AC 7)
        fields["rules_followed"] = rules_followed
    entry, _ = entries.update_or_create(user=user, opening_execution=opening, defaults=fields)
    return entry


# --- Manual entry (ADR-0008 sections 1, 4, 5) ---------------------------------------------------

MANUAL = Execution.SOURCE_MANUAL  # "manual": the source and the broker of every manual fill
MANUAL_CURRENCY = "USD"  # spec ruling: fixed, never posted (AC 12)
_SIDES = {"long": ("buy", "sell"), "short": ("sell", "buy")}  # (entry, exit), topstep-import 5
# Written to every leg of a manual trade (domain section 7.4); Close copies them from the entry.
_SHARED = (
    "broker_trade_id", "broker_account_label", "symbol", "quantity", "contract_multiplier",
    "currency",
)


class AlreadyClosed(Exception):
    """The trade has its exit already; nothing was written (AC 36)."""


class ExitBeforeEntry(Exception):
    """Raised under the row lock; args[0] is the stored entry time. Nothing was written."""


def manual_key(nonce: str) -> str:
    """broker_trade_id of one manual trade: "manual:" + 32 hex (ADR-0008 section 1)."""
    return f"manual:{nonce}"


def _manual_fills(user, pk) -> list[Execution] | None:
    """The one gate for every write to an existing manual fill (close here; edit and delete
    in PR M2b). Call it inside the service's atomic(). None for a missing or another user's
    id, an imported fill, a NULL-key manual row (test stand-ins for fill-only data), a closing
    fill, or a key with more than 2 fills (logged).
    Two locking statements on purpose: the first locks the opener, so a second close of the
    same trade waits there; the second starts after that wait and, under READ COMMITTED, sees
    an exit the first close committed. A single locking SELECT would not."""
    manual = Execution.objects.for_user(user).filter(
        source=MANUAL, broker=MANUAL, broker_trade_id__isnull=False
    )
    opener = manual.select_for_update().filter(pk=pk).first()
    if opener is None:
        return None
    fills = list(
        manual.select_for_update()
        .filter(broker_trade_id=opener.broker_trade_id)
        .order_by("executed_at", "id")
    )
    if fills[0].pk != pk:
        return None
    if len(fills) > 2:
        log.error("manual trade has %d fills: user=%s", len(fills), user.pk)  # no key, no ids
        return None
    return fills


def add_manual_trade(
    user, nonce, *, symbol, side, quantity, entry_price, entry_time, exit_price, exit_time,
    fees, account, contract_multiplier,
) -> int:
    """Values come from a valid ManualTradeForm; nonce from the view's signed add token. The
    opening (entry) fill's pk. A replay of one token returns the first request's trade and
    writes nothing (AC 14)."""
    key = manual_key(nonce)
    with transaction.atomic():
        # Serialize this user's adds: two POSTs of one token must not both miss the lookup
        # below (one key on two entry fills: the matcher would make one double-size trade).
        # The lookup is a later statement, so it sees a fill committed while this one waited.
        # ponytail: per-user lock on manual adds; a user adds trades by hand, seconds apart,
        # so contention is nil.
        list(get_user_model().objects.select_for_update().filter(pk=user.pk).values_list("pk"))
        # ponytail: broker_trade_id has no index; this filters inside one user's rows (the
        # user-leading indexes). Add an index only on measurement (ADR-0008 section 8).
        first = (
            Execution.objects.for_user(user).filter(source=MANUAL, broker_trade_id=key)
            .order_by("pk").values_list("pk", flat=True).first()
        )
        if first is not None:
            return first
        entry_side, exit_side = _SIDES[side]
        common = {
            "user": user, "broker": MANUAL, "source": MANUAL, "broker_trade_id": key,
            "broker_account_label": account, "symbol": symbol, "quantity": quantity,
            "contract_multiplier": contract_multiplier, "currency": MANUAL_CURRENCY,
        }
        entry = Execution.objects.create(
            side=entry_side, price=entry_price, fees=Decimal("0"), executed_at=entry_time,
            **common,
        )
        if exit_price is not None:
            # Inserted after the entry: on equal times the lower pk keeps the entry the opener
            # (ADR-0008 section 1 rule 3, vector 32a). All fees sit on the exit (domain 7.3).
            Execution.objects.create(
                side=exit_side, price=exit_price, fees=fees, executed_at=exit_time, **common
            )
    return entry.pk


def close_manual_trade(user, pk, *, exit_price, exit_time, fees) -> bool | None:
    """One full-size exit fill (AC 34). None = no such manual trade. Raises AlreadyClosed or
    ExitBeforeEntry, decided from the locked rows, not from the rendered form."""
    with transaction.atomic():
        fills = _manual_fills(user, pk)
        if fills is None:
            return None
        if len(fills) == 2:
            raise AlreadyClosed
        [entry] = fills
        if exit_time < entry.executed_at:  # compared in UTC; equal is fine (domain 7.1)
            raise ExitBeforeEntry(entry.executed_at)
        Execution.objects.create(
            user=user, broker=MANUAL, source=MANUAL,
            side="sell" if entry.side == "buy" else "buy",
            price=exit_price, fees=fees, executed_at=exit_time,
            **{name: getattr(entry, name) for name in _SHARED},
        )
    return True
