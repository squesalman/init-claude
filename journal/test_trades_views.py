"""
PR D: /trades/ list, sorting and the three stat cards.
Specs: docs/product/features/import-and-list.md sections 2, 3, 5, 6 and AC 9-25;
docs/design/auth-and-trades-list.md 5.1, 5.3, 5.4, 6.1-6.4; follow-ups row 27. Synthetic data only.
"""

import re
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts import copy
from journal import copy as import_copy
from journal.display import duration, money, price, quantity
from journal.models import Execution
from journal.test_import_views import UNSUPPORTED, upload, uploaded
from journal.test_topstep_parser import T1, T4, csv_bytes

User = get_user_model()
T0 = datetime(timezone.now().year, 6, 15, 14, 0, tzinfo=UTC)  # current year: no year shown
_ids = count(1)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="tr-a@example.com", password="x", timezone="UTC")


@pytest.fixture
def other(db):
    return User.objects.create_user(email="tr-b@example.com", password="x", timezone="UTC")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def _leg(user, side, px, at, tid, symbol, label, **kw):
    return Execution(
        **kw, user=user, broker="topstep", broker_trade_id=tid, broker_account_label=label,
        symbol=symbol, side=side, quantity=Decimal(1), price=Decimal(px), currency="USD",
        executed_at=at, source=Execution.SOURCE_MANUAL,
    )


def legs(user, net=None, minutes=0, symbol="CLZ6", label="", hold=timedelta(minutes=1)):
    """One trade as executions: a long of 1 at 100, closed `hold` later so net P&L == net
    (no fees, multiplier 1). net=None leaves it open."""
    tid, at = str(next(_ids)), T0 + timedelta(minutes=minutes)
    out = [_leg(user, "buy", "100", at, tid, symbol, label)]
    if net is not None:
        out.append(_leg(user, "sell", Decimal(100) + Decimal(net), at + hold, tid, symbol, label))
    return out


def make(user, *specs, **kw):
    """specs: net values (str) or None for open; each trade opens one minute after the last."""
    rows = [e for i, net in enumerate(specs) for e in legs(user, net, minutes=i, **kw)]
    Execution.unscoped.bulk_create(rows)


def ctx(client, query=""):
    resp = client.get("/trades/" + query)
    assert resp.status_code == 200
    return resp.context


def symbols(context):
    return [t["symbol"] for t in context["trades"]]


# --- display helpers ----------------------------------------------------------------------


@pytest.mark.parametrize("value, text", [
    ("1234.56", "+$1,234.56"), ("-312.40", "-$312.40"), ("0.00", "$0.00"), ("-0.00", "$0.00"),
    ("0", "$0.00"), ("25", "+$25.00"), ("1234567.5", "+$1,234,567.50"), ("-0.01", "-$0.01"),
])
def test_money_has_sign_dollar_separator_two_dp_and_never_minus_zero(value, text):
    assert money(Decimal(value)) == text


@pytest.mark.parametrize("seconds, text", [
    (0, "0s"), (45, "45s"), (59, "59s"), (60, "1m"), (12 * 60 + 59, "12m"), (3599, "59m"),
    (3600, "1h 00m"), (3900, "1h 05m"), (86399, "23h 59m"), (86400, "1d 0h"),
    (2 * 86400 + 3 * 3600 + 59 * 60, "2d 3h"),
])
def test_duration_buckets(seconds, text):
    assert duration(timedelta(seconds=seconds)) == text


@pytest.mark.parametrize("value, text", [
    ("80.0000000000", "80.00"), ("80.1500000000", "80.15"), ("80.0030000000", "80.003"),
    ("19850.2500000000", "19,850.25"), ("1.2345", "1.2345"), ("-37.6300000000", "-37.63"),
    ("1.08345", "1.08345"),  # 6E-style tick: never rounded away at display
    ("80.1234567891", "80.1234567891"),
])
def test_price_shows_stored_precision_trimmed_min_2(value, text):
    assert price(Decimal(value)) == text


@pytest.mark.parametrize("value, text", [
    ("2.0000000000", "2"), ("1.5000000000", "1.5"), ("0.1250000000", "0.125"),
    ("1000.0000000000", "1,000"), ("0.0000012500", "0.00000125"),
])
def test_quantity_shows_stored_precision_trimmed(value, text):
    assert quantity(Decimal(value)) == text


# --- empty states (5.4) ---------------------------------------------------------------------


def test_state_a_no_imports_no_cards_no_table(logged_in):
    c = ctx(logged_in)
    assert (c["empty_heading"], c["empty_body"], c["empty_action"], c["empty_url"]) == (
        copy.TRADES_EMPTY_HEADING, copy.TRADES_EMPTY_BODY, copy.TRADES_EMPTY_ACTION, "/imports/"
    )
    assert c["trades"] == [] and c["cards"] is None


def test_state_b_imports_but_no_trades_links_the_most_recent_import(logged_in):
    uploaded(logged_in, csv_bytes(UNSUPPORTED))  # the only row fails
    last = uploaded(logged_in, csv_bytes(UNSUPPORTED))  # same Id, fails again
    c = ctx(logged_in)
    assert (c["empty_heading"], c["empty_body"], c["empty_action"], c["empty_url"]) == (
        copy.TRADES_EMPTY_HEADING, copy.TRADES_EMPTY_B_BODY, copy.TRADES_EMPTY_B_ACTION,
        f"/imports/{last.pk}/",
    )
    assert c["trades"] == [] and c["cards"] is None


def test_state_c_only_open_trades_shows_table_and_n0_cards(logged_in, user):
    make(user, None, None)
    c = ctx(logged_in)
    assert c["empty_heading"] is None
    assert [t["result"] for t in c["trades"]] == ["open", "open"]
    cards = c["cards"]
    assert cards["win_rate"]["value"] == "n/a (n=0)" and cards["win_rate"]["note"] is None
    assert cards["total_pnl"]["value"] == "— (n=0)"
    assert cards["avg_r"]["value"] == "— (n=0)" and cards["avg_r"]["help"] == copy.AVG_R_HELP


def test_non_usd_closed_trade_logs_a_warning_instead_of_silently_dropping_from_the_cards(
    logged_in, user, caplog
):
    """Code review finding: the cards read compute_stats(...).get("USD") only, so a non-USD
    closed trade would show n=0 everywhere while the table still lists it, with no error.
    Slice 1 is USD-only (design doc), so this is latent, but it must not fail silently."""
    eur_at = T0 + timedelta(hours=1)
    Execution.unscoped.bulk_create([
        Execution(
            user=user, broker="topstep", broker_trade_id="eur-1", broker_account_label="",
            symbol="6EZ6", side=side, quantity=Decimal(1), price=Decimal(px), currency="EUR",
            executed_at=at, source=Execution.SOURCE_MANUAL,
        )
        for side, px, at in (("buy", "100", eur_at), ("sell", "101", eur_at + timedelta(minutes=1)))
    ])

    with caplog.at_level("WARNING", logger="journal.views"):
        c = ctx(logged_in)

    assert [t["symbol"] for t in c["trades"]] == ["6EZ6"]
    assert c["cards"]["total_pnl"]["value"] == "— (n=0)"  # still no EUR card: slice 1 is USD-only
    assert any("non-USD" in r.getMessage() for r in caplog.records)


def test_more_than_1000_trades_logs_that_the_ceiling_is_passed(logged_in, user, caplog):
    """Follow-ups 30: _user_trades' ponytail accepts TRADES_CEILING (1,000) trades; every
    request past it logs a warning, instead of slowing down silently."""
    make(user, *["1"] * 1000)
    with caplog.at_level("WARNING", logger="journal.views"):
        ctx(logged_in)
    assert not any("ceiling" in r.getMessage() for r in caplog.records)

    make(user, "1")
    with caplog.at_level("WARNING", logger="journal.views"):
        ctx(logged_in)
    assert any("ceiling" in r.getMessage() for r in caplog.records)


# --- stat cards (section 3, AC 17-23) -------------------------------------------------------


def test_vector_5_cards(logged_in, user):
    make(user, "50", "-30", "0", "10", "-10", "0", "5")
    cards = ctx(logged_in)["cards"]
    assert cards["win_rate"]["value"] == "60.00% (n=5)"
    assert cards["win_rate"]["note"] == "Excludes 2 breakeven trades."
    assert cards["win_rate"]["sr"] == "Win rate: 60.00 percent, based on 5 trades"
    assert cards["total_pnl"]["value"] == "+$25.00 (n=7)"
    assert cards["total_pnl"]["tone"] == "gain"
    assert cards["total_pnl"]["sr"] == "Total P&L: plus 25.00 dollars, based on 7 trades"
    assert cards["avg_r"]["value"] == "— (n=0)"
    assert cards["avg_r"]["sr"] == "Avg R: not available, based on 0 trades"


def test_one_breakeven_uses_the_singular_note(logged_in, user):
    make(user, "5", "0")
    assert ctx(logged_in)["cards"]["win_rate"]["note"] == "Excludes 1 breakeven trade."


def test_only_breakeven_gives_na_win_rate_and_zero_total_without_sign(logged_in, user):
    make(user, "0", "0")
    cards = ctx(logged_in)["cards"]
    assert cards["win_rate"]["value"] == "n/a (n=0)"
    assert cards["win_rate"]["sr"] == "Win rate: not available, based on 0 trades"
    assert cards["win_rate"]["note"] == "Excludes 2 breakeven trades."
    assert cards["total_pnl"]["value"] == "$0.00 (n=2)" and cards["total_pnl"]["tone"] is None


def test_negative_total_prints_the_sign(logged_in, user):
    make(user, "-300", "-12.40")
    total = ctx(logged_in)["cards"]["total_pnl"]
    assert total["value"] == "-$312.40 (n=2)" and total["tone"] == "loss"
    assert total["sr"] == "Total P&L: minus 312.40 dollars, based on 2 trades"


def test_open_trades_are_excluded_from_the_cards(logged_in, user):
    make(user, "10", None, "-5")
    cards = ctx(logged_in)["cards"]
    assert cards["win_rate"]["value"] == "50.00% (n=2)"
    assert cards["total_pnl"]["value"] == "+$5.00 (n=2)"


def test_avg_r_stays_n0_with_help_whatever_the_trade_count(logged_in, user):
    make(user, *["1"] * 12)
    avg_r = ctx(logged_in)["cards"]["avg_r"]
    assert (avg_r["value"], avg_r["help"]) == ("— (n=0)", copy.AVG_R_HELP)


def test_disclosure_names_the_users_zone(client, db):
    u = User.objects.create_user(email="tz@example.com", password="x", timezone="Asia/Tokyo")
    make(u, "1")
    client.force_login(u)
    c = ctx(client)
    assert c["zone"] == "Asia/Tokyo"
    assert c["calc_items"][-1] == copy.CALC_TIMES.format(zone="Asia/Tokyo")


_EVALUATIVE = re.compile(
    r"\b(good|bad|poor|should|only|low|high|better|worse|than|great|average trader)\b", re.I
)


def test_card_and_help_copy_has_no_evaluative_words():
    """AC 22: no grading words or comparisons in any card, help or disclosure string."""
    strings = [
        copy.WIN_RATE_VALUE, copy.WIN_RATE_EMPTY, copy.BREAKEVEN_NOTE_ONE,
        copy.BREAKEVEN_NOTE_MANY, copy.TOTAL_PNL_VALUE, copy.NO_VALUE, copy.AVG_R_VALUE,
        copy.AVG_R_HELP, copy.ACROSS_ALL_ACCOUNTS, copy.CALC_SUMMARY, copy.CALC_WIN_RATE,
        copy.CALC_TOTAL_PNL, copy.CALC_AVG_R, copy.CALC_TIMES, copy.WIN_RATE_SR,
        copy.WIN_RATE_SR_EMPTY, copy.TOTAL_PNL_SR, copy.TOTAL_PNL_SR_EMPTY,
        copy.AVG_R_SR, copy.AVG_R_SR_EMPTY,
        copy.TRADES_EMPTY_B_BODY, copy.TRADES_EMPTY_B_ACTION,
    ]
    # "(trades with one entry only)" in CALC_AVG_R is scope, not grading (verbatim from
    # docs/design/journaling.md 12); every other "only" still fails.
    strings = [s.replace("one entry only", "one entry") for s in strings]
    assert [s for s in strings if _EVALUATIVE.search(s)] == []
    assert not re.search(r"\d{4}|coming soon", copy.AVG_R_HELP, re.I)  # AC 21: no date, no promise


# --- rows ------------------------------------------------------------------------------------


def test_row_shape_closed_and_open(logged_in, user):
    make(user, "-150", hold=timedelta(hours=1, minutes=5))
    make(user, None)
    rows = {r["result"]: r for r in ctx(logged_in)["trades"]}
    loss = rows["loss"]
    assert loss == {
        "symbol": "CLZ6", "direction": "long", "quantity": "1", "entry": "100.00",
        "exit": "-50.00", "opened_at": T0, "opened": "Jun 15, 2:00 PM", "duration": "1h 05m",
        "result": "loss", "net_pnl": "-$150.00", "account": "no name",
        # J2 journal column (ADR-0007 section 9); journal/test_journal_views.py covers states.
        "id": loss["id"], "journal_url": f"/trades/{loss['id']}/journal/",
        "journal_state": "add", "journal_label": "Add journal",
        "journal_sr": "Add journal for CLZ6, opened Jun 15, 2:00 PM",
    }
    assert rows["open"]["exit"] is None and rows["open"]["duration"] is None
    assert rows["open"]["net_pnl"] is None


def test_opened_shows_the_year_when_not_the_current_year(logged_in, user):
    back = T0.replace(year=T0.year - 3)
    Execution.unscoped.bulk_create(legs(user, "1", minutes=(back - T0) / timedelta(minutes=1)))
    assert ctx(logged_in)["trades"][0]["opened"] == f"Jun 15, {back.year}, 2:00 PM"


def test_times_are_in_the_users_zone(client, db):
    u = User.objects.create_user(email="ny@example.com", password="x", timezone="America/New_York")
    make(u, "1")
    client.force_login(u)
    assert ctx(client)["trades"][0]["opened"] == "Jun 15, 10:00 AM"


def test_t4_nested_rows_are_two_trades(logged_in):
    """AC 11, ADR-0004: the nested same-direction rows stay two trades, not one."""
    uploaded(logged_in, csv_bytes(*T4))
    trades = ctx(logged_in)["trades"]
    assert len(trades) == 2
    assert sorted(t["net_pnl"] for t in trades) == ["+$497.00", "-$103.00"]


def test_t1_net_pnl_after_fees(logged_in):
    uploaded(logged_in, csv_bytes(T1))
    assert ctx(logged_in)["trades"][0]["net_pnl"] == "+$291.96"  # AC 13


def test_same_timestamp_legs_are_matched_in_id_order(logged_in, user):
    """Follow-ups row 27: the read query must order by (executed_at, id). The sell is stored
    first (so an unordered scan returns it first) but has the higher id; id order is file
    order, so buy-then-sell is a long, not a short."""
    sell = _leg(user, "sell", "101", T0, "x1", "CLZ6", "", id=10**9 + 2)
    buy = _leg(user, "buy", "100", T0, "x1", "CLZ6", "", id=10**9 + 1)
    Execution.unscoped.bulk_create([sell])
    Execution.unscoped.bulk_create([buy])
    [trade] = ctx(logged_in)["trades"]
    assert (trade["direction"], trade["net_pnl"]) == ("long", "+$1.00")


# --- sorting (section 2, design 5.1) --------------------------------------------------------


def test_default_sort_newest_opened_first_with_id_tiebreak(logged_in, user):
    make(user, "1", "2", symbol="A")  # opened T0, T0+1m
    Execution.unscoped.bulk_create(legs(user, "3", minutes=1, symbol="B"))  # same time, higher id
    c = ctx(logged_in)
    assert [(t["symbol"], t["net_pnl"]) for t in c["trades"]] == [
        ("B", "+$3.00"), ("A", "+$2.00"), ("A", "+$1.00"),
    ]
    assert (c["sort"], c["dir"]) == ("opened", "desc")


def test_opened_ascending(logged_in, user):
    make(user, "1", "2", "3")
    assert [t["net_pnl"] for t in ctx(logged_in, "?sort=opened&dir=asc")["trades"]] == [
        "+$1.00", "+$2.00", "+$3.00",
    ]


def test_symbol_sort_both_ways_ties_newest_first(logged_in, user):
    Execution.unscoped.bulk_create(
        legs(user, "1", 0, symbol="MCLZ6") + legs(user, "2", 1, symbol="CLZ6")
        + legs(user, "3", 2, symbol="MCLZ6")
    )
    asc = ctx(logged_in, "?sort=symbol&dir=asc")["trades"]
    assert [(t["symbol"], t["net_pnl"]) for t in asc] == [
        ("CLZ6", "+$2.00"), ("MCLZ6", "+$3.00"), ("MCLZ6", "+$1.00"),
    ]
    desc = ctx(logged_in, "?sort=symbol&dir=desc")["trades"]
    assert [(t["symbol"], t["net_pnl"]) for t in desc] == [
        ("MCLZ6", "+$3.00"), ("MCLZ6", "+$1.00"), ("CLZ6", "+$2.00"),
    ]


def test_pnl_sort_puts_open_trades_last_both_ways(logged_in, user):
    make(user, "5", None, "-20", "0", None, "30")
    desc = ctx(logged_in, "?sort=pnl&dir=desc")["trades"]
    assert [t["net_pnl"] for t in desc] == ["+$30.00", "+$5.00", "$0.00", "-$20.00", None, None]
    asc = ctx(logged_in, "?sort=pnl&dir=asc")["trades"]
    assert [t["net_pnl"] for t in asc] == ["-$20.00", "$0.00", "+$5.00", "+$30.00", None, None]


@pytest.mark.parametrize("query", [
    "?sort=bogus", "?sort=bogus&dir=up", "?dir=sideways", "?sort=%00&dir=%ff",
    "?sort=pnl&sort=symbol&dir=asc&dir=desc&dir=", "?sort=PNL", "?sort=" + "x" * 5000,
    "?sort[]=a&dir[]=b",
])
def test_garbage_sort_params_fall_back_silently(logged_in, user, query):
    make(user, "1", "2")
    c = ctx(logged_in, query)
    assert [t["net_pnl"] for t in c["trades"]] in (["+$2.00", "+$1.00"], ["+$1.00", "+$2.00"])
    if "sort=pnl" not in query:
        assert (c["sort"], c["dir"]) == ("opened", "desc")


def test_too_many_query_fields_fall_back_to_the_default_sort(logged_in, user):
    make(user, "1")
    c = ctx(logged_in, "?sort=pnl&dir=asc&" + "a=1&" * 2000)  # over DATA_UPLOAD_MAX_NUMBER_FIELDS
    assert (c["sort"], c["dir"]) == ("opened", "desc")


def test_bad_dir_with_a_valid_sort_uses_that_columns_default(logged_in, user):
    make(user, "1")
    c = ctx(logged_in, "?sort=symbol&dir=up")
    assert (c["sort"], c["dir"]) == ("symbol", "asc")


def test_sort_links_toggle_the_active_column_and_carry_aria(logged_in, user):
    make(user, "1")
    links = ctx(logged_in)["sort_links"]
    assert links["opened"] == {
        "url": "/trades/?sort=opened&dir=asc", "aria_sort": "descending",
        "sorted_text": copy.SORTED_NEWEST,
    }
    assert links["symbol"] == {
        "url": "/trades/?sort=symbol&dir=asc", "aria_sort": "none", "sorted_text": None,
    }
    assert links["pnl"]["url"] == "/trades/?sort=pnl&dir=desc"
    links = ctx(logged_in, "?sort=pnl&dir=asc")["sort_links"]
    assert links["pnl"] == {
        "url": "/trades/?sort=pnl&dir=desc", "aria_sort": "ascending",
        "sorted_text": copy.SORTED_LOWEST,
    }


# --- multi-account (section 2, design 5.3) --------------------------------------------------


def test_account_column_hidden_for_one_label(logged_in, user):
    make(user, "1", "2", label="Combine 50K")
    c = ctx(logged_in)
    assert c["show_account"] is False and c["across_accounts"] is None


def test_account_column_shown_for_two_labels_blank_counts_as_one(logged_in, user):
    make(user, "1", label="Combine 50K")
    make(user, "2", label="")
    c = ctx(logged_in)
    assert c["show_account"] is True
    assert c["across_accounts"] == copy.ACROSS_ALL_ACCOUNTS == "Across all accounts."
    assert {t["account"] for t in c["trades"]} == {"Combine 50K", import_copy.LIST_ACCOUNT_BLANK}


def test_no_trades_means_no_account_column(logged_in):
    assert ctx(logged_in)["show_account"] is False


# --- tenant isolation (section 5, blocks merge) ---------------------------------------------


def test_isolation_b_never_sees_a(client, user, other):
    a_client = type(client)()
    a_client.force_login(user)
    uploaded(a_client, label="A-private")
    assert len(ctx(a_client)["trades"]) == 5

    client.force_login(other)
    c = ctx(client)
    assert c["trades"] == [] and c["cards"] is None
    assert c["empty_body"] == copy.TRADES_EMPTY_BODY  # state A: B has no imports of its own
    assert "A-private" not in client.get("/trades/").content.decode()

    uploaded(client, csv_bytes(T1))
    c = ctx(client)
    assert len(c["trades"]) == 1 and c["show_account"] is False
    assert c["cards"]["total_pnl"]["value"] == "+$291.96 (n=1)"
    assert c["cards"]["win_rate"]["value"] == "100.00% (n=1)"
    assert ctx(a_client)["cards"]["total_pnl"]["value"].endswith("(n=5)")


def test_isolation_b_with_failed_import_links_only_its_own(client, user, other):
    a_client = type(client)()
    a_client.force_login(user)
    uploaded(a_client, csv_bytes(UNSUPPORTED))
    client.force_login(other)
    mine = uploaded(client, csv_bytes(UNSUPPORTED))
    assert ctx(client)["empty_url"] == f"/imports/{mine.pk}/"


# --- query budget (section 6) ---------------------------------------------------------------

QUERY_CEILING = 4  # measured 3 (session, user, executions); empty state adds 1


@pytest.mark.parametrize("trades", [177, 500])  # 354 and 1,000 executions
def test_query_count_is_constant(logged_in, user, django_assert_max_num_queries, trades):
    make(user, *[str(i % 7 - 3) for i in range(trades - 1)], None)
    for query in ("", "?sort=pnl&dir=asc", "?sort=symbol"):
        start = time.perf_counter()
        with django_assert_max_num_queries(QUERY_CEILING):
            c = ctx(logged_in, query)
        elapsed = time.perf_counter() - start
        assert len(c["trades"]) == trades
        assert elapsed < 1.0, elapsed  # section 6: under 1 s at ~1,000 executions


def test_upload_then_list_on_the_fixture(logged_in):
    assert upload(logged_in).status_code == 302
    c = ctx(logged_in)
    assert len(c["trades"]) == 5
    assert c["cards"]["win_rate"]["value"] == "75.00% (n=4)"
    assert c["cards"]["total_pnl"]["value"] == "+$704.44 (n=5)"
    assert c["cards"]["win_rate"]["note"] == "Excludes 1 breakeven trade."
