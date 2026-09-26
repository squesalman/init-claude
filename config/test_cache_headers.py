"""
Follow-ups row 19: every response to an authenticated request carries
`Cache-Control: private, no-store`, so a shared or back-button cache never replays one
user's page. Set once in config/middleware.py, not per view.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

# Every page a logged-in user can GET. New authenticated views (PR C: /imports/...) append
# here so the header is checked for them too.
AUTHENTICATED_URLS = ["/", "/trades/", "/login/", "/signup/"]


@pytest.mark.django_db
@pytest.mark.parametrize("url", AUTHENTICATED_URLS)
def test_authenticated_responses_are_private_no_store(client, url):
    user = get_user_model().objects.create_user(email="cache@example.com", password="x")
    client.force_login(user)

    cache_control = client.get(url)["Cache-Control"]

    assert "no-store" in cache_control
    assert "private" in cache_control


@pytest.mark.django_db
def test_anonymous_login_page_is_not_forced_to_no_store(client):
    response = client.get("/login/")

    assert "no-store" not in response.get("Cache-Control", "")


@pytest.mark.django_db
def test_authenticated_404_is_private_no_store(client):
    user = get_user_model().objects.create_user(email="cache404@example.com", password="x")
    client.force_login(user)

    response = client.get("/no-such-page/")

    assert response.status_code == 404
    assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
def test_authenticated_403_is_private_no_store():
    user = get_user_model().objects.create_user(email="cache403@example.com", password="x")
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    response = client.post("/logout/")  # no CSRF token

    assert response.status_code == 403
    assert "no-store" in response["Cache-Control"]
