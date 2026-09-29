"""
PR C1 import UI, read side: upload form, /imports/ list, /imports/<id>/ detail.
Specs: docs/design/import-account-label.md (states, copy), docs/product/features/
import-account-label.md (acceptance criteria), import-and-list.md section 5 (isolation),
ADR-0006 (tenant rules), follow-ups row 24 (upload checklist). Synthetic data only.
"""

import io
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.utils import FileProxyMixin
from django.db import DatabaseError
from django.test import Client, RequestFactory
from django.utils import timezone

from config.middleware import UserTimezoneMiddleware
from config.test_pages import Doc
from journal import copy
from journal.forms import safe_filename
from journal.importers.topstep import FILE_NOT_RECOGNISED
from journal.models import MAX_RAW_FILE_BYTES, Execution, ImportBatch, RawImportRow
from journal.services import FILE_TOO_LARGE, TRY_AGAIN, ImportRetryError
from journal.test_topstep_parser import T1, T2, csv_bytes

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "topstep_synthetic.csv"
User = get_user_model()

T3 = {**T2, "Id": "9000000099"}  # a fresh Id, imports next to a conflict so the label is known
T1_CHANGED = {**T1, "EnteredAt": "12/19/2026 08:59:00 +00:00"}  # same Id, other contents
T1_BAD_PNL = {**T1, "Id": "9000000098", "PnL": "1.00"}  # fails the P&L cross-check
UNSUPPORTED = {**T1, "Id": "9000000097", "ContractName": "ZZZ99Z6"}  # fails: unknown root


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="up-a@example.com", password="x", timezone="America/New_York"
    )


@pytest.fixture
def other(db):
    return User.objects.create_user(email="up-b@example.com", password="x")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def upload(client, content=None, label="", name="trades.csv", **headers):
    f = SimpleUploadedFile(name, FIXTURE.read_bytes() if content is None else content)
    return client.post(
        "/imports/", {"broker": "topstep", "file": f, "account": label}, headers=headers
    )


def uploaded(client, content=None, label=""):
    """Upload and return the new batch (asserts the PRG redirect)."""
    resp = upload(client, content, label)
    assert resp.status_code == 302, resp.content[:500]
    return ImportBatch.unscoped.get(pk=int(resp["Location"].rstrip("/").rsplit("/", 1)[1]))


def detail(client, batch, query=""):
    return client.get(f"/imports/{batch.pk}/{query}")


def labels_of(user):
    return set(
        Execution.objects.for_user(user).values_list("broker_account_label", flat=True)
    )


# --- public timestamp helper (no duplicate parsing in the view) --------------------------


def test_topstep_timestamp_helper_is_public_and_returns_utc():
    from journal.importers.topstep import parse_timestamp

    got = parse_timestamp({"EnteredAt": "12/19/2026 20:00:00 +08:00"}, "EnteredAt")
    assert got.isoformat() == "2026-12-19T12:00:00+00:00"
    with pytest.raises(ValueError):
        parse_timestamp({"EnteredAt": "not a time"}, "EnteredAt")


# --- upload --------------------------------------------------------------------------------


def test_upload_redirects_to_the_new_import_with_a_success_flash(logged_in, user):
    resp = upload(logged_in)

    batch = ImportBatch.objects.for_user(user).get()
    assert resp.status_code == 302
    assert resp["Location"] == f"/imports/{batch.pk}/"
    page = logged_in.get(resp["Location"])
    assert [str(m) for m in page.context["messages"]] == [copy.UPLOAD_DONE]


def test_blank_account_stores_blank_label(logged_in, user):
    uploaded(logged_in, csv_bytes(T1))
    assert labels_of(user) == {""}


def test_account_is_trimmed_and_case_kept(logged_in, user):
    uploaded(logged_in, csv_bytes(T1), label="  Combine 50K  ")
    assert labels_of(user) == {"Combine 50K"}


def test_account_of_64_characters_is_accepted(logged_in, user):
    uploaded(logged_in, csv_bytes(T1), label="A" * 64)
    assert labels_of(user) == {"A" * 64}


def test_account_of_65_characters_is_rejected_and_nothing_is_imported(logged_in, user):
    resp = upload(logged_in, csv_bytes(T1), label="A" * 65)

    assert resp.status_code == 200
    assert resp.context["form"].errors["account"] == [
        copy.ACCOUNT_TOO_LONG % {"show_value": 65, "limit_value": 64}
    ]
    assert "65 characters and the limit is 64" in resp.content.decode()
    assert resp.context["account_open"] is True
    assert not ImportBatch.objects.for_user(user).exists()


def test_no_file_shows_the_choose_a_file_message(logged_in, user):
    resp = logged_in.post("/imports/", {"broker": "topstep", "account": ""})

    assert resp.context["form"].errors["file"] == [copy.FILE_MISSING]
    assert not ImportBatch.objects.for_user(user).exists()


@pytest.mark.parametrize("content", [b"hello,world\r\n1,2\r\n", b"\xff\xfe\x00garbage", b""])
def test_wrong_or_garbled_file_shows_the_importer_message_under_the_file(
    logged_in, user, content
):
    resp = upload(logged_in, content)

    assert resp.status_code == 200
    assert resp.context["form"].errors["file"] == [FILE_NOT_RECOGNISED]
    assert not ImportBatch.objects.for_user(user).exists()


def test_file_over_10_mib_is_rejected_before_it_is_read(logged_in, user):
    big = io.BytesIO(b"x" * (MAX_RAW_FILE_BYTES + 1))
    big.name = "big.csv"

    def no_read(self):
        raise AssertionError("the upload was read before its size was checked")

    with patch.object(FileProxyMixin, "read", property(no_read)):
        resp = logged_in.post("/imports/", {"broker": "topstep", "file": big, "account": ""})

    assert resp.context["form"].errors["file"] == [FILE_TOO_LARGE]
    assert not ImportBatch.objects.for_user(user).exists()


@pytest.mark.parametrize(
    "name, stored",
    [
        ("..\\..\\windows\\evil.csv", "evil.csv"),
        ("dir/sub/plain.csv", "plain.csv"),
        ("tab\there\x07bell\u202eflip.csv", "tabherebellflip.csv"),
        ("x" * 300 + ".csv", "x" * 251 + ".csv"),  # Django's upload handler keeps the suffix
    ],
)
def test_filename_is_reduced_to_a_safe_basename(logged_in, user, name, stored):
    resp = upload(logged_in, csv_bytes(T1), name=name)

    assert resp.status_code == 302
    batch = ImportBatch.objects.for_user(user).get()
    assert batch.filename == stored


def test_safe_filename_strips_controls_and_caps_length():
    assert safe_filename("a\\b/c\x00d\u200fe.csv") == "cde.csv"
    assert safe_filename("y" * 300) == "y" * 255


def test_concurrent_upload_race_shows_try_again_and_keeps_the_form(logged_in, user):
    with patch("journal.views.import_file", side_effect=ImportRetryError(TRY_AGAIN)):
        resp = upload(logged_in, csv_bytes(T1), label="Combine 50K")

    assert resp.status_code == 200
    assert resp.context["form"].non_field_errors() == [TRY_AGAIN]
    assert resp.context["form"]["account"].value() == "Combine 50K"


def test_database_failure_shows_try_again_not_a_500(logged_in, user):
    with patch("journal.views.import_file", side_effect=DatabaseError("secret sql")):
        resp = upload(logged_in, csv_bytes(T1))

    assert resp.status_code == 200
    assert resp.context["form"].non_field_errors() == [TRY_AGAIN]
    assert "secret sql" not in resp.content.decode()


def test_anonymous_is_sent_to_login_with_next(client, db):
    assert client.get("/imports/")["Location"] == "/login/?next=/imports/"
    resp = upload(client, csv_bytes(T1))
    assert resp.status_code == 302 and resp["Location"] == "/login/?next=/imports/"
    assert not ImportBatch.unscoped.exists()


def test_upload_requires_a_csrf_token(user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    resp = upload(client, csv_bytes(T1))

    assert resp.status_code == 403
    assert not ImportBatch.objects.for_user(user).exists()


# --- upload form page: Account field, datalist ---------------------------------------------


def test_upload_page_starts_blank_with_account_closed_for_a_new_user(logged_in):
    resp = logged_in.get("/imports/")

    assert resp.context["account_labels"] == []
    assert resp.context["account_open"] is False
    assert resp.context["account_placeholder"] == copy.ACCOUNT_PLACEHOLDER_EMPTY
    assert resp.context["form"]["account"].value() in (None, "")


def test_datalist_offers_own_labels_most_recent_first_and_stays_blank(logged_in, user):
    uploaded(logged_in, csv_bytes(T1), label="A")
    uploaded(logged_in, csv_bytes(T2), label="B")
    uploaded(logged_in, csv_bytes(T3), label="")

    resp = logged_in.get("/imports/")

    assert resp.context["account_labels"] == ["B", "A"]
    assert resp.context["account_open"] is True
    assert resp.context["account_placeholder"] == copy.ACCOUNT_PLACEHOLDER_SAVED
    assert resp.context["form"]["account"].value() in (None, "")


def test_datalist_caps_at_ten_labels(logged_in, user):
    batch = ImportBatch.objects.create(
        user=user, broker="topstep", filename="f.csv", file_sha256="0" * 64, raw_file=b""
    )
    row = RawImportRow.objects.create(
        user=user, import_batch=batch, line_number=2, raw={}, status="imported"
    )
    for i in range(12):
        Execution.objects.create(
            user=user, broker="topstep", broker_account_label=f"L{i:02}",
            broker_execution_id=f"id{i}", symbol="CLZ6", side="buy", quantity=1, price=1,
            currency="USD", executed_at=timezone.now(), source="import", raw_import_row=row,
        )

    labels = logged_in.get("/imports/").context["account_labels"]

    assert labels == [f"L{i:02}" for i in range(11, 1, -1)]


# --- imports list --------------------------------------------------------------------------


def _batches(user, n):
    return [
        ImportBatch.objects.create(
            user=user, broker="topstep", filename=f"f{i}.csv", file_sha256=f"{i:064}",
            raw_file=b"", row_count=1, imported_count=1,
        )
        for i in range(n)
    ]


def test_empty_list(logged_in):
    resp = logged_in.get("/imports/")

    assert list(resp.context["page_obj"]) == []
    assert copy.LIST_EMPTY_HEADING in resp.content.decode()


def test_list_is_newest_first_25_per_page(logged_in, user):
    made = _batches(user, 26)

    first = logged_in.get("/imports/").context["page_obj"]
    second = logged_in.get("/imports/?page=2").context["page_obj"]

    assert [b.pk for b in first] == [b.pk for b in reversed(made)][:25]
    assert [b.pk for b in second] == [made[0].pk]
    assert first.has_next() and not second.has_next()


def test_list_bad_page_value_falls_back(logged_in, user):
    _batches(user, 2)
    assert len(logged_in.get("/imports/?page=abc").context["page_obj"]) == 2


def test_list_too_many_query_fields_fall_back_to_page_one(logged_in, user):
    """Follow-ups 29b: over DATA_UPLOAD_MAX_NUMBER_FIELDS reads as no query, never a 400."""
    made = _batches(user, 26)
    resp = logged_in.get("/imports/?page=2&from=banner&" + "a=1&" * 2000)
    assert resp.status_code == 200
    assert [b.pk for b in resp.context["page_obj"]] == [b.pk for b in reversed(made)][:25]
    assert not resp.context["focus_account"]


def test_list_rows_carry_account_label_and_needs_attention(logged_in, user):
    clean = uploaded(logged_in, csv_bytes(T1), label="Combine 50K")
    conflict = uploaded(logged_in, csv_bytes(T1_CHANGED), label="Combine 50K")  # nothing imported
    failed = uploaded(logged_in, csv_bytes(T2, T1_BAD_PNL), label="")

    rows = {b.pk: b for b in logged_in.get("/imports/").context["page_obj"]}

    assert (rows[clean.pk].account_label, rows[clean.pk].needs_attention) == ("Combine 50K", False)
    assert (rows[conflict.pk].account_label, rows[conflict.pk].needs_attention) == (None, True)
    assert (rows[failed.pk].account_label, rows[failed.pk].needs_attention) == ("", True)


def test_list_query_count_does_not_grow_with_rows(logged_in, user, django_assert_max_num_queries):
    batches = _batches(user, 25)
    RawImportRow.objects.create(
        user=user, import_batch=batches[3], line_number=2, raw={}, status="skipped_conflict"
    )
    with django_assert_max_num_queries(6):
        resp = logged_in.get("/imports/")
        assert len(resp.context["page_obj"]) == 25


def test_list_never_loads_raw_file(logged_in, user):
    _batches(user, 1)
    batch = logged_in.get("/imports/").context["page_obj"][0]
    assert "raw_file" in batch.get_deferred_fields()


# --- import detail -------------------------------------------------------------------------


def test_clean_import(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1, T2), label="Combine 50K")

    ctx = detail(logged_in, batch).context

    assert ctx["counts"] == {
        "imported": 2, "skipped_duplicate": 0, "skipped_conflict": 0, "failed": 0,
        "skipped": 0, "needs_attention": 0, "all": 2,
    }
    assert ctx["conflict_banner"] is None and ctx["failed_notice"] is None
    assert ctx["hint"] is None
    assert ctx["summary_lead"] == copy.SUMMARY_CLEAN.format(n=2)
    assert ctx["status"] == "all"
    assert ctx["account_line"] == copy.ACCOUNT_LINE.format(label="Combine 50K")
    assert "raw_file" in ctx["batch"].get_deferred_fields()


def test_duplicates_only(logged_in, user):
    uploaded(logged_in, csv_bytes(T1, T2))
    again = uploaded(logged_in, csv_bytes(T1, T2))

    ctx = detail(logged_in, again).context

    assert ctx["counts"]["skipped_duplicate"] == 2 and ctx["counts"]["skipped"] == 2
    assert ctx["conflict_banner"] is None
    assert ctx["summary_lead"] == copy.SUMMARY_DUPLICATES_ONLY
    assert ctx["status"] == "all"
    assert ctx["account_line"] == copy.ACCOUNT_LINE_NOT_RECORDED


def test_conflict_banner_blank_label(logged_in, user):
    uploaded(logged_in, csv_bytes(T1))
    batch = uploaded(logged_in, csv_bytes(T1_CHANGED, T3))

    ctx = detail(logged_in, batch).context

    assert ctx["conflict_banner"] == {
        "title": copy.CONFLICT_TITLE_ONE, "body": copy.CONFLICT_BODY_BLANK,
    }
    assert "Delete this import first, then upload again with an Account name" in (
        copy.CONFLICT_BODY_BLANK
    )
    assert ctx["counts"]["skipped_conflict"] == 1
    assert ctx["status"] == "needs_attention"
    assert ctx["account_line"] == copy.ACCOUNT_LINE_BLANK


def test_conflict_banner_filled_label_names_it(logged_in, user):
    uploaded(logged_in, csv_bytes(T1, T2), label="Combine 50K")
    batch = uploaded(
        logged_in, csv_bytes(T1_CHANGED, {**T2, "EnteredAt": "12/19/2026 09:59:00 +00:00"}, T3),
        label="Combine 50K",
    )

    banner = detail(logged_in, batch).context["conflict_banner"]

    assert banner["title"] == copy.CONFLICT_TITLE_MANY.format(n=2)
    assert banner["body"] == copy.CONFLICT_BODY_FILLED.format(label="Combine 50K")


def test_failed_only_shows_quiet_notice_and_needs_attention(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T2, T1_BAD_PNL))

    ctx = detail(logged_in, batch).context

    assert ctx["conflict_banner"] is None
    assert ctx["failed_notice"] == copy.FAILED_NOTICE_ONE
    assert ctx["status"] == "needs_attention"
    assert [r["status"] for r in ctx["rows"]] == ["failed"]


def test_conflicts_and_failed_default_to_needs_attention_conflicts_first(logged_in, user):
    uploaded(logged_in, csv_bytes(T1))
    batch = uploaded(logged_in, csv_bytes(T1_BAD_PNL, UNSUPPORTED, T1_CHANGED, T3))

    ctx = detail(logged_in, batch).context

    assert ctx["conflict_banner"] is not None
    assert ctx["failed_notice"] == copy.FAILED_NOTICE_MANY.format(n=2)
    assert ctx["counts"]["needs_attention"] == 3
    assert [r["status"] for r in ctx["rows"]] == ["skipped_conflict", "failed", "failed"]
    assert [r["line_number"] for r in ctx["rows"]] == [4, 2, 3]


@pytest.mark.parametrize("label", ["", "Combine 50K"])
def test_all_rows_conflict_uses_the_neutral_banner(logged_in, user, label):
    uploaded(logged_in, csv_bytes(T1, T2), label=label)
    batch = uploaded(
        logged_in, csv_bytes(T1_CHANGED, {**T2, "EnteredAt": "12/19/2026 09:59:00 +00:00"}),
        label=label,
    )

    banner = detail(logged_in, batch).context["conflict_banner"]

    assert banner == {
        "title": copy.CONFLICT_TITLE_MANY.format(n=2), "body": copy.CONFLICT_BODY_NOT_RECORDED,
    }
    assert "Add an Account name" not in banner["body"]
    assert banner["body"] == (
        "Their IDs match trades you already imported, but the details differ. Nothing was "
        "lost or overwritten. Check the Account name you used, then upload the file again."
    )


def test_summary_clean_singular_and_plural(logged_in, user):
    one = uploaded(logged_in, csv_bytes(T1))
    two = uploaded(logged_in, csv_bytes(T2, T3))

    assert detail(logged_in, one).context["summary_lead"] == "All 1 row imported."
    assert detail(logged_in, two).context["summary_lead"] == "All 2 rows imported."


def test_count_strings_singular_and_plural():
    assert copy.CONFLICT_TITLE_ONE == "1 row was not imported"
    assert copy.CONFLICT_TITLE_MANY.format(n=3) == "3 rows were not imported"
    assert copy.FAILED_NOTICE_ONE.startswith("1 row could not be read. See it ")
    assert copy.FAILED_NOTICE_MANY.format(n=3).startswith("3 rows could not be read. See them ")


def test_nothing_added_lead(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1_BAD_PNL))
    assert detail(logged_in, batch).context["summary_lead"] == copy.SUMMARY_NOTHING_ADDED


@pytest.mark.parametrize(
    "query, status, n",
    [
        ("", "needs_attention", 3),
        ("?status=all", "all", 4),
        ("?status=imported", "imported", 1),
        ("?status=skipped_duplicate", "skipped_duplicate", 0),
        ("?status=skipped_conflict", "skipped_conflict", 1),
        ("?status=failed", "failed", 2),
        ("?status=bogus", "needs_attention", 3),
        ("?status=<script>", "needs_attention", 3),
    ],
)
def test_status_filter(logged_in, user, query, status, n):
    uploaded(logged_in, csv_bytes(T1))
    batch = uploaded(logged_in, csv_bytes(T1_BAD_PNL, UNSUPPORTED, T1_CHANGED, T3))

    ctx = detail(logged_in, batch, query).context

    assert ctx["status"] == status
    assert len(ctx["rows"]) == n
    assert [f["value"] for f in ctx["filters"] if f["current"]] == [status]


def test_filters_list_counts_and_urls(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T2, T1_BAD_PNL))

    filters = detail(logged_in, batch).context["filters"]

    assert [(f["value"], f["label"], f["count"]) for f in filters] == [
        ("needs_attention", copy.FILTER_LABELS["needs_attention"], 1),
        ("all", copy.FILTER_LABELS["all"], 2),
        ("imported", copy.FILTER_LABELS["imported"], 1),
        ("skipped_duplicate", copy.FILTER_LABELS["skipped_duplicate"], 0),
        ("skipped_conflict", copy.FILTER_LABELS["skipped_conflict"], 0),
        ("failed", copy.FILTER_LABELS["failed"], 1),
    ]
    assert filters[1]["url"] == f"/imports/{batch.pk}/?status=all"


def test_needs_attention_tab_is_absent_and_ignored_when_nothing_needs_attention(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))

    ctx = detail(logged_in, batch, "?status=needs_attention").context

    assert "needs_attention" not in [f["value"] for f in ctx["filters"]]
    assert ctx["status"] == "all"


def test_row_display_values_come_from_raw_and_time_in_user_zone(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))

    resp = detail(logged_in, batch)
    row = resp.context["rows"][0]

    assert (row["line_number"], row["symbol"], row["size"]) == (2, "CLZ6", "2")
    assert row["status_label"] == "Imported"
    assert row["entered_at"].isoformat() == "2026-12-19T09:00:00+00:00"
    assert "Dec 19, 4:00 AM" in resp.content.decode()  # 09:00 UTC in America/New_York


def test_row_with_unreadable_time_shows_no_time(logged_in, user):
    batch = uploaded(logged_in, csv_bytes({**T1, "Id": "9000000096", "EnteredAt": "garbage"}))

    row = detail(logged_in, batch).context["rows"][0]

    assert row["status"] == "failed" and row["entered_at"] is None


def test_detail_pages_50_rows_and_keeps_the_filter(logged_in, user):
    rows = [{**T1, "Id": str(9100000000 + i)} for i in range(51)]
    batch = uploaded(logged_in, csv_bytes(*rows))

    first = detail(logged_in, batch, "?status=imported").context
    second = detail(logged_in, batch, "?status=imported&page=2").context

    assert len(first["rows"]) == 50 and len(second["rows"]) == 1
    assert first["showing"] == copy.SHOWING.format(start=1, end=50, total=51)
    assert second["showing"] == copy.SHOWING.format(start=51, end=51, total=51)
    assert second["rows"][0]["line_number"] == 52
    assert first["page_query"] == "status=imported"


def test_detail_query_count_is_constant(logged_in, user, django_assert_max_num_queries):
    uploaded(logged_in, csv_bytes(T1))
    rows = [{**T1, "Id": str(9100000000 + i)} for i in range(60)] + [T1_CHANGED, T1_BAD_PNL]
    batch = uploaded(logged_in, csv_bytes(*rows))

    with django_assert_max_num_queries(9):
        resp = detail(logged_in, batch, "?status=all")
        assert len(resp.context["rows"]) == 50


def test_missing_batch_is_404(logged_in):
    assert logged_in.get("/imports/999999/").status_code == 404


# --- "uploaded before" hint (design 3.2) ---------------------------------------------------


def test_hint_plain_same_label(logged_in, user):
    first = uploaded(logged_in, csv_bytes(T1), label="A")
    again = uploaded(logged_in, csv_bytes(T1), label="A")

    hint = detail(logged_in, again).context["hint"]

    assert hint["mismatch"] is False
    assert hint["url"] == f"/imports/{first.pk}/"
    local = timezone.localtime(first.uploaded_at, user_zone(user))
    assert hint["text"] == copy.HINT_PLAIN.format(date=f"{local:%b} {local.day}")
    assert hint["link_text"] == copy.HINT_PLAIN_LINK


def user_zone(user):
    from zoneinfo import ZoneInfo

    return ZoneInfo(user.timezone)


def test_hint_mismatch_names_the_old_label(logged_in, user):
    first = uploaded(logged_in, csv_bytes(T1), label="Combine 50K")
    again = uploaded(logged_in, csv_bytes(T1), label="Funded")

    hint = detail(logged_in, again).context["hint"]

    assert hint["mismatch"] is True
    assert hint["title"] == copy.HINT_MISMATCH_TITLE
    assert "with Account 'Combine 50K'" in hint["text"]
    assert "adds these trades a second time" in hint["text"]
    assert hint["url"] == f"/imports/{first.pk}/"


def test_hint_mismatch_old_label_blank(logged_in, user):
    uploaded(logged_in, csv_bytes(T1), label="")
    again = uploaded(logged_in, csv_bytes(T1), label="Funded")

    hint = detail(logged_in, again).context["hint"]

    assert hint["mismatch"] is True
    assert "with no Account name" in hint["text"]


def test_no_hint_on_first_upload(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    assert detail(logged_in, batch).context["hint"] is None


def test_plain_hint_links_the_latest_earlier_batch_when_none_imported_anything(logged_in, user):
    uploaded(logged_in, csv_bytes(T1_BAD_PNL))
    second = uploaded(logged_in, csv_bytes(T1_BAD_PNL))
    third = uploaded(logged_in, csv_bytes(T1_BAD_PNL))

    hint = detail(logged_in, third).context["hint"]

    assert hint["mismatch"] is False
    assert hint["url"] == f"/imports/{second.pk}/"
    assert hint["link_text"] == copy.HINT_PLAIN_LINK


def test_no_mismatch_banner_when_the_earlier_batch_imported_nothing(logged_in, user):
    uploaded(logged_in, csv_bytes(T1), label="A")
    all_conflict = uploaded(logged_in, csv_bytes(T1_CHANGED), label="A")  # imports nothing
    now = uploaded(logged_in, csv_bytes(T1_CHANGED), label="B")  # imports, label known

    hint = detail(logged_in, now).context["hint"]

    assert hint["mismatch"] is False
    assert hint["url"] == f"/imports/{all_conflict.pk}/"


def test_hint_links_the_most_recent_earlier_batch_that_imported_something(logged_in, user):
    uploaded(logged_in, csv_bytes(T1), label="A")
    second = uploaded(logged_in, csv_bytes(T1), label="B")  # imports under B
    uploaded(logged_in, csv_bytes(T1), label="B")  # all duplicates, imports nothing
    fourth = uploaded(logged_in, csv_bytes(T1), label="B")

    hint = detail(logged_in, fourth).context["hint"]

    assert hint["url"] == f"/imports/{second.pk}/"
    assert hint["mismatch"] is False


def test_hint_never_points_forward(logged_in, user):
    first = uploaded(logged_in, csv_bytes(T1))
    uploaded(logged_in, csv_bytes(T1))
    assert detail(logged_in, first).context["hint"] is None


# --- tenant isolation (import-and-list.md section 5) ---------------------------------------


def test_isolation_between_two_users(client, user, other):
    client.force_login(user)
    a_batch = uploaded(client, FIXTURE.read_bytes(), label="Secret Desk")
    missing = client.get("/imports/999999/")

    client.force_login(other)
    theirs = client.get(f"/imports/{a_batch.pk}/")
    assert theirs.status_code == 404
    assert theirs.content == client.get("/imports/999999/").content
    assert missing.status_code == 404

    page = client.get("/imports/")
    assert "Secret Desk" not in page.content.decode()
    assert page.context["account_labels"] == []
    assert list(page.context["page_obj"]) == []

    b_batch = uploaded(client, FIXTURE.read_bytes(), label="Secret Desk")
    ctx = detail(client, b_batch, "?status=all").context
    assert ctx["counts"]["skipped_duplicate"] == a_batch.rows.filter(
        status="skipped_duplicate"
    ).count()  # only the in-file repeat, never A's rows
    assert ctx["counts"]["skipped_conflict"] == 0
    assert ctx["counts"]["imported"] == a_batch.imported_count
    assert ctx["hint"] is None
    assert f"/imports/{a_batch.pk}/" not in detail(client, b_batch).content.decode()


# --- escaping (follow-ups row 24) ----------------------------------------------------------

EVIL = "<script>alert(1)</script>\"'"


def test_hostile_values_render_escaped(logged_in, user):
    name = "<img src=x onerror=alert(1)>\"q'.csv"
    label = "\"><script>alert(2)</script>"
    hostile = {**T1, "Id": "9000000095", "ContractName": "<script>x</script>Z6", "Size": EVIL}
    batch_resp = upload(logged_in, csv_bytes(T1, hostile), label=label, name=name)
    batch = ImportBatch.objects.for_user(user).get()
    assert batch_resp.status_code == 302

    pages = [
        detail(logged_in, batch, "?status=all").content.decode(),
        logged_in.get("/imports/").content.decode(),
    ]
    for html in pages:
        assert "<script>alert" not in html
        assert "<img src=x" not in html
        assert "<script>x</script>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;&quot;q&#x27;.csv" in pages[0]
    assert "&lt;script&gt;x&lt;/scrip…" in pages[0]  # raw symbol, cut to 16 characters
    assert "&lt;script&gt;x&lt;/scr" in pages[0]  # importer's error text echoes the root


def test_datalist_label_with_quotes_cannot_break_out(logged_in, user):
    label = "\" autofocus onfocus=\"alert(1)"
    uploaded(logged_in, csv_bytes(T1), label=label)

    html = logged_in.get("/imports/").content.decode()
    options = Doc(html).all("option")

    assert {"value": label} in options
    assert 'onfocus="alert' not in html


def test_banner_and_row_texts_have_no_blame_words(logged_in, user):
    uploaded(logged_in, csv_bytes(T1, T2), label="X")
    batch = uploaded(
        logged_in, csv_bytes(T1_CHANGED, T1_BAD_PNL, UNSUPPORTED, T3), label="X"
    )
    ctx = detail(logged_in, batch, "?status=all").context
    texts = [ctx["conflict_banner"]["title"], ctx["conflict_banner"]["body"],
             ctx["failed_notice"], *(r["note"] for r in ctx["rows"]),
             copy.CONFLICT_BODY_BLANK]
    for text in texts:
        assert not re.search(r"error|invalid|you should have", text, re.I), text


# --- nav, timezone middleware, htmx ----------------------------------------------------------


def test_nav_marks_imports_current_on_list_and_detail(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    for path in ("/imports/", f"/imports/{batch.pk}/"):
        links = Doc(logged_in.get(path).content.decode()).all("a", href="/imports/")
        nav = [a for a in links if "nav-link" in a.get("class", "")]
        assert len(nav) == 2 and all(a.get("aria-current") == "page" for a in nav), path


def _zone_seen_by_view(request):
    seen = {}

    def view(req):
        seen["zone"] = timezone.get_current_timezone_name()
        return None

    UserTimezoneMiddleware(view)(request)
    return seen["zone"]


def test_timezone_middleware_activates_the_users_zone(user):
    request = RequestFactory().get("/")
    request.user = user
    assert _zone_seen_by_view(request) == "America/New_York"


def test_timezone_middleware_resets_for_anonymous(db):
    from django.contrib.auth.models import AnonymousUser

    timezone.activate(user_zone(type("U", (), {"timezone": "Asia/Tokyo"})))
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    assert _zone_seen_by_view(request) == "UTC"


def test_htmx_request_when_logged_out_gets_401_and_hx_redirect(client, db):
    resp = client.get("/imports/5/?status=failed", headers={"HX-Request": "true"})

    assert resp.status_code == 401
    assert resp["HX-Redirect"] == "/login/?next=/imports/5/%3Fstatus%3Dfailed"


def test_htmx_upload_when_logged_out_gets_401(client, db):
    resp = upload(client, csv_bytes(T1), **{"HX-Request": "true"})
    assert resp.status_code == 401
    assert resp["HX-Redirect"] == "/login/?next=/imports/"


def test_detail_htmx_request_returns_only_the_rows_fragment(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))

    resp = detail(logged_in, batch, "?status=imported")
    frag = logged_in.get(
        f"/imports/{batch.pk}/?status=imported", headers={"HX-Request": "true"}
    )

    names = [t.name for t in frag.templates]
    assert "journal/partials/import_rows.html" in names and "base.html" not in names
    assert "base.html" in [t.name for t in resp.templates]
    assert "HX-Request" in frag["Vary"]


def test_htmx_history_restore_gets_the_full_page(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    resp = logged_in.get(
        f"/imports/{batch.pk}/",
        headers={"HX-Request": "true", "HX-History-Restore-Request": "true"},
    )
    assert "base.html" in [t.name for t in resp.templates]


def test_htmx_upload_success_uses_hx_redirect(logged_in, user):
    resp = upload(logged_in, csv_bytes(T1), **{"HX-Request": "true"})

    batch = ImportBatch.objects.for_user(user).get()
    assert resp.status_code == 200
    assert resp["HX-Redirect"] == f"/imports/{batch.pk}/"


def test_htmx_upload_account_error_returns_only_the_account_field(logged_in, user):
    resp = upload(logged_in, csv_bytes(T1), label="A" * 65, **{"HX-Request": "true"})

    names = [t.name for t in resp.templates]
    assert resp.status_code == 200
    assert "journal/partials/account_field.html" in names and "base.html" not in names
    assert "65 characters" in resp.content.decode()


def test_htmx_upload_file_error_returns_the_full_page_retargeted(logged_in, user):
    resp = upload(logged_in, b"nope", **{"HX-Request": "true"})

    assert "base.html" in [t.name for t in resp.templates]
    assert (resp["HX-Retarget"], resp["HX-Reswap"]) == ("body", "outerHTML")


# --- fix batch 2: body cap, bad stored zone, label control characters, long symbol --------

MULTIPART = "multipart/form-data; boundary=BoUnDaRy"


def _cap():
    from config.middleware import MAX_REQUEST_BYTES

    return MAX_REQUEST_BYTES


def test_body_cap_is_the_file_cap_plus_one_mib():
    assert _cap() == MAX_RAW_FILE_BYTES + 1024 * 1024


@pytest.mark.parametrize("log_in", [False, True])
def test_over_cap_upload_gets_413_before_the_view(client, user, log_in):
    if log_in:
        client.force_login(user)
    with patch("journal.views.import_file") as spy:
        resp = client.post(
            "/imports/", data=b"x", content_type=MULTIPART, CONTENT_LENGTH=str(_cap() + 1)
        )
    assert resp.status_code == 413
    assert b"x" not in resp.content.replace(b"x-", b"")  # nothing echoed
    spy.assert_not_called()
    assert not ImportBatch.unscoped.exists()


def _through_cap_middleware(**meta):
    from config.middleware import RequestBodyLimitMiddleware

    # A body makes RequestFactory set the multipart content type; the length is overridden.
    request = RequestFactory().generic("POST", "/imports/", b"x", content_type=MULTIPART)
    request.META.update(meta)
    reached = []
    response = RequestBodyLimitMiddleware(lambda r: reached.append(r) or "view")(request)
    return response, reached


def test_exactly_at_cap_passes_through():
    response, reached = _through_cap_middleware(CONTENT_LENGTH=str(_cap()))
    assert response == "view" and reached


def test_multipart_post_without_length_gets_411():
    response, reached = _through_cap_middleware(CONTENT_LENGTH="")
    assert response.status_code == 411 and not reached


def test_second_file_part_is_rejected(logged_in, user):
    files = [SimpleUploadedFile("a.csv", csv_bytes(T1)), SimpleUploadedFile("b.csv", csv_bytes(T2))]
    resp = logged_in.post("/imports/", {"broker": "topstep", "file": files, "account": ""})
    assert resp.status_code == 400
    assert not ImportBatch.objects.for_user(user).exists()


def test_normal_upload_still_works_through_the_cap(logged_in, user):
    assert upload(logged_in, csv_bytes(T1)).status_code == 302


@pytest.mark.parametrize("stored", ["America", "Etc"])
def test_directory_like_stored_zone_falls_back_to_utc(logged_in, user, stored):
    User.objects.filter(pk=user.pk).update(timezone=stored)
    user.refresh_from_db()
    request = RequestFactory().get("/")
    request.user = user

    assert _zone_seen_by_view(request) == "UTC"
    assert logged_in.get("/imports/").status_code == 200


@pytest.mark.parametrize("label", ["A\r\nB", "A\nB", "A\tB", "A‎B", "A\x1bB"])
def test_account_with_control_characters_is_rejected(logged_in, user, label):
    resp = upload(logged_in, csv_bytes(T1), label=label)

    assert resp.status_code == 200
    assert copy.ACCOUNT_CONTROL_CHARACTERS in resp.context["form"].errors["account"]
    assert not ImportBatch.objects.for_user(user).exists()


def test_account_control_characters_copy():
    assert copy.ACCOUNT_CONTROL_CHARACTERS == (
        "Account names can't contain line breaks or other control characters. Remove them "
        "and try again."
    )


def test_account_with_nul_is_still_rejected(logged_in, user):
    resp = upload(logged_in, csv_bytes(T1), label="A\x00B")
    assert resp.context["form"].errors["account"]
    assert not ImportBatch.objects.for_user(user).exists()


@pytest.mark.parametrize(
    "label, stored", [("Konto ü", "Konto ü"), ("  Combine 50K ", "Combine 50K"), (" \t ", "")]
)
def test_normal_and_non_ascii_account_names_still_accepted(logged_in, user, label, stored):
    uploaded(logged_in, csv_bytes(T1), label=label)
    assert labels_of(user) == {stored}


def test_long_symbol_on_a_failed_row_is_cut_to_16_characters(logged_in, user):
    huge = {**T1, "Id": "9000000094", "ContractName": "Q" * 100_000}
    batch = uploaded(logged_in, csv_bytes(huge))

    resp = detail(logged_in, batch, "?status=all")
    row = resp.context["rows"][0]

    assert row["status"] == "failed"
    assert row["symbol"] == "Q" * 16 + "…"
    assert "Q" * 17 not in resp.content.decode()


# --- code-review fix batch: timezone leak, empty filename ---------------------------------


def test_timezone_middleware_deactivates_after_the_response(user):
    from config.middleware import UserTimezoneMiddleware as Middleware

    timezone.deactivate()
    request = RequestFactory().get("/")
    request.user = user
    Middleware(lambda r: timezone.get_current_timezone_name())(request)

    assert timezone.get_current_timezone_name() == "UTC"


def test_timezone_middleware_deactivates_when_the_view_raises(user):
    from config.middleware import UserTimezoneMiddleware as Middleware

    timezone.deactivate()
    request = RequestFactory().get("/")
    request.user = user

    def boom(req):
        raise RuntimeError("view failed")

    with pytest.raises(RuntimeError):
        Middleware(boom)(request)
    assert timezone.get_current_timezone_name() == "UTC"


def test_zone_does_not_leak_into_the_next_request(client, user, other):
    client.force_login(user)  # America/New_York
    assert client.get("/imports/").status_code == 200
    assert timezone.get_current_timezone_name() == "UTC"

    client.force_login(other)  # UTC
    batch = uploaded(client, csv_bytes(T1))
    assert "Dec 19, 9:00 AM" in detail(client, batch).content.decode()


@pytest.mark.parametrize("name", ["\x00\x01\x02", "   ", "\x07 \x1b"])
def test_safe_filename_never_returns_blank(name):
    assert safe_filename(name) == "import.csv"


def test_control_only_upload_name_is_treated_as_no_file(logged_in, user):
    # Django's multipart parser drops such a name, so the form asks for a file.
    resp = upload(logged_in, csv_bytes(T1), name="\x01\x02\x03")

    assert resp.context["form"].errors["file"] == [copy.FILE_MISSING]
    assert not ImportBatch.objects.for_user(user).exists()


def test_whitespace_only_upload_name_is_stored_and_shown_as_the_fallback(logged_in, user):
    resp = upload(logged_in, csv_bytes(T1), name="   ")

    assert resp.status_code == 302
    batch = ImportBatch.objects.for_user(user).get()
    assert batch.filename == "import.csv"
    for page in (logged_in.get("/imports/"), detail(logged_in, batch)):
        html = page.content.decode()
        assert "import.csv" in html
        assert not re.search(rf'<a [^>]*href="/imports/{batch.pk}/"[^>]*>\s*</a>', html)
