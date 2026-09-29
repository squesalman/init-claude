"""
PR C2 "Delete this import": journal.services.delete_import_batch and /imports/<id>/delete/.
Spec: docs/adr/0005-batch-delete.md (decision + "Tests to write first", numbered below),
docs/design/import-account-label.md 3.3, 3.4, 5. Synthetic data only.
"""

from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.db.models.deletion import RestrictedError
from django.test import Client
from django.utils import dateformat, timezone

from config.test_pages import Doc
from journal import copy
from journal.models import Execution, ImportBatch, JournalEntry, RawImportRow
from journal.services import StaleConfirm, delete_import_batch
from journal.test_import_views import T1, T2, csv_bytes, uploaded
from journal.views import _shown_signer

User = get_user_model()
HTMX = {"HX-Request": "true"}


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="del-a@example.com", password="x", timezone="America/New_York"
    )


@pytest.fixture
def other(db):
    return User.objects.create_user(email="del-b@example.com", password="x")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def rows(i):
    """A fresh Topstep row (its own Id), so any number of trades can be imported."""
    return {**T1, "Id": str(9100000000 + i)}


def journal(user, batch, note="", rules=None, n=1):
    """Journal n of the batch's not-yet-journaled executions."""
    free = (
        Execution.objects.for_user(user)
        .filter(raw_import_row__import_batch=batch, journal_entry__isnull=True)
        .order_by("executed_at", "pk")[:n]
    )
    return [
        JournalEntry.objects.create(
            user=user, opening_execution=e, note=note, rules_followed=rules
        )
        for e in free
    ]


def manual_execution(user):
    return Execution.objects.create(
        user=user, broker="manual", symbol="AAPL", side=Execution.SIDE_BUY, quantity="1",
        price="1", currency="USD", executed_at=timezone.now(), source=Execution.SOURCE_MANUAL,
    )


def snapshot():
    """Every row of every user: an isolation failure anywhere shows up here."""
    return tuple(
        sorted(m.unscoped.values_list("pk", flat=True))
        for m in (ImportBatch, RawImportRow, Execution, JournalEntry)
    )


def confirm_url(pk):
    return f"/imports/{pk}/delete/"


def post_delete(client, pk, max_pk=0, tick=False, headers=None, **extra):
    """max_pk: the newest journal-entry pk the dialog showed (0 = none shown)."""
    token = _shown_signer(int(client.session["_auth_user_id"]), pk).sign(str(max_pk))
    data = {"shown": token, **extra}
    if tick:
        data["confirm"] = "on"
    return client.post(confirm_url(pk), data, headers=headers or {})


def flashes(client, resp):
    """The newest flash on the redirect target (uploaded() leaves its own flash unread)."""
    return [str(m) for m in client.get(resp["Location"]).context["messages"]][-1:]


def batch_executions(user, batch):
    return Execution.objects.for_user(user).filter(raw_import_row__import_batch=batch)


# --- ADR-0005 tests to write first ---------------------------------------------------------


def test_1_delete_removes_the_batch_its_rows_and_executions_and_nothing_else(logged_in, user):
    a = uploaded(logged_in, csv_bytes(T1))
    b = uploaded(logged_in, csv_bytes(T2))
    manual = manual_execution(user)
    a_execs = list(batch_executions(user, a).values_list("pk", flat=True))
    b_execs = set(batch_executions(user, b).values_list("pk", flat=True))
    assert a_execs and b_execs

    resp = post_delete(logged_in, a.pk)

    assert resp.status_code == 302 and resp["Location"] == "/imports/?from=delete"
    assert not ImportBatch.unscoped.filter(pk=a.pk).exists()
    assert not RawImportRow.unscoped.filter(import_batch_id=a.pk).exists()
    assert not Execution.unscoped.filter(pk__in=a_execs).exists()
    assert set(batch_executions(user, b).values_list("pk", flat=True)) == b_execs
    assert Execution.unscoped.filter(pk=manual.pk).exists()
    assert flashes(logged_in, resp) == ["Import deleted. 1 trade removed. Ready when you are."]


def test_2_journaled_batch_without_the_tick_deletes_nothing(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    (entry,) = journal(user, batch, note="waited for the retest")
    logged_in.get(confirm_url(batch.pk))
    before = snapshot()

    resp = post_delete(logged_in, batch.pk, max_pk=entry.pk, tick=False)

    assert resp.status_code == 200
    assert snapshot() == before
    assert resp.context["notice"] == copy.DELETE_NOT_TICKED
    assert resp.context["journal_count"] == 1


def test_3_tick_and_confirmed_entries_deletes_the_journal_entries_too(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    entries = journal(user, batch, note="a note", n=2)
    logged_in.get(confirm_url(batch.pk))

    resp = post_delete(logged_in, batch.pk, max_pk=entries[-1].pk, tick=True)

    assert resp.status_code == 302
    assert not JournalEntry.unscoped.filter(pk__in=[e.pk for e in entries]).exists()
    assert not ImportBatch.unscoped.filter(pk=batch.pk).exists()
    assert flashes(logged_in, resp) == [
        "Import deleted. 2 trades and 2 journal entries removed. Ready when you are."
    ]


def test_4_stale_confirm_deletes_nothing_and_rerenders_fresh_counts(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    (seen,) = journal(user, batch, note="first", n=1)
    journal(user, batch, note="", n=1)  # journaled in another tab after the dialog opened
    before = snapshot()

    resp = post_delete(logged_in, batch.pk, max_pk=seen.pk, tick=True)

    assert resp.status_code == 200
    assert snapshot() == before
    assert resp.context["notice"] == copy.DELETE_STALE
    assert resp.context["journal_count"] == 2 and resp.context["noted_count"] == 1


def test_4_service_rolls_back_the_journal_delete_on_a_stale_confirm(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    seen, _ = journal(user, batch, n=2)
    before = snapshot()

    with pytest.raises(StaleConfirm):
        delete_import_batch(user, batch.pk, confirmed_max_pk=seen.pk)

    assert snapshot() == before


def test_4f_service_refuses_a_swap_that_lands_inside_the_delete(logged_in, user, monkeypatch):
    """Security review: another transaction deletes the shown entry and adds a newer one
    just before our journal delete runs. Counting first then deleting all saw n == shown
    and removed the unseen entry; the service must delete only the confirmed pks and
    refuse when anything else remains."""
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    (seen,) = journal(user, batch, n=1)
    real_delete = QuerySet.delete
    swapped = []

    def delete_after_a_swap(qs):
        if qs.model is JournalEntry and not swapped:
            swapped.append(True)  # the "other transaction", once
            JournalEntry.unscoped.filter(pk=seen.pk).delete()
            journal(user, batch, n=1)
        return real_delete(qs)

    monkeypatch.setattr(QuerySet, "delete", delete_after_a_swap)

    with pytest.raises(StaleConfirm):
        delete_import_batch(user, batch.pk, confirmed_max_pk=seen.pk)

    assert swapped and ImportBatch.unscoped.filter(pk=batch.pk).exists()


def test_4e_entry_swapped_between_get_and_post_is_refused(logged_in, user):
    """Follow-ups 29d: one entry deleted and another added between the GET and the POST
    keeps the count at 1, so a count check would delete an entry the user never saw. The
    token carries the newest pk shown instead, and the new entry's pk is higher."""
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    (seen,) = journal(user, batch, n=1)
    page = Doc(logged_in.get(confirm_url(batch.pk)).content.decode())
    seen.delete()  # another tab
    journal(user, batch, n=1)  # another tab, a different trade
    before = snapshot()

    resp = logged_in.post(
        confirm_url(batch.pk),
        {"shown": page.one("input", name="shown")["value"], "confirm": "on"},
    )

    assert resp.status_code == 200 and resp.context["notice"] == copy.DELETE_STALE
    assert snapshot() == before


def test_4b_forged_or_unsigned_token_cannot_bypass_the_stale_check(logged_in, user):
    """Code review: an unbounded client-posted value made the stale check never fire. The
    newest pk shown now travels as a signed token, so a forged or missing one reads as 0."""
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    journal(user, batch, n=1)
    before = snapshot()

    for bad in ("999999", "abc", "", "-5"):
        resp = logged_in.post(confirm_url(batch.pk), {"shown": bad, "confirm": "on"})
        assert resp.status_code == 200 and resp.context["notice"] == copy.DELETE_STALE
        assert snapshot() == before


def test_4d_second_tab_cannot_widen_what_the_first_tab_confirmed(logged_in, user):
    """Tab A shows 1 entry; another tab renders after a 2nd entry exists. Tab A's own
    token still names only the 1st, so its submit is stale rather than deleting 2 entries."""
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    journal(user, batch, n=1)
    page = Doc(logged_in.get(confirm_url(batch.pk)).content.decode())
    tab_a = page.one("input", name="shown")["value"]
    journal(user, batch, n=1)
    logged_in.get(confirm_url(batch.pk))  # tab B renders with 2
    before = snapshot()

    resp = logged_in.post(confirm_url(batch.pk), {"shown": tab_a, "confirm": "on"})

    assert resp.status_code == 200 and resp.context["notice"] == copy.DELETE_STALE
    assert snapshot() == before


def test_4c_honest_posted_token_still_deletes_normally(logged_in, user):
    """Regression guard for the fix above: a client that posts back the token it was
    actually shown (the ordinary, non-adversarial case) is unaffected."""
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    entries = journal(user, batch, n=2)
    page = Doc(logged_in.get(confirm_url(batch.pk)).content.decode())

    resp = logged_in.post(
        confirm_url(batch.pk),
        {"shown": page.one("input", name="shown")["value"], "confirm": "on"},
    )

    assert resp.status_code == 302
    assert not JournalEntry.unscoped.filter(pk__in=[e.pk for e in entries]).exists()


def test_5_isolation_other_user_gets_404_and_nothing_changes(logged_in, user, other):
    batch = uploaded(logged_in, csv_bytes(T1))
    (entry,) = journal(user, batch)
    before = snapshot()
    intruder = Client()
    intruder.force_login(other)

    assert intruder.get(confirm_url(batch.pk)).status_code == 404
    assert post_delete(intruder, batch.pk, max_pk=entry.pk, tick=True).status_code == 404
    with pytest.raises(ImportBatch.DoesNotExist):
        delete_import_batch(other, batch.pk, confirmed_max_pk=entry.pk)
    assert snapshot() == before


def test_6_all_duplicate_batch_removes_no_executions(logged_in, user):
    first = uploaded(logged_in, csv_bytes(T1, T2))
    again = uploaded(logged_in, csv_bytes(T1, T2))
    assert again.imported_count == 0
    kept = set(batch_executions(user, first).values_list("pk", flat=True))

    result = delete_import_batch(user, again.pk, confirmed_max_pk=0)

    assert (result.trade_count, result.journal_count) == (0, 0)
    assert not ImportBatch.unscoped.filter(pk=again.pk).exists()
    assert set(batch_executions(user, first).values_list("pk", flat=True)) == kept


def test_6_all_duplicate_batch_flash_says_no_trades_affected(logged_in):
    uploaded(logged_in, csv_bytes(T1))
    again = uploaded(logged_in, csv_bytes(T1))

    resp = post_delete(logged_in, again.pk)

    assert flashes(logged_in, resp) == [
        "Import deleted. No trades were affected. Ready when you are."
    ]


def test_7_bare_execution_delete_with_a_journal_entry_still_raises(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    (entry,) = journal(user, batch)

    with pytest.raises(RestrictedError):
        entry.opening_execution.delete()


# --- GET, CSRF, gone, stale links ----------------------------------------------------------


LONG_NOTE = ("Waited for the <b>retest</b> " * 20)[:497] + "END"  # 500 chars, tail detectable


def test_get_renders_the_confirm_and_never_deletes(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1, T2), label="Combine 50K")
    journal(user, batch, note=LONG_NOTE, rules=True)
    journal(user, batch, note="", rules=None)
    before = snapshot()

    resp = logged_in.get(confirm_url(batch.pk))

    assert resp.status_code == 200
    assert snapshot() == before
    ctx = resp.context
    assert ctx["batch"].pk == batch.pk
    assert ctx["account_line"] == "Account: Combine 50K"
    assert (ctx["trade_count"], ctx["other_rows_count"]) == (2, 0)
    assert (ctx["journal_count"], ctx["noted_count"]) == (2, 1)
    assert ctx["notice"] is None
    first, second = ctx["journal_list"]
    assert first["symbol"] and first["opened_at"] is not None
    assert first["rules"] == copy.RULES_FOLLOWED and second["rules"] == copy.RULES_NOT_ANSWERED
    assert first["note"] == LONG_NOTE and second["note"] == ""  # full: users copy it from here
    assert ctx["show_all_url"] is None
    assert ctx["checkbox_label"] == (
        "Also delete my 2 journal entries (1 with a written note). This can't be undone."
    )
    assert ctx["delete_button"] == copy.DELETE_BUTTON_WITH_ENTRIES


def test_confirm_body_variants(logged_in, user):
    plain = uploaded(logged_in, csv_bytes(T1))
    ctx = logged_in.get(confirm_url(plain.pk)).context
    assert ctx["body"] == (
        "This removes the 1 trade that came from this file, and the upload record. "
        "Your other imports stay as they are."
    )
    assert ctx["journal_body"] is None and ctx["checkbox_label"] is None
    assert ctx["delete_button"] == copy.DELETE_BUTTON

    empty = uploaded(logged_in, csv_bytes(T1))  # all duplicates
    assert logged_in.get(confirm_url(empty.pk)).context["body"] == copy.DELETE_BODY_NO_TRADES


def test_whitespace_only_note_is_not_counted_as_written(logged_in, user):
    """Follow-ups 29c: a note of only spaces/newlines loses nothing, so the dialog must not
    count it as a written note."""
    batch = uploaded(logged_in, csv_bytes(T1, T2))
    journal(user, batch, note=" \n\t ")
    journal(user, batch, note="real")
    ctx = logged_in.get(confirm_url(batch.pk)).context
    assert (ctx["journal_count"], ctx["noted_count"]) == (2, 1)


def test_too_many_query_fields_on_the_confirm_read_as_no_query(logged_in, user):
    """Follow-ups 29b: over DATA_UPLOAD_MAX_NUMBER_FIELDS is ignored, never a 400."""
    batch = uploaded(logged_in, csv_bytes(*[rows(i) for i in range(11)]))
    journal(user, batch, n=21)

    resp = logged_in.get(confirm_url(batch.pk) + "?all=1&from=banner&" + "a=1&" * 2000)

    assert resp.status_code == 200
    assert len(resp.context["journal_list"]) == 20 and not resp.context["from_banner"]


def test_checkbox_label_singular_and_no_notes(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    journal(user, batch, note="")
    ctx = logged_in.get(confirm_url(batch.pk)).context
    assert ctx["checkbox_label"] == (
        "Also delete my 1 journal entry (none with written notes). This can't be undone."
    )


def test_journal_list_shows_20_then_offers_show_all(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(*[rows(i) for i in range(11)]))
    journal(user, batch, n=21)

    ctx = logged_in.get(confirm_url(batch.pk)).context
    assert len(ctx["journal_list"]) == 20 and ctx["journal_count"] == 21
    assert ctx["show_all_url"] == confirm_url(batch.pk) + "?all=1"

    ctx = logged_in.get(ctx["show_all_url"]).context
    assert len(ctx["journal_list"]) == 21 and ctx["show_all_url"] is None


def test_reupload_line_when_a_later_upload_of_the_same_file_added_nothing(logged_in):
    first = uploaded(logged_in, csv_bytes(T1))
    ctx = logged_in.get(confirm_url(first.pk)).context
    assert ctx["reupload_line"] is None

    again = uploaded(logged_in, csv_bytes(T1))
    ctx = logged_in.get(confirm_url(first.pk)).context
    local = timezone.localtime(again.uploaded_at, ZoneInfo("America/New_York"))  # user's tz
    date = dateformat.format(local, "M j")
    assert ctx["reupload_line"] == copy.DELETE_REUPLOAD_LINE.format(date=date)
    # The re-upload itself added no trades, so it gets the no-trades body, not the line.
    assert logged_in.get(confirm_url(again.pk)).context["reupload_line"] is None


def test_post_without_csrf_is_rejected(user):
    owner = Client(enforce_csrf_checks=True)
    owner.force_login(user)
    batch = ImportBatch.objects.create(
        user=user, broker="topstep", filename="x.csv", file_sha256="0" * 64, raw_file=b""
    )
    before = snapshot()

    resp = owner.post(confirm_url(batch.pk), {"journal_count": 0})

    assert resp.status_code == 403
    assert snapshot() == before


def test_double_delete_redirects_with_already_deleted_flash(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    post_delete(logged_in, batch.pk)

    resp = post_delete(logged_in, batch.pk)

    assert resp.status_code == 302 and resp["Location"] == "/imports/"
    assert flashes(logged_in, resp) == [copy.DELETE_ALREADY_GONE]
    get = logged_in.get(confirm_url(batch.pk))
    assert get.status_code == 302 and flashes(logged_in, get) == [copy.DELETE_ALREADY_GONE]


def test_old_detail_url_of_a_deleted_import_redirects_with_flash(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    post_delete(logged_in, batch.pk)

    resp = logged_in.get(f"/imports/{batch.pk}/")

    assert resp.status_code == 302 and resp["Location"] == "/imports/"
    assert flashes(logged_in, resp) == [copy.DELETED_BEFORE]


def test_someone_elses_deleted_or_unknown_id_is_still_404(logged_in, other):
    batch = uploaded(logged_in, csv_bytes(T1))
    post_delete(logged_in, batch.pk)
    intruder = Client()
    intruder.force_login(other)

    for c, pk in ((intruder, batch.pk), (logged_in, batch.pk + 1000)):
        assert c.get(f"/imports/{pk}/").status_code == 404
        assert c.get(confirm_url(pk)).status_code == 404
        assert post_delete(c, pk).status_code == 404


def test_login_required(client, user):
    batch = ImportBatch.objects.create(
        user=user, broker="topstep", filename="x.csv", file_sha256="0" * 64, raw_file=b""
    )
    assert client.get(confirm_url(batch.pk)).status_code == 302
    resp = client.get(confirm_url(batch.pk), headers=HTMX)
    assert resp.status_code == 401 and "HX-Redirect" in resp


# --- htmx and "from the banner" ------------------------------------------------------------


def test_htmx_get_returns_the_dialog_body_only(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))

    full = logged_in.get(confirm_url(batch.pk))
    part = logged_in.get(confirm_url(batch.pk), headers=HTMX)

    assert [t.name for t in part.templates][0] == "journal/partials/delete_confirm.html"
    assert [t.name for t in full.templates][0] == "journal/import_delete.html"
    assert "<html" not in part.content.decode() and "<html" in full.content.decode()
    assert "HX-Request" in part["Vary"]


def test_htmx_post_success_uses_hx_redirect(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))

    resp = post_delete(logged_in, batch.pk, headers=HTMX)

    assert resp.status_code == 200 and resp["HX-Redirect"] == "/imports/?from=delete"
    assert not ImportBatch.unscoped.filter(pk=batch.pk).exists()


def test_htmx_post_stale_rerenders_the_dialog_body(logged_in, user):
    batch = uploaded(logged_in, csv_bytes(T1))
    journal(user, batch)

    resp = post_delete(logged_in, batch.pk, headers=HTMX)

    assert resp.status_code == 200
    assert [t.name for t in resp.templates][0] == "journal/partials/delete_confirm.html"
    assert resp.context["notice"] == copy.DELETE_STALE


def test_htmx_post_already_gone_uses_hx_redirect(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    post_delete(logged_in, batch.pk)

    resp = post_delete(logged_in, batch.pk, headers=HTMX)

    assert resp["HX-Redirect"] == "/imports/"


def test_from_banner_appends_the_account_line_and_opens_the_field(logged_in):
    batch = uploaded(logged_in, csv_bytes(T1))
    assert logged_in.get(confirm_url(batch.pk) + "?from=banner").context["from_banner"]

    resp = post_delete(logged_in, batch.pk, **{"from": "banner"})

    assert resp["Location"] == "/imports/?from=banner"
    page = logged_in.get(resp["Location"])
    assert [str(m) for m in page.context["messages"]][-1:] == [
        "Import deleted. 1 trade removed. Ready when you are. " + copy.DELETED_FROM_BANNER
    ]
    assert page.context["account_open"] and page.context["focus_account"]


def test_imports_page_opens_account_after_any_delete_but_focuses_only_from_banner(logged_in):
    ctx = logged_in.get("/imports/?from=delete").context
    assert ctx["account_open"] and not ctx["focus_account"]
    ctx = logged_in.get("/imports/").context
    assert not ctx["account_open"] and not ctx["focus_account"]
