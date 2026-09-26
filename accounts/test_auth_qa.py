"""
QA edge cases for PR A (auth). Complements accounts/test_auth.py; see the AC map in the QA report.
Spec: docs/product/features/import-and-list.md section 4 and AC 1-8, design 4.1-4.3.
Every test is deterministic (no clock, no network); the one thread test asserts only
interleaving-independent outcomes.
"""

import html
import logging
import re
import threading
from unittest.mock import patch
from urllib.parse import quote, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client

from accounts import copy
from accounts.forms import TIMEZONE_NAMES

User = get_user_model()

GOOD_PW = "correct-horse-battery"
CANARY = "Canary-9f3kQ7-plaintext"  # unique, passes every validator, must never reach logs/DB


def signup_data(**over):
    data = {"email": "new@example.com", "password1": GOOD_PW, "password2": GOOD_PW, "timezone": "America/New_York"}
    data.update(over)
    return data


def logged_in(client, user):
    return client.session.get("_auth_user_id") == str(user.pk)


@pytest.fixture
def user(db):
    return User.objects.create_user(email="Trader@Example.com", password=GOOD_PW)


PASSWORD_STRINGS = {
    copy.SIGNUP_PASSWORD_TOO_SHORT,
    copy.SIGNUP_PASSWORD_TOO_COMMON,
    copy.SIGNUP_PASSWORD_ALL_DIGITS,
    copy.SIGNUP_PASSWORD_LIKE_EMAIL,
    copy.SIGNUP_PASSWORD_MISSING,
}

# --- AC 2: duplicate email, whitespace / case / normalization ---------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("variant", ["  new@example.com  ", "\tnew@example.com\n", "NEW@example.COM", " New@EXAMPLE.com "])
def test_duplicate_email_differing_by_case_or_whitespace_creates_no_second_account(client, variant):
    User.objects.create_user(email="new@example.com", password=GOOD_PW)
    resp = client.post("/signup/", signup_data(email=variant))
    assert resp.status_code == 200
    assert User.objects.count() == 1
    assert resp.context["form"].non_field_errors() == [copy.SIGNUP_EMAIL_UNUSABLE]
    assert "email" not in resp.context["form"].errors
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_email_is_trimmed_and_domain_lowercased_but_login_ignores_case_everywhere(client):
    client.post("/signup/", signup_data(email="  Pad@Example.COM "))
    stored = User.objects.get().email
    assert stored == "Pad@example.com"  # Django normalize_email: domain lowercased, local part kept
    client.post("/logout/")
    for typed in ["pad@example.com", "PAD@EXAMPLE.COM", "  Pad@example.com  "]:
        c = Client()
        resp = c.post("/login/", {"email": typed, "password": GOOD_PW})
        assert resp.status_code == 302, typed
        assert c.session["_auth_user_id"] == str(User.objects.get().pk)


@pytest.mark.django_db
def test_duplicate_email_message_never_says_registered_taken_or_in_use(client):
    User.objects.create_user(email="new@example.com", password=GOOD_PW)
    body = html.unescape(client.post("/signup/", signup_data()).content.decode()).lower()
    for word in ("registered", "taken", "in use", "already exists", "already have an account with"):
        assert word not in body, word


@pytest.mark.django_db
def test_duplicate_email_with_a_weak_password_shows_only_the_password_error(client):
    """No extra tell: the duplicate message appears only once the rest of the form is valid."""
    User.objects.create_user(email="new@example.com", password=GOOD_PW)
    resp = client.post("/signup/", signup_data(password1="123", password2="123"))
    assert resp.context["form"].non_field_errors() == []
    assert "password1" in resp.context["form"].errors


# --- deterministic and real double-submit race ------------------------------------------------


@pytest.mark.django_db
def test_lost_race_on_the_real_db_constraint_shows_the_generic_message(client):
    """The form/model checks both pass (patched off), so the DB unique index is what rejects it."""
    User.objects.create_user(email="new@example.com", password=GOOD_PW)
    with patch.object(User, "full_clean", lambda self, *a, **k: None):
        resp = client.post("/signup/", signup_data(email="NEW@example.com"))
    assert resp.status_code == 200
    assert resp.context["form"].non_field_errors() == [copy.SIGNUP_EMAIL_UNUSABLE]
    assert User.objects.count() == 1
    # the connection is still usable after the swallowed IntegrityError
    assert User.objects.filter(email__iexact="new@example.com").count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_signups_same_email_yield_one_account_and_no_server_error():
    barrier = threading.Barrier(2)
    outcomes = []

    def worker(email):
        try:
            c = Client()
            barrier.wait(timeout=15)
            r = c.post("/signup/", signup_data(email=email))
            outcomes.append((r.status_code, r.get("Location"), "_auth_user_id" in c.session))
        except Exception as exc:  # a 500 surfaces as a raised exception in the test client
            outcomes.append(("EXC", repr(exc), False))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(e,)) for e in ("race@example.com", "RACE@example.com")]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]

    assert sorted(o[0] for o in outcomes) == [200, 302], outcomes
    assert User.objects.count() == 1
    assert sum(1 for o in outcomes if o[2]) == 1  # only the winner is logged in


# --- AC 1 / 8 / password field behaviour ------------------------------------------------------


@pytest.mark.django_db
def test_password_whitespace_is_significant_never_trimmed(client):
    padded = f" {GOOD_PW} "
    assert client.post("/signup/", signup_data(password1=padded, password2=GOOD_PW)).status_code == 200
    assert not User.objects.exists()  # trailing/leading space is a mismatch, not silently trimmed

    client.post("/signup/", signup_data(password1=padded, password2=padded))
    u = User.objects.get()
    assert u.check_password(padded) and not u.check_password(GOOD_PW)
    client.post("/logout/")
    c = Client()
    assert c.post("/login/", {"email": u.email, "password": GOOD_PW}).status_code == 200
    assert c.post("/login/", {"email": u.email, "password": padded}).status_code == 302


@pytest.mark.django_db
def test_mismatch_plus_weak_password_shows_both_messages_each_under_its_own_field(client):
    resp = client.post("/signup/", signup_data(password1="12345678", password2="123456789"))
    errors = resp.context["form"].errors
    assert errors["password2"] == [copy.SIGNUP_CONFIRM_MISMATCH]
    assert copy.SIGNUP_PASSWORD_ALL_DIGITS in errors["password1"]


@pytest.mark.django_db
def test_password_length_boundary_seven_rejected_eight_accepted(client):
    resp = client.post("/signup/", signup_data(password1="q7Zk!mP", password2="q7Zk!mP"))
    assert resp.context["form"].errors["password1"] == [copy.SIGNUP_PASSWORD_TOO_SHORT]
    resp = client.post("/signup/", signup_data(password1="q7Zk!mPw", password2="q7Zk!mPw"))
    assert resp.status_code == 302


@pytest.mark.django_db
@pytest.mark.parametrize(
    "password",
    ["123", "1234567", "12345678", "password", "password1", "qwertyuiop", "abc", "trader@example.com", "aaaaaaaa"],
)
def test_every_password_failure_is_a_design_string_never_raw_django_english(client, password):
    resp = client.post("/signup/", signup_data(email="trader@example.com", password1=password, password2=password))
    assert resp.status_code == 200
    errors = resp.context["form"].errors["password1"]
    assert errors and set(errors) <= PASSWORD_STRINGS, errors


@pytest.mark.django_db
@pytest.mark.parametrize("password", ["traderexample1", "example.com1", "trader1234"])
def test_similar_to_email_is_caught_by_local_part_and_domain_pieces(client, password):
    resp = client.post("/signup/", signup_data(email="trader@example.com", password1=password, password2=password))
    assert copy.SIGNUP_PASSWORD_LIKE_EMAIL in resp.context["form"].errors.get("password1", [])


@pytest.mark.django_db
def test_similar_to_email_check_is_case_insensitive(client):
    resp = client.post("/signup/", signup_data(email="TRADER@EXAMPLE.COM", password1="trader@example.com", password2="trader@example.com"))
    assert copy.SIGNUP_PASSWORD_LIKE_EMAIL in resp.context["form"].errors["password1"]


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["password1", "password2"])
def test_failed_signup_never_echoes_either_password_field(client, field):
    resp = client.post("/signup/", signup_data(**{"password1": CANARY, "password2": CANARY + "x"}))
    assert CANARY.encode() not in resp.content


@pytest.mark.django_db
def test_failed_login_never_echoes_the_password(client, user):
    resp = client.post("/login/", {"email": user.email, "password": CANARY})
    assert CANARY.encode() not in resp.content


# --- very long / hostile input: 200 or 4xx, never 500, never an account -------------------------


def _email_of_length(n):
    domain = ".".join(["d" * 60] * 3) + ".com"  # 187 chars
    local = "x" * (n - len(domain) - 1)
    email = f"{local}@{domain}"
    assert len(email) == n
    return email


@pytest.mark.django_db
def test_email_of_exactly_254_chars_signs_up_and_logs_in_but_255_is_rejected(client):
    ok = _email_of_length(254)
    assert client.post("/signup/", signup_data(email=ok)).status_code == 302
    client.post("/logout/")
    c = Client()
    assert c.post("/login/", {"email": ok.upper(), "password": GOOD_PW}).status_code == 302

    resp = Client().post("/signup/", signup_data(email=_email_of_length(255)))
    assert resp.status_code == 200 and "email" in resp.context["form"].errors
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_absurdly_long_email_and_password_do_not_crash_signup_or_login(client, user):
    resp = client.post("/signup/", signup_data(email="a" * 100_000 + "@example.com"))
    assert resp.status_code == 200 and User.objects.count() == 1
    resp = client.post("/login/", {"email": "a" * 100_000, "password": "b" * 200_000})
    assert resp.status_code == 200
    assert resp.context["form"].non_field_errors() == [copy.LOGIN_BAD_CREDENTIALS]


@pytest.mark.django_db
def test_long_password_up_to_5000_chars_works_end_to_end(client):
    pw = "Lg9-" * 1250
    assert client.post("/signup/", signup_data(password1=pw, password2=pw)).status_code == 302
    client.post("/logout/")
    assert Client().post("/login/", {"email": "new@example.com", "password": pw}).status_code == 302


@pytest.mark.django_db
def test_request_body_over_the_django_limit_is_a_400_not_a_500(client):
    resp = client.post("/login/", {"email": "a@b.com", "password": "x" * 3_000_000})
    assert resp.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize("path, body", [
    ("/signup/", signup_data(email="a\x00b@example.com")),
    ("/signup/", signup_data(password1="pw\x00" + GOOD_PW, password2="pw\x00" + GOOD_PW)),
    ("/signup/", signup_data(timezone="UTC\x00")),
    ("/login/", {"email": "a\x00b@example.com", "password": GOOD_PW}),
    ("/login/", {"email": "a@example.com", "password": "pw\x00pw"}),
])
def test_nul_bytes_are_a_form_error_not_a_server_error(client, path, body):
    resp = client.post(path, body)
    assert resp.status_code == 200
    assert not User.objects.exists() or "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_junk_and_empty_posts_render_the_form_without_error(client, user):
    for path in ("/signup/", "/login/"):
        assert client.post(path, {}).status_code == 200
        assert client.post(path, {"csrfmiddlewaretoken": "x"}).status_code == 200
    assert User.objects.count() == 1


# --- XSS: hostile but form-valid values must be escaped wherever they are echoed ----------------


@pytest.mark.django_db
def test_quoted_local_part_email_is_escaped_in_nav_and_title(client):
    evil = '"<b>x</b>"@example.com'
    resp = client.post("/signup/", signup_data(email=evil), follow=True)
    if User.objects.exists():  # Django's validator accepts quoted local parts
        assert b"<b>x</b>" not in resp.content
        assert b"&lt;b&gt;x&lt;/b&gt;" in resp.content
    else:
        assert resp.status_code == 200


@pytest.mark.django_db
def test_login_echo_of_email_and_next_is_escaped(client):
    payload = '"><script>alert(1)</script>'
    resp = client.post("/login/", {"email": payload, "password": "x", "next": "/x" + payload})
    assert b"<script>alert(1)" not in resp.content
    resp = client.get("/login/", {"next": "/x" + payload})
    assert b"<script>alert(1)" not in resp.content


# --- unicode emails ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_idn_domain_email_signs_up_and_exact_typed_login_works(client):
    client.post("/signup/", signup_data(email="Kai@München.de"))
    u = User.objects.get()
    client.post("/logout/")
    c = Client()
    assert c.post("/login/", {"email": "Kai@München.de", "password": GOOD_PW}).status_code == 302
    assert c.session["_auth_user_id"] == str(u.pk)


@pytest.mark.django_db
def test_idn_domain_duplicate_in_other_case_is_blocked_at_signup(client):
    client.post("/signup/", signup_data(email="kai@münchen.de"))
    client.post("/logout/")
    resp = Client().post("/signup/", signup_data(email="KAI@MÜNCHEN.DE"))
    assert resp.status_code == 200 and User.objects.count() == 1


@pytest.mark.django_db
def test_idn_domain_login_is_case_insensitive_like_ascii(client):
    """Same promise as ASCII: typing the domain in another case still logs in.

    Login lowers the typed email with Postgres lower(), which folds non-ASCII letters only
    when the database's LC_CTYPE is a Unicode locale (postgres:16-alpine initdb gives
    en_US.utf8). Under a C/POSIX ctype lower('Ü') stays 'Ü', so this cannot hold there.
    """
    with connection.cursor() as cur:
        cur.execute("SELECT lower('Ü'), datctype FROM pg_database WHERE datname = current_database()")
        folded, ctype = cur.fetchone()
    if folded != "ü":
        pytest.skip(f"DB lc_ctype {ctype!r} does not case-fold non-ASCII in lower()")
    client.post("/signup/", signup_data(email="kai@münchen.de"))
    client.post("/logout/")
    c = Client()
    assert c.post("/login/", {"email": "kai@MÜNCHEN.de", "password": GOOD_PW}).status_code == 302


@pytest.mark.django_db
def test_unicode_local_part_is_either_rejected_cleanly_or_fully_usable(client):
    resp = client.post("/signup/", signup_data(email="josé@example.com"))
    if User.objects.exists():
        client.post("/logout/")
        assert Client().post("/login/", {"email": "josé@example.com", "password": GOOD_PW}).status_code == 302
    else:
        assert resp.status_code == 200
        assert resp.context["form"].errors["email"] == [copy.SIGNUP_EMAIL_MALFORMED]


# --- AC 7: time zone --------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("zone", ["", "   ", "\t", "Not/A_Zone", "america/new_york", "UTC ; DROP", "../etc/passwd", "EST5EDT/x", "Asia/Tokyo/"])
def test_bad_time_zone_is_rejected_with_the_design_string_and_no_account(client, zone):
    resp = client.post("/signup/", signup_data(timezone=zone))
    assert resp.status_code == 200
    assert resp.context["form"].errors == {"timezone": [copy.SIGNUP_TIMEZONE_INVALID]}
    assert not User.objects.exists()


@pytest.mark.django_db
def test_time_zone_field_missing_from_the_post_is_rejected(client):
    data = signup_data()
    del data["timezone"]
    resp = client.post("/signup/", data)
    assert resp.context["form"].errors == {"timezone": [copy.SIGNUP_TIMEZONE_INVALID]}
    assert not User.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("zone", ["Asia/Tokyo", "Pacific/Kiritimati", "America/St_Johns", "Australia/Lord_Howe", "UTC", "Etc/GMT+5"])
def test_valid_time_zone_is_persisted_and_usable_by_zoneinfo(client, zone):
    client.post("/signup/", signup_data(timezone=f"  {zone}  "))  # surrounding spaces trimmed
    u = User.objects.get()
    assert u.timezone == zone
    ZoneInfo(u.timezone)


def test_every_name_offered_in_the_datalist_is_loadable_by_zoneinfo():
    """PR D renders times with ZoneInfo(user.timezone); a name that validates but cannot load would 500 /trades/."""
    bad = []
    for name in TIMEZONE_NAMES:
        try:
            ZoneInfo(name)
        except Exception as exc:
            bad.append((name, repr(exc)))
    assert not bad, bad


@pytest.mark.django_db
def test_bad_time_zone_error_keeps_the_typed_value_and_clears_passwords(client):
    resp = client.post("/signup/", signup_data(timezone="Nowhere/Land"))
    assert resp.context["form"]["timezone"].value() == "Nowhere/Land"
    assert GOOD_PW.encode() not in resp.content


# --- AC 5: ?next= variants ---------------------------------------------------------------------

UNSAFE_NEXT = [
    "https://evil.example/x",
    "http://evil.example",
    "http://testserver.evil.example/",
    "//evil.example/x",
    "///evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "/\\/evil.example",
    "\\/evil.example",
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "data:text/html,x",
    "https:evil.example",
    "\t//evil.example",
    "/\t/evil.example",
    " //evil.example",
    "//evil.example\\@testserver",
    "http://testserver@evil.example/",
    "http://testserver:80@evil.example/",
]


def assert_same_origin(location):
    joined = urlparse(urljoin("http://testserver/login/", location))
    assert joined.hostname == "testserver" and joined.scheme == "http", location


@pytest.mark.django_db
@pytest.mark.parametrize("next_url", UNSAFE_NEXT)
def test_unsafe_next_is_ignored_on_post_body_and_on_query_string(next_url, user):
    for kwargs in ({"data": {"email": user.email, "password": GOOD_PW, "next": next_url}},
                   {"data": {"email": user.email, "password": GOOD_PW}, "QUERY_STRING": urlencode({"next": next_url})}):
        c = Client()
        resp = c.post("/login/", **kwargs)
        assert resp.status_code == 302 and resp["Location"] == "/trades/", (next_url, resp["Location"])


@pytest.mark.django_db
@pytest.mark.parametrize("next_url", UNSAFE_NEXT)
def test_unsafe_next_on_get_shows_no_notice_and_no_hidden_field(client, next_url):
    resp = client.get("/login/", {"next": next_url})
    assert resp.status_code == 200
    assert resp.context["next"] == "" and resp.context["login_required_notice"] is None
    assert 'name="next"' not in resp.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "next_url",
    ["%2F%2Fevil.example", "%252F%252Fevil.example", "/%2F/evil.example", "/imports/\r\nSet-Cookie: pwn=1",
     "/imports/%0d%0aSet-Cookie:%20pwn=1", "/imports/\x00"],
)
def test_encoded_and_control_char_next_never_leaves_the_origin_or_splits_headers(next_url, user):
    c = Client()
    resp = c.post("/login/", {"email": user.email, "password": GOOD_PW, "next": next_url})
    assert resp.status_code == 302
    assert "\r" not in resp["Location"] and "\n" not in resp["Location"]
    assert "pwn" not in resp.cookies
    assert_same_origin(resp["Location"])
    c2 = Client()
    resp = c2.post("/login/", {"email": user.email, "password": GOOD_PW}, QUERY_STRING="next=" + quote(next_url, safe="%"))
    assert resp.status_code == 302 and "pwn" not in resp.cookies
    assert_same_origin(resp["Location"])


@pytest.mark.django_db
@pytest.mark.parametrize("next_url, expected", [
    ("/imports/", "/imports/"),
    ("/trades/?sort=pnl&dir=asc", "/trades/?sort=pnl&dir=asc"),
    ("http://testserver/imports/", "http://testserver/imports/"),
])
def test_safe_next_variants_are_honoured(next_url, expected, user):
    resp = Client().post("/login/", {"email": user.email, "password": GOOD_PW, "next": next_url})
    assert resp["Location"] == expected


@pytest.mark.django_db
def test_anonymous_protected_url_round_trips_through_login_with_its_query_string(client, user):
    resp = client.get("/trades/?sort=pnl&dir=asc")
    assert resp.status_code == 302
    login_url = resp["Location"]
    assert login_url.startswith("/login/?next=")
    page = client.get(login_url)
    assert page.context["next"] == "/trades/?sort=pnl&dir=asc"
    assert 'name="next"' in page.content.decode()
    resp = client.post(login_url, {"email": user.email, "password": GOOD_PW, "next": page.context["next"]})
    assert resp["Location"] == "/trades/?sort=pnl&dir=asc"


@pytest.mark.django_db
def test_next_survives_a_failed_login_attempt_and_is_used_on_the_retry(client, user):
    resp = client.post("/login/", {"email": user.email, "password": "wrong-pass-123", "next": "/imports/"})
    assert resp.status_code == 200 and resp.context["next"] == "/imports/"
    assert 'name="next" value="/imports/"' in resp.content.decode()
    resp = client.post("/login/", {"email": user.email, "password": GOOD_PW, "next": "/imports/"})
    assert resp["Location"] == "/imports/"


# --- AC 3: bad credentials are indistinguishable ------------------------------------------------


def _scrub(resp, email):
    body = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", resp.content.decode())
    return body.replace(html.escape(email, quote=True), "EMAIL")


@pytest.mark.django_db
def test_wrong_password_unknown_email_and_inactive_render_the_same_page(user):
    inactive = User.objects.create_user(email="off@example.com", password=GOOD_PW, is_active=False)
    cases = {
        "wrong": (user.email, "wrong-pass-123"),
        "unknown": ("nobody@example.com", GOOD_PW),
        "inactive": (inactive.email, GOOD_PW),
        "inactive-wrong": (inactive.email, "wrong-pass-123"),
        "malformed": ("not an email", GOOD_PW),
    }
    seen = {}
    for name, (email, pw) in cases.items():
        c = Client()
        resp = c.post("/login/", {"email": email, "password": pw})
        seen[name] = (
            resp.status_code,
            _scrub(resp, email),
            resp["Content-Type"],
            resp.get("Cache-Control"),
            sorted(resp.cookies),
        )
        assert "_auth_user_id" not in c.session
        form = resp.context["form"]
        assert form.non_field_errors() == [copy.LOGIN_BAD_CREDENTIALS], name
        assert list(form.errors) == ["__all__"], name  # no field-level hint
    first = seen["wrong"]
    assert first[0] == 200
    for name, got in seen.items():
        assert got == first, name
    assert copy.LOGIN_BAD_CREDENTIALS in html.unescape(first[1])


@pytest.mark.django_db
def test_unknown_email_login_still_runs_the_password_hasher(client):
    """Timing parity is the design's requirement (4.2); ModelBackend hashes a dummy password for unknown users."""
    with patch.object(User, "set_password", autospec=True) as dummy_hash:
        client.post("/login/", {"email": "nobody@example.com", "password": GOOD_PW})
    assert dummy_hash.call_count == 1


@pytest.mark.django_db
def test_inactive_user_with_the_right_password_cannot_log_in_and_a_live_session_dies(client, user):
    client.force_login(user)
    assert client.get("/trades/").status_code == 200
    User.objects.filter(pk=user.pk).update(is_active=False)
    resp = client.get("/trades/")
    assert resp.status_code == 302 and resp["Location"].startswith("/login/")


# --- logged-in visitors, logout, sessions -----------------------------------------------------


@pytest.mark.django_db
def test_logged_in_post_to_signup_creates_nothing_and_post_to_login_does_not_switch_user(client, user):
    other = User.objects.create_user(email="other@example.com", password=GOOD_PW)
    client.force_login(user)
    resp = client.post("/signup/", signup_data(email="third@example.com"))
    assert resp.status_code == 302 and resp["Location"] == "/trades/"
    assert not User.objects.filter(email="third@example.com").exists()
    resp = client.post("/login/", {"email": other.email, "password": GOOD_PW})
    assert resp.status_code == 302 and resp["Location"] == "/trades/"
    assert logged_in(client, user) and not logged_in(client, other)


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "put", "delete", "patch", "head"])
def test_logout_url_rejects_every_method_but_post(client, user, method):
    client.force_login(user)
    resp = getattr(client, method)("/logout/")
    assert resp.status_code == 405 and logged_in(client, user)


@pytest.mark.django_db
def test_logout_kills_the_server_side_session_so_a_stolen_cookie_cannot_be_replayed(client, user):
    client.force_login(user)
    old_key = client.cookies["sessionid"].value
    assert client.get("/trades/").status_code == 200
    resp = client.post("/logout/")
    assert resp.status_code == 302 and resp["Location"] == "/login/"
    assert not logged_in(client, user)
    assert [str(m) for m in resp.wsgi_request._messages] == [copy.LOGOUT_FLASH]
    replay = Client()
    replay.cookies["sessionid"] = old_key
    resp = replay.get("/trades/")
    assert resp.status_code == 302 and resp["Location"].startswith("/login/")
    assert client.get("/trades/").status_code == 302


@pytest.mark.django_db
def test_anonymous_logout_post_is_harmless(client):
    resp = client.post("/logout/")
    assert resp.status_code == 302 and resp["Location"] == "/login/"


@pytest.mark.django_db
def test_login_rotates_the_session_key(client, user):
    client.get("/login/")
    client.post("/login/", {"email": user.email, "password": "nope-nope-nope"})  # may create an anonymous session
    before = client.cookies.get("sessionid").value if "sessionid" in client.cookies else None
    client.post("/login/", {"email": user.email, "password": GOOD_PW})
    after = client.cookies["sessionid"].value
    assert after and after != before


@pytest.mark.django_db
def test_session_and_csrf_cookie_flags(client, user):
    resp = client.post("/login/", {"email": user.email, "password": GOOD_PW})
    session = resp.cookies["sessionid"]
    assert session["httponly"] is True
    assert session["samesite"] == "Lax"
    assert session["path"] == "/"
    csrf = Client().get("/login/").cookies["csrftoken"]
    assert csrf["samesite"] == "Lax"


@pytest.mark.django_db
def test_signup_and_logout_flashes_show_once_then_disappear(client):
    resp = client.post("/signup/", signup_data(), follow=True)
    assert copy.SIGNUP_WELCOME in html.unescape(resp.content.decode())
    assert copy.SIGNUP_WELCOME not in html.unescape(client.get("/trades/").content.decode())
    resp = client.post("/logout/", follow=True)
    assert copy.LOGOUT_FLASH in html.unescape(resp.content.decode())
    assert copy.LOGOUT_FLASH not in html.unescape(client.get("/login/").content.decode())


@pytest.mark.django_db
def test_csrf_is_enforced_on_signup_login_and_logout(user):
    c = Client(enforce_csrf_checks=True)
    resp = c.post("/signup/", signup_data())
    assert resp.status_code == 403 and b"That page expired." in resp.content
    assert User.objects.count() == 1
    resp = c.post("/login/", {"email": user.email, "password": GOOD_PW})
    assert resp.status_code == 403 and "_auth_user_id" not in c.session
    c.force_login(user)
    assert c.post("/logout/").status_code == 403
    assert logged_in(c, user)


# --- /trades/ ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_trades_no_store_holds_for_the_page_and_the_login_redirect(client, user):
    redirect = client.get("/trades/")
    assert redirect.status_code == 302
    client.force_login(user)
    page = client.get("/trades/")
    assert page.status_code == 200
    for resp in (redirect, page):
        cc = resp["Cache-Control"]
        assert "no-store" in cc and "no-cache" in cc and "private" in cc, cc


@pytest.mark.django_db
def test_trades_rejects_non_get_gracefully_and_ignores_garbage_query(client, user):
    client.force_login(user)
    for q in ("?sort=%00&dir=%ff", "?sort[]=a&dir[]=b", "?" + "a=1&" * 2000):
        assert client.get("/trades/" + q).status_code == 200


# --- AC 6: no plaintext password anywhere -----------------------------------------------------


@pytest.mark.django_db
def test_password_never_reaches_logs_stdout_or_the_database(client, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="django.db.backends")
    connection.force_debug_cursor = True  # makes django.db.backends log every statement with its params
    try:
        pw = CANARY
        # failing signups (mismatch, weak, duplicate, bad zone, 500), successful signup, then logins
        client.post("/signup/", signup_data(password1=pw, password2=pw + "x"))
        client.post("/signup/", signup_data(password1="1234567", password2="1234567"))
        client.post("/signup/", signup_data(timezone="Nope/Nope", password1=pw, password2=pw))
        with patch("accounts.forms.SignupForm.save", side_effect=RuntimeError("boom")):
            Client(raise_request_exception=False).post("/signup/", signup_data(password1=pw, password2=pw))
        Client(enforce_csrf_checks=True).post("/signup/", signup_data(password1=pw, password2=pw))
        client.post("/signup/", signup_data(password1=pw, password2=pw))
        client.post("/logout/")
        c = Client()
        c.post("/login/", {"email": "new@example.com", "password": pw + "wrong"})
        c.post("/login/", {"email": "nobody@example.com", "password": pw})
        c.post("/login/", {"email": "new@example.com", "password": pw})
        c.get("/trades/")
        c.get("/logout/")
        c.post("/logout/")
    finally:
        connection.force_debug_cursor = False

    assert caplog.records, "logging capture is not wired up"
    assert any(r.name == "django.db.backends" for r in caplog.records), "SQL logging was not captured"
    # SQL params are in the captured text (the stored hash is), so a plaintext leak into SQL would be seen too.
    assert "pbkdf2_sha256$" in "\n".join(r.getMessage() for r in caplog.records)
    logged = "\n".join(r.getMessage() for r in caplog.records) + caplog.text + "".join(capsys.readouterr())
    for secret in (CANARY, "1234567"):
        assert secret not in logged, f"{secret!r} leaked into logs/stdout"

    row = User.objects.values().get(email="new@example.com")
    assert CANARY not in " ".join(str(v) for v in row.values())
    assert row["password"].startswith("pbkdf2_sha256$")


# --- two-user isolation for /trades/ (PR A scope: the page passes no user-owned data yet) --------


@pytest.mark.django_db
def test_trades_shows_nothing_of_another_users_account_or_rows_and_never_mixes_sessions(client):
    from django.utils import timezone

    from journal.models import Execution, ImportBatch

    a = User.objects.create_user(email="alice-secret@example.com", password=GOOD_PW, timezone="Asia/Tokyo")
    b = User.objects.create_user(email="bob@example.com", password=GOOD_PW)
    ImportBatch.objects.create(
        user=a, broker="topstep", filename="alice-private-file.csv", file_sha256="1" * 64, raw_file=b""
    )
    Execution.objects.create(
        user=a, broker="manual", symbol="ALICEONLY", side=Execution.SIDE_BUY, quantity="1", price="1",
        currency="USD", executed_at=timezone.now(), source=Execution.SOURCE_MANUAL,
    )

    client_a = Client()
    client_a.force_login(a)
    client.force_login(b)
    for path in ("/trades/", "/trades/?sort=symbol&dir=asc"):
        resp = client.get(path)
        assert resp.status_code == 200 and resp.wsgi_request.user == b
        body = resp.content.decode()
        for leak in ("alice-secret", "ALICEONLY", "alice-private-file", "Asia/Tokyo"):
            assert leak not in body, (path, leak)
        assert "bob@example.com" in body
        # nothing user-owned is passed to the template yet; PR D adds rows and must extend this
        assert not any(key in resp.context for key in ("trades", "executions", "batches"))

    # A's concurrent session is untouched by B's requests.
    assert client_a.get("/trades/").wsgi_request.user == a

    # Same browser, user switch through logout: no residue of B in A's page, none of A in B's.
    client.post("/logout/")
    resp = client.post("/login/", {"email": a.email, "password": GOOD_PW})
    assert resp["Location"] == "/trades/"
    body = client.get("/trades/").content.decode()
    assert "alice-secret@example.com" in body and "bob@example.com" not in body
    assert client.session["_auth_user_id"] == str(a.pk)


@pytest.mark.django_db
def test_another_users_id_in_the_session_cookie_cannot_be_forged(client, user):
    """A tampered/forged session cookie is anonymous, not user 1."""
    client.force_login(user)
    real = client.cookies["sessionid"].value
    forged = Client()
    forged.cookies["sessionid"] = real[:-1] + ("a" if real[-1] != "a" else "b")
    resp = forged.get("/trades/")
    assert resp.status_code == 302 and resp["Location"].startswith("/login/")


# --- design 4.4 "Server failure" state ----------------------------------------------------------
# BUG (accounts/views.py:24 and :55): copy.SIGNUP_SERVER_FAILURE / LOGIN_SERVER_FAILURE are defined
# but never used; a database error during signup or login escapes as the generic 500 page instead of
# the in-form message design 4.4 specifies ("Above the form: We couldn't ... Try again in a moment.").


@pytest.mark.django_db
def test_signup_database_failure_shows_the_design_server_failure_message(client, caplog):
    from django.db import OperationalError

    with patch("accounts.forms.SignupForm.save", side_effect=OperationalError(f"db down {GOOD_PW}")):
        resp = Client(raise_request_exception=False).post("/signup/", signup_data())
    assert resp.status_code == 200
    assert resp.context["form"].non_field_errors() == [copy.SIGNUP_SERVER_FAILURE]
    assert copy.SIGNUP_SERVER_FAILURE in html.unescape(resp.content.decode())
    assert caplog.records and GOOD_PW not in caplog.text  # logged, without the password


@pytest.mark.django_db
def test_login_database_failure_shows_the_design_server_failure_message(client, user, caplog):
    from django.db import OperationalError

    with patch("accounts.views.authenticate", side_effect=OperationalError(f"db down {GOOD_PW}")):
        resp = Client(raise_request_exception=False).post("/login/", {"email": user.email, "password": GOOD_PW})
    assert resp.status_code == 200
    assert resp.context["form"].non_field_errors() == [copy.LOGIN_SERVER_FAILURE]
    assert copy.LOGIN_SERVER_FAILURE in html.unescape(resp.content.decode())
    assert caplog.records and GOOD_PW not in caplog.text  # logged, without the password
