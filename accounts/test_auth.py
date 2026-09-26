"""
Signup / login / logout behaviour (docs/product/features/import-and-list.md section 4, AC 1-8)
with the exact copy from docs/design/auth-and-trades-list.md section 4 (held in accounts/copy.py).
The mandatory tenant-isolation check (ADR-0002), the race, unsafe-next, bad-credential, logout,
no-store and DB-failure cases live in accounts/test_auth_qa.py.
"""

import pytest
from django.contrib.auth import get_user_model

from accounts import copy

User = get_user_model()

GOOD_PW = "correct-horse-battery"


def signup_data(**over):
    data = {
        "email": "new@example.com",
        "password1": GOOD_PW,
        "password2": GOOD_PW,
        "timezone": "America/New_York",
    }
    data.update(over)
    return data


def logged_in(client, user):
    return client.session.get("_auth_user_id") == str(user.pk)


# --- signup -------------------------------------------------------------------------------


@pytest.mark.django_db
def test_signup_creates_user_logs_in_and_lands_on_trades(client):
    resp = client.post("/signup/", signup_data())

    assert resp.status_code == 302 and resp["Location"] == "/trades/"
    user = User.objects.get(email="new@example.com")
    assert user.timezone == "America/New_York"
    assert logged_in(client, user)
    assert [str(m) for m in resp.wsgi_request._messages] == [copy.SIGNUP_WELCOME]


@pytest.mark.django_db
def test_signup_stores_a_hash_never_the_plaintext(client):
    client.post("/signup/", signup_data())
    user = User.objects.get(email="new@example.com")
    assert user.password != GOOD_PW and GOOD_PW not in user.password
    assert user.password.startswith("pbkdf2_")
    assert user.check_password(GOOD_PW)


@pytest.mark.django_db
def test_signup_get_prefills_utc_and_offers_the_zone_list(client):
    resp = client.get("/signup/")
    assert resp.status_code == 200
    assert resp.context["form"]["timezone"].value() == "UTC"
    assert "America/New_York" in resp.context["timezones"]


@pytest.mark.django_db
@pytest.mark.parametrize("variant", ["New@Example.com", "NEW@EXAMPLE.COM", "new@example.com"])
def test_signup_duplicate_email_any_case_shows_one_generic_form_level_message(client, variant):
    User.objects.create_user(email="new@example.com", password=GOOD_PW)

    resp = client.post("/signup/", signup_data(email=variant))

    assert resp.status_code == 200
    assert User.objects.count() == 1
    form = resp.context["form"]
    assert form.non_field_errors() == [copy.SIGNUP_EMAIL_UNUSABLE]
    assert not form.errors.get("email")  # not attached to the Email field
    assert form["email"].value() == variant  # email value is kept
    assert not logged_in(client, User.objects.get())


@pytest.mark.django_db
def test_signup_password_mismatch_error_is_under_confirm_password(client):
    resp = client.post("/signup/", signup_data(password2="something-else-entirely"))

    assert resp.status_code == 200
    assert resp.context["form"].errors == {"password2": [copy.SIGNUP_CONFIRM_MISMATCH]}
    assert not User.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "password, message",
    [
        ("Ab1", copy.SIGNUP_PASSWORD_TOO_SHORT),
        ("password", copy.SIGNUP_PASSWORD_TOO_COMMON),
        ("83920174", copy.SIGNUP_PASSWORD_ALL_DIGITS),
        ("newnew@example.com", copy.SIGNUP_PASSWORD_LIKE_EMAIL),
    ],
)
def test_signup_weak_password_uses_design_copy(client, password, message):
    resp = client.post("/signup/", signup_data(password1=password, password2=password))

    assert resp.status_code == 200
    assert message in resp.context["form"].errors["password1"]
    assert not User.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("zone", ["Not/A_Real_Zone", "", "   ", "new york"])
def test_signup_rejects_missing_or_unknown_timezone(client, zone):
    resp = client.post("/signup/", signup_data(timezone=zone))

    assert resp.status_code == 200
    assert resp.context["form"].errors == {"timezone": [copy.SIGNUP_TIMEZONE_INVALID]}
    assert not User.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field, value, message",
    [
        ("email", "", copy.SIGNUP_EMAIL_MISSING),
        ("email", "not-an-email", copy.SIGNUP_EMAIL_MALFORMED),
        ("password1", "", copy.SIGNUP_PASSWORD_MISSING),
        ("password2", "", copy.SIGNUP_CONFIRM_MISSING),
    ],
)
def test_signup_required_and_malformed_field_messages(client, field, value, message):
    resp = client.post("/signup/", signup_data(**{field: value}))

    assert resp.status_code == 200
    assert message in resp.context["form"].errors[field]
    assert not User.objects.exists()


@pytest.mark.django_db
def test_signup_trims_email_and_password_fields_are_not_echoed(client):
    resp = client.post("/signup/", signup_data(email="  pad@example.com  ", password2="nope-nope-nope"))
    assert resp.context["form"].cleaned_data["email"] == "pad@example.com"
    assert GOOD_PW.encode() not in resp.content


# --- login --------------------------------------------------------------------------------


@pytest.fixture
def user(db):
    return User.objects.create_user(email="Trader@Example.com", password=GOOD_PW)


@pytest.mark.django_db
def test_login_success_lands_on_trades_and_email_is_case_insensitive(client, user):
    resp = client.post("/login/", {"email": "trader@example.com", "password": GOOD_PW})
    assert resp.status_code == 302 and resp["Location"] == "/trades/"
    assert logged_in(client, user)


@pytest.mark.django_db
def test_login_follows_a_safe_next_from_query_or_post_body(client, user):
    resp = client.post("/login/?next=/imports/", {"email": user.email, "password": GOOD_PW})
    assert resp["Location"] == "/imports/"

    client.post("/logout/")
    resp = client.post("/login/", {"email": user.email, "password": GOOD_PW, "next": "/imports/"})
    assert resp["Location"] == "/imports/"


@pytest.mark.django_db
def test_login_missing_fields_use_design_copy(client):
    resp = client.post("/login/", {"email": "", "password": ""})
    errors = resp.context["form"].errors
    assert errors == {"email": [copy.LOGIN_EMAIL_MISSING], "password": [copy.LOGIN_PASSWORD_MISSING]}


@pytest.mark.django_db
def test_login_page_shows_login_required_notice_only_with_next(client):
    assert client.get("/login/").context["login_required_notice"] is None
    resp = client.get("/login/?next=/trades/")
    assert resp.context["login_required_notice"] == copy.LOGIN_REQUIRED
    assert resp.context["next"] == "/trades/"
    assert client.get("/login/?next=https://evil.example/").context["next"] == ""


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/login/", "/signup/"])
def test_logged_in_user_is_redirected_away_from_login_and_signup(client, user, path):
    client.force_login(user)
    resp = client.get(path)
    assert resp.status_code == 302 and resp["Location"] == "/trades/"


# --- root, protected pages ---------------------------------------------------------------


@pytest.mark.django_db
def test_root_redirects_by_auth_state(client, user):
    assert client.get("/")["Location"] == "/login/"
    client.force_login(user)
    assert client.get("/")["Location"] == "/trades/"


@pytest.mark.django_db
def test_anonymous_trades_redirects_to_login_keeping_next(client):
    resp = client.get("/trades/")
    assert resp.status_code == 302 and resp["Location"] == "/login/?next=/trades/"


# --- error-report hygiene (security review M1) --------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path, data, fields",
    [
        ("/signup/", signup_data(), ["password1", "password2"]),
        ("/login/", {"email": "x@example.com", "password": GOOD_PW}, ["password"]),
    ],
)
def test_password_fields_are_marked_sensitive_for_error_reports(client, path, data, fields):
    resp = client.post(path, data)
    assert sorted(resp.wsgi_request.sensitive_post_parameters) == fields


# --- template copy comes from copy.py via the view context (follow-ups row 21) ------------


@pytest.mark.django_db
def test_signup_login_and_trades_copy_is_passed_from_copy_py(client, user):
    ctx = client.get("/signup/").context
    assert ctx["timezone_help"] == copy.SIGNUP_TIMEZONE_HELP
    assert ctx["timezone_help_detected"] == copy.SIGNUP_TIMEZONE_HELP_DETECTED
    assert ctx["busy_label"] == copy.SIGNUP_BUSY
    assert client.get("/login/").context["busy_label"] == copy.LOGIN_BUSY

    client.force_login(user)
    ctx = client.get("/trades/").context
    assert (ctx["empty_heading"], ctx["empty_body"], ctx["empty_action"]) == (
        copy.TRADES_EMPTY_HEADING, copy.TRADES_EMPTY_BODY, copy.TRADES_EMPTY_ACTION
    )


@pytest.mark.django_db
def test_signup_detected_zone_help_reaches_the_script_through_a_data_attribute(client):
    body = client.get("/signup/").content.decode()
    assert body.count("We detected this from your browser") == 1  # not retyped in the JS
    assert f'data-detected="{copy.SIGNUP_TIMEZONE_HELP_DETECTED}"' in body
