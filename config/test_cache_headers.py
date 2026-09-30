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
AUTHENTICATED_URLS = ["/", "/trades/", "/login/", "/signup/", "/imports/"]


@pytest.mark.django_db
@pytest.mark.parametrize("url", AUTHENTICATED_URLS)
def test_authenticated_responses_are_private_no_store(client, url):
    user = get_user_model().objects.create_user(email="cache@example.com", password="x")
    client.force_login(user)

    cache_control = client.get(url)["Cache-Control"]

    assert "no-store" in cache_control
    assert "private" in cache_control


@pytest.mark.django_db
def test_import_detail_is_private_no_store(client):
    from journal.models import ImportBatch

    user = get_user_model().objects.create_user(email="cachedet@example.com", password="x")
    batch = ImportBatch.objects.create(
        user=user, broker="topstep", filename="f.csv", file_sha256="0" * 64, raw_file=b""
    )
    client.force_login(user)

    for url in ("/imports/", f"/imports/{batch.pk}/", f"/imports/{batch.pk}/delete/"):
        response = client.get(url)
        assert response.status_code == 200, url  # a 404 is no-store too; prove the page
        assert "no-store" in response["Cache-Control"], url
        assert "private" in response["Cache-Control"], url


@pytest.mark.django_db
def test_journal_page_and_rules_posts_are_private_no_store(client):
    """ADR-0007 test 9: the journal URL takes an id, so it can't join the list above."""
    from django.utils import timezone

    from journal.models import Execution

    user = get_user_model().objects.create_user(email="cachejr@example.com", password="x")
    opening = Execution.objects.create(
        user=user, broker="manual", symbol="X", side="buy", quantity="1", price="1",
        currency="USD", executed_at=timezone.now(), source=Execution.SOURCE_MANUAL,
    )
    client.force_login(user)
    url = f"/trades/{opening.pk}/journal/"

    responses = {
        "journal GET": client.get(url),
        "journal POST": client.post(url, {"note": "n"}),
        "rules POST htmx": client.post(
            "/rules/", {"trading_rules": "r", "next": url}, headers={"HX-Request": "true"}
        ),
        "rules POST": client.post("/rules/", {"trading_rules": "r", "next": url}),
    }

    assert responses["journal GET"].status_code == 200  # a 404 is no-store too; prove the page
    for name, response in responses.items():
        assert "no-store" in response["Cache-Control"], name
        assert "private" in response["Cache-Control"], name


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
