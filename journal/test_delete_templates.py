"""
Rendered-markup checks for the PR C2 "Delete this import" UI (docs/design/
import-account-label.md 3.2, 3.3, 3.4, 3.5, 5, 6, 7): the confirm body (dialog and no-JS
page), the three entry points, and focus after a delete. Behaviour lives in
test_delete_views.py; the JS in test_app_js.py.
"""

import re

import pytest
from django.contrib.auth import get_user_model

from config.test_pages import Doc, assert_every_input_is_labelled
from journal import copy
from journal.models import JournalEntry
from journal.test_delete_views import confirm_url, journal, post_delete, rows
from journal.test_import_views import T1, T1_CHANGED, T2, T3, csv_bytes, detail, uploaded

HTMX = {"HX-Request": "true"}
BLANK_BANNER = (
    "Their IDs match trades you already imported, but the details differ. This usually means "
    "the file is from a different account. Nothing was lost or overwritten. Delete this "
    "import first, then upload again with an Account name, so nothing is counted twice."
)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        email="deltpl@example.com", password="x", timezone="America/New_York"
    )


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def body(resp):
    return resp.content.decode()


def delete_links(markup, pk):
    """Every control that opens the confirm for this batch."""
    return [a for t, a in Doc(markup).tags if t == "a" and a.get("href", "").startswith(
        confirm_url(pk))]


# --- confirm body: variants (3.3) -------------------------------------------------------------


def test_simple_variant_has_no_checkbox_focuses_cancel_and_says_permanent(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    markup = body(logged_in.get(confirm_url(batch.pk), headers=HTMX))
    d = Doc(markup)

    assert not d.all("input", type="checkbox")
    assert d.one("a", **{"data-cancel": ""})["autofocus"] == ""
    assert copy.DELETE_PERMANENT in d.text and copy.DELETE_BUTTON in d.text
    assert d.one("input", name="journal_count")["value"] == "0"
    form = d.one("form")
    assert form["hx-post"] == confirm_url(batch.pk) and form["hx-target"] == "#delete-confirm"
    assert d.one("h2", id="delete-title")
    assert d.one("p", id="delete-desc")
    assert d.one("button", type="submit")["x-bind:disabled"] == "locked"  # false: no box


def test_no_trades_variant_uses_the_no_trades_body(logged_in):
    uploaded(logged_in, csv_bytes(T1))
    again = uploaded(logged_in, csv_bytes(T1))
    d = Doc(body(logged_in.get(confirm_url(again.pk), headers=HTMX)))
    assert copy.DELETE_BODY_NO_TRADES in d.text and copy.DELETE_PERMANENT in d.text


def test_two_step_variant_lists_entries_in_a_labelled_region_and_gates_on_the_tick(
    logged_in, user
):
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    journal(user, batch, note="Waited for the <b>retest</b>", rules=True)
    journal(user, batch, note="", rules=False)
    markup = body(logged_in.get(confirm_url(batch.pk), headers=HTMX))
    d = Doc(markup)

    assert d.one("h2", id="delete-title")["autofocus"] == ""  # title, never Delete
    region = d.one("div", role="region")
    assert region["tabindex"] == "0" and region["aria-label"] == copy.DELETE_LIST_HEADING
    assert "“Waited for the &lt;b&gt;retest&lt;/b&gt;”" in markup  # escaped, never |safe
    assert copy.DELETE_NO_NOTE in d.text
    assert "Rules: followed" in d.text and "Rules: not followed" in d.text
    assert copy.DELETE_LIST_HELPER in d.text
    assert "View trade" not in d.text  # no trade page yet: no broken link

    tick = d.one("input", type="checkbox")
    assert (tick["name"], tick["value"], tick["x-ref"]) == ("confirm", "on", "tick")
    label = re.search(r"<label[^>]*>(.*?)</label>", markup, re.S).group(1)
    assert 'name="confirm"' in label  # the label wraps the box: the full sentence names it
    assert "Also delete my 2 journal entries (1 with a written note). This can't be undone." in (
        Doc(label).text
    )
    assert "disabled" not in d.one("button", type="submit")  # no JS: enabled, server enforces
    assert d.one("button", type="submit")["x-bind:disabled"] == "locked"
    root = d.one("div", id="delete-confirm")
    assert root["x-data"] == "deleteConfirm"
    assert root["data-live-on"] == copy.DELETE_LIVE_ENABLED
    assert root["data-live-off"] == copy.DELETE_LIVE_DISABLED
    assert d.one("input", name="journal_count")["value"] == "2"
    assert copy.DELETE_BUTTON_WITH_ENTRIES in d.text
    assert_every_input_is_labelled(d)


def test_more_than_20_entries_offer_show_all(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(*[rows(i) for i in range(11)]))
    journal(user, batch, n=21)
    d = Doc(body(logged_in.get(confirm_url(batch.pk), headers=HTMX)))
    show = d.one("a", href=confirm_url(batch.pk) + "?all=1")
    assert show["hx-get"] == show["href"] and show["hx-target"] == "#delete-confirm"
    assert "Show all 21" in d.text


def test_not_ticked_notice_sits_above_the_box_and_focuses_it(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    journal(user, batch)
    logged_in.get(confirm_url(batch.pk))  # seeds the session-confirmed count the POST checks
    markup = body(post_delete(logged_in, batch.pk, count=1, headers=HTMX))
    d = Doc(markup)
    assert d.one("input", type="checkbox")["autofocus"] == ""
    notice = markup.index(copy.DELETE_NOT_TICKED)
    assert notice < markup.index('type="checkbox"')
    assert 'role="status"' in markup[markup.rindex("<p ", 0, notice):notice]


def test_not_ticked_notice_is_not_shown_when_the_entries_vanished_before_the_post(
    logged_in, user
):
    """Code review finding: if the journal entries are deleted between the GET and this POST,
    the (session) shown-count is still truthy so 'tick the box' is set, but the fresh read
    finds nothing to confirm and renders no checkbox: a notice pointing at a box that isn't
    there. The notice is now gated on the box existing."""
    batch = uploaded(logged_in, csv_bytes(T1))
    entries = journal(user, batch)
    logged_in.get(confirm_url(batch.pk))  # shown = 1 is stashed in the session
    JournalEntry.unscoped.filter(pk__in=[e.pk for e in entries]).delete()  # gone in another tab

    markup = body(post_delete(logged_in, batch.pk, count=1, headers=HTMX))

    assert copy.DELETE_NOT_TICKED not in markup
    assert 'type="checkbox"' not in markup


def test_stale_notice_is_on_top_focused_and_the_box_is_unticked(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    journal(user, batch, n=2)
    markup = body(post_delete(logged_in, batch.pk, count=1, tick=True, headers=HTMX))
    d = Doc(markup)
    notice = d.one("p", id="delete-notice")
    assert notice["role"] == "status" and notice["tabindex"] == "-1" and "autofocus" in notice
    assert markup.index(copy.DELETE_STALE) < markup.index('id="delete-desc"')
    assert "checked" not in d.one("input", type="checkbox")
    assert d.one("input", name="journal_count")["value"] == "2"


def test_from_banner_is_posted_back(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    d = Doc(body(logged_in.get(confirm_url(batch.pk) + "?from=banner", headers=HTMX)))
    assert d.one("input", name="from")["value"] == "banner"


def test_network_failure_region_carries_the_failed_copy(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    d = Doc(body(logged_in.get(confirm_url(batch.pk), headers=HTMX)))
    assert d.one("div", **{"data-request-error": copy.DELETE_FAILED})["role"] == "status"


def test_busy_label_on_submit(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    d = Doc(body(logged_in.get(confirm_url(batch.pk), headers=HTMX)))
    assert d.one("button", type="submit")["data-busy"] == copy.DELETE_BUSY


def test_filename_with_markup_stays_escaped(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    batch.filename = "<img src=x onerror=alert(1)>.csv"
    batch.save(update_fields=["filename"])
    markup = body(logged_in.get(confirm_url(batch.pk), headers=HTMX))
    assert "<img" not in markup and "&lt;img" in markup


# --- no-JS full page (3.3) --------------------------------------------------------------------


def test_full_page_is_a_plain_form_with_an_h1_and_cancel_back_to_detail(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    markup = body(logged_in.get(confirm_url(batch.pk)))
    d = Doc(markup)
    assert d.one("h1", id="delete-title")
    form = d.one("form", action=confirm_url(batch.pk))
    assert form["method"] == "post" and "hx-post" not in form
    assert d.one("a", **{"data-cancel": ""})["href"] == f"/imports/{batch.pk}/"
    assert "<dialog" not in markup


# --- entry points (3.2, 3.5, 4) ---------------------------------------------------------------


def test_detail_header_opens_the_dialog(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    markup = body(detail(logged_in, batch))
    (link,) = delete_links(markup, batch.pk)
    assert link["href"] == confirm_url(batch.pk)  # no-JS fallback: the full page
    assert link["hx-get"] == confirm_url(batch.pk)
    assert link["hx-target"] == "#delete-dialog-body" and "data-opens-dialog" in link
    d = Doc(markup)
    d.one("dialog", id="delete-dialog")
    d.one("div", id="delete-dialog-body")
    assert d.one("h1")["tabindex"] == "-1"  # focus fallback when the opener is gone


def test_blank_conflict_banner_says_delete_first_and_has_the_button(logged_in):
    uploaded(logged_in, csv_bytes(T1))
    batch = uploaded(logged_in, csv_bytes(T1_CHANGED, T3))
    markup = body(detail(logged_in, batch))
    assert copy.CONFLICT_BODY_BLANK == BLANK_BANNER
    banner = markup[markup.index('id="conflict-banner"'):markup.index('id="import-summary"')]
    assert BLANK_BANNER in Doc(banner).text.replace("\n", " ")
    links = delete_links(markup, batch.pk)
    url = confirm_url(batch.pk)
    assert [a["href"] for a in links] == [url, url + "?from=banner"]  # header, then banner
    assert all(a["hx-get"] == a["href"] for a in links)
    assert copy.DELETE_ACTION in Doc(banner).text


def test_not_recorded_conflict_banner_offers_no_delete(logged_in):
    uploaded(logged_in, csv_bytes(T1, T2))
    batch = uploaded(
        logged_in, csv_bytes(T1_CHANGED, {**T2, "EnteredAt": "12/19/2026 09:59:00 +00:00"})
    )
    markup = body(detail(logged_in, batch))
    assert 'id="conflict-banner"' in markup
    assert [a["href"] for a in delete_links(markup, batch.pk)] == [confirm_url(batch.pk)]


def test_mismatch_banner_is_attention_with_a_delete_button(logged_in):
    uploaded(logged_in, csv_bytes(T1), label="A")
    second = uploaded(logged_in, csv_bytes(T1), label="B")
    markup = body(detail(logged_in, second))
    banner = markup[markup.index('id="hint-banner"'):markup.index('id="import-summary"')]
    assert "bg-attention-bg" in banner
    assert f'href="{confirm_url(second.pk)}?from=banner"' in banner


def test_list_row_menu_is_a_disclosure_with_view_and_delete(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    markup = body(logged_in.get("/imports/"))
    d = Doc(markup)
    menus = d.all("details", **{"data-menu": ""})
    assert len(menus) == 2  # desktop table and mobile card; one shown at a time
    summaries = [a for t, a in d.tags if t == "summary" and a.get("aria-label")]
    assert {s["aria-label"] for s in summaries} == {f"Actions for {batch.filename}"}
    links = delete_links(markup, batch.pk)
    assert len(links) == 2 and all(a["hx-target"] == "#delete-dialog-body" for a in links)
    assert copy.DELETE_ACTION in d.text and "View details" in d.text
    d.one("dialog", id="delete-dialog")


def test_empty_list_has_no_dialog(logged_in):
    assert "<dialog" not in body(logged_in.get("/imports/"))


# --- after delete (3.4) -----------------------------------------------------------------------


def test_after_a_banner_delete_the_account_input_is_focused(logged_in):
    d = Doc(body(logged_in.get("/imports/?from=banner")))
    assert d.one("input", name="account")["autofocus"] == ""
    assert "open" in d.one("details", id="account-details")


def test_after_any_other_delete_the_flash_is_focused(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    resp = post_delete(logged_in, batch.pk)
    markup = body(logged_in.get(resp["Location"]))
    d = Doc(markup)
    flash = d.one("div", id="flash")
    assert flash["tabindex"] == "-1" and "autofocus" in flash
    assert "autofocus" not in d.one("input", name="account")


def test_plain_imports_page_autofocuses_nothing(logged_in):
    uploaded(logged_in, csv_bytes(T1))
    assert "autofocus" not in body(logged_in.get("/imports/"))


def test_hidden_attribute_beats_display_utilities_in_the_compiled_css():
    # The request-error <p class="flex" hidden> showed its icon (live check, PR C2).
    from django.conf import settings

    css = (settings.BASE_DIR / "static" / "css" / "app.css").read_text()
    assert "[hidden]{display:none!important}" in css
