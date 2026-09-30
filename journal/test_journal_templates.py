"""
PR J3: render-level checks for the journal page, the rules panel, the trades list journal
column and the Avg R card (docs/design/journaling.md 3, 5, 6, 12a). Every assertion reads the
rendered HTML, never resp.context: template behaviour is what these guard.
"""

import html
import re
from decimal import Decimal

import pytest
from django.conf import settings

from accounts import copy as accounts_copy
from config.test_pages import Doc
from journal import copy
from journal.forms import TEXT_LIMIT
from journal.models import JournalEntry
from journal.test_journal_views import HTMX, _with_r, body, closed_trade, fill, url

TEMPLATES = settings.BASE_DIR / "templates"
APP_CSS = settings.BASE_DIR / "static" / "css" / "app.css"
J3_TEMPLATES = [
    TEMPLATES / "journal" / "journal_form.html",
    TEMPLATES / "journal" / "trade_not_found.html",
    *(TEMPLATES / "journal" / "partials" / name for name in (
        "trade_summary.html", "rules_panel.html", "rules_question.html", "journal_link.html",
        "trade_cards.html", "trade_table.html", "stat_card.html",
    )),
    *(TEMPLATES / "partials" / name for name in ("field.html", "field_errors.html", "icon.html")),
]


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        email="jt@example.com", password="x", timezone="UTC"
    )


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def page(client, pk):
    resp = client.get(url(pk))
    assert resp.status_code == 200
    return body(resp)


def classes(attrs):
    return attrs.get("class", "").split()


def risk_details(markup):
    return re.search(r'<details id="risk-details".*?</details>', markup, re.S)


def rules_details(markup):
    return re.search(r'<details id="rules-details".*?</details>', markup, re.S)


# --- /trades/: distinct row and card ids (design 3.1, 5.1, 5.2; follow-ups row 40) -----------


def test_row_and_card_have_distinct_ids_with_scroll_margin(logged_in, user):
    opening, _ = closed_trade(user)

    d = Doc(body(logged_in.get("/trades/")))

    row = d.one("tr", id=f"trade-{opening.pk}")
    card = d.one("li", id=f"trade-card-{opening.pk}")
    assert "scroll-mt-16" in classes(row) and "scroll-mt-16" in classes(card)
    ids = [a["id"] for _, a in d.tags if "id" in a]
    assert len(ids) == len(set(ids)), "duplicate id on /trades/"


def test_each_card_has_one_link_a_44px_journal_line_after_a_top_border(logged_in, user):
    closed_trade(user, symbol="AAA")
    closed_trade(user, symbol="BBB")
    markup = body(logged_in.get("/trades/"))
    cards = re.findall(r'<li id="trade-card-\d+".*?</li>', markup, re.S)

    assert len(cards) == 2
    for card in cards:
        links = Doc(card).all("a")
        assert len(links) == 1  # one target per card (design 5.2)
        assert {"flex", "w-full", "min-h-[44px]"} <= set(classes(links[0]))
        assert re.search(r'<p class="[^"]*\bborder-t\b[^"]*\bpt-2\b[^"]*">\s*<a ', card)


def _link_in(markup, pk, where):
    """The journal link's markup in the table (row) or card list."""
    start = markup.index(f'id="trade-{pk}"' if where == "row" else f'id="trade-card-{pk}"')
    at = markup.index(f'href="{url(pk)}"', start)
    return markup[markup.rindex("<a ", 0, at): markup.index("</a>", at)]


def test_journal_link_style_and_icon_follow_the_state(logged_in, user):
    """Design 5.3: journaled = text color + medium weight; not yet = muted; icons plus /
    document / pencil, the same icon for Yes and No (no result reading)."""
    trades = {name: closed_trade(user, symbol=name)[0] for name in ("ADD", "YES", "NO", "NOTE")}
    for name, fields in {"YES": {"rules_followed": True}, "NO": {"rules_followed": False},
                         "NOTE": {"note": "n"}}.items():
        JournalEntry.objects.create(user=user, opening_execution=trades[name], **fields)
    markup = body(logged_in.get("/trades/"))

    for where in ("row", "card"):
        link = {n: _link_in(markup, t.pk, where) for n, t in trades.items()}
        tag = {n: Doc(v).all("a")[0] for n, v in link.items()}
        svg = {n: re.search(r"<svg.*?</svg>", v, re.S).group(0) for n, v in link.items()}
        for name in ("YES", "NO"):
            assert {"font-medium", "text-text"} <= set(classes(tag[name]))
            assert "text-text-muted" not in classes(tag[name])
        for name in ("ADD", "NOTE"):
            assert "text-text-muted" in classes(tag[name])
            assert "font-medium" not in classes(tag[name])
        assert svg["YES"] == svg["NO"]
        assert len({svg["ADD"], svg["YES"], svg["NOTE"]}) == 3
        assert f'<span aria-hidden="true">{copy.LIST_JOURNAL_ADD}</span>' in link["ADD"]
        assert '<span class="sr-only">' in link["ADD"]


# --- Journal page: back links (3.1, 3.7) ------------------------------------------------------


def test_back_links_are_a_css_switched_pair_top_and_bottom(logged_in, user):
    opening, _ = closed_trade(user)
    d = Doc(page(logged_in, opening.pk))

    table = d.all("a", href=f"/trades/#trade-{opening.pk}")
    cards = d.all("a", href=f"/trades/#trade-card-{opening.pk}")

    assert len(table) == 2 and len(cards) == 2  # top "Trades" and bottom "Back to trades"
    for a in table:
        assert {"hidden", "sm:inline-flex"} <= set(classes(a))
    for a in cards:
        assert "sm:hidden" in classes(a) and "hidden" not in classes(a)
    markup = page(logged_in, opening.pk)
    for text in (copy.JOURNAL_BACK, copy.JOURNAL_BACK_TO_TRADES):
        assert len(re.findall(r"<a [^>]*>(?:(?!</a>).)*" + re.escape(text) + "</a>", markup,
                              re.S)) >= 2


# --- Trade summary header (3.1) ---------------------------------------------------------------


def test_header_has_side_icon_signed_pnl_with_tone_and_escaped_account(logged_in, user):
    opening, _ = closed_trade(user, entry="50.00", exit="51.00", label="<b>Combine</b>")
    markup = page(logged_in, opening.pk)

    h1 = re.search(r"<h1.*?</h1>", markup, re.S).group(0)
    assert "MNQZ6" in h1 and "Long" in h1 and "<svg" in h1
    assert re.search(r'<span class="[^"]*\btext-gain\b[^"]*">\+\$1\.00</span>', markup)
    assert "&lt;b&gt;Combine&lt;/b&gt;" in markup and "<b>Combine" not in markup


def test_open_trade_header_says_open(logged_in, user):
    opening = fill(user, "buy")
    markup = page(logged_in, opening.pk)

    head = markup[markup.index("<h1"): markup.index(copy.JOURNAL_INTRO)]
    assert "badge-open" in head and ">Open</span>" in head
    assert "text-gain" not in head and "text-loss" not in head


# --- My rules panel (3.2, 4.2) ----------------------------------------------------------------


def test_closed_panel_with_no_rules_shows_add_and_the_muted_prompt(logged_in, user):
    opening, _ = closed_trade(user)
    markup = page(logged_in, opening.pk)
    details = rules_details(markup).group(0)

    assert "open" not in Doc(details).one("details", id="rules-details")
    summary = re.search(r"<summary.*?</summary>", details, re.S).group(0)
    assert f'<span class="rules-action-idle">{copy.RULES_ACTION_ADD}</span>' in summary
    assert f'<span class="rules-action-close">{copy.RULES_ACTION_CLOSE}</span>' in summary
    after = markup[rules_details(markup).end():]
    preview = re.match(r'\s*<p class="([^"]*)">(.*?)</p>', after, re.S)
    assert preview, "the preview must be the element right after </details>"
    assert preview.group(1).split() == ["rules-preview", "text-text-muted"]
    assert preview.group(2) == copy.RULES_PROMPT


def test_saved_rules_preview_keeps_line_breaks_escaped_and_says_edit(logged_in, user):
    user.trading_rules = "Wait for the retest.\nMax 2 contracts.\n<script>x</script>"
    user.save()
    opening, _ = closed_trade(user)
    markup = page(logged_in, opening.pk)

    summary = re.search(r"<summary.*?</summary>", rules_details(markup).group(0), re.S).group(0)
    assert f'<span class="rules-action-idle">{copy.RULES_ACTION_EDIT}</span>' in summary
    preview = re.match(r'\s*<p class="([^"]*)">(.*?)</p>', markup[rules_details(markup).end():],
                       re.S)
    assert preview.group(1) == "rules-preview"  # saved rules: normal text color
    assert preview.group(2) == (
        "Wait for the retest.\nMax 2 contracts.\n&lt;script&gt;x&lt;/script&gt;"
    )
    assert "<br" not in preview.group(2)


def test_htmx_rules_save_renders_the_panel_open_with_the_focused_status(logged_in, user):
    opening, _ = closed_trade(user)

    markup = body(logged_in.post(
        "/rules/", {"trading_rules": "Max 2.", "next": url(opening.pk)}, headers=HTMX
    ))

    assert re.search(r'<details id="rules-details"[^>]*\bopen\b', markup)
    status = re.search(r"<p ([^>]*)>(?:(?!</p>).)*" + re.escape(copy.RULES_SAVED), markup, re.S)
    assert status and 'role="status"' in status.group(1)
    assert 'tabindex="-1"' in status.group(1) and "autofocus" in status.group(1)
    # The preview after the open <details> is hidden by CSS (details[open] + .rules-preview).
    assert re.search(r'</details>\s*<p class="rules-preview">Max 2\.</p>', markup)


def test_rules_too_long_error_is_focusable_and_tied_to_the_textarea(logged_in, user):
    markup = body(logged_in.post("/rules/", {"trading_rules": "r" * 10_001}, headers=HTMX))
    d = Doc(markup)

    error = d.one("div", id="id_trading_rules_error")
    assert error["tabindex"] == "-1" and "autofocus" in error
    textarea = d.one("textarea", name="trading_rules")
    assert textarea["aria-invalid"] == "true"
    assert "id_trading_rules_error" in textarea["aria-describedby"]
    assert re.search(r'<details id="rules-details"[^>]*\bopen\b', markup)


def test_no_js_rules_save_lands_on_the_journal_page_with_the_flash_and_a_closed_panel(
        logged_in, user):
    """The htmx swap's full-page fallback (design 2, 3.2): 302 + flash, panel closed again."""
    opening, _ = closed_trade(user)

    resp = logged_in.post("/rules/", {"trading_rules": "Max 2.", "next": url(opening.pk)},
                          follow=True)

    assert resp.redirect_chain == [(url(opening.pk), 302)]
    markup = body(resp)
    flash = re.search(r'<div id="flash"([^>]*)>(.*?)</div>\s*<main', markup, re.S)
    assert flash and 'tabindex="-1"' in flash.group(1) and "autofocus" in flash.group(1)
    assert copy.RULES_SAVED in flash.group(2)
    assert "open" not in Doc(markup).one("details", id="rules-details")
    assert re.search(r'</details>\s*<p class="rules-preview">Max 2\.</p>', markup)


def test_no_js_rules_error_renders_the_full_page_with_the_panel_open(logged_in, user):
    opening, _ = closed_trade(user)

    resp = logged_in.post("/rules/", {"trading_rules": "r" * 10_001, "next": url(opening.pk)})

    assert resp.status_code == 200
    d = Doc(body(resp))
    assert "open" in d.one("details", id="rules-details")
    error = d.one("div", id="id_trading_rules_error")
    assert error["tabindex"] == "-1" and "autofocus" in error
    d.one("form", action=url(opening.pk))  # the whole journal page, not the bare panel


def test_other_field_errors_are_not_autofocused(logged_in, user):
    """The focus flag is the rules panel's only: the journal form's summary takes focus."""
    opening, _ = closed_trade(user)
    d = Doc(body(logged_in.post(url(opening.pk), {"note": "n" * 10_001})))

    error = d.one("div", id="id_note_error")
    assert "tabindex" not in error and "autofocus" not in error


def test_rules_request_error_region_keeps_the_shape_app_js_toggles(logged_in, user):
    opening, _ = closed_trade(user)
    markup = page(logged_in, opening.pk)

    region = re.search(r'<div role="status" data-request-error="([^"]*)">\s*<p [^>]*hidden>'
                       r".*?<span data-request-error-text></span>", markup, re.S)
    assert region and html.unescape(region.group(1)) == copy.RULES_SAVE_FAILED


# --- The question (3.3) -----------------------------------------------------------------------


def test_question_is_a_fieldset_of_two_label_tiles_with_no_default(logged_in, user):
    opening, _ = closed_trade(user)
    markup = page(logged_in, opening.pk)
    d = Doc(markup)

    fieldset = d.one("fieldset")
    assert fieldset["aria-describedby"] == "id_rules_followed_helptext"
    assert copy.FOLLOWED_HELP in re.search(r'id="id_rules_followed_helptext"[^>]*>(.*?)</p>',
                                           markup, re.S).group(1)
    assert re.search(r"<legend[^>]*>" + re.escape(copy.FOLLOWED_LEGEND) + "</legend>", markup)
    radios = d.all("input", type="radio")
    assert [r["name"] for r in radios] == ["rules_followed", "rules_followed"]
    assert not [r for r in radios if "checked" in r]
    for i, text in enumerate((copy.FOLLOWED_YES, copy.FOLLOWED_NO)):
        tile = re.search(rf'<label for="id_rules_followed_{i}" class="radio-tile">(.*?)</label>',
                         markup, re.S)
        assert tile, i
        assert f'id="id_rules_followed_{i}"' in tile.group(1)
        assert text in html.unescape(tile.group(1))
        assert '<span class="radio-check">' in tile.group(1)


def test_saved_answer_is_checked_and_an_error_joins_the_fieldset_description(logged_in, user):
    opening, _ = closed_trade(user)
    JournalEntry.objects.create(user=user, opening_execution=opening, rules_followed=False)
    d = Doc(page(logged_in, opening.pk))
    assert [r["value"] for r in d.all("input", type="radio") if "checked" in r] == ["false"]

    d = Doc(body(logged_in.post(url(opening.pk), {"rules_followed": "maybe"})))
    assert d.one("fieldset")["aria-describedby"] == (
        "id_rules_followed_helptext id_rules_followed_error"
    )
    d.one("a", href="#id_rules_followed")  # the error summary line ...
    d.one("div", id="id_rules_followed")  # ... has a target to land on


# --- Note and rules counters (3.4) ------------------------------------------------------------


def test_note_and_rules_use_the_counter_component_with_no_maxlength(logged_in, user):
    opening, _ = closed_trade(user)
    markup = page(logged_in, opening.pk)
    d = Doc(markup)

    counters = d.all("div", x_data="charCounter")
    assert len(counters) == 2
    too_long = {c["data-too-long"] for c in counters}
    assert too_long == {copy.NOTE_TOO_LONG, copy.RULES_TOO_LONG}
    for c in counters:
        assert (c["data-limit"], c["data-from"]) == (str(TEXT_LIMIT), "9000")
        assert c["data-counter"] == copy.COUNTER
        assert (c["data-near"], c["data-over"]) == (copy.COUNTER_LIVE_NEAR, copy.COUNTER_LIVE_OVER)
        assert (c["x-on:input"], c["x-on:focusout"]) == ("update", "leave")
    for name in ("note", "trading_rules"):
        assert "maxlength" not in d.one("textarea", name=name)
    # No-JS path: the counter and the live region render empty; the server message rules.
    assert len(re.findall(r'<p role="status" class="sr-only" x-text="live"></p>', markup)) == 2
    assert len(re.findall(r'<p [^>]*x-text="counter"></p>', markup)) == 2


# --- Risk section (3.5, 3.6) ------------------------------------------------------------------


def test_planned_risk_has_the_hidden_currency_suffix_and_the_stop_has_none(logged_in, user):
    opening, _ = closed_trade(user)
    markup = page(logged_in, opening.pk)
    d = Doc(markup)

    boxes = re.findall(r'<div class="input-suffix-box">(.*?)</div>', markup, re.S)
    assert len(boxes) == 1
    assert 'id="id_planned_risk_amount"' in boxes[0]
    assert boxes[0].rstrip().endswith('<span class="input-suffix" aria-hidden="true">USD</span>')
    assert 'id="risk-currency"' not in markup
    risk = d.one("input", name="planned_risk_amount")
    assert "id_planned_risk_amount_helptext" in risk["aria-describedby"]
    helptext = re.search(r'id="id_planned_risk_amount_helptext"[^>]*>(.*?)</p>', markup).group(1)
    assert "in USD" in helptext
    for name in ("stop_price", "planned_risk_amount"):
        a = d.one("input", name=name)
        assert (a["type"], a["inputmode"], a["autocomplete"]) == ("text", "decimal", "off")
        assert (a["autocapitalize"], a["spellcheck"]) == ("none", "false")


def test_risk_section_is_closed_without_risk_and_open_with_a_risk_error(logged_in, user):
    opening, _ = closed_trade(user)
    assert "open" not in Doc(page(logged_in, opening.pk)).one("details", id="risk-details")

    markup = body(logged_in.post(url(opening.pk), {"planned_risk_amount": "abc"}))
    assert "open" in Doc(markup).one("details", id="risk-details")
    assert copy.RISK_NOT_NUMBER in risk_details(markup).group(0)  # the error is reachable


def test_both_set_line_sits_inside_the_risk_section(logged_in, user):
    opening, _ = closed_trade(user)
    JournalEntry.objects.create(
        user=user, opening_execution=opening, stop_price=Decimal("49"),
        planned_risk_amount=Decimal("5"), risk_currency="USD",
    )
    markup = page(logged_in, opening.pk)

    assert copy.RISK_BOTH_SET in risk_details(markup).group(0)


def test_status_line_is_after_the_collapsed_section_with_no_live_role(logged_in, user):
    opening, _ = closed_trade(user)
    JournalEntry.objects.create(
        user=user, opening_execution=opening, planned_risk_amount=Decimal("5"),
        risk_currency="USD",
    )
    markup = page(logged_in, opening.pk)

    details = risk_details(markup)
    assert copy.R_STATUS_OK not in details.group(0)
    status = re.match(r'\s*<p id="r-status"([^>]*)>(.*?)</p>', markup[details.end():], re.S)
    assert status, "the status line must follow the risk <details>"
    assert "role=" not in status.group(1)
    assert copy.R_STATUS_OK in status.group(2)


# --- Actions (3.7) ----------------------------------------------------------------------------


def test_actions_bar_has_the_busy_save_button_and_the_back_pair(logged_in, user):
    opening, _ = closed_trade(user)
    markup = page(logged_in, opening.pk)

    bar = re.search(r'<div class="action-bar">(.*?)</div>', markup, re.S).group(1)
    button = Doc(bar).one("button", type="submit")
    assert button["data-busy"] == copy.JOURNAL_SAVE_BUSY
    assert {"btn-primary", "w-full", "sm:w-auto"} <= set(classes(button))
    assert copy.JOURNAL_SAVE in bar and bar.count(copy.JOURNAL_BACK_TO_TRADES) == 2


# --- 404 (4.6) --------------------------------------------------------------------------------


def test_not_found_page_copy(logged_in, user):
    markup = html.unescape(body(logged_in.get(url(999_999))))

    assert re.search(r"<h1[^>]*>" + re.escape(copy.NOT_FOUND_TITLE) + "</h1>", markup)
    assert copy.NOT_FOUND_BODY in markup
    assert re.search(r'<a href="/trades/"[^>]*>' + re.escape(copy.NOT_FOUND_LINK) + "</a>",
                     markup)


# --- Avg R card (6) ---------------------------------------------------------------------------


def avg_r_card(client):
    markup = body(client.get("/trades/"))
    card = re.search(r'<section aria-labelledby="card-avg_r".*?</section>', markup, re.S)
    return html.unescape(card.group(0))


def test_avg_r_card_n_0_shows_help_then_left_out(logged_in, user):
    _with_r(user, "5")  # closed, no risk: left out, n = 0

    card = avg_r_card(logged_in)

    assert accounts_copy.AVG_R_HELP in card and copy.AVG_R_LEFT_OUT_ONE in card
    assert card.index(accounts_copy.AVG_R_HELP) < card.index(copy.AVG_R_LEFT_OUT_ONE)


def test_avg_r_card_with_a_value_shows_left_out_and_no_help(logged_in, user):
    _with_r(user, "20", "100")
    _with_r(user, "3")
    _with_r(user, "-4")

    card = avg_r_card(logged_in)

    assert copy.AVG_R_LEFT_OUT_MANY.format(n=2) in card
    assert accounts_copy.AVG_R_HELP not in card


def test_avg_r_card_with_only_open_trades_has_no_left_out_line(logged_in, user):
    fill(user, "buy")

    card = avg_r_card(logged_in)

    assert accounts_copy.NO_VALUE in card and "Left out" not in card


# --- Template hygiene and the CSS bundle (CSP, follow-ups row 41) -----------------------------


@pytest.mark.parametrize("path", J3_TEMPLATES, ids=lambda p: p.name)
def test_j3_templates_have_no_unsafe_output_inline_code_or_alpine_expressions(path):
    src = path.read_text(encoding="utf-8")
    assert "|safe" not in src and "autoescape off" not in src and "mark_safe" not in src
    assert not re.search(r"\son[a-z]+\s*=", src, re.I), "inline event handler"
    assert "<script" not in src and "style=" not in src
    for value in re.findall(r'\sx-[a-z:.-]+="([^"]*)"', src):
        assert re.fullmatch(r"[A-Za-z_$][\w$]*", value), value  # CSP build: names only


def _class_tokens(src):
    tokens = set()
    src = re.sub(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", " ", src, flags=re.S)  # tags hold quotes
    for value in re.findall(r'\sclass="([^"]*)"', src):
        tokens.update(value.split())
    return tokens


def _css_escape(token):
    return re.sub(r"([:\[\]/.()%,#&])", r"\\\1", token)


@pytest.mark.parametrize("path", J3_TEMPLATES, ids=lambda p: p.name)
def test_every_class_the_templates_use_is_in_the_bundle(path):
    """The committed app.css is rebuilt from the templates (scripts/tailwind.sh); a class
    missing here is a stale bundle (follow-ups row 41)."""
    css = APP_CSS.read_text(encoding="utf-8")
    missing = [t for t in sorted(_class_tokens(path.read_text(encoding="utf-8")))
               if "." + _css_escape(t) not in css]
    assert not missing, missing


def test_bundle_has_the_rules_preview_and_disclosure_rules():
    css = APP_CSS.read_text(encoding="utf-8")
    for needle in (
        "details[open]+.rules-preview{display:none}", "white-space:pre-line",
        "-webkit-line-clamp:3", "details[open] .rules-action-idle",
        "details[open] .rules-action-close", "env(safe-area-inset-bottom)",
    ):
        assert needle in css, needle


def test_open_trade_exit_has_screen_reader_text_on_the_journal_page(logged_in, user):
    """Review PR 12: `copy` on the journal page is journal.copy, so EXIT_OPEN_SR rendered empty
    and a screen reader heard the exit as a bare hyphen."""
    opening = fill(user, "buy", qty="1", price="10")

    markup = page(logged_in, opening.pk)

    assert accounts_copy.EXIT_OPEN_SR in Doc(markup).text
    assert re.search(r'sr-only">\s*' + re.escape(accounts_copy.EXIT_OPEN_SR), markup)


def test_suffix_box_rings_on_keyboard_focus_only():
    """Review PR 12: :focus-within rang the box on a mouse click; the app convention (and the
    radio tile) is :focus-visible."""
    css = APP_CSS.read_text(encoding="utf-8")

    assert ".input-suffix-box:has(input:focus-visible)" in css
    assert ".input-suffix-box:focus-within" not in css
