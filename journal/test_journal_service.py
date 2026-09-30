"""
journal.services find_trade / save_journal_entry (ADR-0007 section 6; spec AC 5, 7, 8, 12, 17;
domain vector 21). Synthetic executions only.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from django.contrib.auth import get_user_model

from journal.models import CrossTenantForeignKeyError, Execution, JournalEntry
from journal.services import find_trade, save_journal_entry

User = get_user_model()
T0 = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
_minutes = count()
BLANK = dict(note="", rules_followed=None, stop_price=None, planned_risk_amount=None)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="js-a@example.com", password="x")


@pytest.fixture
def other(db):
    return User.objects.create_user(email="js-b@example.com", password="x")


def fill(user, side, qty="1", price="100", symbol="MNQZ6", label="", currency="USD",
         trade_id=None):
    return Execution.objects.create(
        user=user, broker="manual", broker_account_label=label, broker_trade_id=trade_id,
        symbol=symbol, side=side, quantity=Decimal(qty), price=Decimal(price), currency=currency,
        executed_at=T0 + timedelta(minutes=next(_minutes)), source=Execution.SOURCE_MANUAL,
    )


def round_trip(user, **kw):
    return fill(user, "buy", **kw), fill(user, "sell", **{**kw, "price": "101"})


# --- find_trade ------------------------------------------------------------------------------


def test_find_trade_returns_the_opening_execution_and_its_trade(user):
    opening, _ = round_trip(user)

    found, trade = find_trade(user, opening.pk)

    assert found == opening
    assert (trade.opening_execution_id, trade.net_pnl, trade.entry_lot_count) == (
        opening.pk, Decimal("1.00"), 1,
    )


def test_find_trade_is_none_for_a_missing_id_a_closing_fill_and_another_users_id(user, other):
    opening, closing = round_trip(user)

    assert find_trade(user, 999_999) is None
    assert find_trade(user, closing.pk) is None  # AC 12
    assert find_trade(other, opening.pk) is None  # AC 11


def test_find_trade_narrows_to_the_openers_label_and_symbol(user, django_assert_num_queries):
    fill(user, "sell", label="B")  # would pair with A's buy if buckets crossed labels
    fill(user, "sell", symbol="CLZ6")
    opening, closing = round_trip(user, label="A")

    with django_assert_num_queries(2):
        _, trade = find_trade(user, opening.pk)

    assert trade.execution_ids == (opening.pk, closing.pk)


def test_find_trade_finds_a_flip_leftover_by_the_flipping_fill(user):
    fill(user, "buy", qty="10")
    flip = fill(user, "sell", qty="15", price="105")

    _, trade = find_trade(user, flip.pk)

    assert (trade.direction, trade.quantity, trade.is_open) == ("short", 5, True)


def test_find_trade_counts_entry_lots_for_a_scale_in(user):
    opening = fill(user, "buy", qty="100", price="10.00")
    fill(user, "buy", qty="50", price="10.20")

    assert find_trade(user, opening.pk)[1].entry_lot_count == 2


# --- save_journal_entry ------------------------------------------------------------------------


def save(user, opening, **fields):
    trade = find_trade(user, opening.pk)[1]
    return save_journal_entry(user, opening, trade, **{**BLANK, **fields})


def test_all_blank_with_no_entry_creates_no_row(user):
    opening, _ = round_trip(user)

    assert save(user, opening) is None  # AC 5
    assert not JournalEntry.unscoped.exists()


def test_note_only_creates_one_unanswered_entry_for_the_user(user):
    opening, _ = round_trip(user)

    entry = save(user, opening, note="Waited for the retest.")

    assert JournalEntry.unscoped.get() == entry
    assert (entry.user, entry.opening_execution) == (user, opening)
    assert entry.note == "Waited for the retest."
    assert entry.rules_followed is None and entry.risk_currency is None


def test_an_absent_answer_never_clears_a_saved_one(user):
    opening, _ = round_trip(user)
    save(user, opening, rules_followed=True)

    entry = save(user, opening, note="edited only the note")  # AC 7

    assert entry.rules_followed is True and entry.note == "edited only the note"
    assert save(user, opening, rules_followed=False).rules_followed is False  # Yes <-> No


def test_risk_currency_is_derived_from_the_trade_and_cleared_with_the_amount(user):
    opening, _ = round_trip(user, currency="EUR")  # vector 21

    entry = save(user, opening, planned_risk_amount=Decimal("40.00"))
    assert (entry.planned_risk_amount, entry.risk_currency) == (Decimal("40.00"), "EUR")

    entry = save(user, opening, stop_price=Decimal("99"))
    entry.refresh_from_db()
    assert (entry.planned_risk_amount, entry.risk_currency) == (None, None)
    assert entry.stop_price == Decimal("99")


def test_an_entry_created_in_another_tab_is_updated_not_duplicated(user):
    opening, _ = round_trip(user)
    trade = find_trade(user, opening.pk)[1]  # this tab loaded the page with no entry
    JournalEntry.objects.create(user=user, opening_execution=opening, note="tab 1")

    entry = save_journal_entry(user, opening, trade, **{**BLANK, "note": "tab 2"})  # AC 8

    assert JournalEntry.unscoped.count() == 1
    assert JournalEntry.unscoped.get().note == "tab 2" == entry.note


def test_blanking_an_existing_entry_keeps_the_row_and_its_answer(user):
    opening, _ = round_trip(user)
    save(user, opening, note="n", rules_followed=True, stop_price=Decimal("99"))

    entry = save(user, opening)

    entry.refresh_from_db()
    assert (entry.note, entry.stop_price, entry.rules_followed) == ("", None, True)


def test_cross_tenant_opening_is_refused_by_the_save_guard(user, other):
    opening, _ = round_trip(user)
    trade = find_trade(user, opening.pk)[1]

    with pytest.raises(CrossTenantForeignKeyError):
        save_journal_entry(other, opening, trade, **{**BLANK, "note": "not mine"})

    assert not JournalEntry.unscoped.exists()
