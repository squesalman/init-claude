"""
QA gap tests for PR C1 (import UI, read side). Written against the specs
(docs/product/features/import-account-label.md criteria, docs/design/import-account-label.md,
import-and-list.md section 5), not against the code's own output. Synthetic data only.
"""

import html
import re
from datetime import UTC, datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from journal import copy
from journal.models import Execution, ImportBatch, RawImportRow
from journal.test_import_views import (
    T1,
    T1_BAD_PNL,
    T1_CHANGED,
    T2,
    T3,
    csv_bytes,
    detail,
    upload,
    uploaded,
)

User = get_user_model()
HTMX = {"HX-Request": "true"}


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="qa-a@example.com", password="x", timezone="America/New_York"
    )


@pytest.fixture
def other(db):
    return User.objects.create_user(email="qa-b@example.com", password="x", timezone="Asia/Tokyo")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def text(resp):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", resp.content.decode())).split())


# --- acceptance criteria 5-10 through the rendered page ---------------------------------------


def test_ac5_blank_conflict_is_skipped_writes_nothing_and_banner_shows_count(logged_in, user):
    uploaded(logged_in, csv_bytes(T1))
    n_before = Execution.objects.for_user(user).count()
    batch = uploaded(logged_in, csv_bytes(T1_CHANGED, T3))  # T3 imports, so the label is known

    page = text(detail(logged_in, batch))

    assert "1 row was not imported" in page and copy.CONFLICT_BODY_BLANK in page
    conflict = RawImportRow.objects.for_user(user).get(status="skipped_conflict")
    assert not Execution.objects.for_user(user).filter(raw_import_row=conflict).exists()
    assert Execution.objects.for_user(user).count() == n_before + 2  # only T3 (entry+exit)


def test_ac6_same_ids_other_contents_under_a_new_label_all_import(logged_in, user):
    uploaded(logged_in, csv_bytes(T1))
    batch = uploaded(logged_in, csv_bytes(T1_CHANGED), label="B")

    ctx = detail(logged_in, batch).context

    assert ctx["counts"]["imported"] == 1 and ctx["counts"]["skipped_conflict"] == 0
    assert ctx["conflict_banner"] is None


def test_ac7_conflict_row_shows_symbol_time_size_and_reason_in_the_page(logged_in, user):
    uploaded(logged_in, csv_bytes(T1))
    batch = uploaded(logged_in, csv_bytes(T1_CHANGED, T3))

    resp = detail(logged_in, batch, "?status=skipped_conflict")
    body = text(resp)

    assert "Skipped (conflict)" in body and "CLZ6" in body
    assert "Dec 19, 3:59 AM" in body  # 08:59 UTC in America/New_York
    row = resp.context["rows"][0]
    assert row["size"] == "2" and row["note"] and row["note"] in body
    assert resp.context["counts"]["skipped_conflict"] == len(resp.context["rows"])


def test_ac9_identical_reupload_same_label_is_duplicate_no_banner_and_link(logged_in, user):
    first = uploaded(logged_in, csv_bytes(T1, T2), label="A")
    again = uploaded(logged_in, csv_bytes(T1, T2), label="A")

    resp = detail(logged_in, again)
    body = resp.content.decode()

    assert resp.context["counts"]["skipped_duplicate"] == 2
    assert resp.context["counts"]["skipped_conflict"] == 0
    assert 'id="conflict-banner"' not in body and 'id="hint-banner"' not in body
    assert f'href="/imports/{first.pk}/"' in body and copy.HINT_PLAIN_LINK in body


def test_ac10_other_label_upload_proceeds_and_warns(logged_in, user):
    uploaded(logged_in, csv_bytes(T1, T2), label="A")
    again = uploaded(logged_in, csv_bytes(T1, T2), label="B")

    resp = detail(logged_in, again)

    assert resp.context["counts"]["imported"] == 2  # not blocked
    assert "adds these trades a second time" in text(resp)


def test_ac4_datalist_html_lists_b_then_a_input_blank(logged_in, user):
    uploaded(logged_in, csv_bytes(T1), label="A")
    uploaded(logged_in, csv_bytes(T2), label="B")

    body = logged_in.get("/imports/").content.decode()

    datalist = body[body.index("<datalist") : body.index("</datalist>")]
    assert re.findall(r'<option value="([^"]*)">', datalist) == ["B", "A"]
    assert re.search(r'<input type="text" name="account" id="id_account" value=""', body)


# --- upload edge cases not yet covered --------------------------------------------------------


def test_unknown_broker_is_a_form_error_not_a_500(logged_in, user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    resp = logged_in.post(
        "/imports/",
        {"broker": "evil", "file": SimpleUploadedFile("t.csv", csv_bytes(T1)), "account": ""},
    )
    assert resp.status_code == 200 and "broker" in resp.context["form"].errors
    assert not ImportBatch.objects.for_user(user).exists()


@pytest.mark.parametrize("label", ["   ", "\t"])
def test_whitespace_only_label_is_blank(logged_in, user, label):
    uploaded(logged_in, csv_bytes(T1), label=label)
    labels = Execution.objects.for_user(user).values_list("broker_account_label", flat=True)
    assert set(labels) == {""}


def test_nul_in_label_is_a_form_error_not_a_500(logged_in, user):
    resp = upload(logged_in, csv_bytes(T1), label="A\x00B")
    assert resp.status_code == 200
    assert not ImportBatch.objects.for_user(user).exists()


def test_nul_byte_inside_the_csv_does_not_500(logged_in, user):
    resp = upload(logged_in, csv_bytes({**T1, "ContractName": "CL\x00Z6"}))
    assert resp.status_code in (200, 302)  # a friendly outcome, never a traceback


def test_label_with_newline_is_stored_as_typed_or_rejected_but_never_breaks_the_page(
    logged_in, user
):
    resp = upload(logged_in, csv_bytes(T1), label="A\r\nB")
    assert resp.status_code in (200, 302)
    body = logged_in.get("/imports/").content.decode()
    assert body.count("<datalist") == 1 and body.count("</datalist>") == 1


def test_wrong_extension_with_valid_csv_bytes_is_judged_by_content(logged_in, user):
    resp = upload(logged_in, csv_bytes(T1), name="trades.png")
    assert resp.status_code == 302


def test_get_of_upload_post_only_paths_and_head_are_safe(logged_in):
    assert logged_in.put("/imports/").status_code in (200, 405)


# --- list and detail: hostile params ----------------------------------------------------------


@pytest.mark.parametrize(
    "page", ["-1", "0", "99999999999999999999", "abc", "1e3", "", "1.5", "%00"]
)
def test_detail_bad_page_values_never_500(logged_in, user, page):
    batch = uploaded(logged_in, csv_bytes(T1))
    assert logged_in.get(f"/imports/{batch.pk}/?status=all&page={page}").status_code == 200
    assert logged_in.get(f"/imports/?page={page}").status_code == 200


@pytest.mark.parametrize(
    "query",
    ["?status=", "?status=ALL", "?status=all&status=failed", "?status=%00", "?status=" + "x" * 5000,
     "?status[]=all", "?status=imported' OR 1=1--"],
)
def test_detail_hostile_status_values_fall_back_quietly(logged_in, user, query):
    batch = uploaded(logged_in, csv_bytes(T1))
    resp = logged_in.get(f"/imports/{batch.pk}/{query}")
    assert resp.status_code == 200
    assert resp.context["status"] in ("all", "failed")  # last-wins is Django's rule


def test_detail_paging_boundaries_50_and_51_and_100_101(logged_in, user):
    def make(n):
        rows = [{**T1, "Id": str(9200000000 + n * 1000 + i)} for i in range(n)]
        return uploaded(logged_in, csv_bytes(*rows))

    for n, pages in ((50, 1), (51, 2), (100, 2), (101, 3)):
        batch = make(n)
        ctx = detail(logged_in, batch, "?status=all").context
        assert ctx["page_obj"].paginator.num_pages == pages, n
        last = detail(logged_in, batch, f"?status=all&page={pages}").context
        assert last["rows"][-1]["line_number"] == n + 1
        assert not last["page_obj"].has_next()
        beyond = detail(logged_in, batch, f"?status=all&page={pages + 5}").context
        assert beyond["page_obj"].number == pages  # clamps to the last page


def test_list_boundary_25_and_26_batches(logged_in, user):
    for i in range(25):
        ImportBatch.objects.create(
            user=user, broker="topstep", filename=f"f{i}.csv", file_sha256=f"{i:064}", raw_file=b""
        )
    assert logged_in.get("/imports/").context["page_obj"].paginator.num_pages == 1
    ImportBatch.objects.create(
        user=user, broker="topstep", filename="f25.csv", file_sha256="9" * 64, raw_file=b""
    )
    assert logged_in.get("/imports/").context["page_obj"].paginator.num_pages == 2


def test_list_ordering_ties_are_stable_by_pk(logged_in, user):
    when = datetime(2026, 1, 1, tzinfo=UTC)
    made = [
        ImportBatch.objects.create(
            user=user, broker="topstep", filename=f"f{i}.csv", file_sha256=f"{i:064}", raw_file=b""
        )
        for i in range(3)
    ]
    ImportBatch.unscoped.filter(pk__in=[b.pk for b in made]).update(uploaded_at=when)
    got = [b.pk for b in logged_in.get("/imports/").context["page_obj"]]
    assert got == [b.pk for b in reversed(made)]


# --- time zones -------------------------------------------------------------------------------


def _row(user, batch, line, entered_at):
    return RawImportRow.objects.create(
        user=user, import_batch=batch, line_number=line, status="imported",
        raw={"ContractName": "CLZ6", "Size": "1", "EnteredAt": entered_at},
    )


def test_row_times_across_the_us_dst_boundary(logged_in, user):
    batch = ImportBatch.objects.create(
        user=user, broker="topstep", filename="dst.csv", file_sha256="a" * 64, raw_file=b"",
        row_count=2, imported_count=2,
    )
    _row(user, batch, 2, "03/08/2026 06:59:00 +00:00")  # 1:59 AM EST, last minute before the jump
    _row(user, batch, 3, "03/08/2026 07:00:00 +00:00")  # 3:00 AM EDT
    _row(user, batch, 4, "11/01/2026 05:30:00 +00:00")  # 1:30 AM EDT (first pass)
    _row(user, batch, 5, "11/01/2026 06:30:00 +00:00")  # 1:30 AM EST (second pass)

    body = text(logged_in.get(f"/imports/{batch.pk}/?status=all"))

    for expected in ("Mar 8, 1:59 AM", "Mar 8, 3:00 AM", "Nov 1, 1:30 AM"):
        assert expected in body
    assert body.count("Nov 1, 1:30 AM") >= 2  # both passes are shown, 1 h apart in UTC


def test_two_users_see_the_same_instant_in_their_own_zone(client, user, other):
    client.force_login(user)
    batch_a = uploaded(client, csv_bytes(T1))
    client.force_login(other)
    batch_b = uploaded(client, csv_bytes(T1))
    now = datetime(2026, 12, 19, 12, 0, tzinfo=UTC)
    ImportBatch.unscoped.filter(pk__in=[batch_a.pk, batch_b.pk]).update(uploaded_at=now)

    body_b = text(detail(client, batch_b))
    client.force_login(user)
    body_a = text(detail(client, batch_a))

    assert "Dec 19, 2026, 7:00 AM (America/New_York)" in body_a
    assert "Dec 19, 2026, 9:00 PM (Asia/Tokyo)" in body_b
    assert "Dec 19, 4:00 AM" in body_a and "Dec 19, 6:00 PM" in body_b  # row times too
    assert "Asia/Tokyo" not in body_a and "America/New_York" not in body_b


def test_list_times_use_the_users_zone_and_name_it_once_per_table(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    ImportBatch.unscoped.filter(pk=batch.pk).update(
        uploaded_at=datetime(2026, 7, 1, 3, 30, tzinfo=UTC)
    )

    markup = logged_in.get("/imports/").content.decode()
    table = markup[markup.index("<table") : markup.index("</table>")]

    assert "Jun 30, 11:30 PM" in table  # EDT, previous local day
    assert table.count("America/New_York") == 1


# --- isolation, extended ----------------------------------------------------------------------


def test_htmx_fragment_for_another_users_batch_is_404_identical_to_missing(client, user, other):
    client.force_login(user)
    a_batch = uploaded(client, csv_bytes(T1), label="Secret Desk")
    client.force_login(other)

    theirs = client.get(f"/imports/{a_batch.pk}/?status=all", headers=HTMX)
    missing = client.get("/imports/999999/?status=all", headers=HTMX)

    assert theirs.status_code == missing.status_code == 404
    assert theirs.content == missing.content
    assert "Secret Desk" not in theirs.content.decode()


def test_other_user_never_sees_a_hint_or_datalist_from_a_shared_hash(client, user, other):
    client.force_login(user)
    a_batch = uploaded(client, csv_bytes(T1, T2), label="Secret Desk")
    client.force_login(other)

    b_batch = uploaded(client, csv_bytes(T1, T2), label="")
    body = detail(client, b_batch).content.decode()
    listing = client.get("/imports/").content.decode()

    assert detail(client, b_batch).context["counts"]["imported"] == 2
    assert f"/imports/{a_batch.pk}/" not in body + listing
    assert "Secret Desk" not in body + listing
    assert 'id="hint-banner"' not in body


def test_anonymous_htmx_detail_gets_401_not_a_404_leak(client, user):
    client.force_login(user)
    batch = uploaded(client, csv_bytes(T1))
    anon = Client()
    resp = anon.get(f"/imports/{batch.pk}/", headers=HTMX)
    assert resp.status_code == 401 and "HX-Redirect" in resp


# --- htmx contract ----------------------------------------------------------------------------


def test_htmx_try_again_error_is_retargeted_to_the_whole_page(logged_in, user):
    from unittest.mock import patch

    from journal.services import TRY_AGAIN, ImportRetryError

    with patch("journal.views.import_file", side_effect=ImportRetryError(TRY_AGAIN)):
        resp = upload(logged_in, csv_bytes(T1), **HTMX)

    assert (resp["HX-Retarget"], resp["HX-Reswap"]) == ("body", "outerHTML")
    assert TRY_AGAIN in resp.content.decode()


def test_htmx_account_and_file_error_together_takes_the_full_page_path(logged_in, user):
    resp = logged_in.post(
        "/imports/", {"broker": "topstep", "account": "A" * 65}, headers=HTMX
    )  # no file and a too-long Account
    assert resp["HX-Retarget"] == "body" and "<html" in resp.content.decode()


def test_htmx_get_of_the_list_page_is_a_full_page_without_retarget(logged_in):
    resp = logged_in.get("/imports/", headers=HTMX)
    assert "<html" in resp.content.decode() and "HX-Retarget" not in resp


def test_detail_full_page_and_fragment_both_vary_on_hx_request(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    for headers in ({}, HTMX):
        assert "HX-Request" in logged_in.get(f"/imports/{batch.pk}/", headers=headers)["Vary"]


# --- rendering --------------------------------------------------------------------------------


def test_one_h1_and_no_heading_skips_on_list_and_detail(logged_in, user):
    uploaded(logged_in, csv_bytes(T1))
    batch = uploaded(logged_in, csv_bytes(T1_CHANGED, T1_BAD_PNL, T3))
    for body in (
        logged_in.get("/imports/").content.decode(),
        detail(logged_in, batch).content.decode(),
    ):
        levels = [int(n) for n in re.findall(r"<h([1-6])[\s>]", body)]
        assert levels.count(1) == 1
        assert all(b - a <= 1 for a, b in zip(levels, levels[1:], strict=False)), levels


def test_row_error_note_from_hostile_raw_value_is_escaped(logged_in, user):
    hostile = {**T1, "Id": "9000000094", "Type": "<b onmouseover=x>Long</b>"}
    batch = uploaded(logged_in, csv_bytes(hostile))
    body = detail(logged_in, batch, "?status=all").content.decode()
    assert "<b onmouseover" not in body
