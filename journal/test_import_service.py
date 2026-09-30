"""
journal/services.py import_file(): ADR-0006 Decision 1 (write path and its five tests),
ADR-0004 section 2 (row classification), and the synthetic fixture end to end.
"""

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from journal import services
from journal.importers.topstep import ImportFileError
from journal.matching import derive_trades
from journal.models import Execution, ImportBatch, RawImportRow, UserScopedManager
from journal.stats import compute_stats
from journal.test_topstep_parser import T1, T2, csv_bytes

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "topstep_synthetic.csv"
FIXTURE_LINES = 8


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(email="imp-a@example.com", password="x")


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(email="imp-b@example.com", password="x")


def run(user, content=None, label=""):
    return services.import_file(user, "trades.csv", content or FIXTURE.read_bytes(), label)


def statuses(batch):
    return [r.status for r in batch.rows.order_by("line_number")]


def executions(user):
    return Execution.objects.for_user(user)


def test_fixture_imports_with_per_row_statuses_and_final_counts(user):
    batch = run(user)

    assert statuses(batch) == [
        "imported", "imported", "failed", "imported", "imported", "imported", "failed",
        "skipped_duplicate",
    ]
    assert (batch.row_count, batch.imported_count, batch.skipped_count, batch.failed_count) == (
        8, 5, 1, 2,
    )
    assert batch.broker == "topstep" and batch.filename == "trades.csv"
    assert bytes(batch.raw_file) == FIXTURE.read_bytes()
    assert len(batch.file_sha256) == 64
    assert executions(user).count() == 10
    failed = batch.rows.filter(status="failed").order_by("line_number")
    assert all(r.error for r in failed)
    assert all(isinstance(v, str) for r in batch.rows.all() for v in r.raw.values())


def test_imported_executions_carry_the_legs(user):
    run(user)

    exit_leg = executions(user).get(broker_execution_id="9000000001:exit")
    assert (exit_leg.broker, exit_leg.source, exit_leg.broker_trade_id) == (
        "topstep", "import", "9000000001",
    )
    assert (exit_leg.side, exit_leg.fees, exit_leg.currency) == ("sell", Decimal("8.04"), "USD")
    assert exit_leg.contract_multiplier == 1000
    assert exit_leg.raw_import_row.line_number == 2


def test_fixture_end_to_end_trades_and_stats(user):
    run(user)

    trades = derive_trades(executions(user).order_by("executed_at", "id"))
    assert len(trades) == 5  # T4's nested rows stay two trades
    usd = compute_stats(trades, {})["USD"]
    assert (usd.win_rate, usd.win_rate_n, usd.breakeven_count) == (Decimal("75.00"), 4, 1)
    assert (usd.total_pnl, usd.total_n) == (Decimal("704.44"), 5)


# --- ADR-0006 tests 1-5 ---------------------------------------------------------------------


def test_query_budget(user, django_assert_max_num_queries):
    content = FIXTURE.read_bytes()
    with django_assert_max_num_queries(3 + 3 * FIXTURE_LINES):
        run(user, content)


def test_same_file_twice_adds_nothing(user):
    run(user)
    second = run(user)

    assert second.imported_count == 0
    assert set(statuses(second)) == {"skipped_duplicate", "failed"}
    assert executions(user).count() == 10
    assert ImportBatch.objects.for_user(user).count() == 2


def test_repeated_id_in_one_file_same_contents_is_duplicate_different_is_conflict(user):
    changed = {**T1, "ExitPrice": "80.20", "PnL": "400.00"}
    batch = run(user, csv_bytes(T1, T1, changed))

    assert statuses(batch) == ["imported", "skipped_duplicate", "skipped_conflict"]
    conflict = batch.rows.get(status="skipped_conflict")
    assert conflict.error == (
        "Id already imported with different contents. If this is a second account, fill in "
        "the Account field and re-upload."
    )
    assert (batch.skipped_count, executions(user).count()) == (2, 2)


def test_fees_are_not_compared_for_duplicates(user):
    run(user, csv_bytes(T1))
    batch = run(user, csv_bytes({**T1, "Fees": "9.99"}))

    assert statuses(batch) == ["skipped_duplicate"]


def test_only_one_leg_existing_is_a_conflict(user):
    Execution.objects.create(
        user=user, broker="topstep", broker_execution_id="9000000001:entry", symbol="CLZ6",
        side="buy", quantity="2", price="80.00", currency="USD", source="import",
        executed_at=timezone.now(),
    )
    batch = run(user, csv_bytes(T1))

    assert statuses(batch) == ["skipped_conflict"]
    assert executions(user).count() == 1


def test_failure_mid_import_leaves_nothing_behind(user):
    real_create = UserScopedManager.create
    calls = {"executions": 0}

    def flaky_create(manager, **kwargs):
        if manager.model is Execution:
            calls["executions"] += 1
            if calls["executions"] == 2:
                raise RuntimeError("boom")
        return real_create(manager, **kwargs)

    with patch.object(UserScopedManager, "create", autospec=True, side_effect=flaky_create):
        with pytest.raises(RuntimeError):
            run(user)

    assert ImportBatch.objects.for_user(user).count() == 0
    assert RawImportRow.objects.for_user(user).count() == 0
    assert executions(user).count() == 0


def test_same_ids_for_two_users_both_import_and_stay_isolated(user, other_user):
    a, b = run(user), run(other_user)

    assert a.imported_count == b.imported_count == 5
    assert executions(user).count() == executions(other_user).count() == 10
    assert not executions(user).filter(user=other_user).exists()
    assert list(ImportBatch.objects.for_user(other_user)) == [b]
    assert not RawImportRow.objects.for_user(other_user).filter(import_batch=a).exists()


# --- Account label --------------------------------------------------------------------------


def test_same_ids_under_another_label_import_again_and_label_is_trimmed(user):
    run(user, csv_bytes(T1), label="Combine 50K")
    batch = run(user, csv_bytes(T1), label="  combine 50k \t")

    assert statuses(batch) == ["imported"]
    labels = sorted(executions(user).values_list("broker_account_label", flat=True).distinct())
    assert labels == ["Combine 50K", "combine 50k"]


# --- File-level failures and the concurrent-upload race -------------------------------------


def test_unrecognised_file_writes_nothing(user):
    with pytest.raises(ImportFileError):
        run(user, b"not,a,topstep,file\r\n1,2,3,4\r\n")

    assert ImportBatch.objects.for_user(user).count() == 0


def test_oversized_file_is_rejected_before_parsing(user):
    with patch.object(services, "MAX_RAW_FILE_BYTES", 10):
        with pytest.raises(ImportFileError):
            run(user, csv_bytes(T1))

    assert ImportBatch.objects.for_user(user).count() == 0


def test_lost_race_on_the_dedupe_index_asks_to_try_again_and_writes_nothing(user):
    run(user, csv_bytes(T1))
    # Simulate a concurrent upload that committed after our pre-fetch ran.
    with patch.object(services, "_existing_legs", return_value={}):
        with pytest.raises(services.ImportRetryError):
            run(user, csv_bytes(T2, T1))

    assert ImportBatch.objects.for_user(user).count() == 1
    assert executions(user).count() == 2


# --- Review fix batch: backstop on label and filename -------------------------------------


@pytest.mark.parametrize(
    "filename, label",
    [
        ("trades.csv", "A" * 65),
        ("a" * 252 + ".csv", ""),
        ("trades\x00.csv", ""),
        ("trades.csv", "Acct\x00"),
    ],
)
def test_bad_label_or_filename_writes_nothing(user, filename, label):
    with pytest.raises(ImportFileError):
        services.import_file(user, filename, csv_bytes(T1), label)

    assert ImportBatch.objects.for_user(user).count() == 0
    assert executions(user).count() == 0


def test_label_and_filename_at_their_limits_import(user):
    batch = services.import_file(user, "a" * 251 + ".csv", csv_bytes(T1), "  " + "A" * 64 + " ")

    assert batch.imported_count == 1
    assert executions(user).first().broker_account_label == "A" * 64
