"""QA gap tests: stats half-even tie, no-store on POST/redirect responses, extreme-date crash."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from journal.importers.topstep import parse
from journal.stats import compute_stats
from journal.test_topstep_parser import T1, csv_bytes


def _closed(net):
    return SimpleNamespace(is_open=False, net_pnl=Decimal(net), currency="USD")


def test_win_rate_exact_tie_rounds_half_even_not_half_up():
    # 1 win in 32 = 3.125 exactly: half-even 3.12, half-up 3.13.
    usd = compute_stats([_closed("1")] + [_closed("-1")] * 31)["USD"]

    assert usd.win_rate == Decimal("3.12")


@pytest.mark.django_db
def test_no_store_on_redirect_and_post_responses_for_logged_in_user(client):
    user = get_user_model().objects.create_user(email="qa-cache@example.com", password="x")
    client.force_login(user)

    redirect = client.get("/login/")  # logged-in user is bounced away
    post = client.post("/trades/")  # wrong method, still an authenticated response

    for response in (redirect, post):
        assert "no-store" in response["Cache-Control"], response.status_code


@pytest.mark.parametrize("ts", ["01/01/0001 00:00:00 +08:00", "12/31/9999 23:59:59 -08:00"])
def test_extreme_timestamp_fails_the_row_not_the_upload(ts):
    [row] = parse(csv_bytes({**T1, "EnteredAt": ts}))

    assert row.legs == () and "EnteredAt" in row.error
