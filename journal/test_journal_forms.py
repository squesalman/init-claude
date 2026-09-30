"""
Journal entry and rules forms (ADR-0007 section 5; docs/design/journaling.md 3.3-3.5, 4.3;
docs/product/features/journaling.md AC 9, 10, 13, 15, 16, 18, 28). The forms are
validated against unsaved instances and a trade-shaped stand-in; the DB is only used by
full_clean (Django checks CheckConstraints with a query).
"""

import html
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from journal import copy
from journal.forms import JournalEntryForm, RulesForm, UserScopedModelForm
from journal.models import JournalEntry

pytestmark = pytest.mark.django_db

LONG_50 = SimpleNamespace(
    direction="long", avg_entry_price=Decimal("50.00"), entry_lot_count=1, currency="USD"
)
SHORT_50 = SimpleNamespace(
    direction="short", avg_entry_price=Decimal("50.00"), entry_lot_count=1, currency="USD"
)
MULTI_LEG = SimpleNamespace(
    direction="long", avg_entry_price=Decimal("10.07"), entry_lot_count=2, currency="USD"
)


def form(data, trade=LONG_50, instance=None):
    return JournalEntryForm(
        data, instance=instance or JournalEntry(), user=get_user_model()(), trade=trade
    )


def cleaned(data, trade=LONG_50):
    f = form(data, trade)
    assert f.is_valid(), f.errors
    return f.cleaned_data


def error(data, field, trade=LONG_50):
    f = form(data, trade)
    assert not f.is_valid()
    return f.errors.as_data()[field][0]


def test_is_a_user_scoped_model_form_with_exactly_the_four_fields():
    assert issubclass(JournalEntryForm, UserScopedModelForm)
    assert list(form({}).fields) == ["rules_followed", "note", "stop_price", "planned_risk_amount"]


def test_all_blank_cleans_to_nones_and_empty_note():
    assert cleaned({}) == {
        "rules_followed": None, "note": "", "stop_price": None, "planned_risk_amount": None,
    }


# --- AC 13: nothing outside the four fields is read ------------------------------------------


def test_posted_opening_execution_user_and_risk_currency_are_ignored():
    instance = JournalEntry()
    f = form(
        {"note": "n", "opening_execution": "999", "user": "999", "risk_currency": "EUR",
         "planned_risk_amount": "5"},
        instance=instance,
    )

    assert f.is_valid(), f.errors
    assert set(f.cleaned_data) == {"rules_followed", "note", "stop_price", "planned_risk_amount"}
    assert instance.opening_execution_id is None and instance.risk_currency is None


# --- Rules followed (3.3) ----------------------------------------------------------------------


@pytest.mark.parametrize("posted, value", [("true", True), ("false", False), ("", None)])
def test_rules_followed_radios(posted, value):
    assert cleaned({"rules_followed": posted})["rules_followed"] is value


def test_rules_followed_rejects_anything_else():
    assert error({"rules_followed": "maybe"}, "rules_followed").code == "invalid_choice"


@pytest.mark.parametrize("saved, checked", [(True, "true"), (False, "false")])
def test_rules_followed_prefills_the_saved_answer(saved, checked):
    markup = str(form(None, instance=JournalEntry(rules_followed=saved))["rules_followed"])

    assert markup.count("checked") == 1
    assert re.search(rf'<input[^>]*value="{checked}"[^>]*checked', markup)


def test_rules_followed_has_no_default_answer():
    markup = str(form(None)["rules_followed"])

    assert "checked" not in markup
    assert copy.FOLLOWED_YES in html.unescape(markup) and copy.FOLLOWED_NO in html.unescape(markup)


# --- Note (3.4, AC 9, 10) ----------------------------------------------------------------------


def test_whitespace_only_note_is_stored_empty():
    assert cleaned({"note": "  \r\n\t  "})["note"] == ""


def test_note_is_trimmed_and_crlf_normalised():
    assert cleaned({"note": "  a\r\nb  "})["note"] == "a\nb"


def test_note_of_10000_after_crlf_normalisation_saves():
    text = "x" * 9998 + "\r\n" + "x"  # 10,001 raw, 10,000 after \r\n -> \n

    assert len(cleaned({"note": text})["note"]) == 10_000


def test_note_over_10000_after_normalisation_is_rejected_with_the_design_message():
    text = "x" * 10_000 + "\r\n" + "x"  # 10,002 after normalisation

    err = error({"note": text}, "note")

    assert err.code == "max_length"
    assert err.message % err.params == (
        "That note is 10002 characters and the limit is 10000. Shorten it a little."
    )


def test_note_length_counts_code_points():
    assert len(cleaned({"note": "\U0001f600" * 10_000})["note"]) == 10_000


def test_note_textarea_has_no_maxlength_attribute():
    markup = str(form(None)["note"])

    assert "<textarea" in markup and "maxlength" not in markup


def test_note_rejects_nul_characters():
    assert error({"note": "a\x00b"}, "note").code == "null_characters_not_allowed"


# --- Stop and planned risk parsing (3.5, 4.3) ----------------------------------------------------


@pytest.mark.parametrize(
    "text, value",
    [("62.50", "62.50"), (" 62.50 ", "62.50"), ("1,250.50", "1250.50"), ("1,000,000", "1000000"),
     ("5", "5"), (".5", "0.5"), ("0.0001", "0.0001")],
)
def test_planned_risk_parses_plain_and_grouped_numbers(text, value):
    got = cleaned({"planned_risk_amount": text})["planned_risk_amount"]

    assert got == Decimal(value) and str(got) == value  # never quantized


@pytest.mark.parametrize("text", ["abc", "1.2.3", "1,25", "12,34.5", "1e5", "$5", "5 USD",
                                  "--5", "NaN", "Infinity", ",", "1,,000"])
def test_not_a_number(text):
    assert error({"planned_risk_amount": text}, "planned_risk_amount").message == (
        copy.RISK_NOT_NUMBER
    )
    assert error({"stop_price": text}, "stop_price").message == copy.STOP_NOT_NUMBER


@pytest.mark.parametrize("text", ["1" * 41, "1" * 5000, "x" + "1" * 5000, "," * 41])
def test_overlong_input_is_rejected_before_any_number_regex_runs(text):
    """Security review: a regex on megabytes of digits is a ReDoS. The length cap (longest
    valid value is ~30 chars) makes this deterministic, not a timing test. Digits-only
    overlong input reads "too large"; anything else "not a number"."""
    expected = copy.NUMBER_TOO_LARGE if text.isdigit() else None  # only-separators: not a number
    for field, not_number in (("planned_risk_amount", copy.RISK_NOT_NUMBER),
                              ("stop_price", copy.STOP_NOT_NUMBER)):
        assert error({field: text}, field).message == (expected or not_number)


@pytest.mark.parametrize("text", ["0", "0.00", "-5", "-0.0001", "-0"])
def test_planned_risk_not_positive(text):
    err = error({"planned_risk_amount": text}, "planned_risk_amount")

    assert (err.code, err.message) == ("risk_not_positive", copy.RISK_NOT_POSITIVE)


@pytest.mark.parametrize(
    "field, text, message",
    [
        # Over-scale: rejected, never rounded (0.00001 would round to 0 in NUMERIC(19,4)).
        ("planned_risk_amount", "0.00001", copy.RISK_DECIMALS),
        ("planned_risk_amount", "62.50001", copy.RISK_DECIMALS),
        ("stop_price", "0.00000000001", copy.STOP_DECIMALS),
        ("stop_price", "49.500000000001", copy.STOP_DECIMALS),
        # Over-digits: NUMERIC(19,4) holds 15 whole digits, NUMERIC(20,10) holds 10.
        ("planned_risk_amount", "10000000000000000", copy.NUMBER_TOO_LARGE),
        ("planned_risk_amount", "1000000000000000", copy.NUMBER_TOO_LARGE),
        ("stop_price", "12345678901", copy.NUMBER_TOO_LARGE),
        ("stop_price", "-12345678901", copy.NUMBER_TOO_LARGE),
        ("planned_risk_amount", "1" * 400, copy.NUMBER_TOO_LARGE),
    ],
)
def test_precision_limits_are_form_errors(field, text, message):
    assert error({field: text}, field).message == message


@pytest.mark.parametrize(
    "field, text",
    [("planned_risk_amount", "999999999999999.9999"), ("stop_price", "9999999999.9999999999"),
     ("stop_price", "0.0000000001")],
)
def test_values_at_the_column_limits_are_accepted(field, text):
    f = form({field: text}, trade=MULTI_LEG)  # no side check in the way

    assert f.is_valid(), f.errors
    assert f.cleaned_data[field] == Decimal(text)


def test_number_inputs_are_text_with_decimal_keyboard():
    for name in ("stop_price", "planned_risk_amount"):
        markup = str(form(None)[name])
        assert 'type="text"' in markup and 'inputmode="decimal"' in markup, markup
        assert 'autocomplete="off"' in markup and "step" not in markup


# --- Stop side check (AC 16, 18; domain section 3) ----------------------------------------------


@pytest.mark.parametrize("stop", ["50.00", "50.01", "60"])
def test_long_stop_at_or_above_entry_is_rejected(stop):
    err = error({"stop_price": stop}, "stop_price")

    assert (err.code, err.message) == ("stop_not_a_risk", copy.STOP_WRONG_SIDE)


@pytest.mark.parametrize("stop", ["50.00", "49.50"])
def test_short_stop_at_or_below_entry_is_rejected(stop):
    assert error({"stop_price": stop}, "stop_price", trade=SHORT_50).code == "stop_not_a_risk"


def test_stop_on_the_loss_side_is_accepted_and_a_negative_stop_is_a_price():
    assert cleaned({"stop_price": "49.50"})["stop_price"] == Decimal("49.50")
    assert cleaned({"stop_price": "50.50"}, trade=SHORT_50)["stop_price"] == Decimal("50.50")
    assert cleaned({"stop_price": "-1"})["stop_price"] == Decimal("-1")


def test_stop_check_skipped_when_planned_risk_is_set():
    got = cleaned({"stop_price": "60", "planned_risk_amount": "59.00"})  # vector 9

    assert (got["stop_price"], got["planned_risk_amount"]) == (Decimal("60"), Decimal("59.00"))


def test_multi_leg_stop_saves_unchecked():
    assert cleaned({"stop_price": "11.00"}, trade=MULTI_LEG)["stop_price"] == Decimal("11.00")


def test_stop_check_does_not_pile_on_when_planned_risk_is_itself_invalid():
    f = form({"stop_price": "60", "planned_risk_amount": "abc"})

    assert not f.is_valid()
    assert set(f.errors) == {"planned_risk_amount"}


# --- Rules form (AC 28) ----------------------------------------------------------------------


def test_rules_form_normalises_and_trims():
    f = RulesForm({"trading_rules": "  Wait for the retest.\r\nMax 2.  "})

    assert f.is_valid()
    assert f.cleaned_data["trading_rules"] == "Wait for the retest.\nMax 2."


def test_rules_form_blank_clears_the_rules():
    f = RulesForm({"trading_rules": "   "})

    assert f.is_valid() and f.cleaned_data["trading_rules"] == ""


def test_rules_over_10000_rejected_with_the_design_message():
    f = RulesForm({"trading_rules": "r" * 10_001})

    assert not f.is_valid()
    err = f.errors.as_data()["trading_rules"][0]
    assert err.message % err.params == (
        "Your rules are 10001 characters and the limit is 10000. Shorten them a little."
    )
    assert "maxlength" not in str(RulesForm()["trading_rules"])
