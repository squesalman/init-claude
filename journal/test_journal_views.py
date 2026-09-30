"""
PR J2: /trades/<id>/journal/, POST /rules/, the /trades/ journal column and Avg R card.
Specs: docs/adr/0007-journaling.md sections 8-10; docs/product/features/journaling.md AC 1-34;
docs/design/journaling.md 3-6, 12 (copy). Synthetic data only.
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.utils import timezone

from accounts import copy as accounts_copy
from config.test_pages import Doc
from journal import copy
from journal.models import CrossTenantForeignKeyError, Execution, JournalEntry
from journal.views import _r_status

User = get_user_model()
HTMX = {"HX-Request": "true"}
T0 = datetime(timezone.now().year, 6, 15, 14, 0, tzinfo=UTC)
_minutes = count()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="jv-a@example.com", password="x", timezone="UTC")


@pytest.fixture
def other(db):
    return User.objects.create_user(email="jv-b@example.com", password="x", timezone="UTC")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def fill(user, side, qty="1", price="50.00", symbol="MNQZ6", currency="USD", label=""):
    return Execution.objects.create(
        user=user, broker="manual", broker_account_label=label, symbol=symbol, side=side,
        quantity=Decimal(qty), price=Decimal(price), currency=currency,
        executed_at=T0 + timedelta(minutes=next(_minutes)), source=Execution.SOURCE_MANUAL,
    )


def closed_trade(user, entry="50.00", exit="51.00", side="buy", **kw):
    """One single-entry trade; returns (opening, closing)."""
    return fill(user, side, price=entry, **kw), fill(
        user, "sell" if side == "buy" else "buy", price=exit, **kw
    )


def url(pk):
    return f"/trades/{pk}/journal/"


def body(resp):
    return resp.content.decode()


def snapshot():
    return tuple(
        sorted(JournalEntry.unscoped.values_list(
            "pk", "user_id", "note", "rules_followed", "stop_price", "planned_risk_amount",
            "risk_currency", "updated_at",
        ))
    ), tuple(sorted(User.objects.values_list("pk", "trading_rules")))


# --- GET: blank and prefilled (AC 2, 5a, 6) ----------------------------------------------------


def test_get_with_no_entry_shows_a_blank_form_and_the_rules_panel(logged_in, user):
    opening, _ = closed_trade(user)

    resp = logged_in.get(url(opening.pk))

    assert resp.status_code == 200
    assert [t.name for t in resp.templates][0] == "journal/journal_form.html"
    c = resp.context
    assert c["trade"]["id"] == opening.pk and c["trade"]["symbol"] == "MNQZ6"
    assert not c["form"].is_bound and c["form"]["note"].value() in ("", None)
    assert c["rules_open"] is False and c["notice"] is None and c["r_status"] is None
    assert c["back_url"] == f"/trades/#trade-{opening.pk}"
    assert not JournalEntry.unscoped.exists()  # AC 34: viewing creates no row
    d = Doc(body(resp))
    d.one("div", id="rules-panel")
    assert not [a for a in d.all("input", type="radio") if "checked" in a]
    assert copy.JOURNAL_TITLE.format(symbol="MNQZ6") in d.text
    assert copy.JOURNAL_BACK_TO_TRADES in d.text and "Cancel" not in d.text


def test_rules_panel_form_posts_to_rules_with_htmx_and_is_not_nested(logged_in, user):
    opening, _ = closed_trade(user)
    markup = body(logged_in.get(url(opening.pk)))
    d = Doc(markup)

    rules_form = d.one("form", action="/rules/")
    assert rules_form["method"] == "post"
    assert (rules_form["hx-post"], rules_form["hx-target"], rules_form["hx-swap"]) == (
        "/rules/", "#rules-panel", "outerHTML",
    )
    assert d.one("input", name="next")["value"] == url(opening.pk)
    journal_form = d.one("form", action=url(opening.pk))
    assert "hx-post" not in journal_form  # full POST + PRG in this slice
    rules_at = markup.index('action="/rules/"')
    journal_at = markup.index(f'action="{url(opening.pk)}"')
    assert rules_at < journal_at
    assert markup.index("</form>", rules_at) < journal_at  # closed first: never nested


def test_saved_values_prefill_the_form(logged_in, user):
    opening, _ = closed_trade(user)
    JournalEntry.objects.create(
        user=user, opening_execution=opening, note="Waited.", rules_followed=False,
        stop_price=Decimal("49.5"), planned_risk_amount=Decimal("62.5"), risk_currency="USD",
    )

    resp = logged_in.get(url(opening.pk))

    d = Doc(body(resp))
    assert "Waited." in d.text
    [checked] = [a for a in d.all("input", type="radio") if "checked" in a]
    assert checked["value"] == "false"
    assert d.one("input", name="stop_price")["value"] == "49.5"  # no stored-scale zeros
    assert d.one("input", name="planned_risk_amount")["value"] == "62.5"
    assert resp.context["risk_open"] and resp.context["both_set"]


# --- POST: save, PRG, nothing to save (AC 3-7, 9) ----------------------------------------------


def test_note_only_saves_and_redirects_to_the_same_page_with_a_flash(logged_in, user):
    opening, _ = closed_trade(user)

    resp = logged_in.post(url(opening.pk), {"note": "  Waited for the retest.  "})

    assert resp.status_code == 302 and resp["Location"] == url(opening.pk)
    entry = JournalEntry.objects.for_user(user).get()
    assert (entry.opening_execution, entry.note, entry.rules_followed) == (
        opening, "Waited for the retest.", None,
    )
    page = logged_in.get(resp["Location"])
    assert [str(m) for m in page.context["messages"]] == [copy.JOURNAL_SAVED]


@pytest.mark.parametrize("posted, stored", [("true", True), ("false", False)])
def test_answer_saves(logged_in, user, posted, stored):
    opening, _ = closed_trade(user)

    logged_in.post(url(opening.pk), {"rules_followed": posted})

    assert JournalEntry.objects.for_user(user).get().rules_followed is stored


def test_all_blank_with_no_entry_creates_nothing_and_says_so(logged_in, user):
    opening, _ = closed_trade(user)

    blank = {"note": "   ", "stop_price": "", "planned_risk_amount": ""}
    resp = logged_in.post(url(opening.pk), blank)

    assert resp.status_code == 200
    assert resp.context["notice"] == {"variant": "info", "text": copy.JOURNAL_NOTHING_TO_SAVE}
    assert copy.JOURNAL_NOTHING_TO_SAVE in Doc(body(resp)).text
    assert not JournalEntry.unscoped.exists()


def test_editing_only_the_note_keeps_the_answer(logged_in, user):
    opening, _ = closed_trade(user)
    logged_in.post(url(opening.pk), {"rules_followed": "true"})

    logged_in.post(url(opening.pk), {"note": "later thoughts"})  # AC 7: no radio posted

    entry = JournalEntry.objects.for_user(user).get()
    assert (entry.rules_followed, entry.note) == (True, "later thoughts")


def test_posted_opening_execution_is_ignored(logged_in, user):
    opening, _ = closed_trade(user)
    elsewhere, _ = closed_trade(user, symbol="CLZ6")

    logged_in.post(url(opening.pk), {
        "note": "n", "opening_execution": elsewhere.pk, "user": 999, "risk_currency": "EUR",
        "planned_risk_amount": "10",
    })  # AC 13

    entry = JournalEntry.objects.for_user(user).get()
    assert (entry.opening_execution_id, entry.user_id, entry.risk_currency) == (
        opening.pk, user.pk, "USD",
    )


def test_risk_currency_follows_the_trade(logged_in, user):
    opening, _ = closed_trade(user, currency="EUR")  # vector 21

    logged_in.post(url(opening.pk), {"planned_risk_amount": "40.00"})
    entry = JournalEntry.objects.for_user(user).get()
    assert (entry.planned_risk_amount, entry.risk_currency) == (Decimal("40.00"), "EUR")

    logged_in.post(url(opening.pk), {"planned_risk_amount": "", "note": "keep the row"})
    entry.refresh_from_db()
    assert (entry.planned_risk_amount, entry.risk_currency) == (None, None)


# --- POST invalid: 200 re-render, values kept, nothing saved (hard requirements 1, 2) ------------


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("planned_risk_amount", "0.00001", copy.RISK_DECIMALS),  # over-scale, NUMERIC(19,4)
        ("planned_risk_amount", "10000000000000000", copy.NUMBER_TOO_LARGE),  # over-digits
        ("stop_price", "49.00000000001", copy.STOP_DECIMALS),  # over-scale, NUMERIC(20,10)
        ("stop_price", "12345678901", copy.NUMBER_TOO_LARGE),  # over-digits
        ("planned_risk_amount", "0", copy.RISK_NOT_POSITIVE),  # the CHECK never sees it
        ("planned_risk_amount", "-5", copy.RISK_NOT_POSITIVE),
        ("planned_risk_amount", "abc", copy.RISK_NOT_NUMBER),
        ("stop_price", "51", copy.STOP_WRONG_SIDE),  # long at 50.00
    ],
)
def test_bad_risk_values_re_render_200_with_the_field_message(logged_in, user, field, value,
                                                              message):
    opening, _ = closed_trade(user)
    JournalEntry.objects.create(user=user, opening_execution=opening, note="saved")
    before = snapshot()

    resp = logged_in.post(url(opening.pk), {"note": "typed text", field: value})

    assert resp.status_code == 200
    assert resp.context["form"].errors[field] == [message]
    assert resp.context["risk_open"] is True  # the error is reachable
    d = Doc(body(resp))
    assert message in d.text and "typed text" in d.text
    assert d.one("input", name=field)["value"] == value
    assert accounts_copy.SIGNUP_ERROR_SUMMARY_TITLE in d.text
    assert snapshot() == before


def test_note_over_the_limit_re_renders_and_saves_nothing(logged_in, user):
    opening, _ = closed_trade(user)

    resp = logged_in.post(url(opening.pk), {"note": "x" * 10_001})

    assert resp.status_code == 200 and "note" in resp.context["form"].errors
    assert not JournalEntry.unscoped.exists()


def test_a_failed_submit_shows_the_status_line_for_the_saved_values_not_the_posted_ones(
    logged_in, user
):
    opening, _ = closed_trade(user)
    JournalEntry.objects.create(
        user=user, opening_execution=opening, planned_risk_amount=Decimal("10"),
        risk_currency="USD",
    )

    resp = logged_in.post(url(opening.pk), {"planned_risk_amount": "0"})

    assert resp.context["r_status"] == copy.R_STATUS_OK


# --- Save failures: 200 re-render, text kept (AC 14, 14a) -----------------------------------------


def test_database_error_on_save_keeps_the_text(logged_in, user, caplog):
    opening, _ = closed_trade(user)

    with patch.object(JournalEntry, "save", side_effect=DatabaseError("disk full")):
        with caplog.at_level(logging.ERROR, logger="journal.views"):
            resp = logged_in.post(url(opening.pk), {"note": "my long note after a loss"})

    assert resp.status_code == 200
    assert resp.context["notice"] == {"variant": "attention", "text": copy.JOURNAL_SAVE_FAILED}
    d = Doc(body(resp))
    assert copy.JOURNAL_SAVE_FAILED in d.text and "my long note after a loss" in d.text
    assert not JournalEntry.unscoped.exists()
    assert [r.getMessage() for r in caplog.records if r.name == "journal.views"] == [
        f"journal save failed: DatabaseError user={user.pk} model=JournalEntry"
    ]


def test_cross_tenant_error_on_save_is_a_form_notice_and_logs_no_values(logged_in, user, caplog):
    opening, _ = closed_trade(user)
    leak = CrossTenantForeignKeyError(
        "JournalEntry.opening_execution (pk=77) belongs to user_id=4242, not this row's user_id=1."
    )

    with patch("journal.views.save_journal_entry", side_effect=leak):
        with caplog.at_level(logging.ERROR, logger="journal.views"):
            resp = logged_in.post(url(opening.pk), {"note": "secret words"})

    assert resp.status_code == 200
    assert resp.context["notice"]["text"] == copy.JOURNAL_SAVE_FAILED
    assert "secret words" in Doc(body(resp)).text
    [line] = [r for r in caplog.records if r.name == "journal.views"]
    assert line.getMessage() == (
        f"journal save failed: CrossTenantForeignKeyError user={user.pk} model=JournalEntry"
    )
    assert line.exc_info is None and "4242" not in line.getMessage()


# --- Tenant isolation (AC 11, 12; ADR-0007 test 3) ------------------------------------------------


def _not_found_shape(resp):
    """Status, template and the page's <main> (masked CSRF tokens in the nav differ per
    render, so the whole body is not byte-identical, ADR-0007 test 3)."""
    markup = body(resp)
    return (
        resp.status_code, [t.name for t in resp.templates][:1],
        markup[markup.index("<main"):markup.index("</main>")],
    )


def test_another_users_trade_is_a_404_identical_to_a_missing_id(client, user, other):
    opening, closing = closed_trade(user)
    JournalEntry.objects.create(user=user, opening_execution=opening, note="A's note")
    client.force_login(other)
    before = snapshot()

    missing = client.get(url(999_999))
    foreign_get = client.get(url(opening.pk))
    foreign_post = client.post(url(opening.pk), {"note": "B was here", "rules_followed": "true"})
    closing_fill = client.get(url(closing.pk))

    assert missing.status_code == 404
    assert [t.name for t in missing.templates][0] == "journal/trade_not_found.html"
    for resp in (foreign_get, foreign_post, closing_fill):
        assert _not_found_shape(resp) == _not_found_shape(missing)
    assert "A's note" not in body(foreign_get) and "MNQZ6" not in body(foreign_get)
    assert copy.NOT_FOUND_TITLE in Doc(body(missing)).text
    assert snapshot() == before


def test_closing_fill_of_my_own_trade_is_a_404(logged_in, user):
    _, closing = closed_trade(user)

    resp = logged_in.post(url(closing.pk), {"note": "n"})

    assert resp.status_code == 404 and not JournalEntry.unscoped.exists()


def test_login_required_and_methods(client, user):
    opening, _ = closed_trade(user)

    assert client.get(url(opening.pk))["Location"].startswith("/login/")
    htmx = client.get(url(opening.pk), headers=HTMX)
    assert htmx.status_code == 401 and htmx["HX-Redirect"].startswith("/login/")

    client.force_login(user)
    assert client.put(url(opening.pk)).status_code == 405
    assert client.get("/rules/").status_code == 405


# --- Multi-leg, open trade and the status line (AC 18, 23, 24; design 3.5, 3.6) -


def test_multi_leg_stop_saves_unchecked_with_the_hint_and_the_status_line(logged_in, user):
    opening = fill(user, "buy", qty="100", price="10.00")
    fill(user, "buy", qty="50", price="10.20")
    fill(user, "sell", qty="150", price="10.50")

    assert logged_in.get(url(opening.pk)).context["risk_open"] is True  # multi-leg opens it
    resp = logged_in.post(url(opening.pk), {"stop_price": "11.00"})  # vector 19
    assert resp.status_code == 302

    page = logged_in.get(url(opening.pk))
    c = page.context
    assert copy.STOP_HELP_MULTI_LEG in Doc(body(page)).text  # rendered, not just in context
    assert c["r_status"] == copy.R_STATUS_STOP_MULTI_LEG
    assert c["trade"]["entries"] == copy.TRADE_ENTRIES.format(n=2)


def test_stop_help_names_the_side_and_the_entry(logged_in, user):
    """Review PR 11: the help must be in the rendered HTML and wired to the inputs; a context
    key the template never renders (or a BoundField cached before help_text was set) hid it."""
    long_open, _ = closed_trade(user, entry="19850.25", exit="19862.00")
    short_open, _ = closed_trade(user, entry="80.30", exit="80.15", side="sell", symbol="CLZ6")

    long_page = Doc(body(logged_in.get(url(long_open.pk))))
    assert copy.STOP_HELP_LONG.format(entry="19,850.25") in long_page.text
    assert copy.RISK_HELP.format(currency="USD") in long_page.text
    assert "id_stop_price_helptext" in long_page.one("input", name="stop_price")["aria-describedby"]
    assert "id_planned_risk_amount_helptext" in (
        long_page.one("input", name="planned_risk_amount")["aria-describedby"]
    )
    short_page = Doc(body(logged_in.get(url(short_open.pk))))
    assert copy.STOP_HELP_SHORT.format(entry="80.30") in short_page.text
    c = logged_in.get(url(long_open.pk)).context
    assert (c["trade"]["entries"], c["risk_currency"]) == (None, "USD")


def test_stop_help_survives_a_post_error_re_render(logged_in, user):
    """Review PR 11 (high pass): validation builds every BoundField before the view runs, so
    help_text set in the view never rendered on a POST. It must come from the form itself."""
    opening, _ = closed_trade(user, entry="19850.25", exit="19862.00")

    resp = logged_in.post(url(opening.pk), {"stop_price": "19999"})  # wrong side of the entry

    assert resp.status_code == 200
    page = Doc(body(resp))
    assert copy.STOP_HELP_LONG.format(entry="19,850.25") in page.text
    assert copy.RISK_HELP.format(currency="USD") in page.text
    assert "id_stop_price_helptext" in page.one("input", name="stop_price")["aria-describedby"]


def test_open_trade_status_line_is_visible_without_opening_the_risk_section(logged_in, user):
    """Review PR 11: R_STATUS_TRADE_OPEN sat inside the collapsed <details> and was never seen."""
    opening = fill(user, "buy", qty="1", price="10")

    html_ = body(logged_in.get(url(opening.pk)))
    inside = re.search(r'<details id="risk-details".*?</details>', html_, re.S).group(0)

    assert copy.R_STATUS_TRADE_OPEN in Doc(html_).text
    assert copy.R_STATUS_TRADE_OPEN not in inside


def test_open_trade_can_be_journaled_and_says_so(logged_in, user):
    opening = fill(user, "buy")

    assert logged_in.get(url(opening.pk)).context["r_status"] == copy.R_STATUS_TRADE_OPEN
    assert logged_in.post(url(opening.pk), {"stop_price": "49"}).status_code == 302


@pytest.mark.parametrize(
    "saved, status",
    [
        (dict(stop_price=Decimal("49.50")), copy.R_STATUS_OK),
        (dict(planned_risk_amount=Decimal("5"), risk_currency="USD"), copy.R_STATUS_OK),
        (dict(stop_price=Decimal("50.50")), copy.R_STATUS_STOP_NOT_A_RISK),  # legacy row
        (dict(planned_risk_amount=Decimal("5"), risk_currency="EUR"),
         copy.R_STATUS_CURRENCY_MISMATCH.format(risk_currency="EUR", trade_currency="USD")),
        (dict(note="no risk"), None),
    ],
)
def test_status_line_by_reason(logged_in, user, saved, status):
    opening, _ = closed_trade(user)
    JournalEntry.objects.create(user=user, opening_execution=opening, **saved)

    assert logged_in.get(url(opening.pk)).context["r_status"] == status


def test_status_line_for_a_legacy_non_positive_planned_risk():
    """The CHECK now forbids the row, so no DB row can carry it; the mapping still must."""
    trade = SimpleNamespace(is_open=False, currency="USD")
    entry = SimpleNamespace(stop_price=None, planned_risk_amount=Decimal("0"), risk_currency="USD")

    assert _r_status(trade, entry) == copy.R_STATUS_RISK_NOT_POSITIVE


# --- My rules: POST /rules/ (AC 26-30; ADR-0007 section 8) -


def test_rules_panel_is_closed_with_the_prompt_or_the_saved_rules(logged_in, user):
    opening, _ = closed_trade(user)
    c = logged_in.get(url(opening.pk)).context
    assert (c["rules_open"], c["rules_text"]) == (False, "")
    assert copy.RULES_PROMPT in Doc(body(logged_in.get(url(opening.pk)))).text

    user.trading_rules = "Max 2 contracts."
    user.save()
    d = Doc(body(logged_in.get(url(opening.pk))))
    assert "Max 2 contracts." in d.text and copy.RULES_ACTION_EDIT in d.text
    assert "open" not in d.one("details", id="rules-details")


def test_htmx_rules_save_returns_the_open_panel_and_touches_no_entry(logged_in, user):
    opening, _ = closed_trade(user)
    entry = JournalEntry.objects.create(
        user=user, opening_execution=opening, note="n", rules_followed=True
    )
    before = (entry.updated_at, entry.rules_followed)

    resp = logged_in.post(
        "/rules/", {"trading_rules": "Wait for the retest.\r\nMax 2.", "next": url(opening.pk)},
        headers=HTMX,
    )

    assert resp.status_code == 200
    assert [t.name for t in resp.templates][0] == "journal/partials/rules_panel.html"
    assert "HX-Request" in resp["Vary"]
    c = resp.context
    assert (c["rules_open"], c["rules_status"], c["rules_notice"]) == (True, copy.RULES_SAVED, None)
    d = Doc(body(resp))
    d.one("div", id="rules-panel")
    assert "open" in d.one("details", id="rules-details")
    assert copy.RULES_SAVED in d.text
    user.refresh_from_db()
    assert user.trading_rules == "Wait for the retest.\nMax 2."
    entry.refresh_from_db()
    assert (entry.updated_at, entry.rules_followed) == before  # AC 27


def test_htmx_rules_too_long_is_a_200_field_error_and_saves_nothing(logged_in, user):
    resp = logged_in.post("/rules/", {"trading_rules": "r" * 10_001}, headers=HTMX)

    assert resp.status_code == 200 and resp.context["rules_open"] is True
    assert "trading_rules" in resp.context["rules_form"].errors
    user.refresh_from_db()
    assert user.trading_rules == ""


def test_htmx_rules_database_error_keeps_the_text(logged_in, user):
    with patch.object(User, "save", side_effect=DatabaseError("down")):
        resp = logged_in.post("/rules/", {"trading_rules": "my rules text"}, headers=HTMX)

    assert resp.status_code == 200
    assert resp.context["rules_notice"] == copy.RULES_SAVE_FAILED
    d = Doc(body(resp))
    assert copy.RULES_SAVE_FAILED in d.text and "my rules text" in d.text
    assert resp.context["rules_text"] == ""  # the preview still shows what is saved
    user.refresh_from_db()
    assert user.trading_rules == ""


def test_no_js_rules_save_redirects_to_the_server_built_journal_url(logged_in, user):
    opening, _ = closed_trade(user)

    resp = logged_in.post(
        "/rules/", {"trading_rules": "rules", "next": f"{url(opening.pk)}?x=1#frag"}
    )

    assert resp.status_code == 302 and resp["Location"] == url(opening.pk)
    assert [str(m) for m in logged_in.get(resp["Location"]).context["messages"]] == [
        copy.RULES_SAVED
    ]


@pytest.mark.parametrize(
    "next_url",
    ["https://evil.example/trades/1/journal/", "//evil.example/trades/1/journal/",
     "/imports/", "/trades/abc/journal/", "javascript:alert(1)", "", "http://testserver/nope/"],
)
def test_no_js_rules_save_with_a_bad_next_goes_to_trades(logged_in, user, next_url):
    resp = logged_in.post("/rules/", {"trading_rules": "rules", "next": next_url})

    assert resp.status_code == 302 and resp["Location"] == "/trades/"


def test_no_js_rules_error_re_renders_the_journal_page_with_the_panel_open(logged_in, user):
    opening, _ = closed_trade(user)
    JournalEntry.objects.create(user=user, opening_execution=opening, note="saved note")

    resp = logged_in.post("/rules/", {"trading_rules": "r" * 10_001, "next": url(opening.pk)})

    assert resp.status_code == 200
    assert [t.name for t in resp.templates][0] == "journal/journal_form.html"
    c = resp.context
    assert c["rules_open"] is True and "trading_rules" in c["rules_form"].errors
    assert "saved note" in Doc(body(resp)).text  # the journal form shows the saved entry


def test_no_js_rules_database_error_shows_the_save_failed_notice(logged_in, user):
    """Review PR 11: the rules notice must reach the panel on the no-JS journal page, not be
    swallowed by the page's own notice parameter (which left an empty info box)."""
    opening, _ = closed_trade(user)

    with patch.object(User, "save", side_effect=DatabaseError("down")):
        resp = logged_in.post(
            "/rules/", {"trading_rules": "my rules text", "next": url(opening.pk)}
        )

    assert resp.status_code == 200
    assert resp.context["rules_notice"] == copy.RULES_SAVE_FAILED
    assert not resp.context["notice"]  # the journal's own notice stays empty
    d = Doc(body(resp))
    assert copy.RULES_SAVE_FAILED in d.text and "my rules text" in d.text


def test_no_js_rules_error_for_someone_elses_trade_is_the_404(client, user, other):
    opening, _ = closed_trade(user)
    client.force_login(other)

    resp = client.post("/rules/", {"trading_rules": "r" * 10_001, "next": url(opening.pk)})

    assert resp.status_code == 404
    assert [t.name for t in resp.templates][0] == "journal/trade_not_found.html"


def test_no_js_rules_error_without_next_is_a_400(logged_in):
    resp = logged_in.post("/rules/", {"trading_rules": "r" * 10_001})

    assert resp.status_code == 400 and resp["Content-Type"].startswith("text/plain")


def test_rules_only_ever_change_the_posting_user(client, user, other):
    user.trading_rules = "A's rules"
    user.save()
    client.force_login(other)

    client.post("/rules/", {"trading_rules": "B's rules", "user": user.pk, "id": user.pk})  # AC 29

    user.refresh_from_db()
    other.refresh_from_db()
    assert (user.trading_rules, other.trading_rules) == ("A's rules", "B's rules")


def test_rules_login_required(client, db):
    resp = client.post("/rules/", {"trading_rules": "x"}, headers=HTMX)
    assert resp.status_code == 401 and resp["HX-Redirect"].startswith("/login/")

    resp = client.post("/rules/", {"trading_rules": "x"})
    assert resp.status_code == 302 and resp["Location"].startswith("/login/")


def test_editing_rules_never_changes_past_answers(logged_in, user):
    one, _ = closed_trade(user)
    two, _ = closed_trade(user, symbol="CLZ6")
    logged_in.post(url(one.pk), {"rules_followed": "true"})
    logged_in.post("/rules/", {"trading_rules": "new wording"}, headers=HTMX)
    logged_in.post(url(two.pk), {"rules_followed": "false"})  # AC 30

    answers = dict(JournalEntry.objects.for_user(user).values_list("opening_execution_id",
                                                                   "rules_followed"))
    assert answers == {one.pk: True, two.pk: False}


# --- /trades/ journal column and the Avg R card (AC 1, 20-22, 31; ADR-0007 section 9) -


def rows(client):
    return {t["id"]: t for t in client.get("/trades/").context["trades"]}


def test_journal_column_labels_for_every_state(logged_in, user):
    trades = {name: closed_trade(user, symbol=name)[0] for name in
              ("NONE", "NOTE", "RISK", "BOTH", "YES", "NO", "EMPTY")}
    for name, fields in {
        "NOTE": dict(note="n"), "RISK": dict(stop_price=Decimal("49")),
        "BOTH": dict(note="n", planned_risk_amount=Decimal("5"), risk_currency="USD"),
        "YES": dict(rules_followed=True, note="n"), "NO": dict(rules_followed=False),
        "EMPTY": dict(),
    }.items():
        JournalEntry.objects.create(user=user, opening_execution=trades[name], **fields)

    by_id = rows(logged_in)

    expected = {
        "NONE": ("add", copy.LIST_JOURNAL_ADD), "NOTE": ("note_only", copy.LIST_JOURNAL_NOTE_ONLY),
        "RISK": ("risk_only", copy.LIST_JOURNAL_RISK_ONLY),
        "BOTH": ("note_and_risk", copy.LIST_JOURNAL_NOTE_AND_RISK),
        "YES": ("followed", copy.LIST_JOURNAL_FOLLOWED),
        "NO": ("not_followed", copy.LIST_JOURNAL_NOT_FOLLOWED),
        "EMPTY": ("add", copy.LIST_JOURNAL_ADD),  # design 11 item 2
    }
    for name, (state, label) in expected.items():
        row = by_id[trades[name].pk]
        assert (row["journal_state"], row["journal_label"]) == (state, label), name
        assert row["journal_url"] == url(trades[name].pk)
        assert row["journal_sr"] == copy.LIST_JOURNAL_SR_CONTEXT.format(
            state=label, symbol=name, opened=row["opened"]
        )


def test_rows_and_cards_link_to_the_journal_with_an_anchor_id(logged_in, user):
    opening, _ = closed_trade(user)

    d = Doc(body(logged_in.get("/trades/")))

    d.one("tr", id=f"trade-{opening.pk}")
    d.one("li", id=f"trade-{opening.pk}")
    links = d.all("a", href=url(opening.pk))
    assert len(links) == 2  # table and card
    assert copy.LIST_JOURNAL_ADD in d.text


def _with_r(user, net, risk=None):
    opening, _ = closed_trade(user, entry="100", exit=str(Decimal(100) + Decimal(net)))
    if risk:
        JournalEntry.objects.create(
            user=user, opening_execution=opening, planned_risk_amount=Decimal(risk),
            risk_currency="USD",
        )


def test_avg_r_card_vector_23(logged_in, user):
    _with_r(user, "20", "100")
    _with_r(user, "5", "100")
    _with_r(user, "3")
    _with_r(user, "-4")
    open_one = fill(user, "buy", price="100")
    JournalEntry.objects.create(
        user=user, opening_execution=open_one, planned_risk_amount=Decimal("100"),
        risk_currency="USD",
    )

    avg_r = logged_in.get("/trades/").context["cards"]["avg_r"]

    assert avg_r["value"] == "0.12 (n=2)"
    assert avg_r["left_out"] == copy.AVG_R_LEFT_OUT_MANY.format(n=2)
    assert avg_r["help"] is None
    assert "0.12 (n=2)" in Doc(body(logged_in.get("/trades/"))).text


def test_avg_r_card_vector_17_and_the_singular_left_out(logged_in, user):
    opening, _ = closed_trade(user, entry="50.00", exit="51.18", qty="100")  # net 118.00
    JournalEntry.objects.create(user=user, opening_execution=opening, stop_price=Decimal("49.50"))
    _with_r(user, "5")  # no risk: left out
    losing, _ = closed_trade(user, entry="50.00", exit="49.48", qty="100")  # net -52.00
    JournalEntry.objects.create(user=user, opening_execution=losing, stop_price=Decimal("49.50"))
    _with_r(user, "118", "59")  # 2.0000

    avg_r = logged_in.get("/trades/").context["cards"]["avg_r"]

    assert avg_r["value"] == "1.11 (n=3)"
    assert avg_r["left_out"] == copy.AVG_R_LEFT_OUT_ONE


def test_avg_r_card_n_0_never_shows_zero(logged_in, user):
    _with_r(user, "5")

    avg_r = logged_in.get("/trades/").context["cards"]["avg_r"]

    assert avg_r["value"] == accounts_copy.NO_VALUE and "0.00" not in avg_r["value"]
    assert avg_r["help"] == accounts_copy.AVG_R_HELP
    assert avg_r["left_out"] == copy.AVG_R_LEFT_OUT_ONE  # shown even when n = 0


def test_avg_r_card_only_open_trades_has_no_left_out_line(logged_in, user):
    fill(user, "buy")

    avg_r = logged_in.get("/trades/").context["cards"]["avg_r"]

    assert (avg_r["value"], avg_r["left_out"]) == (accounts_copy.NO_VALUE, None)


def test_trades_isolation_never_uses_another_users_entries(client, user, other):
    mine, _ = closed_trade(other)
    theirs, _ = closed_trade(user)
    JournalEntry.objects.create(
        user=user, opening_execution=theirs, planned_risk_amount=Decimal("1"), risk_currency="USD"
    )
    client.force_login(other)

    c = client.get("/trades/").context

    assert list(rows(client)) == [mine.pk]
    assert c["cards"]["avg_r"]["value"] == accounts_copy.NO_VALUE


def test_trades_query_count_does_not_grow_with_entries(logged_in, user,
                                                       django_assert_max_num_queries):
    for i in range(30):
        opening, _ = closed_trade(user, symbol=f"S{i}")
        JournalEntry.objects.create(user=user, opening_execution=opening, note="n")

    with django_assert_max_num_queries(4):  # session, user, executions, entries
        assert len(logged_in.get("/trades/").context["trades"]) == 30


def test_journal_page_query_count(logged_in, user, django_assert_max_num_queries):
    opening, _ = closed_trade(user)
    for i in range(20):
        closed_trade(user, symbol=f"S{i}")

    with django_assert_max_num_queries(5):  # session, user, opening, bucket, entry
        assert logged_in.get(url(opening.pk)).status_code == 200
