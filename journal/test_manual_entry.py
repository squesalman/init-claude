"""
PR M2a: manual trade entry, add and close (ADR-0008 sections 1-9; spec
docs/product/features/manual-entry.md AC 1-36, 49-53; design docs/design/manual-entry.md 4-6,
12, 13; domain docs/domain/pnl-and-matching.md section 7, vectors 24-34). Edit and delete
(vectors 29, 33 through the UI; AC 37-48) arrive with PR M2b. Synthetic data only.
"""

import html
import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext

from config.test_pages import Doc
from journal import copy, display
from journal.forms import CloseTradeForm, ManualTradeForm, UserScopedModelForm
from journal.importers.topstep import MULTIPLIERS
from journal.models import CrossTenantForeignKeyError, Execution, JournalEntry
from journal.services import (
    AlreadyClosed,
    ExitBeforeEntry,
    _manual_fills,
    add_manual_trade,
    close_manual_trade,
    find_trade,
    import_file,
)
from journal.stats import compute_stats, r_multiple
from journal.test_topstep_parser import T1, csv_bytes

User = get_user_model()
NOW = datetime(2027, 1, 15, 12, 0, tzinfo=UTC)  # every test runs at this instant
ADD = "/trades/new/"
PV = "/trades/point-value/"
KEY = re.compile(r"manual:[0-9a-f]{32}")


@pytest.fixture(autouse=True)
def frozen_now():
    with patch("django.utils.timezone.now", return_value=NOW):
        yield


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="me-a@example.com", password="x", timezone="America/New_York"
    )


@pytest.fixture
def other(db):
    return User.objects.create_user(email="me-b@example.com", password="x", timezone="UTC")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def body(resp):
    return resp.content.decode()


def token_of(resp):
    return Doc(body(resp)).one("input", name="form_token")["value"]


def fresh_token(client):
    return token_of(client.get(ADD))


AAPL = {
    "symbol": "AAPL", "side": "long", "quantity": "10", "entry_price": "100.00",
    "entry_time": "2026-03-02 09:30", "point_value": "1", "pv_for": "AAPL",
}
CLOSED = {**AAPL, "exit_price": "105.00", "exit_time": "2026-03-02 10:00", "fees": "2.00"}


def post_add(client, token=None, **fields):
    data = {"form_token": token or fresh_token(client), **fields}
    return client.post(ADD, data)


def add(client, **fields):
    resp = post_add(client, **fields)
    assert resp.status_code == 302, errors_of(resp)
    return int(re.fullmatch(r"/trades/(\d+)/journal/", resp["Location"])[1])


def errors_of(resp):
    form = resp.context and resp.context.get("form")
    return dict(form.errors) if form is not None else resp.status_code


def field_error(resp, name):
    """The rendered message text inside a field's _error element, unescaped."""
    found = re.search(rf'<div id="id_{name}_error"[^>]*>(.*?)</div>', body(resp), re.S)
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", found[1]).split())) if found else None


def text(resp):
    return Doc(body(resp)).text


def fills(user):
    return list(Execution.objects.for_user(user).order_by("pk"))


def trade_of(user, pk):
    found = find_trade(user, pk)
    assert found is not None
    return found[1]


def import_t1(user):
    import_file(user, "t1.csv", csv_bytes(T1), "")


# --- Entry points (AC 1) -------------------------------------------------------------------------


def test_add_trade_link_on_trades_empty_and_populated(logged_in, user):
    empty = Doc(body(logged_in.get("/trades/")))
    assert [a for a in empty.all("a", href=ADD)], "empty state"
    add(logged_in, **AAPL)
    populated = body(logged_in.get("/trades/"))
    assert re.search(rf'<a href="{ADD}"[^>]*>Add trade</a>', populated)


# --- Add GET -------------------------------------------------------------------------------------


def test_add_get_renders_the_blank_form(logged_in, user):
    resp = logged_in.get(ADD)

    assert resp.status_code == 200
    assert [t.name for t in resp.templates][0] == "journal/trade_form.html"
    markup = body(resp)
    d = Doc(markup)
    assert copy.ADD_TITLE in d.text and copy.ADD_INTRO in d.text
    symbol = d.one("input", name="symbol")
    assert "autofocus" in symbol and symbol["aria-required"] == "true"
    assert symbol["aria-describedby"] == "id_symbol_helptext id_symbol_currency_hint"
    assert "maxlength" not in symbol
    assert not [r for r in d.all("input", type="radio") if "checked" in r]  # no default side
    entry_time = d.one("input", name="entry_time")
    assert entry_time["type"] == "datetime-local" and "value" not in entry_time
    assert "In your time zone, America/New_York." in d.text
    assert copy.POINT_VALUE_PENDING in d.text and not d.all("input", name="point_value")
    assert not d.all("input", name="currency") and "USD" in d.text  # read-only, never an input
    assert re.search(r'<details id="more-details"[^>]*>', markup)[0].find(" open") == -1
    assert d.one("input", name="form_token")["type"] == "hidden"
    assert not Execution.unscoped.exists()


def test_symbol_currency_hint_text_sits_inside_its_own_element(logged_in):
    markup = body(logged_in.get(ADD))

    hint = re.search(r'<p id="id_symbol_currency_hint"[^>]*>(.*?)</p>', markup, re.S)
    assert hint and html.unescape(hint[1]) == copy.SYMBOL_CURRENCY_HINT
    help_at = markup.index('id="id_symbol_helptext"')
    assert help_at < markup.index('id="id_symbol_currency_hint"')  # directly under the help


def test_add_answers_head_and_refuses_put(logged_in):
    assert logged_in.head(ADD).status_code == 200
    assert logged_in.put(ADD).status_code == 405


def test_add_and_close_need_login(client, db):
    assert client.get(ADD).status_code == 302
    assert client.get("/trades/1/close/").status_code == 302
    htmx = client.get(ADD, headers={"HX-Request": "true"})
    assert htmx.status_code == 401 and "HX-Redirect" in htmx


# --- Add POST: saved shapes and vectors ---------------------------------------------------------


def test_add_open_writes_one_manual_fill_and_lands_on_the_journal(logged_in, user):
    resp = post_add(logged_in, **AAPL)

    [entry] = fills(user)
    assert resp.status_code == 302 and resp["Location"] == f"/trades/{entry.pk}/journal/"
    assert (entry.source, entry.broker, entry.broker_execution_id, entry.user_id) == (
        "manual", "manual", None, user.pk,
    )
    assert KEY.fullmatch(entry.broker_trade_id)
    assert (entry.side, entry.currency, entry.fees, entry.contract_multiplier) == (
        "buy", "USD", Decimal("0"), Decimal("1"),
    )
    page = body(logged_in.get(resp["Location"]))
    assert copy.TRADE_ADDED in page
    row = logged_in.get("/trades/").context["trades"][0]
    assert (row["result"], row["net_pnl"]) == ("open", None)  # AC 2


def test_vector_25_closed_long_puts_all_fees_on_the_exit(logged_in, user):
    pk = add(logged_in, **CLOSED)

    entry, exit_ = fills(user)
    assert (entry.side, entry.fees, exit_.side, exit_.fees) == (
        "buy", Decimal("0"), "sell", Decimal("2.00"),
    )
    assert entry.broker_trade_id == exit_.broker_trade_id and entry.pk < exit_.pk
    trade = trade_of(user, pk)
    assert (trade.is_open, trade.gross_pnl, trade.fees, trade.net_pnl) == (
        False, Decimal("50.00"), Decimal("2.00"), Decimal("48.00"),
    )


@pytest.mark.parametrize("fields, multiplier, net", [
    ({"symbol": "CLZ6", "side": "long", "quantity": "2", "entry_price": "80.00",
      "exit_price": "80.15", "fees": "8.04"}, "1000", "291.96"),  # T1
    ({"symbol": "MCLZ6", "side": "short", "quantity": "1", "entry_price": "80.00",
      "exit_price": "79.80", "fees": "1.52"}, "100", "18.48"),  # T2
])
def test_ac_6_known_roots_take_the_lookup(logged_in, user, fields, multiplier, net):
    times = {"entry_time": "2026-03-02 09:30", "exit_time": "2026-03-02 09:35"}
    pk = add(logged_in, **fields, **times)

    assert {f.contract_multiplier for f in fills(user)} == {Decimal(multiplier)}
    assert trade_of(user, pk).net_pnl == Decimal(net)


@pytest.mark.parametrize("posted", ["1", "abc", "-1", "0", "9" * 50])
def test_vector_27b_known_root_ignores_any_posted_point_value(logged_in, user, posted):
    pk = add(
        logged_in, symbol="CLZ6", side="long", quantity="2", entry_price="80.00",
        exit_price="80.15", fees="8.04", entry_time="2026-03-02 09:30",
        exit_time="2026-03-02 09:35", point_value=posted, pv_for="CLZ6",
    )

    assert {f.contract_multiplier for f in fills(user)} == {Decimal("1000")}
    assert trade_of(user, pk).net_pnl == Decimal("291.96")


def test_vector_27_unknown_root_uses_the_typed_point_value(logged_in, user):
    pk = add(
        logged_in, symbol="ZZZH7", side="long", quantity="2", entry_price="5000.00",
        exit_price="5002.50", fees="4.20", entry_time="2026-03-02 09:30",
        exit_time="2026-03-02 09:35", point_value="25", pv_for="ZZZH7",
    )
    trade = trade_of(user, pk)
    entry = JournalEntry(stop_price=Decimal("4995.00"), planned_risk_amount=None)

    assert (trade.gross_pnl, trade.net_pnl) == (Decimal("125.00"), Decimal("120.80"))
    assert r_multiple(trade, entry) == (Decimal("0.4832"), None)


def test_vector_26_short_entry_is_the_sell_fill(logged_in, user):
    pk = add(
        logged_in, symbol="TSLA", side="short", quantity="5", entry_price="200.00",
        exit_price="195.50", fees="1.25", entry_time="2026-03-02 09:30",
        exit_time="2026-03-02 09:40", point_value="1", pv_for="TSLA",
    )
    entry_fill, exit_fill = fills(user)
    trade = trade_of(user, pk)

    assert (entry_fill.side, exit_fill.side, trade.direction) == ("sell", "buy", "short")
    assert (trade.gross_pnl, trade.net_pnl) == (Decimal("22.50"), Decimal("21.25"))
    stop = JournalEntry(stop_price=Decimal("202.00"), planned_risk_amount=None)
    assert r_multiple(trade, stop) == (Decimal("2.1250"), None)


def test_vector_28_fees_keep_4_dp_and_net_rounds_once_half_even(logged_in, user):
    pk = add(logged_in, **{**CLOSED, "fees": "1.015"})
    trade = trade_of(user, pk)

    assert fills(user)[1].fees == Decimal("1.0150")
    assert (trade.gross_pnl, trade.fees, trade.net_pnl) == (
        Decimal("50.00"), Decimal("1.02"), Decimal("48.98"),
    )


def test_vector_24_open_then_closed_keeps_the_trade_id_and_the_journal(logged_in, user):
    pk = add(logged_in, **AAPL)
    open_trade = trade_of(user, pk)
    assert (open_trade.is_open, open_trade.net_pnl, open_trade.entry_lot_count) == (True, None, 1)
    assert r_multiple(open_trade, None) == (None, "trade_open")
    logged_in.post(f"/trades/{pk}/journal/", {"note": "plan", "stop_price": "99.00"})
    entry = JournalEntry.objects.for_user(user).get()

    resp = logged_in.post(
        f"/trades/{pk}/close/",
        {"exit_price": "105.00", "exit_time": "2026-03-02 10:00", "fees": "2.00"},
    )

    assert resp.status_code == 302 and resp["Location"] == f"/trades/{pk}/journal/"
    closed = trade_of(user, pk)
    assert (closed.opening_execution_id, closed.is_open, closed.net_pnl) == (
        pk, False, Decimal("48.00"),
    )
    kept = JournalEntry.objects.for_user(user).get()
    assert (kept.pk, kept.opening_execution_id, kept.note) == (entry.pk, pk, "plan")
    assert r_multiple(closed, kept) == (Decimal("4.8000"), None)
    assert copy.TRADE_CLOSED in body(logged_in.get(resp["Location"]))


def test_vector_32a_equal_times_save_with_the_entry_as_opener(logged_in, user):
    pk = add(logged_in, **{**CLOSED, "exit_price": "100.50", "fees": "",
                           "exit_time": "2026-03-02 09:30"})
    entry, exit_ = fills(user)
    trade = trade_of(user, pk)

    assert entry.executed_at == exit_.executed_at and entry.pk < exit_.pk
    assert (trade.direction, trade.opening_execution_id, trade.gross_pnl) == (
        "long", entry.pk, Decimal("5.00"),
    )


def test_vector_30_two_open_trades_on_one_symbol_stay_apart_and_close_alone(logged_in, user):
    a = add(logged_in, **AAPL)
    b = add(logged_in, **{**AAPL, "quantity": "5", "entry_price": "101.00",
                          "entry_time": "2026-03-02 10:00"})
    assert [t.is_open for t in (trade_of(user, a), trade_of(user, b))] == [True, True]

    logged_in.post(f"/trades/{b}/close/", {
        "exit_price": "103.00", "exit_time": "2026-03-02 11:00", "fees": "1.00",
    })

    trade_a, trade_b = trade_of(user, a), trade_of(user, b)
    assert (trade_a.is_open, trade_a.quantity) == (True, Decimal("10"))
    assert (trade_b.is_open, trade_b.net_pnl) == (False, Decimal("9.00"))
    assert len(logged_in.get("/trades/").context["trades"]) == 2


def test_vector_31_manual_and_imported_never_pair_and_both_count(logged_in, user):
    import_t1(user)
    imported = fills(user)
    add(logged_in, symbol="CLZ6", side="long", quantity="1", entry_price="80.00",
        exit_price="80.10", fees="3.00", entry_time="2026-12-01 09:00",
        exit_time="2026-12-01 09:05")
    derived = sorted(
        (t for t in (find_trade(user, f.pk) for f in fills(user)) if t), key=lambda t: t[0].pk
    )

    assert [t.net_pnl for _, t in derived] == [Decimal("291.96"), Decimal("97.00")]
    assert [f.pk for f in imported] == [f.pk for f in fills(user)[:2]]  # untouched
    usd = compute_stats([t for _, t in derived], {})["USD"]
    assert (usd.total_pnl, usd.win_rate_n) == (Decimal("388.96"), 2)


def test_ac_24_closed_manual_counts_in_cards_and_open_in_none(logged_in, user):
    add(logged_in, **CLOSED)
    add(logged_in, **AAPL)  # open: in no n

    cards = logged_in.get("/trades/").context["cards"]
    assert cards["total_pnl"]["value"] == "+$48.00 (n=1)"
    assert cards["win_rate"]["value"] == "100.00% (n=1)"


def test_ac_25_manual_trade_row_opens_its_journal(logged_in, user):
    pk = add(logged_in, **AAPL)

    page = body(logged_in.get("/trades/"))
    assert f'href="/trades/{pk}/journal/"' in page
    assert logged_in.post(f"/trades/{pk}/journal/", {"note": "x"}).status_code == 302


# --- Add POST: validation (200, values kept, nothing written) -----------------------------------


def test_ac_3_every_blank_required_input_has_its_own_error(logged_in, user):
    resp = post_add(logged_in)

    assert resp.status_code == 200 and not Execution.unscoped.exists()
    expected = {
        "symbol": copy.SYMBOL_REQUIRED, "side": copy.SIDE_REQUIRED,
        "quantity": copy.QUANTITY_REQUIRED, "entry_price": copy.ENTRY_PRICE_REQUIRED,
        "entry_time": copy.ENTRY_TIME_REQUIRED,
    }
    assert errors_of(resp) == {k: [v] for k, v in expected.items()}
    d = Doc(body(resp))
    for name, message in expected.items():
        assert field_error(resp, name) == message
        assert d.all("a", href=f"#id_{name}"), name  # error summary links to the field
    assert copy.POINT_VALUE_PENDING in d.text  # a blank symbol: no point-value error


def test_values_are_kept_on_a_validation_re_render(logged_in):
    resp = post_add(logged_in, **{**AAPL, "side": "", "symbol": "  aapl ", "account": "Main"})

    d = Doc(body(resp))
    assert d.one("input", name="symbol")["value"] == "AAPL"  # normalized (design 4.2)
    assert d.one("input", name="quantity")["value"] == "10"
    assert d.one("input", name="entry_time")["value"] == "2026-03-02 09:30"
    assert d.one("input", name="account")["value"] == "Main"
    assert re.search(r'<details id="more-details"[^>]* open', body(resp))  # holds a value


def test_symbol_error_extends_aria_describedby_with_the_error_id(logged_in):
    resp = post_add(logged_in, **{**AAPL, "symbol": ""})

    symbol = Doc(body(resp)).one("input", name="symbol")
    assert symbol["aria-describedby"] == (
        "id_symbol_helptext id_symbol_currency_hint id_symbol_error"
    )
    assert symbol["aria-invalid"] == "true"
    assert copy.SYMBOL_CURRENCY_HINT.split(";")[0] in Doc(body(resp)).text  # still shown


@pytest.mark.parametrize("side", ["", "up", "buy"])
def test_ac_4_side_has_no_default_and_only_long_or_short(logged_in, side):
    resp = post_add(logged_in, **{**AAPL, "side": side})

    assert field_error(resp, "side") == copy.SIDE_REQUIRED


def test_ac_7_exit_pair_is_both_or_neither(logged_in, user):
    no_time = post_add(logged_in, **{**AAPL, "exit_price": "105"})
    no_price = post_add(logged_in, **{**AAPL, "exit_time": "2026-03-02 10:00"})
    bad_time = post_add(logged_in, **{**AAPL, "exit_time": "soon"})

    assert errors_of(no_time) == {"exit_time": [copy.EXIT_TIME_NEEDED]}
    assert errors_of(no_price) == {"exit_price": [copy.EXIT_PRICE_NEEDED]}
    assert errors_of(bad_time) == {"exit_time": [copy.TIME_INVALID]}  # one error per field
    assert not Execution.unscoped.exists()


def test_ac_8_exit_before_entry_is_an_error_on_the_exit_naming_the_entry(logged_in):
    resp = post_add(logged_in, **{**CLOSED, "exit_time": "2026-03-02 09:29"})

    message = copy.EXIT_BEFORE_ENTRY.format(entry="Mar 2, 2026, 9:30 AM")
    assert errors_of(resp) == {"exit_time": [message]}
    assert field_error(resp, "exit_time") == message
    assert not Execution.unscoped.exists()


@pytest.mark.parametrize("entry, exit_", [
    ("2026-03-08 01:45", "2026-03-08 03:15"),  # 06:45Z -> 07:15Z, 30 minutes across the gap
    ("2026-11-01 00:45", "2026-11-01 02:15"),  # 04:45Z -> 07:15Z, 2h30 across the overlap
])
def test_vector_34_exit_versus_entry_is_compared_in_utc(logged_in, user, entry, exit_):
    add(logged_in, **{**CLOSED, "entry_time": entry, "exit_time": exit_})

    assert fills(user)[1].executed_at.minute == 15


@pytest.mark.parametrize("value, error", [
    ("0", copy.QUANTITY_NOT_POSITIVE), ("-1", copy.QUANTITY_NOT_POSITIVE),
    ("abc", copy.QUANTITY_NOT_NUMBER), ("0.00000000001", copy.QUANTITY_DECIMALS),
    ("12345678901", copy.NUMBER_TOO_LARGE),
    pytest.param("9" * 100_000, copy.NUMBER_TOO_LARGE, id="huge"),
])
def test_ac_9_10_quantity_errors(logged_in, value, error):
    assert errors_of(post_add(logged_in, **{**AAPL, "quantity": value})) == {"quantity": [error]}


@pytest.mark.parametrize("value, stored", [
    ("0.5", "0.5"), ("0.0000000001", "0.0000000001"), ("1,234.50", "1234.5"),
])
def test_ac_9_10_13_quantity_saves_exactly(logged_in, user, value, stored):
    add(logged_in, **{**AAPL, "quantity": value})

    assert fills(user)[0].quantity == Decimal(stored)


@pytest.mark.parametrize("name", ["entry_price", "exit_price"])
@pytest.mark.parametrize("value, error", [
    ("0", copy.PRICE_ZERO), ("-0.00", copy.PRICE_ZERO), ("abc", copy.PRICE_NOT_NUMBER),
    ("1.12345678901", copy.PRICE_DECIMALS), ("1" * 25, copy.NUMBER_TOO_LARGE),
])
def test_ac_9_10_price_errors(logged_in, name, value, error):
    resp = post_add(logged_in, **{**CLOSED, name: value})

    assert errors_of(resp) == {name: [error]}


def test_ac_9_negative_prices_save(logged_in, user):
    add(logged_in, **{**CLOSED, "entry_price": "-37.63", "exit_price": "-30"})

    assert [f.price for f in fills(user)] == [Decimal("-37.63"), Decimal("-30")]


@pytest.mark.parametrize("value, error", [
    ("A" * 33, copy.SYMBOL_TOO_LONG), ("ß" * 17, copy.SYMBOL_TOO_LONG),  # 34 once upper
    ("   ", copy.SYMBOL_REQUIRED),
])
def test_ac_11_symbol_errors(logged_in, value, error):
    resp = post_add(logged_in, **{**AAPL, "symbol": value, "pv_for": value})

    assert errors_of(resp) == {"symbol": [error]}
    assert copy.POINT_VALUE_PENDING in Doc(body(resp)).text


def test_ac_11_symbol_is_stored_trimmed_and_uppercase(logged_in, user):
    add(logged_in, **{**AAPL, "symbol": "  aapl "})

    assert fills(user)[0].symbol == "AAPL"


def test_ac_12_50_posted_currency_owner_source_and_keys_are_ignored(logged_in, user, other):
    user.base_currency = "EUR"
    user.save()
    add(logged_in, **CLOSED, currency="EUR", user=other.pk, source="import", broker="topstep",
        broker_trade_id="9000000001", broker_execution_id="x:entry")

    assert {(f.currency, f.user_id, f.source, f.broker, f.broker_execution_id)
            for f in fills(user)} == {("USD", user.pk, "manual", "manual", None)}
    assert KEY.fullmatch(fills(user)[0].broker_trade_id)
    assert not Execution.objects.for_user(other).exists()


def test_ac_13_fees_round_trip_as_exact_decimals(logged_in, user):
    add(logged_in, **{**CLOSED, "fees": "1.5"})

    assert str(fills(user)[1].fees) == "1.5000"


# --- Double submit and the signed token (AC 14, ADR-0008 section 4) ------------------------------


def test_ac_14_a_replayed_token_returns_the_same_redirect_and_no_second_trade(logged_in, user):
    token = fresh_token(logged_in)
    first = post_add(logged_in, token, **CLOSED)
    again = post_add(logged_in, token, **{**CLOSED, "quantity": "99"})

    assert first.status_code == again.status_code == 302
    assert first["Location"] == again["Location"]
    assert len(fills(user)) == 2 and fills(user)[0].quantity == Decimal("10")
    assert copy.TRADE_ADDED in body(logged_in.get(again["Location"]))


def test_ac_14_two_rendered_forms_with_identical_content_make_two_trades(logged_in, user):
    add(logged_in, **AAPL)
    add(logged_in, **AAPL)

    assert len({f.broker_trade_id for f in fills(user)}) == 2


@pytest.mark.parametrize(
    "token", ["", "abc", "a" * 32, pytest.param("x" * 10_000, id="huge"), "forged:sig"]
)
def test_a_forged_or_missing_token_writes_nothing_and_says_save_failed(logged_in, token):
    resp = logged_in.post(ADD, {**AAPL, "form_token": token})

    assert resp.status_code == 200 and not Execution.unscoped.exists()
    assert copy.MANUAL_SAVE_FAILED in text(resp)
    fresh = token_of(resp)
    assert fresh != token and logged_in.post(ADD, {**AAPL, "form_token": fresh}).status_code == 302


def test_another_users_token_is_refused(client, user, other):
    client.force_login(other)
    foreign = fresh_token(client)
    client.force_login(user)

    resp = client.post(ADD, {**AAPL, "form_token": foreign})

    assert resp.status_code == 200 and not Execution.unscoped.exists()


def test_invalid_submit_then_valid_submit_makes_exactly_one_trade(logged_in, user):
    token = fresh_token(logged_in)
    bad = post_add(logged_in, token, **{**AAPL, "side": ""})
    assert token_of(bad) == token  # a validation re-render keeps a valid token

    assert post_add(logged_in, token_of(bad), **AAPL).status_code == 302
    assert post_add(logged_in, token_of(bad), **AAPL).status_code == 302  # replay
    assert len(fills(user)) == 1


# --- Save failure (AC 15) ------------------------------------------------------------------------


@pytest.mark.parametrize("error", [DatabaseError("boom"), CrossTenantForeignKeyError("x")])
def test_ac_15_save_failure_re_renders_with_values_and_notice(logged_in, caplog, error):
    with patch("journal.views.add_manual_trade", side_effect=error):
        with caplog.at_level(logging.ERROR, logger="journal.views"):
            resp = post_add(logged_in, **AAPL)

    assert resp.status_code == 200
    assert copy.MANUAL_SAVE_FAILED in text(resp)
    assert Doc(body(resp)).one("input", name="symbol")["value"] == "AAPL"
    assert "boom" not in caplog.text and type(error).__name__ in caplog.text


def test_ac_15_a_real_database_error_rolls_back_both_fills(logged_in, user):
    real = Execution.objects.create
    calls = []

    def second_insert_fails(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise DatabaseError("exit insert")
        return real(**kwargs)

    with patch.object(Execution.objects, "create", side_effect=second_insert_fails):
        resp = post_add(logged_in, **CLOSED)

    assert resp.status_code == 200 and not Execution.unscoped.exists()


# --- Point value (AC 16-19) ----------------------------------------------------------------------


def test_ac_16_known_root_is_read_only_on_an_error_re_render(logged_in):
    resp = post_add(logged_in, **{**CLOSED, "symbol": "CLZ6", "side": ""})

    d = Doc(body(resp))
    assert not d.all("input", name="point_value") and not d.all("input", name="pv_for")
    assert re.search(r"<dt[^>]*>Point value</dt><dd[^>]*>1,000</dd>", body(resp))
    assert copy.POINT_VALUE_KNOWN_HELP.format(root="CL") in text(resp)


@pytest.mark.parametrize("symbol", ["AAPL", "ESZ6", "MNQZ6"])
def test_ac_17_unknown_root_asks_for_a_required_empty_input(logged_in, symbol):
    resp = post_add(logged_in, **{**AAPL, "symbol": symbol, "point_value": "", "pv_for": ""})

    needed = copy.POINT_VALUE_NEEDED.format(symbol=symbol)
    assert errors_of(resp) == {"point_value": [needed]}
    d = Doc(body(resp))
    pv = d.one("input", name="point_value")
    assert pv["aria-required"] == "true" and "value" not in pv and "placeholder" not in pv
    assert d.one("input", name="pv_for")["value"] == symbol
    assert d.all("a", href="#id_point_value")


@pytest.mark.parametrize("value, error", [
    ("0", copy.POINT_VALUE_NOT_POSITIVE), ("-1", copy.POINT_VALUE_NOT_POSITIVE),
    ("x", copy.POINT_VALUE_NOT_NUMBER), ("1.12345678901", copy.POINT_VALUE_DECIMALS),
    ("1" * 25, copy.NUMBER_TOO_LARGE),
])
def test_ac_17_point_value_errors(logged_in, value, error):
    resp = post_add(logged_in, **{**AAPL, "point_value": value})

    assert errors_of(resp) == {"point_value": [error]}


def test_point_value_typed_for_another_symbol_is_dropped(logged_in, user):
    resp = post_add(logged_in, **{**AAPL, "symbol": "ESZ6", "point_value": "1", "pv_for": "AAPL"})

    assert errors_of(resp) == {"point_value": [copy.POINT_VALUE_NEEDED.format(symbol="ESZ6")]}
    assert "value" not in Doc(body(resp)).one("input", name="point_value")
    assert not Execution.unscoped.exists()


def test_ac_19_switching_to_a_known_root_uses_the_lookup(logged_in, user):
    add(logged_in, **{**AAPL, "symbol": "CLZ6", "point_value": "1", "pv_for": "AAPL"})

    assert fills(user)[0].contract_multiplier == Decimal("1000")


def test_ac_18_a_root_added_to_multipliers_is_known_with_no_other_change(logged_in, user):
    with patch.dict(MULTIPLIERS, {"ZZZ": Decimal("25")}):
        add(logged_in, **{**AAPL, "symbol": "ZZZH7", "point_value": "", "pv_for": ""})

    assert fills(user)[0].contract_multiplier == Decimal("25")


# --- Time zone and time rules (AC 20-22, vector 34) ---------------------------------------------


@pytest.mark.parametrize("typed, utc", [
    ("2026-03-02 09:30", datetime(2026, 3, 2, 14, 30, tzinfo=UTC)),
    ("2026-03-02T09:30", datetime(2026, 3, 2, 14, 30, tzinfo=UTC)),
    ("2026-03-09 09:30", datetime(2026, 3, 9, 13, 30, tzinfo=UTC)),
    ("2026-03-08 01:59", datetime(2026, 3, 8, 6, 59, tzinfo=UTC)),
    ("2026-03-08 03:00", datetime(2026, 3, 8, 7, 0, tzinfo=UTC)),
    ("2026-11-01 00:59", datetime(2026, 11, 1, 4, 59, tzinfo=UTC)),
    ("2026-11-01 02:00", datetime(2026, 11, 1, 7, 0, tzinfo=UTC)),
])
def test_vector_34_local_time_saves_as_the_stated_utc_instant(logged_in, user, typed, utc):
    add(logged_in, **{**AAPL, "entry_time": typed})

    assert fills(user)[0].executed_at == utc


@pytest.mark.parametrize("typed", [
    "2026-03-08 02:00", "2026-03-08 02:30", "2026-03-08 02:59",  # nonexistent
    "2026-11-01 01:00", "2026-11-01 01:30", "2026-11-01 01:59",  # ambiguous
])
def test_ac_21_dst_gap_and_overlap_are_rejected_never_picked(logged_in, typed):
    resp = post_add(logged_in, **{**AAPL, "entry_time": typed})

    message = copy.TIME_NOT_READABLE.format(zone="America/New_York")
    assert errors_of(resp) == {"entry_time": [message]}
    assert not Execution.unscoped.exists()


def test_ac_20_a_utc_user_stores_the_typed_time_as_utc(client, other):
    client.force_login(other)
    add(client, **AAPL)

    assert fills(other)[0].executed_at == datetime(2026, 3, 2, 9, 30, tzinfo=UTC)


@pytest.mark.parametrize("typed, ok", [
    ("2027-01-15 07:05", True),  # 12:05Z = now + the 5-minute grace
    ("2027-01-15 07:06", False),
])
def test_ac_22_future_times_are_rejected_after_five_minutes_of_grace(logged_in, typed, ok):
    resp = post_add(logged_in, **{**AAPL, "entry_time": typed})

    if ok:
        assert resp.status_code == 302
    else:
        message = copy.TIME_FUTURE.format(zone="America/New_York")
        assert errors_of(resp) == {"entry_time": [message]}


@pytest.mark.parametrize("typed", [
    "2026-03-02T09:30:00", "2026-03-02T09:30Z", "2026-03-02T09:30+00:00", "March 2",
    pytest.param("1" * 10_000, id="huge"),
])
def test_only_the_two_minute_formats_parse(logged_in, typed):
    resp = post_add(logged_in, **{**AAPL, "entry_time": typed})

    assert errors_of(resp) == {"entry_time": [copy.TIME_INVALID]}


def test_year_9999_shifted_past_utc_is_a_future_time_not_invalid(logged_in):
    resp = post_add(logged_in, **{**AAPL, "entry_time": "9999-12-31 23:59"})  # +5h overflows

    assert errors_of(resp) == {"entry_time": [copy.TIME_FUTURE.format(zone="America/New_York")]}


def test_year_one_shifted_past_utc_is_a_field_error_not_a_500(client, db):
    tokyo = User.objects.create_user(email="t@example.com", password="x", timezone="Asia/Tokyo")
    client.force_login(tokyo)

    resp = post_add(client, **{**AAPL, "entry_time": "0001-01-01 00:00"})

    assert errors_of(resp) == {"entry_time": [copy.TIME_TOO_OLD]}


# --- More details: Fees and Account (AC 27-31) ---------------------------------------------------


@pytest.mark.parametrize("fees", ["0", "2.00"])
def test_ac_29_fees_with_no_exit_is_an_error_and_opens_more_details(logged_in, fees):
    resp = post_add(logged_in, **{**AAPL, "fees": fees})

    assert errors_of(resp) == {"fees": [copy.FEES_NEEDS_EXIT]}
    assert re.search(r'<details id="more-details"[^>]* open', body(resp))
    assert re.search(r'<details id="more-details".*id="id_fees_error".*</details>', body(resp),
                     re.S)
    assert not Execution.unscoped.exists()


@pytest.mark.parametrize("value, error", [
    ("-1", copy.FEES_NEGATIVE), ("1.00001", copy.FEES_DECIMALS), ("x", copy.FEES_NOT_NUMBER),
    ("1" * 16, copy.NUMBER_TOO_LARGE),
])
def test_ac_28_fee_errors(logged_in, value, error):
    assert errors_of(post_add(logged_in, **{**CLOSED, "fees": value})) == {"fees": [error]}


def test_ac_28_blank_fees_store_0(logged_in, user):
    add(logged_in, **{**CLOSED, "fees": ""})

    assert [f.fees for f in fills(user)] == [Decimal("0"), Decimal("0")]


def test_ac_30_account_is_trimmed_on_every_fill(logged_in, user):
    add(logged_in, **{**CLOSED, "account": "  Combine 50K "})

    assert {f.broker_account_label for f in fills(user)} == {"Combine 50K"}


@pytest.mark.parametrize("value, error", [
    ("a" * 65, copy.ACCOUNT_TOO_LONG % {"show_value": 65, "limit_value": 64}),
    ("a\tb", copy.ACCOUNT_CONTROL_CHARACTERS),
])
def test_ac_30_account_errors(logged_in, value, error):
    resp = post_add(logged_in, **{**AAPL, "account": value})

    assert errors_of(resp) == {"account": [error]}
    assert re.search(r'<details id="more-details"[^>]* open', body(resp))
    account = Doc(body(resp)).one("input", name="account")
    assert account["aria-describedby"] == "id_account_helptext id_account_error"


def test_ac_31_51_suggestions_span_brokers_and_never_another_users(logged_in, user, other):
    import_file(user, "t1.csv", csv_bytes(T1), "Combine 50K")
    with patch("django.utils.timezone.now", return_value=NOW + timedelta(minutes=1)):
        add(logged_in, **{**AAPL, "account": "Main"})  # used more recently
    add_manual_trade(other, "c" * 32, symbol="AAPL", side="long", quantity=Decimal(1),
                     entry_price=Decimal(1), entry_time=NOW, exit_price=None, exit_time=None,
                     fees=Decimal(0), account="Theirs", contract_multiplier=Decimal(1))

    d = Doc(body(logged_in.get(ADD)))
    assert [o["value"] for o in d.all("option")] == ["Main", "Combine 50K"]
    account = d.one("input", name="account")
    assert account["list"] == "account-labels" and "value" in account and not account["value"]
    assert account["placeholder"] == copy.ACCOUNT_PLACEHOLDER_SAVED


def test_account_placeholder_without_saved_labels(logged_in):
    account = Doc(body(logged_in.get(ADD))).one("input", name="account")

    assert account["placeholder"] == copy.MANUAL_ACCOUNT_PLACEHOLDER


# --- Requirement 1: every Execution constraint maps to a field error ----------------------------

CONSTRAINT_FIELD_ERRORS = {  # constraint -> (posted change, field, message); None = not postable
    "execution_broker_dedupe": None,  # broker_execution_id is never written (NULL)
    "execution_side_valid": ({"side": "sideways"}, "side", copy.SIDE_REQUIRED),
    "execution_quantity_positive": ({"quantity": "0"}, "quantity", copy.QUANTITY_NOT_POSITIVE),
    "execution_price_not_zero": ({"entry_price": "0"}, "entry_price", copy.PRICE_ZERO),
    "execution_source_valid": None,  # a constant in the service
    "execution_contract_multiplier_positive": (
        {"point_value": "0"}, "point_value", copy.POINT_VALUE_NOT_POSITIVE,
    ),
    "execution_currency_iso_format": None,  # a constant in the service (USD)
    "execution_broker_trade_id_not_blank": None,  # from the signed 32-hex nonce only
    "execution_broker_account_label_trimmed": None,  # trimmed by the form; see the test below
    "execution_import_requires_broker_execution_id": None,  # source is always manual
}


def test_the_constraint_map_lists_every_execution_constraint():
    names = {c.name for c in Execution._meta.constraints}

    assert names == set(CONSTRAINT_FIELD_ERRORS), "a new constraint needs a field-level check"


@pytest.mark.parametrize("name", [n for n, v in CONSTRAINT_FIELD_ERRORS.items() if v])
def test_each_postable_constraint_is_a_field_error_not_a_save_failure(logged_in, name):
    change, field, message = CONSTRAINT_FIELD_ERRORS[name]

    resp = post_add(logged_in, **{**CLOSED, **change})

    assert resp.status_code == 200 and errors_of(resp) == {field: [message]}
    assert field_error(resp, field) == message


@pytest.mark.parametrize("change, field, message", [
    ({"exit_price": "0"}, "exit_price", copy.PRICE_ZERO),
    ({"symbol": "A" * 33, "pv_for": "A" * 33}, "symbol", copy.SYMBOL_TOO_LONG),  # VARCHAR(32)
    ({"account": "a" * 65}, "account",  # VARCHAR(64)
     copy.ACCOUNT_TOO_LONG % {"show_value": 65, "limit_value": 64}),
    ({"quantity": "1" * 11}, "quantity", copy.NUMBER_TOO_LARGE),  # NUMERIC(20,10)
    ({"entry_price": "1" * 11}, "entry_price", copy.NUMBER_TOO_LARGE),
    ({"point_value": "1" * 11}, "point_value", copy.NUMBER_TOO_LARGE),
    ({"fees": "1" * 16}, "fees", copy.NUMBER_TOO_LARGE),  # NUMERIC(19,4)
])
def test_column_limits_are_field_errors_too(logged_in, change, field, message):
    resp = post_add(logged_in, **{**CLOSED, **change})

    assert errors_of(resp) == {field: [message]}


def test_label_with_outer_whitespace_passes_the_trimmed_check(logged_in, user):
    add(logged_in, **{**AAPL, "account": "  Main \t"})  # Python strip covers Postgres TRIM

    assert fills(user)[0].broker_account_label == "Main"


def test_a_valid_add_never_shows_model_validation_messages(logged_in):
    resp = post_add(logged_in, **CLOSED)

    assert resp.status_code == 302


# --- Close (AC 32-36) ----------------------------------------------------------------------------


def close_url(pk):
    return f"/trades/{pk}/close/"


EXIT = {"exit_price": "105.00", "exit_time": "2026-03-02 10:00", "fees": "2.00"}


def test_close_get_shows_size_and_label_read_only_and_only_exit_inputs(logged_in, user):
    pk = add(logged_in, **{**AAPL, "account": "Main"})

    resp = logged_in.get(close_url(pk))

    assert resp.status_code == 200
    markup = body(resp)
    d = Doc(markup)
    assert {a["name"] for a in d.all("input") if a.get("name")} == {
        "csrfmiddlewaretoken", "exit_price", "exit_time", "fees",
    }
    assert re.search(r"<dt[^>]*>Size</dt><dd>10 \(the whole position\)</dd>", markup)
    assert "Main" in d.text and copy.CLOSE_INTRO in d.text
    assert "<title>Close AAPL" in markup
    assert all(d.one("input", name=n)["aria-required"] == "true"
               for n in ("exit_price", "exit_time"))
    assert re.search(r'<details id="more-details"[^>]*>', markup)[0].find(" open") == -1
    assert "id_symbol_currency_hint" not in markup and copy.SYMBOL_CURRENCY_HINT not in d.text
    assert d.one("form", action=close_url(pk))["method"] == "post"
    assert d.all("a", href=f"/trades/{pk}/journal/")  # back and cancel


def test_close_answers_head(logged_in, user):
    pk = add(logged_in, **AAPL)

    assert logged_in.head(close_url(pk)).status_code == 200


def test_ac_34_close_adds_one_full_size_exit_copied_from_the_entry(logged_in, user):
    pk = add(logged_in, **{**AAPL, "symbol": "CLZ6", "account": "Main"})
    entry = fills(user)[0]

    resp = logged_in.post(close_url(pk), {**EXIT, "quantity": "1", "side": "long",
                                          "symbol": "X", "account": "Other"})

    assert resp.status_code == 302 and resp["Location"] == f"/trades/{pk}/journal/"
    first, exit_ = fills(user)
    assert first.pk == entry.pk
    same = ("symbol", "broker_account_label", "currency", "contract_multiplier",
            "broker_trade_id", "quantity", "source", "broker", "broker_execution_id")
    assert {n: getattr(exit_, n) for n in same} == {n: getattr(entry, n) for n in same}
    assert (exit_.side, exit_.price, exit_.fees) == ("sell", Decimal("105.00"), Decimal("2.00"))


def test_close_blank_fees_store_0_and_required_exit_fields(logged_in, user):
    pk = add(logged_in, **AAPL)

    blank = logged_in.post(close_url(pk), {})
    assert errors_of(blank) == {
        "exit_price": [copy.EXIT_PRICE_REQUIRED], "exit_time": [copy.EXIT_TIME_REQUIRED],
    }
    logged_in.post(close_url(pk), {**EXIT, "fees": ""})
    assert fills(user)[1].fees == Decimal("0")


@pytest.mark.parametrize("change, field, message", [
    ({"exit_price": "0"}, "exit_price", copy.PRICE_ZERO),
    ({"fees": "-1"}, "fees", copy.FEES_NEGATIVE),
    ({"exit_time": "2027-02-01 10:00"}, "exit_time",
     copy.TIME_FUTURE.format(zone="America/New_York")),
    ({"exit_time": "2026-03-08 02:30"}, "exit_time",
     copy.TIME_NOT_READABLE.format(zone="America/New_York")),
])
def test_close_field_errors(logged_in, user, change, field, message):
    pk = add(logged_in, **AAPL)

    resp = logged_in.post(close_url(pk), {**EXIT, **change})

    assert resp.status_code == 200 and errors_of(resp) == {field: [message]}
    assert len(fills(user)) == 1


def test_ac_35_close_exit_before_entry_names_the_entry_and_equal_is_fine(logged_in, user):
    pk = add(logged_in, **AAPL)

    early = logged_in.post(close_url(pk), {**EXIT, "exit_time": "2026-03-02 09:29"})
    message = copy.EXIT_BEFORE_ENTRY.format(entry="Mar 2, 2026, 9:30 AM")
    assert errors_of(early) == {"exit_time": [message]} and len(fills(user)) == 1
    assert logged_in.post(close_url(pk), {**EXIT, "exit_time": "2026-03-02 09:30"})[
        "Location"] == f"/trades/{pk}/journal/"


def test_ac_36_closing_twice_writes_one_exit_and_says_already_closed(logged_in, user):
    pk = add(logged_in, **AAPL)
    logged_in.post(close_url(pk), EXIT)

    again = logged_in.post(close_url(pk), {**EXIT, "exit_price": "110"})
    get = logged_in.get(close_url(pk))

    for resp in (again, get):
        assert resp.status_code == 302 and resp["Location"] == f"/trades/{pk}/journal/"
    assert len(fills(user)) == 2 and fills(user)[1].price == Decimal("105.00")
    page = body(logged_in.get(f"/trades/{pk}/journal/"))
    assert copy.ALREADY_CLOSED in page


def test_close_service_race_already_closed_redirects(logged_in, user):
    pk = add(logged_in, **AAPL)

    with patch("journal.views.close_manual_trade", side_effect=AlreadyClosed):
        resp = logged_in.post(close_url(pk), EXIT)

    assert resp.status_code == 302 and resp["Location"] == f"/trades/{pk}/journal/"


def test_close_service_exit_before_entry_becomes_a_field_error(logged_in, user):
    pk = add(logged_in, **AAPL)
    moved = datetime(2026, 3, 2, 15, 30, tzinfo=UTC)  # 10:30 New York: another tab's edit

    with patch("journal.views.close_manual_trade", side_effect=ExitBeforeEntry(moved)):
        resp = logged_in.post(close_url(pk), EXIT)

    message = copy.EXIT_BEFORE_ENTRY.format(entry="Mar 2, 2026, 10:30 AM")
    assert resp.status_code == 200 and errors_of(resp) == {"exit_time": [message]}


@pytest.mark.parametrize("error", [DatabaseError("boom"), CrossTenantForeignKeyError("x")])
def test_close_save_failure_re_renders(logged_in, user, error):
    pk = add(logged_in, **AAPL)

    with patch("journal.views.close_manual_trade", side_effect=error):
        resp = logged_in.post(close_url(pk), EXIT)

    assert resp.status_code == 200
    assert copy.MANUAL_SAVE_FAILED in text(resp)
    assert Doc(body(resp)).one("input", name="exit_price")["value"] == "105.00"


def test_close_link_shows_only_on_open_keyed_manual_trades(logged_in, user):
    open_pk = add(logged_in, **AAPL)
    closed_pk = add(logged_in, **{**CLOSED, "symbol": "MSFT", "pv_for": "MSFT"})
    import_t1(user)
    imported = Execution.objects.for_user(user).filter(source="import").order_by("pk")[0].pk
    null_key = Execution.objects.create(
        user=user, broker="manual", source="manual", symbol="NULLK", side="buy",
        quantity=1, price=1, currency="USD", executed_at=NOW,
    ).pk

    page = body(logged_in.get(f"/trades/{open_pk}/journal/"))
    group = re.search(r'<div role="group" aria-label="Trade actions"[^>]*>(.*?)</div>', page)
    assert group and f'href="{close_url(open_pk)}"' in group[1] and copy.TRADE_CLOSE in group[1]
    for pk in (closed_pk, imported, null_key):
        other_page = body(logged_in.get(f"/trades/{pk}/journal/"))
        assert "/close/" not in other_page and 'aria-label="Trade actions"' not in other_page


def test_close_link_survives_the_no_js_rules_re_render(logged_in, user):
    pk = add(logged_in, **AAPL)

    resp = logged_in.post("/rules/", {"trading_rules": "x" * 10_001,
                                      "next": f"/trades/{pk}/journal/"})

    assert resp.status_code == 200 and f'href="{close_url(pk)}"' in body(resp)


# --- Isolation and the one 404 (AC 42, 49, 50; ADR-0008 section 9) -----------------------------


def snapshot():
    return sorted(Execution.unscoped.values_list(
        "pk", "user_id", "price", "quantity", "fees", "executed_at", "symbol",
    ))


def test_close_on_foreign_missing_imported_closing_and_null_key_ids_is_the_one_404(
    logged_in, user, other, client
):
    theirs = add_manual_trade(other, "d" * 32, symbol="AAPL", side="long",
                              quantity=Decimal(1), entry_price=Decimal(1), entry_time=NOW,
                              exit_price=None, exit_time=None, fees=Decimal(0), account="",
                              contract_multiplier=Decimal(1))
    import_t1(user)
    imported = Execution.objects.for_user(user).filter(source="import").order_by("pk")
    mine_closed = add(logged_in, **CLOSED)
    closing_fill = fills(user)[-1].pk
    null_key = Execution.objects.create(
        user=user, broker="manual", source="manual", symbol="NULLK", side="buy",
        quantity=1, price=1, currency="USD", executed_at=NOW,
    ).pk
    missing = Execution.unscoped.order_by("-pk")[0].pk + 100
    before = snapshot()
    reference = logged_in.get(close_url(missing))

    assert mine_closed
    for pk in (theirs, imported[0].pk, imported[1].pk, closing_fill, null_key, missing):
        for resp in (logged_in.get(close_url(pk)), logged_in.post(close_url(pk), EXIT)):
            assert resp.status_code == 404, pk
            assert [t.name for t in resp.templates][0] == "journal/trade_not_found.html"
            assert Doc(body(resp)).text.count(copy.NOT_FOUND_TITLE) == Doc(
                body(reference)
            ).text.count(copy.NOT_FOUND_TITLE)
    assert snapshot() == before


def test_not_found_body_is_the_reworded_copy(logged_in):
    resp = logged_in.get(close_url(999_999))

    assert copy.NOT_FOUND_BODY == "It may have been deleted, or removed along with an import."
    assert copy.NOT_FOUND_BODY in Doc(body(resp)).text


# --- _manual_fills, the one write gate (requirement 6) and the services -------------------------


def test_manual_fills_refuses_everything_but_a_keyed_manual_opener(logged_in, user, other,
                                                                   caplog):
    open_pk = add(logged_in, **AAPL)
    closed_pk = add(logged_in, **{**CLOSED, "symbol": "MSFT", "pv_for": "MSFT"})
    closing = fills(user)[-1].pk
    import_t1(user)
    imported = Execution.objects.for_user(user).filter(source="import").order_by("pk")[0].pk
    null_key = Execution.objects.create(
        user=user, broker="manual", source="manual", symbol="NULLK", side="buy",
        quantity=1, price=1, currency="USD", executed_at=NOW,
    ).pk
    three = add(logged_in, **{**CLOSED, "symbol": "TSLA", "pv_for": "TSLA"})
    extra = fills(user)[-1]
    Execution.objects.create(
        user=user, **{n: getattr(extra, n) for n in (
            "broker", "source", "broker_trade_id", "symbol", "side", "quantity", "price",
            "currency", "executed_at",
        )},
    )

    with transaction.atomic(), caplog.at_level(logging.ERROR, logger="journal.services"):
        assert [f.pk for f in _manual_fills(user, open_pk)] == [open_pk]
        assert [f.pk for f in _manual_fills(user, closed_pk)] == [closed_pk, closed_pk + 1]
        for pk in (closing, imported, null_key, three, 999_999):
            assert _manual_fills(user, pk) is None, pk
        assert _manual_fills(other, open_pk) is None
    assert "3 fills" in caplog.text and "manual:" not in caplog.text


def test_close_service_refuses_imported_and_already_closed(logged_in, user):
    import_t1(user)
    imported = Execution.objects.for_user(user).filter(source="import").order_by("pk")[0]
    closed_pk = add(logged_in, **{**CLOSED, "symbol": "MSFT", "pv_for": "MSFT"})
    before = snapshot()
    exit_args = {"exit_price": Decimal(1), "exit_time": NOW, "fees": Decimal(0)}

    assert close_manual_trade(user, imported.pk, **exit_args) is None
    with pytest.raises(AlreadyClosed):
        close_manual_trade(user, closed_pk, **exit_args)
    imported.refresh_from_db()
    assert snapshot() == before and imported.price == Decimal("80.00")


def test_close_service_rechecks_exit_before_entry_under_the_lock(logged_in, user):
    pk = add(logged_in, **AAPL)

    with pytest.raises(ExitBeforeEntry) as raised:
        close_manual_trade(user, pk, exit_price=Decimal(1), fees=Decimal(0),
                           exit_time=datetime(2026, 3, 2, 14, 29, tzinfo=UTC))
    assert raised.value.args[0] == datetime(2026, 3, 2, 14, 30, tzinfo=UTC)
    assert len(fills(user)) == 1


def test_add_service_replay_returns_the_first_trade(user):
    args = {"symbol": "AAPL", "side": "long", "quantity": Decimal(1), "entry_price": Decimal(1),
            "entry_time": NOW, "exit_price": Decimal(2), "exit_time": NOW, "fees": Decimal(0),
            "account": "", "contract_multiplier": Decimal(1)}

    first = add_manual_trade(user, "e" * 32, **args)
    again = add_manual_trade(user, "e" * 32, **{**args, "quantity": Decimal(9)})

    assert first == again and len(fills(user)) == 2
    assert fills(user)[0].broker_trade_id == "manual:" + "e" * 32


def test_forms_are_user_scoped_and_expose_no_owner_or_key_field():
    for form in (ManualTradeForm, CloseTradeForm):
        assert issubclass(form, UserScopedModelForm)
        assert not {"user", "source", "broker", "currency", "broker_trade_id",
                    "broker_execution_id"} & set(form.base_fields)


# --- Point-value endpoint (design 5.4) -----------------------------------------------------------


def test_point_value_logged_out_is_a_bare_401_without_hx_redirect(client, db):
    for headers in ({}, {"HX-Request": "true"}):
        resp = client.get(PV, {"symbol": "CLZ6"}, headers=headers)
        assert resp.status_code == 401 and "HX-Redirect" not in resp and resp.content == b""


def test_point_value_known_unknown_and_pending(logged_in):
    known = body(logged_in.get(PV, {"symbol": " clz6 "}))
    unknown = body(logged_in.get(PV, {"symbol": "aapl"}))
    pending = body(logged_in.get(PV, {"symbol": ""}))
    too_long = body(logged_in.get(PV, {"symbol": "A" * 10_000}))

    assert re.search(r"<dd[^>]*>1,000</dd>", known) and 'name="point_value"' not in known
    live = r'<p id="point-value-live" role="status" class="sr-only" hx-swap-oob="innerHTML">'
    assert re.search(live + re.escape("Point value for CL is 1,000.") + "</p>", known)
    pv = Doc(unknown).one("input", name="point_value")
    assert pv["aria-required"] == "true" and "value" not in pv
    assert Doc(unknown).one("input", name="pv_for")["value"] == "AAPL"
    assert "Enter one below" in unknown and "id_point_value_error" not in unknown
    for markup in (pending, too_long):
        assert copy.POINT_VALUE_PENDING in html.unescape(markup)
        assert re.search(live + "</p>", markup)


def test_point_value_makes_no_query_of_its_own(logged_in):
    logged_in.get(PV, {"symbol": "CLZ6"})  # warm the session

    with CaptureQueriesContext(connection) as queries:
        resp = logged_in.get(PV, {"symbol": "AAPL"})

    assert resp.status_code == 200
    assert not [q for q in queries.captured_queries if "journal_" in q["sql"]]
    assert len(queries) <= 2  # the session and the user, loaded by middleware


def test_point_value_refuses_post_and_survives_a_garbage_query(logged_in):
    assert logged_in.post(PV, {"symbol": "CLZ6"}).status_code == 405
    garbage = "&".join(f"k{i}=v" for i in range(1500))
    assert logged_in.get(f"{PV}?{garbage}").status_code == 200


# --- Query counts (ADR-0008 section 8) ----------------------------------------------------------


def _count(client, method, path, data=None):
    with CaptureQueriesContext(connection) as queries:
        resp = getattr(client, method)(path, data or {})
    return resp, len(queries)


def test_add_post_runs_in_a_constant_number_of_queries(logged_in, user):
    token = fresh_token(logged_in)
    _, first = _count(logged_in, "post", ADD, {**CLOSED, "form_token": token})
    for _ in range(5):
        add(logged_in, **CLOSED)
    _, later = _count(logged_in, "post", ADD, {**CLOSED, "form_token": fresh_token(logged_in)})

    assert first == later


def test_trades_list_count_is_constant_with_manual_trades(logged_in, user):
    add(logged_in, **CLOSED)
    _, one = _count(logged_in, "get", "/trades/")
    for _ in range(3):
        add(logged_in, **CLOSED)
    _, four = _count(logged_in, "get", "/trades/")

    assert one == four


# --- Templates: no unsafe output -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["trade_form.html", "partials/point_value.html"])
def test_new_templates_have_no_safe_filter_inline_handlers_or_scripts(name):
    from django.conf import settings

    src = (settings.BASE_DIR / "templates" / "journal" / name).read_text(encoding="utf-8")
    assert "|safe" not in src and "autoescape off" not in src
    assert not re.search(r"\son[a-z]+\s*=", src, re.I) and "<script" not in src


def test_symbol_and_label_are_escaped_on_the_close_page(logged_in, user):
    pk = add(logged_in, **{**AAPL, "symbol": "<i>x", "pv_for": "<I>X",
                           "account": "<b>acct"})

    markup = body(logged_in.get(close_url(pk)))
    assert "<I>X" not in markup and "&lt;I&gt;X" in markup and "&lt;b&gt;acct" in markup


# --- Security review fixes (M2a review) ----------------------------------------------------------

# Finding 1 repro: unknown root, every value inside its own NUMERIC(20,10) column, but the
# product overflows the default Decimal context (prec 28) on every read.
BIG = {
    "symbol": "ZZQ", "side": "long", "quantity": Decimal("999999999"),
    "entry_price": Decimal(1), "entry_time": datetime(2026, 3, 2, 14, 30, tzinfo=UTC),
    "exit_price": Decimal("9999999999"), "exit_time": datetime(2026, 3, 2, 15, 0, tzinfo=UTC),
    "fees": Decimal(0), "account": "", "contract_multiplier": Decimal("999999999"),
}
BIG_NET = Decimal("9999999978000000013999999998.00")  # (9999999999 - 1) * 999999999 ** 2
BIG_POST = {
    "symbol": "ZZQ", "pv_for": "ZZQ", "point_value": "999999999", "side": "long",
    "quantity": "999999999", "entry_price": "1", "entry_time": "2026-03-02 09:30",
    "exit_price": "9999999999", "exit_time": "2026-03-02 10:00",
}


def seed_oversized(user, **changes):
    """The data a pre-fix add could commit; the form refuses it now, so seed the service."""
    return add_manual_trade(user, "a" * 32, **{**BIG, **changes})


def planned_risk(amount):
    return JournalEntry(planned_risk_amount=Decimal(amount), risk_currency="USD", stop_price=None)


def test_oversized_trade_derives_exactly_without_raising(user):
    trade = trade_of(user, seed_oversized(user))

    assert trade.gross_pnl == BIG_NET and trade.net_pnl == BIG_NET


def test_oversized_r_multiple_and_avg_r_do_not_raise(user):
    trade = trade_of(user, seed_oversized(user))
    entry = planned_risk("0.0001")

    r, reason = r_multiple(trade, entry)
    stats = compute_stats([trade], {trade.opening_execution_id: entry})

    assert reason is None and r == BIG_NET * 10_000
    assert stats["USD"].avg_r == BIG_NET * 10_000


def test_display_formats_a_20_digit_value_without_raising():
    value = Decimal("12345678901234567890")

    assert display.price(value) == "12,345,678,901,234,567,890.00"
    assert display.quantity(value) == "12,345,678,901,234,567,890"


def test_oversized_trade_keeps_trades_and_journal_pages_rendering(logged_in, user):
    pk = seed_oversized(user)
    JournalEntry.objects.create(
        user=user, opening_execution_id=pk, planned_risk_amount=Decimal("0.0001"),
        risk_currency="USD",
    )

    assert logged_in.get("/trades/").status_code == 200
    assert logged_in.get(f"/trades/{pk}/journal/").status_code == 200


@pytest.mark.parametrize(("change", "fields"), [
    ({}, ["entry_price", "exit_price"]),  # the review's repro: both legs are past 10**15
    ({"point_value": "1"}, ["exit_price"]),
    ({"point_value": "1", "entry_price": "9999999999", "exit_price": "", "exit_time": ""},
     ["entry_price"]),
])
def test_add_refuses_a_price_quantity_point_value_product_past_the_money_range(
    logged_in, user, change, fields
):
    resp = post_add(logged_in, **{**BIG_POST, **change})

    assert errors_of(resp) == {name: [copy.TRADE_VALUE_TOO_LARGE] for name in fields}
    assert not fills(user)


def test_add_just_under_the_money_range_saves(logged_in, user):
    # 999999999 * 1000000 * 1 < 10**15
    add(logged_in, **{**BIG_POST, "point_value": "1", "entry_price": "1000000",
                      "exit_price": "1000000"})


def test_close_refuses_an_exit_whose_product_is_past_the_money_range(logged_in, user):
    pk = seed_oversized(user, exit_price=None, exit_time=None)
    before = snapshot()

    resp = logged_in.post(close_url(pk), {**EXIT, "exit_price": "9999999999"})

    assert errors_of(resp) == {"exit_price": [copy.TRADE_VALUE_TOO_LARGE]}
    assert snapshot() == before


# Finding 2 (a format or control character in the symbol never reaches the database):
# test_control_characters_are_exactly_one_error_on_the_field below.


# Finding 3: a time before 1970 is a field error, on add and close alike (LocalTimeField).
def test_a_time_before_1970_is_a_field_error(logged_in, user):
    resp = post_add(logged_in, **{**AAPL, "entry_time": "1969-12-31 23:59"})

    assert errors_of(resp) == {"entry_time": [copy.TIME_TOO_OLD]}
    assert not fills(user)


# --- Final copy and one-message-per-field rules (design 12, 2026-10-13) --------------------------


def test_final_copy_is_verbatim_and_no_placeholder_is_left():
    assert copy.TRADE_VALUE_TOO_LARGE == (
        "Price, quantity and point value together are too large; check them for a typo."
    )
    assert copy.SYMBOL_CONTROL_CHARACTERS == (
        "Symbols can't contain hidden characters; retype it instead of pasting."
    )
    assert copy.TIME_TOO_OLD == "Use a date in 1970 or later."
    strings = [v for v in vars(copy).values() if isinstance(v, str)]
    assert not [s for s in strings if "PLACEHOLDER" in s]


CONTROL = ["\x00", "​", "‮", "\t"]
SYMBOL_OR_ACCOUNT = [("symbol", "SYMBOL_CONTROL_CHARACTERS"),
                     ("account", "ACCOUNT_CONTROL_CHARACTERS")]


@pytest.mark.parametrize("char", CONTROL, ids=["nul", "zwsp", "bidi", "tab"])
@pytest.mark.parametrize("field, key", SYMBOL_OR_ACCOUNT)
@pytest.mark.parametrize("length", [4, 70], ids=["short", "too_long"])  # control beats length
def test_control_characters_are_exactly_one_error_on_the_field(logged_in, user, field, key,
                                                               char, length):
    value = "AA" + char + "P" * (length - 3)

    resp = post_add(logged_in, **{**AAPL, field: value})

    assert errors_of(resp) == {field: [getattr(copy, key)]}
    assert "Null characters are not allowed." not in body(resp)
    assert not fills(user)


@pytest.mark.parametrize("symbol", ["ES​Z6", "AA\x00PL", "A" * 33])
def test_an_invalid_symbol_keeps_the_point_value_pending_on_submit(logged_in, symbol):
    no_point_value = {k: v for k, v in AAPL.items() if k not in ("point_value", "pv_for")}
    resp = post_add(logged_in, **{**no_point_value, "symbol": symbol})

    assert list(errors_of(resp)) == ["symbol"]
    d = Doc(body(resp))
    assert copy.POINT_VALUE_PENDING in d.text
    assert not d.all("input", name="point_value") and not d.all("input", name="pv_for")


@pytest.mark.parametrize("symbol", ["ES​Z6", "AA\x00PL", "A" * 33])
def test_an_invalid_symbol_keeps_the_point_value_pending_on_the_endpoint(logged_in, symbol):
    markup = body(logged_in.get(PV, {"symbol": symbol}))

    assert copy.POINT_VALUE_PENDING in html.unescape(markup)
    assert 'name="point_value"' not in markup and 'name="pv_for"' not in markup
    assert re.search(r'<p id="point-value-live"[^>]*></p>', markup)  # nothing announced


THIS_YEAR = {**AAPL, "entry_time": "2027-01-02 09:30"}  # NOW is 2027-01-15
THIS_YEAR_ENTRY = "Jan 2, 2027, 9:30 AM"  # always with the year (design 12, 13)


def test_exit_before_entry_names_the_entry_with_the_year_on_add(logged_in):
    early = {"exit_price": "105", "exit_time": "2027-01-02 09:29"}
    resp = post_add(logged_in, **{**THIS_YEAR, **early})

    assert errors_of(resp) == {"exit_time": [copy.EXIT_BEFORE_ENTRY.format(entry=THIS_YEAR_ENTRY)]}


def test_exit_before_entry_names_the_entry_with_the_year_on_close(logged_in, user):
    pk = add(logged_in, **THIS_YEAR)

    page = logged_in.get(close_url(pk))
    early = logged_in.post(close_url(pk), {**EXIT, "exit_time": "2027-01-02 09:29"})
    with patch("journal.views.close_manual_trade",
               side_effect=ExitBeforeEntry(datetime(2027, 1, 2, 14, 30, tzinfo=UTC))):
        race = logged_in.post(close_url(pk), {**EXIT, "exit_time": "2027-01-02 09:31"})

    message = copy.EXIT_BEFORE_ENTRY.format(entry=THIS_YEAR_ENTRY)
    assert page.context["entry_time_display"] == THIS_YEAR_ENTRY
    assert errors_of(early) == {"exit_time": [message]}
    assert errors_of(race) == {"exit_time": [message]}


def test_field_visual_order_is_input_error_help_hint_and_announce_order_help_hint_error(
    logged_in
):
    markup = body(post_add(logged_in, **{**AAPL, "symbol": ""}))

    field = markup[markup.index('<label for="id_symbol"'):]
    order = ['<label for="id_symbol"', 'name="symbol"', 'id="id_symbol_error"',
             'id="id_symbol_helptext"', 'id="id_symbol_currency_hint"']
    at = [field.index(marker) for marker in order]
    assert at == sorted(at)
    assert Doc(markup).one("input", name="symbol")["aria-describedby"] == (
        "id_symbol_helptext id_symbol_currency_hint id_symbol_error"
    )


def test_1970_01_01_is_accepted(logged_in, user):
    add(logged_in, **{**AAPL, "entry_time": "1970-01-01 00:00"})


# Finding 4: the gate and the views' link rule both need broker == manual, not just source.
def test_a_manual_source_row_from_another_broker_is_refused(logged_in, user):
    pk = Execution.objects.create(
        user=user, broker="topstep", source="manual", broker_trade_id="manual:" + "b" * 32,
        symbol="AAPL", side="buy", quantity=1, price=1, currency="USD",
        executed_at=datetime(2026, 3, 2, 14, 30, tzinfo=UTC),
    ).pk

    with transaction.atomic():
        assert _manual_fills(user, pk) is None
    assert copy.NOT_FOUND_TITLE in text(logged_in.get(close_url(pk)))
