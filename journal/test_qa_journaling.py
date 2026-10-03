"""
QA gap tests for journaling J1-J3 (PRs 10-12), written against docs/product/features/journaling.md
(AC 1-34) and docs/domain/pnl-and-matching.md section 3 (vectors 6-23). Only gaps the existing
suite leaves: end-to-end R vectors through real executions -> matching -> form -> /trades/, and
render-level assertions on the HTML (Doc.text is the whole unescaped markup, so element text is
read with regexes here). Synthetic data only.
"""

import html
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from django.contrib.auth import get_user_model

from config.test_pages import Doc
from journal import copy
from journal.models import Execution, JournalEntry
from journal.test_journal_views import body, closed_trade, url

User = get_user_model()
T0 = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
_min = count()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="qj-a@example.com", password="x", timezone="UTC")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def ex(user, side, qty, price, fees="0", mult="1", symbol="MNQZ6", currency="USD"):
    return Execution.objects.create(
        user=user, broker="manual", symbol=symbol, side=side, quantity=Decimal(qty),
        price=Decimal(price), fees=Decimal(fees), contract_multiplier=Decimal(mult),
        currency=currency, executed_at=T0 + timedelta(minutes=next(_min)),
        source=Execution.SOURCE_MANUAL,
    )


def card_text(client, key="avg_r"):
    markup = body(client.get("/trades/"))
    card = re.search(rf'<section aria-labelledby="card-{key}".*?</section>', markup, re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", card.group(0))).split())


# --- AC 16, 18, 19: vectors end-to-end (real fills -> matching -> form -> stats -> card) ---------


def _v6(u):
    o = ex(u, "buy", "100", "50.00")
    ex(u, "sell", "100", "51.20", fees="2.00")
    return o


def _v12(u):
    o = ex(u, "sell", "20", "50.00")
    ex(u, "buy", "20", "48.00", fees="1.00")
    return o


def _v13(u):
    o = ex(u, "buy", "2", "80.00", mult="1000", symbol="CLZ6")
    ex(u, "sell", "2", "80.15", mult="1000", fees="8.04", symbol="CLZ6")
    return o


def _v18(u):  # partial exits: the stop check and risk use the one entry lot
    o = ex(u, "buy", "100", "50.00")
    ex(u, "sell", "40", "51.00", fees="1.00")
    ex(u, "sell", "60", "50.60", fees="1.00")
    return o


def _v2(u):  # scale-in, two partial closes: net 69.00, two entry lots
    o = ex(u, "buy", "100", "10.00", fees="1.00")
    ex(u, "buy", "50", "10.20", fees="0.50")
    ex(u, "sell", "80", "10.50", fees="0.80")
    ex(u, "sell", "70", "10.60", fees="0.70")
    return o


@pytest.mark.parametrize(
    "build, post, card",
    [
        pytest.param(_v6, {"stop_price": "49.50"}, "2.36 (n=1)", id="v6-2.3600"),
        pytest.param(_v6, {"planned_risk_amount": "59.00"}, "2.00 (n=1)", id="v8"),
        pytest.param(_v6, {"stop_price": "49.50", "planned_risk_amount": "59.00"}, "2.00 (n=1)",
                     id="v9-planned-wins"),
        pytest.param(_v12, {"stop_price": "50.50"}, "3.90 (n=1)", id="v12-short"),
        pytest.param(_v13, {"stop_price": "79.90"}, "1.46 (n=1)", id="v13-mult-1.4598"),
        pytest.param(_v18, {"stop_price": "49.50"}, "1.48 (n=1)", id="v18-partial-exits"),
        pytest.param(_v2, {"planned_risk_amount": "30.00"}, "2.30 (n=1)", id="v10b"),
        pytest.param(_v2, {"stop_price": "11.00", "planned_risk_amount": "30.00"}, "2.30 (n=1)",
                     id="v20"),
    ],
)
def test_vectors_end_to_end_through_the_form_and_the_card(logged_in, user, build, post, card):
    opening = build(user)

    resp = logged_in.post(url(opening.pk), post)

    assert resp.status_code == 302, getattr(resp, "context", None) and resp.context["form"].errors
    assert card in card_text(logged_in)


@pytest.mark.parametrize("build", [_v2], ids=["v10a-and-v19"])
@pytest.mark.parametrize("stop", ["9.80", "11.00"])
def test_multi_leg_stop_only_is_left_out_end_to_end(logged_in, user, build, stop):
    opening = build(user)

    assert logged_in.post(url(opening.pk), {"stop_price": stop}).status_code == 302

    text = card_text(logged_in)
    assert copy.AVG_R_LEFT_OUT_ONE in text and "(n=0)" in text and "0.00" not in text


def test_vector_18_stop_side_check_uses_the_entry_lot_on_a_trade_with_partial_exits(
    logged_in, user
):
    opening = _v18(user)

    resp = logged_in.post(url(opening.pk), {"stop_price": "50.00"})  # equal to entry: vector 14

    assert resp.status_code == 200
    assert resp.context["form"].errors["stop_price"] == [copy.STOP_WRONG_SIDE]
    assert not JournalEntry.unscoped.exists()
    # also above the entry lot but below the later exit prices: still the wrong side
    assert logged_in.post(url(opening.pk), {"stop_price": "50.30"}).status_code == 200


def test_vector_11_currency_mismatch_has_no_fallback_to_the_stop_end_to_end(logged_in, user):
    opening = _v6(user)
    JournalEntry.objects.create(
        user=user, opening_execution=opening, stop_price=Decimal("49.50"),
        planned_risk_amount=Decimal("59.00"), risk_currency="EUR",
    )

    text = card_text(logged_in)

    assert "(n=0)" in text and copy.AVG_R_LEFT_OUT_ONE in text
    page = body(logged_in.get(url(opening.pk)))
    status = re.search(r'<p id="r-status"[^>]*>(.*?)</p>', page, re.S).group(1)
    status = re.sub(r"<[^>]+>", " ", status)
    assert html.unescape(" ".join(status.split())) == copy.R_STATUS_CURRENCY_MISMATCH.format(
        risk_currency="EUR", trade_currency="USD"
    )


# --- AC 24: an open trade's risk counts once it closes, with no re-entry -------------------------


def test_open_trade_risk_counts_after_it_closes_without_re_entry(logged_in, user):
    opening = ex(user, "buy", "1", "100")
    assert logged_in.post(url(opening.pk), {"planned_risk_amount": "100"}).status_code == 302

    open_text = card_text(logged_in)
    assert "(n=0)" in open_text and "Left out" not in open_text

    ex(user, "sell", "1", "105")

    assert "0.05 (n=1)" in card_text(logged_in)


# --- AC 3, 4, 31: the list's journal column and cards, in the HTML -------------------------------


def _link(markup, pk, where):
    start = markup.index(f'id="trade-{pk}"' if where == "row" else f'id="trade-card-{pk}"')
    at = markup.index(f'href="{url(pk)}"', start)
    return markup[markup.rindex("<a ", 0, at): markup.index("</a>", at)]


def test_list_shows_every_state_label_and_a_named_keyboard_link_in_table_and_cards(
    logged_in, user
):
    trades = {name: closed_trade(user, symbol=name)[0] for name in
              ("NONE", "NOTE", "RISK", "BOTH", "YES", "NO")}
    for name, fields in {
        "NOTE": dict(note="n"), "RISK": dict(stop_price=Decimal("49")),
        "BOTH": dict(note="n", planned_risk_amount=Decimal("5"), risk_currency="USD"),
        "YES": dict(rules_followed=True), "NO": dict(rules_followed=False),
    }.items():
        JournalEntry.objects.create(user=user, opening_execution=trades[name], **fields)
    markup = body(logged_in.get("/trades/"))
    labels = {
        "NONE": copy.LIST_JOURNAL_ADD, "NOTE": copy.LIST_JOURNAL_NOTE_ONLY,
        "RISK": copy.LIST_JOURNAL_RISK_ONLY, "BOTH": copy.LIST_JOURNAL_NOTE_AND_RISK,
        "YES": copy.LIST_JOURNAL_FOLLOWED, "NO": copy.LIST_JOURNAL_NOT_FOLLOWED,
    }

    assert re.search(r'<th scope="col">Journal</th>', markup)
    for name, label in labels.items():
        for where in ("row", "card"):
            link = _link(markup, trades[name].pk, where)
            a = Doc(link).all("a")[0]
            assert "tabindex" not in a and a["href"] == url(trades[name].pk)  # keyboard reachable
            visible = re.search(r'<span aria-hidden="true">(.*?)</span>', link, re.S).group(1)
            sr = re.search(r'<span class="sr-only">(.*?)</span>', link, re.S).group(1)
            assert html.unescape(visible) == label  # the state is words, not color
            sr = html.unescape(sr)
            assert sr.startswith(f"{label} for {name}, opened ") and len(sr) > len(label) + 20


# --- AC 3, 5a, 27: journal page flash and rules, in the HTML -----------------------------------


def test_saved_flash_is_rendered_in_the_page_after_the_redirect(logged_in, user):
    opening, _ = closed_trade(user)

    resp = logged_in.post(url(opening.pk), {"note": "n"}, follow=True)

    assert resp.redirect_chain == [(url(opening.pk), 302)]
    flash = re.search(r'<div id="flash"[^>]*>(.*?)</div>\s*<main', body(resp), re.S)
    assert flash and copy.JOURNAL_SAVED in flash.group(1)
    assert copy.JOURNAL_SAVED not in body(logged_in.get(url(opening.pk)))  # shown once


def test_blank_form_renders_empty_inputs_and_an_empty_note(logged_in, user):
    opening, _ = closed_trade(user)
    markup = body(logged_in.get(url(opening.pk)))
    d = Doc(markup)

    for name in ("stop_price", "planned_risk_amount"):
        assert d.one("input", name=name).get("value", "") == ""
    note = re.search(r'<textarea[^>]*name="note"[^>]*>(.*?)</textarea>', markup, re.S).group(1)
    assert note.strip() == ""
    assert not [r for r in d.all("input", type="radio") if "checked" in r]


def test_saved_rules_show_in_the_panel_on_every_trade_and_viewing_changes_nothing(
    logged_in, user
):
    one, _ = closed_trade(user)
    two, _ = closed_trade(user, symbol="CLZ6")
    JournalEntry.objects.create(user=user, opening_execution=one, note="n", rules_followed=True)
    logged_in.post("/rules/", {"trading_rules": "Max 2 contracts."})
    before = list(JournalEntry.unscoped.values_list("pk", "updated_at", "rules_followed", "note"))

    for pk in (one.pk, two.pk):
        markup = body(logged_in.get(url(pk)))
        panel = re.search(r'<div id="rules-panel".*?</textarea>', markup, re.S).group(0)
        assert "Max 2 contracts." in panel
        assert "open" not in Doc(markup).one("details", id="rules-details")  # closed after load

    after = list(JournalEntry.unscoped.values_list("pk", "updated_at", "rules_followed", "note"))
    assert after == before
    assert JournalEntry.unscoped.count() == 1  # AC 34: viewing created no row for trade two


# --- AC 8 at the view level: a stale second tab never 500s and never duplicates -------------------


def test_second_tab_post_after_the_first_saved_updates_the_row_without_a_500(logged_in, user):
    opening, _ = closed_trade(user)
    tab1 = logged_in.post(url(opening.pk), {"note": "tab 1"})
    tab2 = logged_in.post(url(opening.pk), {"note": "tab 2", "rules_followed": "false"})

    assert (tab1.status_code, tab2.status_code) == (302, 302)
    entry = JournalEntry.objects.for_user(user).get()
    assert (entry.note, entry.rules_followed) == ("tab 2", False)


# --- AC 25: the card is per currency group (a known slice-1 limit, follow-ups row 30) -------------


def test_non_usd_closed_trades_do_not_leak_into_the_usd_avg_r_card(logged_in, user):
    usd = ex(user, "buy", "1", "100")
    ex(user, "sell", "1", "110")
    eur = ex(user, "buy", "1", "100", symbol="DAX", currency="EUR")
    ex(user, "sell", "1", "140", symbol="DAX", currency="EUR")
    # Both saves must really happen: a dropped EUR save would leave the card at 0.10 anyway.
    assert logged_in.post(url(usd.pk), {"planned_risk_amount": "100"}).status_code == 302
    assert logged_in.post(url(eur.pk), {"planned_risk_amount": "20"}).status_code == 302
    # EUR R would be 2.0, USD 0.1

    text = card_text(logged_in)
    assert "0.10 (n=1)" in text
    assert "Left out" not in text  # a merged-currency card would show the EUR trade as left out


# --- Probes: flip leftover, CRLF at the view, markup in a note --------------------------------


def test_flip_leftover_trade_takes_a_journal_and_checks_the_stop_against_the_flip_price(
    logged_in, user
):
    ex(user, "buy", "10", "100")
    flip = ex(user, "sell", "15", "105")  # closes the long, opens a short of 5 at 105

    assert logged_in.get(url(flip.pk)).status_code == 200
    bad = logged_in.post(url(flip.pk), {"stop_price": "104"})  # short: stop must be above 105
    assert bad.status_code == 200
    assert bad.context["form"].errors["stop_price"] == [copy.STOP_WRONG_SIDE]
    assert logged_in.post(url(flip.pk), {"stop_price": "106"}).status_code == 302


def test_crlf_note_of_exactly_10000_after_normalisation_saves_through_the_view(logged_in, user):
    opening, _ = closed_trade(user)
    crlf, lf = chr(13) + chr(10), chr(10)
    note = crlf.join(["x" * 9] * 1000) + "x"  # 10,000 chars after normalisation, 10,999 raw

    resp = logged_in.post(url(opening.pk), {"note": note})

    assert resp.status_code == 302
    assert JournalEntry.objects.for_user(user).get().note == note.replace(crlf, lf)


def test_note_and_rules_markup_is_escaped_on_the_journal_page(logged_in, user):
    opening, _ = closed_trade(user)
    evil = "</textarea><script>alert(1)</script>"
    # Both saves must really happen, or the escaping of that field is never exercised.
    assert logged_in.post(url(opening.pk), {"note": evil}).status_code == 302
    assert logged_in.post("/rules/", {"trading_rules": evil}).status_code == 302

    markup = body(logged_in.get(url(opening.pk)))

    # note textarea + rules textarea + rules preview
    assert "<script>alert" not in markup and markup.count("&lt;/textarea&gt;") >= 3
