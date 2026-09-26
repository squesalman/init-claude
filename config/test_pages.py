"""
Rendered-HTML checks for the shell, auth pages, /trades/ placeholder and error pages
(docs/design/auth-and-trades-list.md sections 3, 4, 5.4 and the section 8 accessibility list).
Only what is checkable from HTML; visual layout and contrast are not tested here.
"""

import html
import re
from html.parser import HTMLParser

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client

from accounts import copy

User = get_user_model()
PW = "correct-horse-battery"


class Doc(HTMLParser):
    """Flat list of (tag, attrs) plus the unescaped text, enough for these checks."""

    def __init__(self, markup):
        super().__init__()
        self.tags = []
        self.feed(markup)
        self.text = html.unescape(markup)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {k: (v or "") for k, v in attrs}))

    handle_startendtag = handle_starttag

    def all(self, tag, **attrs):
        return [a for t, a in self.tags if t == tag and all(a.get(k.replace("_", "-")) == v for k, v in attrs.items())]

    def one(self, tag, **attrs):
        found = self.all(tag, **attrs)
        assert len(found) == 1, (tag, attrs, found)
        return found[0]


def doc(resp):
    return Doc(resp.content.decode())


def is_focusable(tag, a):
    if tag == "a":
        return "href" in a
    if tag == "input":
        return a.get("type") != "hidden"
    if tag in ("button", "select", "textarea", "summary"):
        return True
    return a.get("tabindex", "-1") != "-1"


@pytest.fixture
def user(db):
    return User.objects.create_user(email="me@example.com", password=PW, timezone="America/New_York")


def status_notice(resp, message):
    """The role="status" element text that carries `message` (it must be inside one)."""
    text = html.unescape(resp.content.decode())
    found = re.search(r'role="status".{0,1500}?' + re.escape(message) + r".{0,600}", text, re.S)
    assert found, message
    return found.group(0)


def get_page(client, user, path):
    if path == "/trades/":
        client.force_login(user)
    return client.get(path)


# --- shell (section 3) ------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/login/", "/signup/", "/trades/"])
def test_every_page_has_the_shell_basics(client, user, path):
    resp = get_page(client, user, path)
    assert resp.status_code == 200
    d = doc(resp)
    assert d.one("html")["lang"] == "en"
    first = next(a for t, a in d.tags if is_focusable(t, a))
    assert first.get("href") == "#main"  # skip link is the first focusable element
    assert len(d.all("h1")) == 1
    d.one("main", id="main")
    assert any(a["href"].endswith("css/app.css") for a in d.all("link", rel="stylesheet"))


def test_logged_in_nav_has_post_logout_current_page_and_mobile_menu(client, user):
    client.force_login(user)
    markup = client.get("/trades/").content.decode()
    d = Doc(markup)
    # Logout is a POST form with a CSRF token, never a link.
    forms = re.findall(r'<form[^>]*action="/logout/"[^>]*>.*?</form>', markup, re.S)
    assert forms and all('method="post"' in f and 'name="csrfmiddlewaretoken"' in f for f in forms)
    assert not d.all("a", href="/logout/")
    assert all(a.get("aria-current") == "page" for a in d.all("a", href="/trades/"))
    assert d.all("a", href="/imports/")
    assert "me@example.com" in d.text
    assert d.all("details") and "Menu" in d.text  # native mobile menu, no JS
    assert not d.all("a", href="/signup/")


def test_logged_out_nav_has_login_and_signup_and_no_logout(client, db):
    d = doc(client.get("/login/"))
    assert all(a.get("aria-current") == "page" for a in d.all("a", href="/login/"))
    assert d.all("a", href="/signup/")
    assert "/logout/" not in d.text


def test_long_email_is_truncated_in_the_middle_with_full_value_in_title(client, db):
    long_email = "a-very-long-mailbox-name@example-domain.com"
    client.force_login(User.objects.create_user(email=long_email, password=PW))
    d = doc(client.get("/trades/"))
    assert d.all("span", title=long_email)
    assert "a-very-long…e-domain.com" in d.text


def test_logout_flash_is_a_status_notice_with_a_close_button(client, user):
    client.force_login(user)
    resp = client.post("/logout/", follow=True)
    notice = status_notice(resp, copy.LOGOUT_FLASH)
    assert "data-dismiss" in notice


# --- signup (4.1, 4.3) ------------------------------------------------------------------


def assert_every_input_is_labelled(d):
    label_for = {a.get("for") for a in d.all("label")}
    for a in d.all("input"):
        if a.get("type") not in ("hidden", "submit"):
            assert a.get("id") in label_for, a


@pytest.mark.django_db
def test_signup_form_fields_labels_and_no_password_toggle(client):
    d = doc(client.get("/signup/"))
    assert_every_input_is_labelled(d)
    passwords = d.all("input", type="password")
    assert [p["name"] for p in passwords] == ["password1", "password2"]
    assert all(p["autocomplete"] == "new-password" and "value" not in p for p in passwords)
    assert "show password" not in d.text.lower()
    assert len(d.all("button")) == 1  # only the submit: no show/hide toggle
    assert d.one("button")["data-busy"] == "Creating..."
    assert d.one("input", id="id_password1")["aria-describedby"] == "id_password1_helptext"
    tz = d.one("input", name="timezone")
    assert tz["list"] == "timezones" and tz["value"] == "UTC" and "data-detect" in tz
    assert "id_timezone_helptext" in tz["aria-describedby"]
    d.one("datalist", id="timezones")
    assert len(d.all("option")) > 300
    assert "Type your time zone, for example America/New_York." in d.text  # no-JS help
    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in d.text
    assert copy.SIGNUP_ERROR_SUMMARY_TITLE not in d.text


@pytest.mark.django_db
def test_signup_errors_show_a_focused_summary_and_messages_under_fields(client):
    resp = client.post(
        "/signup/",
        {"email": "new@example.com", "password1": PW, "password2": PW + "x", "timezone": "Mars/Base"},
    )
    markup = resp.content.decode()
    d = Doc(markup)
    summary = d.one("div", tabindex="-1")
    assert "autofocus" in summary and summary.get("role") != "alert"
    assert 'role="alert"' not in markup
    assert copy.SIGNUP_ERROR_SUMMARY_TITLE in d.text
    assert d.all("a", href="#id_password2") and d.all("a", href="#id_timezone")
    for field, message in (("password2", copy.SIGNUP_CONFIRM_MISMATCH), ("timezone", copy.SIGNUP_TIMEZONE_INVALID)):
        error_block = re.search(rf'<div id="id_{field}_error".*?</div>', markup, re.S).group(0)
        assert message in html.unescape(error_block)
        field_input = d.one("input", id=f"id_{field}")
        assert field_input["aria-invalid"] == "true"
        assert f"id_{field}_error" in field_input["aria-describedby"]
    assert all("value" not in p for p in d.all("input", type="password"))  # cleared
    assert d.one("input", name="email")["value"] == "new@example.com"
    tz = d.one("input", name="timezone")
    assert tz["value"] == "Mars/Base" and "data-detect" not in tz  # keep what the user typed


@pytest.mark.django_db
def test_signup_duplicate_email_message_is_only_in_the_summary(client, user):
    resp = client.post(
        "/signup/", {"email": "ME@example.com", "password1": PW, "password2": PW, "timezone": "UTC"}
    )
    d = doc(resp)
    assert d.text.count(copy.SIGNUP_EMAIL_UNUSABLE) == 1
    assert "id_email_error" not in d.text
    assert d.one("input", name="email")["value"] == "ME@example.com"


# --- login (4.2, 4.3) -------------------------------------------------------------------


@pytest.mark.django_db
def test_login_form_fields_autocomplete_and_autofocus(client):
    d = doc(client.get("/login/"))
    assert_every_input_is_labelled(d)
    email = d.one("input", name="email")
    assert email["autocomplete"] == "username" and "autofocus" in email
    assert d.one("input", name="password")["autocomplete"] == "current-password"
    assert d.one("button")["data-busy"] == "Logging in..."
    assert "forgot" not in d.text.lower() and "remember me" not in d.text.lower()
    assert d.all("a", href="/signup/")


def test_login_bad_credentials_summary_keeps_email_and_clears_password(client, user):
    d = doc(client.post("/login/", {"email": "me@example.com", "password": "wrong-password-1"}))
    assert "autofocus" in d.one("div", tabindex="-1")
    assert copy.SIGNUP_ERROR_SUMMARY_TITLE in d.text and copy.LOGIN_BAD_CREDENTIALS in d.text
    email = d.one("input", name="email")
    assert email["value"] == "me@example.com" and "autofocus" not in email
    assert "value" not in d.one("input", name="password")


@pytest.mark.django_db
def test_login_missing_field_error_is_under_the_field_and_linked(client):
    markup = client.post("/login/", {"email": "", "password": ""}).content.decode()
    d = Doc(markup)
    assert d.all("a", href="#id_email") and d.all("a", href="#id_password")
    email = d.one("input", name="email")
    assert email["aria-invalid"] == "true" and email["aria-describedby"] == "id_email_error"
    block = re.search(r'<div id="id_email_error".*?</div>', markup, re.S).group(0)
    assert copy.LOGIN_EMAIL_MISSING in html.unescape(block)


@pytest.mark.django_db
def test_login_required_notice_is_a_status_notice(client):
    status_notice(client.get("/login/?next=/trades/"), copy.LOGIN_REQUIRED)


# --- /trades/ placeholder (5.4 A) -------------------------------------------------------


def test_trades_placeholder_shows_the_empty_state_with_one_action(client, user):
    client.force_login(user)
    markup = client.get("/trades/").content.decode()
    main = Doc(re.search(r'<main id="main".*?</main>', markup, re.S).group(0))
    assert "No trades yet" in main.text
    assert "Upload your TopstepX 'Trades' export and your trades show up here." in main.text
    assert [a["href"] for a in main.all("a")] == ["/imports/"]
    assert "Import trades" in main.text


# --- error pages --------------------------------------------------------------------------


@pytest.mark.django_db
def test_csrf_failure_page_is_styled_with_coach_copy_and_login_link():
    resp = Client(enforce_csrf_checks=True).post("/login/", {"email": "a@b.c", "password": "x"})
    assert resp.status_code == 403
    d = doc(resp)
    assert "That page expired." in d.text and "Go back, refresh it, and try again." in d.text
    assert d.all("a", href="/login/")
    assert len(d.all("h1")) == 1 and d.one("html")["lang"] == "en"


def test_500_page_has_the_design_copy_and_a_try_again_link():
    d = Doc(render_to_string("500.html"))
    assert "This page didn't load." in d.text
    assert "Nothing was changed. Try again in a moment." in d.text
    assert d.one("a", href="")  # empty href = reload the same URL
    assert "Try again" in d.text and len(d.all("h1")) == 1


def test_compiled_css_is_committed_and_has_the_tokens():
    css = (settings.BASE_DIR / "static" / "css" / "app.css").read_text()
    assert "--focus" in css and ".sr-only" in css


def _contrast(fg, bg):
    def lum(hex_color):
        rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_token_contrast_meets_aa_in_light_and_dark():
    src = (settings.BASE_DIR / "static" / "src" / "input.css").read_text()
    themes = [dict(re.findall(r"--([\w-]+):\s*(#[0-9a-f]{6})", block)) for block in src.split("@media")[:2]]
    text_pairs = [  # 4.5:1
        ("text", "page"), ("text", "surface"), ("text", "surface-raised"), ("text-muted", "surface"),
        ("text-muted", "page"), ("text-muted", "surface-raised"), ("link", "surface"), ("on-primary", "primary"),
        ("gain", "surface"), ("loss", "surface"), ("danger", "surface"), ("attention", "surface"),
        ("text", "info-bg"), ("text", "success-bg"), ("text", "attention-bg"), ("link", "attention-bg"),
    ]
    ui_pairs = [  # 3:1 icons, borders, focus ring
        ("border-strong", "surface"), ("focus", "page"), ("focus", "surface"), ("info", "info-bg"),
        ("success", "success-bg"), ("attention", "attention-bg"), ("primary", "surface"),
    ]
    assert len(themes) == 2
    for theme in themes:
        for fg, bg in text_pairs:
            assert _contrast(theme[fg], theme[bg]) >= 4.5, (fg, bg)
        for fg, bg in ui_pairs:
            assert _contrast(theme[fg], theme[bg]) >= 3, (fg, bg)
