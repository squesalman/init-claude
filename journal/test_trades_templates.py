"""
Rendered-markup checks for /trades/ (docs/design/auth-and-trades-list.md 5.1-5.4, 6, 7, 8).
Context and sorting behaviour are in test_trades_views.py; this file checks structure,
accessibility hooks and escaping. Visual layout and contrast are not testable here.
"""

import re

import pytest
from django.conf import settings

from accounts import copy
from config.test_pages import Doc
from journal.test_import_views import UNSUPPORTED, uploaded
from journal.test_topstep_parser import csv_bytes
from journal.test_trades_views import make

TEMPLATES = settings.BASE_DIR / "templates" / "journal"
NEW_TEMPLATES = [
    TEMPLATES / "trade_list.html",
    *(TEMPLATES / "partials" / name for name in (
        "trade_table.html", "trade_cards.html", "stat_cards.html", "stat_card.html",
        "trades_empty.html", "result_badge.html",
    )),
]


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        email="tt@example.com", password="x", timezone="America/New_York"
    )


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def markup(client, query=""):
    resp = client.get("/trades/" + query)
    assert resp.status_code == 200
    return resp.content.decode()


def main_of(text):
    return re.search(r'<main id="main".*?</main>', text, re.S).group(0)


def region(text, start_tag_pattern, end):
    """The markup from the first tag matching start_tag_pattern up to `end`."""
    return re.search(start_tag_pattern + r".*?" + re.escape(end), text, re.S).group(0)


def table_of(text):
    return region(text, r"<table", "</table>")


def cards_of(text):
    return region(text, r'<ul id="trade-cards"', "</ul>")


# --- desktop table (5.1) --------------------------------------------------------------------


def test_table_has_caption_scoped_headers_and_the_zone_in_opened(logged_in, user):
    make(user, "1", None)
    d = Doc(table_of(markup(logged_in)))
    assert "Your trades" in d.text
    heads = d.all("th", scope="col")
    assert len(heads) == 9  # one account: no Account column
    assert "Opened (America/New_York)" in d.text
    for word in ("Symbol", "Side", "Qty", "Entry", "Exit", "Duration", "Result", "Net P&L"):
        assert word in d.text
    assert "Account" not in d.text


def test_sort_headers_carry_aria_sort_links_and_hidden_sorted_text(logged_in, user):
    make(user, "1")
    text = table_of(markup(logged_in))
    d = Doc(text)
    assert [a["aria-sort"] for a in d.all("th") if "aria-sort" in a] == [
        "descending", "none", "none",
    ]
    hrefs = [a["href"] for a in d.all("a")]
    assert hrefs == [
        "/trades/?sort=opened&dir=asc", "/trades/?sort=symbol&dir=asc",
        "/trades/?sort=pnl&dir=desc",
    ]
    link = re.search(r'<a href="/trades/\?sort=opened&amp;dir=asc".*?</a>', text, re.S).group(0)
    assert f'<span class="sr-only">{copy.SORTED_NEWEST}</span>' in link

    d = Doc(table_of(markup(logged_in, "?sort=pnl&dir=asc")))
    assert [a["aria-sort"] for a in d.all("th") if "aria-sort" in a] == [
        "none", "none", "ascending",
    ]
    assert copy.SORTED_LOWEST in d.text and copy.SORTED_NEWEST not in d.text


def test_rows_show_side_result_badge_and_signed_pnl(logged_in, user):
    make(user, "5", "-3", "0", None)
    d = Doc(table_of(markup(logged_in)))
    for word in ("Win", "Loss", "Breakeven", "Open", "Long"):
        assert word in d.text
    assert "+$5.00" in d.text and "-$3.00" in d.text and "$0.00" in d.text
    assert "not closed yet" in d.text  # open trade's Exit: "-" plus hidden text
    assert all(a.get("aria-hidden") == "true" for a in d.all("svg"))  # icons are decorative


def test_result_badges_use_distinct_icons(logged_in, user):
    make(user, "5", "-3", "0", None)
    text = table_of(markup(logged_in))
    badges = re.findall(r'<span class="badge badge-(\w+)">(.*?)</span></span>', text, re.S)
    assert {kind for kind, _ in badges} == {"win", "loss", "breakeven", "open"}
    paths = {kind: re.search(r"<svg.*?</svg>", body, re.S).group(0) for kind, body in badges}
    assert len(set(paths.values())) == 4


def test_account_column_and_card_line_only_for_two_labels(logged_in, user):
    make(user, "1", label="Combine 50K")
    text = markup(logged_in)
    assert "Account" not in Doc(table_of(text)).text
    assert "Account:" not in cards_of(text)

    make(user, "2", label="")
    text = markup(logged_in)
    d = Doc(table_of(text))
    assert len(d.all("th", scope="col")) == 10 and "Account" in d.text
    assert "Combine 50K" in d.text and "no name" in d.text
    assert cards_of(text).count("Account:") == 2
    assert copy.ACROSS_ALL_ACCOUNTS in main_of(text)


def test_table_wrapper_is_desktop_only_and_cards_mobile_only(logged_in, user):
    make(user, "1", "2", "3")
    text = markup(logged_in)
    assert re.search(r'<div[^>]*class="[^"]*\bhidden sm:block\b[^"]*"[^>]*>\s*<table', text)
    assert re.search(r'<ul id="trade-cards" class="[^"]*\bsm:hidden\b', text)
    assert cards_of(text).count("<li") == 3


# --- mobile cards and sort select (5.2) -----------------------------------------------------


def test_mobile_cards_carry_symbol_side_pnl_result_time_and_details(logged_in, user):
    make(user, "-150", symbol="CLZ6")
    d = Doc(cards_of(markup(logged_in)))
    for part in ("CLZ6", "Long", "-$150.00", "Loss", "Qty 1", "1m"):
        assert part in d.text, part
    assert "100.00 to -50.00" in d.text


def test_mobile_names_the_zone_once_above_the_list(logged_in, user):
    make(user, "1", "2")
    text = main_of(markup(logged_in))
    assert text.count("Times in America/New_York") == 1


@pytest.mark.parametrize("query, selected", [
    ("", "opened_desc"), ("?sort=symbol&dir=desc", "symbol_desc"),
    ("?sort_dir=pnl_asc", "pnl_asc"),
])
def test_sort_select_is_a_labelled_get_form_with_six_options(logged_in, user, query, selected):
    make(user, "1")
    text = markup(logged_in, query)
    form = region(text, r'<form[^>]*method="get"', "</form>")
    d = Doc(form)
    assert d.one("form", method="get")["action"] == "/trades/"
    assert d.one("select")["name"] == "sort_dir"
    options = d.all("option")
    assert [o["value"] for o in options] == [
        "opened_desc", "opened_asc", "symbol_asc", "symbol_desc", "pnl_desc", "pnl_asc",
    ]
    assert [o["value"] for o in options if "selected" in o] == [selected]
    for label in ("Newest opened", "Oldest opened", "Symbol A to Z", "Symbol Z to A",
                  "Highest P&L", "Lowest P&L", "Sort by", "Apply"):
        assert label in d.text
    assert d.one("button")["type"] == "submit"
    assert d.one("select")["id"] in {a["for"] for a in d.all("label")}


@pytest.mark.parametrize("query, expected", [
    ("?sort_dir=symbol_desc", ("symbol", "desc")),
    ("?sort_dir=pnl_asc", ("pnl", "asc")),
    ("?sort_dir=opened_asc&sort=pnl&dir=desc", ("opened", "asc")),  # sort_dir wins
    ("?sort_dir=bogus&sort=symbol&dir=desc", ("symbol", "desc")),  # falls back to the pair
    ("?sort_dir=symbol_sideways", ("opened", "desc")),
    ("?sort_dir=", ("opened", "desc")),
])
def test_combined_sort_dir_param(logged_in, user, query, expected):
    make(user, "1")
    resp = logged_in.get("/trades/" + query)
    assert (resp.context["sort"], resp.context["dir"]) == expected


# --- stat cards (6) -------------------------------------------------------------------------


def test_stat_cards_group_three_labelled_sections_with_sr_values(logged_in, user):
    make(user, "50", "-30", "0", "10", "-10", "0", "5")
    text = main_of(markup(logged_in))
    group = region(
        text, r'<div role="group" aria-label="Summary of your closed trades"', "</details>"
    )
    d = Doc(group)
    sections = d.all("section")
    assert len(sections) == 3
    ids = [s["aria-labelledby"] for s in sections]
    assert [a["id"] for a in d.all("h2")] == ids
    assert "Win rate" in d.text and "Total P&L" in d.text and "Avg R" in d.text
    # Visible compact value is hidden from AT; the spelled-out label is what is announced.
    assert '<span aria-hidden="true">60.00% (n=5)</span>' in group
    assert '<span class="sr-only">Win rate: 60.00 percent, based on 5 trades</span>' in group
    assert "Excludes 2 breakeven trades." in d.text
    assert copy.AVG_R_HELP in d.text


@pytest.mark.parametrize("specs, tone", [(("5",), "text-gain"), (("-5",), "text-loss"),
                                         (("0",), None)])
def test_total_pnl_tone_class(logged_in, user, specs, tone):
    make(user, *specs)
    section = region(main_of(markup(logged_in)), r'<section aria-labelledby="card-total_pnl"',
                     "</section>")
    for cls in ("text-gain", "text-loss"):
        assert (cls in section) == (cls == tone)


def test_calc_disclosure_is_closed_details_with_four_items(logged_in, user):
    make(user, "1")
    text = main_of(markup(logged_in))
    details = region(text, r"<details", "</details>")
    d = Doc(details)
    assert "open" not in d.one("details")
    assert copy.CALC_SUMMARY in d.text
    assert len(d.all("li")) == 4
    assert "Times are shown in America/New_York." in d.text


def test_populated_page_has_the_import_trades_button(logged_in, user):
    make(user, "1")
    d = Doc(main_of(markup(logged_in)))
    assert d.all("a", href="/imports/") and "Import trades" in d.text


# --- empty states (5.4) ---------------------------------------------------------------------


def test_empty_state_a_has_one_action_and_no_cards_or_table(logged_in):
    text = main_of(markup(logged_in))
    d = Doc(text)
    assert copy.TRADES_EMPTY_HEADING in d.text and copy.TRADES_EMPTY_BODY in d.text
    assert [a["href"] for a in d.all("a")] == ["/imports/"]
    assert not d.all("table") and 'role="group"' not in text and not d.all("select")


def test_empty_state_b_links_the_last_import(logged_in):
    batch = uploaded(logged_in, csv_bytes(UNSUPPORTED))
    d = Doc(main_of(markup(logged_in)))
    assert copy.TRADES_EMPTY_B_BODY in d.text and copy.TRADES_EMPTY_B_ACTION in d.text
    assert [a["href"] for a in d.all("a")] == [f"/imports/{batch.pk}/"]
    assert not d.all("table")


# --- escaping --------------------------------------------------------------------------------


def test_symbol_and_account_label_are_escaped(logged_in, user):
    make(user, "1", symbol="<i>Z6", label="\"><script>alert(9)</script>")
    make(user, "2", label="")
    text = markup(logged_in)
    assert "<script>alert(9)" not in text and "<i>Z6" not in text
    assert "&lt;script&gt;alert(9)&lt;/script&gt;" in text and "&lt;i&gt;Z6" in text


@pytest.mark.parametrize("path", NEW_TEMPLATES, ids=lambda p: p.name)
def test_trades_templates_have_no_safe_filter_inline_handlers_or_scripts(path):
    src = path.read_text(encoding="utf-8")
    assert "|safe" not in src and "autoescape off" not in src
    assert not re.search(r"\son[a-z]+\s*=", src, re.I), "inline event handler"
    assert "<script" not in src
