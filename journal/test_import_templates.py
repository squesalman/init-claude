"""
Rendered-markup checks for the PR C1 import pages (docs/design/import-account-label.md
sections 3.1, 3.2, 3.5, 6 and 7). Behaviour and context are covered in
test_import_views.py; this file checks structure, accessibility hooks and the
htmx/Alpine wiring. Visual layout and contrast are not testable here.
"""

import hashlib
import html
import json
import re

import pytest
from django.conf import settings

from config.test_pages import Doc, assert_every_input_is_labelled
from journal import copy
from journal.models import ImportBatch
from journal.test_import_views import (
    T1,
    T1_BAD_PNL,
    T1_CHANGED,
    T2,
    T3,
    UNSUPPORTED,
    csv_bytes,
    detail,
    upload,
    uploaded,
)

TEMPLATES = settings.BASE_DIR / "templates"
NEW_TEMPLATES = [
    TEMPLATES / "journal" / "import_list.html",
    TEMPLATES / "journal" / "import_detail.html",
    *sorted((TEMPLATES / "journal" / "partials").glob("*.html")),
]
VENDOR = settings.BASE_DIR / "static" / "vendor"
HTMX = {"HX-Request": "true"}


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        email="tpl@example.com", password="x", timezone="America/New_York"
    )


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def page(resp):
    return resp.content.decode()


def main_of(markup):
    return re.search(r'<main id="main".*?</main>', markup, re.S).group(0)


def region(markup, element_id):
    """The markup from the element with this id to the end of the document."""
    start = markup.index(f'id="{element_id}"')
    return markup[markup.rindex("<", 0, start):]


@pytest.fixture
def messy_batch(logged_in):
    """Filled label, 1 conflict, 2 failed, 1 imported (the busiest detail page)."""
    uploaded(logged_in, csv_bytes(T1, T2), label="X")
    return uploaded(logged_in, csv_bytes(T1_CHANGED, T1_BAD_PNL, UNSUPPORTED, T3), label="X")


# --- upload form (3.1, 4) -------------------------------------------------------------------


def test_upload_form_posts_multipart_with_and_without_js(logged_in):
    markup = page(logged_in.get("/imports/"))
    d = Doc(markup)
    form = d.one("form", id="upload-form")
    assert form["method"] == "post" and form["enctype"] == "multipart/form-data"
    assert form["action"] == "/imports/"  # no-JS: plain POST
    assert form["hx-post"] == "/imports/" and form["hx-encoding"] == "multipart/form-data"
    assert form["hx-target"] == "#account-field" and form["hx-swap"] == "outerHTML"
    # One disabled <fieldset> disables file, Account and the button while importing.
    assert form["hx-disabled-elt"] == "find fieldset"
    fieldset = re.search(r"<fieldset.*?</fieldset>", markup, re.S).group(0)
    assert 'type="file"' in fieldset and 'name="account"' in fieldset and "<button" in fieldset
    upload_button = [
        b for b in d.all("button", type="submit") if b.get("data-busy") == copy.UPLOAD_BUSY
    ]
    assert len(upload_button) == 1
    assert copy.UPLOAD_BUTTON in Doc(main_of(markup)).text
    assert_every_input_is_labelled(d)
    assert d.one("input", type="file")["accept"] == ".csv,text/csv"


def test_upload_form_has_a_polite_live_region_for_the_busy_state(logged_in):
    d = Doc(page(logged_in.get("/imports/")))
    live = d.one("p", id="upload-live")
    assert live["role"] == "status" and live["data-busy-live"] == copy.UPLOAD_BUSY_LIVE


def test_account_details_closed_without_saved_labels(logged_in):
    d = Doc(page(logged_in.get("/imports/")))
    details = [a for t, a in d.tags if t == "details" and a.get("id") == "account-details"]
    assert details and "open" not in details[0]
    account = d.one("input", name="account")
    assert account["placeholder"] == copy.ACCOUNT_PLACEHOLDER_EMPTY
    assert account["list"] == "account-labels" and account["autocomplete"] == "off"
    assert "maxlength" not in account  # never truncated (design 4)
    assert "id_account_helptext" in account["aria-describedby"]


def test_account_details_open_with_saved_labels(logged_in):
    uploaded(logged_in, csv_bytes(T1), label="Combine 50K")
    d = Doc(page(logged_in.get("/imports/")))
    details = [a for t, a in d.tags if t == "details" and a.get("id") == "account-details"]
    assert "open" in details[0]
    assert d.one("input", name="account")["placeholder"] == copy.ACCOUNT_PLACEHOLDER_SAVED
    assert {"value": "Combine 50K"} in d.all("option")


def test_account_counter_is_an_alpine_component_without_inline_expressions(logged_in):
    markup = page(logged_in.get("/imports/"))
    d = Doc(markup)
    wrapper = d.one("div", id="account-field")
    assert wrapper["x-data"] == "accountField"
    assert wrapper["data-limit"] == "64" and wrapper["data-from"] == "50"
    assert "%(show_value)d" in wrapper["data-too-long"]
    # CSP build: attribute values are plain names, never expressions or string building.
    for _, attrs in d.tags:
        for name, value in attrs.items():
            if name.startswith(("x-", ":", "@")) and name not in ("x-data", "x-ref", "x-cloak"):
                assert re.fullmatch(r"[A-Za-z_][\w.]*", value), (name, value)


def test_account_error_partial_marks_the_field_and_keeps_the_disclosure_open(logged_in, user):
    resp = upload(logged_in, csv_bytes(T1), label="A" * 65, **HTMX)
    markup = page(resp)
    d = Doc(markup)
    assert "open" in [a for t, a in d.tags if t == "details"][0]
    account = d.one("input", name="account")
    assert account["aria-invalid"] == "true"
    assert "id_account_error" in account["aria-describedby"]
    assert "id_account_helptext" in account["aria-describedby"]
    assert account["value"] == "A" * 65
    assert d.one("div", id="account-field")  # same id, so hx-swap outerHTML lands in place


def test_datalist_label_with_script_stays_escaped(logged_in):
    label = "\"><script>alert(3)</script>"
    uploaded(logged_in, csv_bytes(T1), label=label)
    markup = page(logged_in.get("/imports/"))
    assert "<script>alert(3)" not in markup
    assert {"value": label} in Doc(markup).all("option")


def test_file_error_shows_under_the_file_field_and_form_error_summary(logged_in):
    markup = page(upload(logged_in, b"nope"))
    d = Doc(markup)
    file_input = d.one("input", type="file")
    assert file_input["aria-invalid"] == "true"
    assert "id_file_error" in file_input["aria-describedby"]
    assert 'role="alert"' not in markup


# --- imports list (3.5) ---------------------------------------------------------------------


def test_empty_list_shows_the_no_imports_block(logged_in):
    main = Doc(main_of(page(logged_in.get("/imports/"))))
    assert copy.LIST_EMPTY_HEADING in main.text and copy.LIST_EMPTY_BODY in main.text
    assert not main.all("table")
    assert len(main.all("h1")) == 1


def test_list_table_has_caption_scoped_headers_and_zone(logged_in, user):
    uploaded(logged_in, csv_bytes(T1))
    markup = main_of(page(logged_in.get("/imports/")))
    d = Doc(markup)
    assert re.search(r'<caption class="sr-only">\s*' + copy.LIST_HEADING, markup)
    headers = d.all("th", scope="col")
    assert len(headers) == 6
    assert "(America/New_York)" in d.text
    for word in (copy.COL_UPLOADED, copy.COL_FILE, copy.COL_ACCOUNT, copy.COL_IMPORTED,
                 copy.COL_SKIPPED, copy.COL_FAILED):
        assert word in d.text


def test_list_rows_link_to_detail_and_show_counts_in_words_on_mobile(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    markup = main_of(page(logged_in.get("/imports/")))
    d = Doc(markup)
    assert len(d.all("a", href=f"/imports/{batch.pk}/")) == 2  # table + card, one exposed
    cards = region(markup, "import-cards")
    assert "Imported 1, skipped 0, failed 0" in html.unescape(cards)
    assert copy.ACCOUNT_LINE_BLANK.replace("no account name", copy.LIST_ACCOUNT_BLANK) in (
        html.unescape(cards)
    )


def test_list_needs_attention_badge_is_icon_and_text(logged_in, messy_batch):
    markup = main_of(page(logged_in.get("/imports/")))
    badges = re.findall(r'<span class="badge badge-attention">(.*?)</span>\s*</span>', markup, re.S)
    assert badges and all("<svg" in b and copy.NEEDS_ATTENTION in b for b in badges)


def test_list_account_not_recorded_is_a_dash_with_hidden_words(logged_in, user):
    uploaded(logged_in, csv_bytes(UNSUPPORTED))  # imports nothing
    markup = main_of(page(logged_in.get("/imports/")))
    assert re.search(
        r'<span aria-hidden="true">-</span><span class="sr-only">'
        + copy.LIST_ACCOUNT_NOT_RECORDED + "</span>",
        markup,
    )


def test_list_pager_links_keep_to_page_numbers(logged_in, user):
    for i in range(26):
        ImportBatch.objects.create(
            user=user, broker="topstep", filename=f"f{i}.csv", file_sha256=f"{i:064}", raw_file=b""
        )
    d = Doc(main_of(page(logged_in.get("/imports/"))))
    assert d.all("a", href="?page=2")
    d2 = Doc(main_of(page(logged_in.get("/imports/?page=2"))))
    assert d2.all("a", href="?page=1")


# --- import detail (3.2) --------------------------------------------------------------------


def test_detail_header_has_back_link_filename_h1_zone_and_account_line(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1), label="Combine 50K")
    markup = main_of(page(detail(logged_in, batch)))
    d = Doc(markup)
    assert d.all("a", href="/imports/")
    h1 = d.one("h1")
    assert h1["title"] == "trades.csv"
    assert "(America/New_York)" in d.text
    assert "Account: Combine 50K" in d.text and "Broker: Topstep" in d.text


def test_long_filename_is_middle_truncated_with_the_full_name_kept(logged_in, user):
    long = "topstep-export-" + "x" * 80 + "-end.csv"
    resp = upload(logged_in, csv_bytes(T1), name=long)
    markup = main_of(page(logged_in.get(resp["Location"])))
    d = Doc(markup)
    assert d.one("h1")["title"] == long
    assert "…" in d.text and re.search(r'class="sr-only">' + re.escape(long), markup)


def test_conflict_banner_comes_before_summary_and_rows_and_is_a_status(logged_in, messy_batch):
    markup = main_of(page(detail(logged_in, messy_batch)))
    banner = markup.index('id="conflict-banner"')
    summary = markup.index('id="import-summary"')
    rows = markup.index('id="import-rows"')
    assert banner < summary < rows
    d = Doc(markup)
    assert d.one("div", id="conflict-banner")["role"] == "status"
    assert "1 row was not imported" in d.text
    assert 'role="alert"' not in markup
    assert copy.FAILED_NOTICE_MANY.format(n=2) in d.text


def test_summary_card_shows_counts_with_duplicate_and_conflict_split(logged_in, messy_batch):
    markup = main_of(page(detail(logged_in, messy_batch)))
    summary = re.sub(r"<[^>]+>", " ", region(markup, "import-summary").split("</section>")[0])
    squashed = " ".join(html.unescape(summary).split())
    assert "Imported 1" in squashed and "Failed 2" in squashed
    assert "Skipped 1 (duplicate 0, conflict 1)" in squashed


def test_mismatch_hint_is_a_banner_with_a_link(logged_in, user):
    uploaded(logged_in, csv_bytes(T1), label="A")
    second = uploaded(logged_in, csv_bytes(T1), label="B")
    markup = main_of(page(detail(logged_in, second)))
    d = Doc(markup)
    hint = d.one("div", id="hint-banner")
    assert hint["role"] == "status"
    assert copy.HINT_MISMATCH_TITLE in d.text
    assert markup.index('id="hint-banner"') < markup.index('id="import-summary"')


def test_plain_hint_is_a_line_inside_the_summary(logged_in, user):
    first = uploaded(logged_in, csv_bytes(T1))
    second = uploaded(logged_in, csv_bytes(T1))
    markup = main_of(page(detail(logged_in, second)))
    summary = region(markup, "import-summary")
    assert f'href="/imports/{first.pk}/"' in summary
    assert copy.HINT_PLAIN_LINK in summary
    assert 'id="hint-banner"' not in markup


def test_filters_are_links_with_one_aria_current_and_htmx_fallback(logged_in, messy_batch):
    markup = main_of(page(detail(logged_in, messy_batch)))
    d = Doc(region(markup, "import-rows"))
    base = f"/imports/{messy_batch.pk}/?status="
    links = [a for a in d.all("a") if a.get("href", "").startswith(base)]
    assert len(links) == 6
    assert all(a["hx-get"] == a["href"] for a in links)
    current = [a for a in links if a.get("aria-current") == "true"]
    assert [a["href"] for a in current] == [f"/imports/{messy_batch.pk}/?status=needs_attention"]
    assert all("role" not in a for a in links)  # not role="tab" (design 7)
    wrapper = d.one("div", id="import-rows")
    assert wrapper["hx-target"] == "this" and wrapper["hx-swap"] == "outerHTML"
    assert wrapper["hx-push-url"] == "true" and wrapper["hx-indicator"] == "this"


def test_rows_table_has_caption_scope_and_status_icon_plus_text(logged_in, messy_batch):
    markup = page(detail(logged_in, messy_batch, "?status=all"))
    rows = region(markup, "import-rows")
    assert re.search(r'<caption class="sr-only">', rows)
    d = Doc(rows)
    assert len(d.all("th", scope="col")) == 6
    assert "(America/New_York)" in d.text
    badges = re.findall(r'<span class="badge badge-[a-z-]+">(.*?)</span>\s*</span>', rows, re.S)
    assert len(badges) == 2 * 4  # table + cards
    for b in badges:
        assert "<svg" in b
        assert re.search(r"Imported|Skipped \(duplicate\)|Skipped \(conflict\)|Failed", b)


def test_rows_mobile_cards_are_a_list(logged_in, messy_batch):
    markup = page(detail(logged_in, messy_batch, "?status=all"))
    cards = region(markup, "row-cards")
    assert cards.startswith("<ul") and cards.count("<li") >= 4


def test_empty_filter_says_no_rows(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    d = Doc(page(detail(logged_in, batch, "?status=failed")))
    assert copy.EMPTY_FILTER in d.text and not d.all("table")


def test_rows_fragment_is_only_the_region_with_paging_that_keeps_the_filter(logged_in, user):
    rows = [{**T1, "Id": str(9100000000 + i)} for i in range(51)]
    batch = uploaded(logged_in, csv_bytes(*rows))
    markup = page(logged_in.get(f"/imports/{batch.pk}/?status=imported", headers=HTMX))
    assert markup.lstrip().startswith("<div") and "<main" not in markup
    d = Doc(markup)
    nxt = d.one("a", href="?status=imported&page=2")
    assert nxt["hx-get"] == "?status=imported&page=2"
    assert "Showing 1-50 of 51" in d.text


# --- base shell: vendored JS, htmx config, CSP-friendly markup -------------------------------


def test_base_loads_vendored_htmx_alpine_and_app_js_with_safe_htmx_config(logged_in):
    markup = page(logged_in.get("/imports/"))
    d = Doc(markup)
    srcs = [a["src"] for a in d.all("script") if "src" in a]
    assert any(s.endswith("vendor/htmx-2.0.11.min.js") for s in srcs)
    assert any(s.endswith("vendor/alpine-csp-3.17.4.min.js") for s in srcs)
    assert any(s.endswith("js/app.js") for s in srcs)
    assert all("defer" in a for a in d.all("script") if "src" in a)
    # app.js registers Alpine.data on alpine:init, so it must come before Alpine.
    assert srcs.index(next(s for s in srcs if s.endswith("js/app.js"))) < srcs.index(
        next(s for s in srcs if "alpine" in s)
    )
    config =json.loads(d.one("meta", name="htmx-config")["content"])
    assert config["allowEval"] is False and config["selfRequestsOnly"] is True
    assert config["allowScriptTags"] is False and config["historyCacheSize"] == 0
    assert config["includeIndicatorStyles"] is False  # no injected inline <style>
    headers = json.loads(d.one("body")["hx-headers"])
    assert headers["X-CSRFToken"]


def test_vendored_files_match_the_sha256_in_versions_md():
    versions = (VENDOR / "VERSIONS.md").read_text(encoding="utf-8")
    files = sorted(p for p in VENDOR.iterdir() if p.suffix == ".js")
    assert len(files) == 2
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        row = next(line for line in versions.splitlines() if path.name in line)
        assert digest in row, path.name
        assert "https://" in row


def test_vendored_files_are_kept_byte_exact_by_git():
    attrs = (settings.BASE_DIR / ".gitattributes").read_text(encoding="utf-8")
    assert re.search(r"^static/vendor/\*\* -text$", attrs, re.M)


@pytest.mark.parametrize("path", NEW_TEMPLATES, ids=lambda p: p.name)
def test_new_templates_have_no_safe_filter_inline_handlers_or_js_urls(path):
    src = path.read_text(encoding="utf-8")
    assert "|safe" not in src and "autoescape off" not in src
    assert not re.search(r"\son[a-z]+\s*=", src, re.I), "inline event handler"
    assert "javascript:" not in src.lower()
    assert "<script" not in src  # new JS lives in static/js/app.js


def test_base_adds_no_new_inline_script():
    src = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert len(re.findall(r"<script>", src)) == 1  # the pre-existing one only


def test_c1_ships_no_delete_controls(logged_in, messy_batch):
    for resp in (logged_in.get("/imports/"), detail(logged_in, messy_batch)):
        markup = page(resp)
        assert "Delete this import" not in markup and "/delete/" not in markup
        assert "<dialog" not in markup and "Actions for" not in markup
    for path in NEW_TEMPLATES:
        assert "delete" not in path.read_text(encoding="utf-8").lower(), path.name
