import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Case, Count, Exists, Max, OuterRef, Q, Subquery, Value, When
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import dateformat, timezone
from django.utils.cache import patch_vary_headers
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache

from accounts import copy
from journal import copy as import_copy
from journal.decorators import htmx_login_required, is_htmx
from journal.forms import UploadForm, safe_filename
from journal.importers.topstep import ImportFileError, parse_timestamp
from journal.models import Execution, ImportBatch, JournalEntry, RawImportRow
from journal.services import (
    BROKER,
    TRY_AGAIN,
    ImportRetryError,
    StaleConfirm,
    delete_import_batch,
    import_file,
)

log = logging.getLogger(__name__)

IMPORTS_PER_PAGE = 25
ROWS_PER_PAGE = 50
MAX_LABEL_SUGGESTIONS = 10
NEEDS_ATTENTION = "needs_attention"
ALL = "all"
IMPORTED = RawImportRow.STATUS_IMPORTED
DUPLICATE = RawImportRow.STATUS_SKIPPED_DUPLICATE
CONFLICT = RawImportRow.STATUS_SKIPPED_CONFLICT
FAILED = RawImportRow.STATUS_FAILED
STATUSES = (IMPORTED, DUPLICATE, CONFLICT, FAILED)
JOURNAL_PREVIEW = 20  # design 3.3: first 20 entries, then "Show all"
DELETED_KEY = "deleted_imports"  # session: ids this user deleted, for the "already deleted" flash


@never_cache  # back button after logout must not show trade data
@login_required
def trades(request):
    # Placeholder: PR D builds the real list. Until then no user-owned data is passed.
    return render(
        request,
        "journal/trade_list.html",
        {
            "empty_heading": copy.TRADES_EMPTY_HEADING,
            "empty_body": copy.TRADES_EMPTY_BODY,
            "empty_action": copy.TRADES_EMPTY_ACTION,
        },
    )


def _batch_label(user):
    """A batch's Account label, read from its executions (ImportBatch has no label column,
    design decision H1). NULL means the batch imported nothing, so no label was recorded."""
    return Subquery(
        Execution.objects.for_user(user)
        .filter(raw_import_row__import_batch=OuterRef("pk"))
        .values("broker_account_label")[:1]
    )


def _account_labels(user) -> list[str]:
    """Datalist suggestions: the user's own non-blank labels, most recently used first."""
    return list(
        Execution.objects.for_user(user)
        .filter(broker=BROKER)
        .exclude(broker_account_label="")
        .values("broker_account_label")
        .annotate(last_used=Max("created_at"))
        .order_by("-last_used")
        .values_list("broker_account_label", flat=True)[:MAX_LABEL_SUGGESTIONS]
    )


def _form_context(user, form) -> dict:
    labels = _account_labels(user)
    return {
        "form": form,
        "account_labels": labels,  # emit with autoescape or json_script, never string-built
        "account_open": bool(labels) or bool(form.errors),
        "account_placeholder": (
            import_copy.ACCOUNT_PLACEHOLDER_SAVED if labels
            else import_copy.ACCOUNT_PLACEHOLDER_EMPTY
        ),
        "copy": import_copy,
    }


@htmx_login_required
def imports(request):
    user = request.user
    htmx = is_htmx(request)
    form = UploadForm(request.POST, request.FILES) if request.method == "POST" else UploadForm()
    if request.method == "POST":
        if form.is_valid():
            upload = form.cleaned_data["file"]
            try:
                batch = import_file(
                    user, safe_filename(upload.name), upload.read(), form.cleaned_data["account"]
                )
            except ImportFileError as exc:
                log.info("upload rejected: %s", type(exc).__name__)
                form.add_error("file", str(exc))  # str() is user-facing by contract
            except (ImportRetryError, DatabaseError) as exc:
                log.error("upload failed: %s", type(exc).__name__)  # class only
                form.add_error(None, TRY_AGAIN)
            else:
                messages.success(request, import_copy.UPLOAD_DONE)
                return _redirect(request, reverse("import_detail", args=[batch.pk]))
        if htmx and set(form.errors) == {"account"}:
            # Swap only the Account field, so the chosen file stays selected (design Q G).
            return render(request, "journal/partials/account_field.html", _form_context(user, form))

    batches = (
        ImportBatch.objects.for_user(user)
        .defer("raw_file")
        .annotate(
            account_label=_batch_label(user),
            has_conflicts=Exists(
                RawImportRow.objects.for_user(user).filter(
                    import_batch=OuterRef("pk"), status=CONFLICT
                )
            ),
        )
        .order_by("-uploaded_at", "-pk")
    )
    page = Paginator(batches, IMPORTS_PER_PAGE).get_page(request.GET.get("page"))
    page.object_list = list(page.object_list)
    for batch in page.object_list:
        batch.needs_attention = batch.failed_count > 0 or batch.has_conflicts
    context = {**_form_context(user, form), "page_obj": page}
    # Design 1 and 3.4: after a delete the Account field is open; focused only from a banner.
    arrived_from = request.GET.get("from")
    context["account_open"] = context["account_open"] or arrived_from in ("delete", "banner")
    context["focus_account"] = arrived_from == "banner"
    response = render(request, "journal/import_list.html", context)
    if htmx and request.method == "POST":
        # Any other upload problem re-renders the whole page (the file must be re-picked).
        response["HX-Retarget"] = "body"
        response["HX-Reswap"] = "outerHTML"
    return response


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _short(text: str, limit: int = 16) -> str:
    """A failed row's raw cell can be huge; show what the importer's message shows (16)."""
    return text if len(text) <= limit else text[:limit] + "\u2026"


def _row(row) -> dict:
    raw = row.raw if isinstance(row.raw, dict) else {}
    try:
        entered_at = parse_timestamp(raw, "EnteredAt")
    except (KeyError, TypeError, ValueError):
        entered_at = None
    return {
        "line_number": row.line_number,
        "status": row.status,
        "status_label": row.get_status_display(),
        "symbol": _short(_text(raw.get("ContractName"))),
        "entered_at": entered_at,
        "size": _text(raw.get("Size")),
        "note": row.error,
    }


def _summary_lead(counts) -> str | None:
    total = counts[ALL]
    if not total:
        return None
    if counts[IMPORTED] == total:
        return import_copy.SUMMARY_CLEAN_ONE if total == 1 else (
            import_copy.SUMMARY_CLEAN.format(n=total)
        )
    if counts[DUPLICATE] == total:
        return import_copy.SUMMARY_DUPLICATES_ONLY
    if counts[IMPORTED] == 0:
        return import_copy.SUMMARY_NOTHING_ADDED
    return None


def _conflict_banner(conflicts, label) -> dict | None:
    if not conflicts:
        return None
    return {
        "title": (
            import_copy.CONFLICT_TITLE_ONE if conflicts == 1
            else import_copy.CONFLICT_TITLE_MANY.format(n=conflicts)
        ),
        "body": (
            import_copy.CONFLICT_BODY_NOT_RECORDED if label is None  # imported nothing
            else import_copy.CONFLICT_BODY_FILLED.format(label=label) if label
            else import_copy.CONFLICT_BODY_BLANK
        ),
    }


def _failed_notice(failed) -> str | None:
    if not failed:
        return None
    if failed == 1:
        return import_copy.FAILED_NOTICE_ONE
    return import_copy.FAILED_NOTICE_MANY.format(n=failed)


def _account_line(label) -> str:
    if label is None:
        return import_copy.ACCOUNT_LINE_NOT_RECORDED
    if label == "":
        return import_copy.ACCOUNT_LINE_BLANK
    return import_copy.ACCOUNT_LINE.format(label=label)


def _hint(user, batch, label) -> dict | None:
    """Design 3.2: an EARLIER upload of the same bytes; the most recent one that imported at
    least one execution, else the most recent one. Only this user's batches are considered."""
    earlier = (
        ImportBatch.objects.for_user(user)
        .defer("raw_file")
        .filter(file_sha256=batch.file_sha256)
        .filter(
            Q(uploaded_at__lt=batch.uploaded_at)
            | Q(uploaded_at=batch.uploaded_at, pk__lt=batch.pk)
        )
        .annotate(account_label=_batch_label(user))
        .order_by(
            Case(When(imported_count__gt=0, then=Value(0)), default=Value(1)),
            "-uploaded_at",
            "-pk",
        )
        .first()
    )
    if earlier is None:
        return None
    date = dateformat.format(timezone.localtime(earlier.uploaded_at), "M j")
    url = reverse("import_detail", args=[earlier.pk])
    old = earlier.account_label or ""
    # A label is only known for a batch that imported something (read from its executions),
    # so the mismatch banner needs both labels; otherwise the plain hint.
    if label is not None and earlier.account_label is not None and label != old:
        body = import_copy.HINT_MISMATCH_BODY if old else import_copy.HINT_MISMATCH_BODY_BLANK
        return {
            "mismatch": True,
            "title": import_copy.HINT_MISMATCH_TITLE,
            "text": body.format(date=date, label=old),
            "link_text": import_copy.HINT_MISMATCH_LINK.format(date=date),
            "url": url,
        }
    return {
        "mismatch": False,
        "title": None,
        "text": import_copy.HINT_PLAIN.format(date=date),
        "link_text": import_copy.HINT_PLAIN_LINK,
        "url": url,
    }


@htmx_login_required
def import_detail(request, pk):
    user = request.user
    try:
        batch = ImportBatch.objects.for_user(user).defer("raw_file").get(pk=pk)
    except ImportBatch.DoesNotExist:
        return _gone(request, pk, import_copy.DELETED_BEFORE)
    rows = RawImportRow.objects.for_user(user).filter(import_batch=batch)

    by_status = dict(rows.values_list("status").annotate(n=Count("pk")).order_by())
    counts = {status: by_status.get(status, 0) for status in STATUSES}
    counts["skipped"] = counts[DUPLICATE] + counts[CONFLICT]
    counts[NEEDS_ATTENTION] = counts[CONFLICT] + counts[FAILED]
    counts[ALL] = sum(by_status.values())

    label = _label_of(user, batch) if counts[IMPORTED] else None

    values = ([NEEDS_ATTENTION] if counts[NEEDS_ATTENTION] else []) + [ALL, *STATUSES]
    status = request.GET.get("status")
    if status not in values:  # unknown or absent: the design's default, silently
        status = NEEDS_ATTENTION if counts[NEEDS_ATTENTION] else ALL
    base = reverse("import_detail", args=[batch.pk])
    filters = [
        {
            "value": value,
            "label": import_copy.FILTER_LABELS[value],
            "count": counts[value],
            "url": f"{base}?{urlencode({'status': value})}",
            "current": value == status,
        }
        for value in values
    ]

    if status == NEEDS_ATTENTION:
        shown = rows.filter(status__in=(CONFLICT, FAILED)).order_by(
            Case(When(status=CONFLICT, then=Value(0)), default=Value(1)), "line_number"
        )
    elif status == ALL:
        shown = rows.order_by("line_number")
    else:
        shown = rows.filter(status=status).order_by("line_number")
    page = Paginator(shown, ROWS_PER_PAGE).get_page(request.GET.get("page"))
    total = page.paginator.count

    context = {
        "batch": batch,
        "account_line": _account_line(label),
        "counts": counts,
        "conflict_banner": _conflict_banner(counts[CONFLICT], label),
        "failed_notice": _failed_notice(counts[FAILED]),
        "summary_lead": _summary_lead(counts),
        "hint": _hint(user, batch, label),
        "filters": filters,
        "status": status,
        "rows": [_row(row) for row in page.object_list],
        "page_obj": page,
        "page_query": urlencode({"status": status}),
        "showing": (
            import_copy.SHOWING.format(
                start=page.start_index(), end=page.end_index(), total=total
            ) if total else None
        ),
        "copy": import_copy,
    }
    template = "journal/partials/import_rows.html" if is_htmx(request) else (
        "journal/import_detail.html"
    )
    response = render(request, template, context)
    patch_vary_headers(response, ["HX-Request"])
    return response


def _redirect(request, url):
    """PRG redirect; an htmx request gets HX-Redirect so the whole page navigates."""
    if is_htmx(request):
        response = HttpResponse()
        response["HX-Redirect"] = url
        return response
    return redirect(url)


def _label_of(user, batch) -> str | None:
    """The batch's Account label, read from its executions (no label column, H1)."""
    return (
        Execution.objects.for_user(user)
        .filter(raw_import_row__import_batch=batch)
        .values_list("broker_account_label", flat=True)
        .first()
    )


def _gone(request, pk, text):
    """Design 3.3/3.4: an import this user deleted redirects with a flash. Any other missing
    id, including someone else's, is a 404 identical to a missing id. Only this session's
    deletes are remembered (no tombstones, ADR-0005 section 4), so another device gets 404."""
    if pk not in request.session.get(DELETED_KEY, []):
        raise Http404
    messages.info(request, text)
    return _redirect(request, reverse("imports"))


def _plural(n, one, many) -> str:
    return one if n == 1 else many.format(n=n)


def _noted(m) -> str:
    if m == 0:
        return import_copy.NOTED_NONE
    return _plural(m, import_copy.NOTED_ONE, import_copy.NOTED_MANY)


_RULES = {
    True: import_copy.RULES_FOLLOWED,
    False: import_copy.RULES_NOT_FOLLOWED,
    None: import_copy.RULES_NOT_ANSWERED,
}


def _reupload_line(user, batch) -> str | None:
    """Design 3.3: a LATER upload of the same bytes that added nothing, so these trades
    exist only through this batch."""
    later = (
        ImportBatch.objects.for_user(user)
        .defer("raw_file")
        .filter(file_sha256=batch.file_sha256, imported_count=0)
        .filter(
            Q(uploaded_at__gt=batch.uploaded_at)
            | Q(uploaded_at=batch.uploaded_at, pk__gt=batch.pk)
        )
        .order_by("-uploaded_at", "-pk")
        .first()
    )
    if later is None:
        return None
    date = dateformat.format(timezone.localtime(later.uploaded_at), "M j")
    return import_copy.DELETE_REUPLOAD_LINE.format(date=date)


def _body(trade_count, other_rows, n) -> str:
    if trade_count == 0:
        return import_copy.DELETE_BODY_NO_TRADES
    trades = _plural(trade_count, import_copy.TRADES_ONE, import_copy.TRADES_MANY)
    if n or not other_rows:  # variant 2 leads without the skipped-rows aside
        return import_copy.DELETE_BODY.format(trades=trades)
    if other_rows == 1:
        return import_copy.DELETE_BODY_WITH_SKIPPED_ONE.format(trades=trades)
    return import_copy.DELETE_BODY_WITH_SKIPPED.format(trades=trades, k=other_rows)


def _journal_body(n, m) -> str | None:
    if not n:
        return None
    if n == 1:
        return import_copy.DELETE_JOURNAL_BODY_ONE.format(noted=_noted(m))
    return import_copy.DELETE_JOURNAL_BODY_MANY.format(n=n, noted=_noted(m))


def _delete_context(request, batch, notice, from_banner) -> dict:
    """ADR-0005 section 5 inputs, all from scoped querysets, plus finished copy (design 5)."""
    user = request.user
    trade_count = batch.imported_count
    other_rows = batch.row_count - trade_count
    entries = JournalEntry.objects.for_user(user).filter(
        opening_execution__raw_import_row__import_batch=batch
    )
    totals = entries.aggregate(n=Count("pk"), m=Count("pk", filter=~Q(note="")))
    n, m = totals["n"], totals["m"]
    listed = entries.select_related("opening_execution").order_by(
        "opening_execution__executed_at", "pk"
    )
    show_all = request.GET.get("all") == "1"
    if not show_all:
        listed = listed[:JOURNAL_PREVIEW]
    show_all_url = None
    if not show_all and n > JOURNAL_PREVIEW:
        query = {"all": 1, **({"from": "banner"} if from_banner else {})}
        show_all_url = f"{reverse('import_delete', args=[batch.pk])}?{urlencode(query)}"
    return {
        "batch": batch,  # filename; uploaded_at renders in the active (user's) timezone
        "account_line": _account_line(_label_of(user, batch) if trade_count else None),
        "trade_count": trade_count,
        "other_rows_count": other_rows,
        "journal_count": n,  # the form posts this back as journal_count
        "noted_count": m,
        "journal_list": [
            {
                "symbol": e.opening_execution.symbol,
                "opened_at": e.opening_execution.executed_at,
                "rules": _RULES[e.rules_followed],
                "note": _short(e.note, 80),  # "" means no written note
                "url": None,  # no trade page yet (PR D)
            }
            for e in listed
        ],
        "show_all_url": show_all_url,
        "body": _body(trade_count, other_rows, n),
        "journal_body": _journal_body(n, m),
        "checkbox_label": import_copy.DELETE_CHECKBOX.format(
            entries=_plural(n, import_copy.ENTRIES_ONE, import_copy.ENTRIES_MANY),
            noted=_noted(m),
        ) if n else None,
        "reupload_line": _reupload_line(user, batch) if trade_count else None,
        "delete_button": (
            import_copy.DELETE_BUTTON_WITH_ENTRIES if n else import_copy.DELETE_BUTTON
        ),
        "notice": notice,  # DELETE_NOT_TICKED | DELETE_STALE | DELETE_FAILED | None
        "from_banner": from_banner,  # the form posts this back as from=banner
        "copy": import_copy,
    }


def _journal_count(value) -> int:
    """The count the user was shown. Anything unparseable counts as 0, which is safe: a
    lower count can only make the delete refuse (stale), never delete more."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _deleted_flash(result, from_banner) -> str:
    trades = _plural(result.trade_count, import_copy.TRADES_ONE, import_copy.TRADES_MANY)
    if result.trade_count == 0:
        text = import_copy.DELETED_NO_TRADES
    elif result.journal_count:
        entries = _plural(
            result.journal_count, import_copy.ENTRIES_ONE, import_copy.ENTRIES_MANY
        )
        text = import_copy.DELETED_TRADES_AND_ENTRIES.format(trades=trades, entries=entries)
    else:
        text = import_copy.DELETED_TRADES.format(trades=trades)
    return f"{text} {import_copy.DELETED_FROM_BANNER}" if from_banner else text


@htmx_login_required
def import_delete(request, pk):
    """ADR-0005: GET renders the confirm and never deletes; POST deletes. htmx gets the
    dialog body partial; without JS the same body renders as a full page."""
    user = request.user
    source = request.POST if request.method == "POST" else request.GET
    from_banner = source.get("from") == "banner"
    notice = None
    if request.method == "POST":
        shown = _journal_count(request.POST.get("journal_count"))
        ticked = request.POST.get("confirm") == "on"
        if shown and not ticked:
            notice = import_copy.DELETE_NOT_TICKED  # the server enforces the box too
        else:
            try:
                result = delete_import_batch(user, pk, confirmed_journal_count=shown)
            except ImportBatch.DoesNotExist:
                return _gone(request, pk, import_copy.DELETE_ALREADY_GONE)
            except StaleConfirm:
                notice = import_copy.DELETE_STALE
            except DatabaseError as exc:
                log.error("import delete failed: %s", type(exc).__name__)  # class only
                notice = import_copy.DELETE_FAILED
            else:
                # ponytail: last 50 ids only; an older stale link just gets the 404.
                deleted = request.session.get(DELETED_KEY, [])
                request.session[DELETED_KEY] = [*deleted, pk][-50:]
                messages.success(request, _deleted_flash(result, from_banner))
                arrived = "banner" if from_banner else "delete"
                return _redirect(request, f"{reverse('imports')}?from={arrived}")
    try:
        batch = ImportBatch.objects.for_user(user).defer("raw_file").get(pk=pk)
    except ImportBatch.DoesNotExist:
        return _gone(request, pk, import_copy.DELETE_ALREADY_GONE)
    template = "journal/partials/delete_confirm.html" if is_htmx(request) else (
        "journal/import_delete.html"
    )
    response = render(request, template, _delete_context(request, batch, notice, from_banner))
    patch_vary_headers(response, ["HX-Request"])
    return response
