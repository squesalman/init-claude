"""
Topstep import write path, exactly ADR-0006 Decision 1: parse, one pre-fetch, classify
(ADR-0004 section 2), then per-row create() inside one transaction. Model instances are
passed, never *_id=, so the cross-tenant guard in UserOwned.save() costs no queries.
Also the batch delete, ADR-0005 section 3.
"""

import hashlib
from dataclasses import dataclass

from django.db import IntegrityError, transaction

from journal.importers.topstep import ImportFileError, parse
from journal.models import (
    MAX_RAW_FILE_BYTES,
    Execution,
    ImportBatch,
    JournalEntry,
    RawImportRow,
)

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


def delete_import_batch(user, batch_id, confirmed_max_pk: int) -> DeleteResult:
    """ADR-0005 section 3. Raises ImportBatch.DoesNotExist for a missing or someone else's
    id, StaleConfirm (all rolled back) if the batch has a journal entry the user was not
    shown. confirmed_max_pk is the newest entry pk shown (0 = none) and must come from a
    source the caller trusts (the view's signed token), or an inflated value defeats this."""
    with transaction.atomic():
        batch = ImportBatch.objects.for_user(user).defer("raw_file").get(pk=batch_id)
        execs = Execution.objects.for_user(user).filter(raw_import_row__import_batch=batch)
        entries = JournalEntry.objects.for_user(user).filter(opening_execution__in=execs)
        # Delete only what was confirmed, then refuse if anything is left (follow-ups 29d):
        # a newer entry committed before exists() is seen here; one committed after it is
        # blocked by the RESTRICT FK on opening_execution when execs.delete() runs.
        # ponytail: max pk misses (a) an UPDATE that re-points an older entry into this batch
        # (ADR-0003 manual correction, not built yet) and (b) a lower pk committing after a
        # higher one (Postgres sequences are non-transactional; concurrent same-user
        # inserts). Fix for both: sign a hash of the sorted shown pk set instead.
        n, _ = entries.filter(pk__lte=confirmed_max_pk).delete()
        if entries.exists():
            raise StaleConfirm
        execs.delete()
        batch.delete()  # CASCADE raw rows; SET_NULL on execution is now a no-op
    return DeleteResult(trade_count=batch.imported_count, journal_count=n)
