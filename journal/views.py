import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Case, Count, Exists, Max, OuterRef, Q, Subquery, Value, When
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
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
from journal.models import Execution, ImportBatch, RawImportRow
from journal.services import BROKER, TRY_AGAIN, ImportRetryError, import_file

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
                url = reverse("import_detail", args=[batch.pk])
                if htmx:
                    response = HttpResponse()
                    response["HX-Redirect"] = url
                    return response
                return redirect(url)
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
    response = render(
        request, "journal/import_list.html", {**_form_context(user, form), "page_obj": page}
    )
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
    batch = get_object_or_404(ImportBatch.objects.for_user(user).defer("raw_file"), pk=pk)
    rows = RawImportRow.objects.for_user(user).filter(import_batch=batch)

    by_status = dict(rows.values_list("status").annotate(n=Count("pk")).order_by())
    counts = {status: by_status.get(status, 0) for status in STATUSES}
    counts["skipped"] = counts[DUPLICATE] + counts[CONFLICT]
    counts[NEEDS_ATTENTION] = counts[CONFLICT] + counts[FAILED]
    counts[ALL] = sum(by_status.values())

    label = None
    if counts[IMPORTED]:
        label = (
            Execution.objects.for_user(user)
            .filter(raw_import_row__import_batch=batch)
            .values_list("broker_account_label", flat=True)
            .first()
        )

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
