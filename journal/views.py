import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import TooManyFieldsSent
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
from journal import display
from journal.decorators import htmx_login_required, is_htmx
from journal.forms import UploadForm, safe_filename
from journal.importers.topstep import ImportFileError, parse_timestamp
from journal.matching import derive_trades
from journal.models import Execution, ImportBatch, JournalEntry, RawImportRow
from journal.services import (
    BROKER,
    TRY_AGAIN,
    ImportRetryError,
    StaleConfirm,
    delete_import_batch,
    import_file,
)
from journal.stats import compute_stats

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


SORTS = {  # column -> (direction on first click, visually hidden text per direction)
    "opened": ("desc", {"desc": copy.SORTED_NEWEST, "asc": copy.SORTED_OLDEST}),
    "symbol": ("asc", {"asc": copy.SORTED_A_TO_Z, "desc": copy.SORTED_Z_TO_A}),
    "pnl": ("desc", {"desc": copy.SORTED_HIGHEST, "asc": copy.SORTED_LOWEST}),
}
DEFAULT_SORT = "opened"


def _user_trades(user):
    """Follow-ups row 27: derive_trades() needs (executed_at, id) order; id is file order, so
    same-timestamp legs keep their entry-before-exit order. Do not drop the order_by.

    ponytail: loads and matches every execution in Python on each request, no pagination.
    Query count is constant, but memory/CPU are linear and unbounded. Add pagination or move
    the aggregation to SQL past ~1,000 trades, or when the render-time budget in
    docs/product/features/import-and-list.md section 6 is missed.
    """
    return derive_trades(Execution.objects.for_user(user).order_by("executed_at", "id"))


def _sorted(trades, sort, direction):
    """Design 5.1: ties are newest opened first, then higher opening id; the sorts below are
    stable, so the base order breaks their ties. pnl puts open trades last both ways."""
    newest = sorted(trades, key=lambda t: (t.opened_at, t.opening_execution_id), reverse=True)
    if sort == "opened":
        return newest if direction == "desc" else newest[::-1]
    if sort == "symbol":
        return sorted(newest, key=lambda t: t.symbol, reverse=direction == "desc")
    closed = sorted(
        (t for t in newest if not t.is_open), key=lambda t: t.net_pnl, reverse=direction == "desc"
    )
    return closed + [t for t in newest if t.is_open]


def _sort_params(request):
    try:
        query = request.GET
    except TooManyFieldsSent:  # garbage query string: the default sort, never an error page
        query = {}
    sort_dir = query.get("sort_dir")  # mobile <select>: one "column_direction" value (5.2)
    if sort_dir in dict(copy.SORT_OPTIONS):
        sort, _, direction = sort_dir.partition("_")
        return sort, direction
    sort = query.get("sort")
    if sort not in SORTS:
        return DEFAULT_SORT, SORTS[DEFAULT_SORT][0]
    direction = query.get("dir")
    return sort, direction if direction in ("asc", "desc") else SORTS[sort][0]


def _sort_links(sort, direction) -> dict:
    base = reverse("trades")
    links = {}
    for column, (first, texts) in SORTS.items():
        active = column == sort
        target = ("asc" if direction == "desc" else "desc") if active else first
        links[column] = {
            "url": f"{base}?{urlencode({'sort': column, 'dir': target})}",
            "aria_sort": ("ascending" if direction == "asc" else "descending") if active
            else "none",
            "sorted_text": texts[direction] if active else None,
        }
    return links


def _result(trade) -> str:
    if trade.is_open:
        return "open"
    return "win" if trade.net_pnl > 0 else "loss" if trade.net_pnl < 0 else "breakeven"


def _opened(when) -> str:
    """Design 5.1 column 1: "Sep 26, 2:31 PM", with the year when it is not this year."""
    local = timezone.localtime(when)
    this_year = local.year == timezone.localtime().year
    return dateformat.format(local, "M j, g:i A" if this_year else "M j, Y, g:i A")


def _trade_row(trade) -> dict:
    return {
        "symbol": trade.symbol,
        "direction": trade.direction,  # "long" | "short"
        "quantity": display.quantity(trade.quantity),
        "entry": display.price(trade.avg_entry_price),
        "exit": None if trade.is_open else display.price(trade.avg_exit_price),
        "opened_at": trade.opened_at,
        "opened": _opened(trade.opened_at),
        "duration": None if trade.is_open else display.duration(trade.closed_at - trade.opened_at),
        "result": _result(trade),  # "win" | "loss" | "breakeven" | "open"
        "net_pnl": None if trade.is_open else display.money(trade.net_pnl),
        "account": trade.account_label or import_copy.LIST_ACCOUNT_BLANK,
    }


def _cards(stats) -> dict:
    """Section 3 / design 6. stats is None when there are no closed trades (n=0 everywhere).
    Slice 1 is USD only (design 5.3)."""
    win_n = stats.win_rate_n if stats else 0
    breakeven = stats.breakeven_count if stats else 0
    total_n = stats.total_n if stats else 0
    total = stats.total_pnl if stats else None
    avg_r = stats.avg_r if stats else None
    avg_r_n = stats.avg_r_n if stats else 0
    return {
        "win_rate": {
            "value": copy.WIN_RATE_VALUE.format(rate=stats.win_rate, n=win_n) if win_n
            else copy.WIN_RATE_EMPTY,
            "note": _plural(breakeven, copy.BREAKEVEN_NOTE_ONE, copy.BREAKEVEN_NOTE_MANY)
            if breakeven else None,
            "sr": copy.WIN_RATE_SR.format(rate=stats.win_rate, n=win_n) if win_n
            else copy.WIN_RATE_SR_EMPTY,
        },
        "total_pnl": {
            "value": copy.TOTAL_PNL_VALUE.format(amount=display.money(total), n=total_n)
            if total_n else copy.NO_VALUE,
            "tone": None if not total_n or total == 0 else "gain" if total > 0 else "loss",
            "sr": copy.TOTAL_PNL_SR.format(
                sign="plus " if total > 0 else "minus " if total < 0 else "",
                amount=f"{abs(total):,.2f}",
                n=total_n,
            ) if total_n else copy.TOTAL_PNL_SR_EMPTY,
        },
        "avg_r": {
            "value": copy.AVG_R_VALUE.format(r=avg_r, n=avg_r_n) if avg_r_n else copy.NO_VALUE,
            "help": None if avg_r_n else copy.AVG_R_HELP,  # shown only when n = 0
            "sr": copy.AVG_R_SR.format(r=avg_r, n=avg_r_n) if avg_r_n else copy.AVG_R_SR_EMPTY,
        },
    }


@never_cache  # back button after logout must not show trade data
@login_required
def trades(request):
    """Compute-on-read (ADR-0003 section 5): derive, sort in Python, cards from the same list.
    No pagination or filters in slice 1 (import-and-list.md section 2)."""
    user = request.user
    derived = _user_trades(user)
    sort, direction = _sort_params(request)
    show_account = len({t.account_label for t in derived}) >= 2
    zone = timezone.get_current_timezone_name()
    # ponytail: slice 1 is USD-only (design doc); a non-USD closed trade would otherwise
    # show as (n=0) on every card with no error. Warn instead of failing silently. The trades
    # table also hard-codes "$" via display.money; both go when a second currency ships.
    stats = compute_stats(derived)
    if set(stats) - {"USD"}:
        log.warning("non-USD closed trades present, not reflected in stat cards: %s", set(stats))
    context = {
        "trades": [_trade_row(t) for t in _sorted(derived, sort, direction)],
        "sort": sort,
        "dir": direction,
        "sort_links": _sort_links(sort, direction),
        "show_account": show_account,
        "across_accounts": copy.ACROSS_ALL_ACCOUNTS if show_account else None,
        "zone": zone,
        "times_in": copy.TIMES_IN.format(zone=zone),
        "sort_value": f"{sort}_{direction}",
        "copy": copy,
        "cards": _cards(stats.get("USD")) if derived else None,
        "calc_summary": copy.CALC_SUMMARY,
        "calc_items": [
            copy.CALC_WIN_RATE, copy.CALC_TOTAL_PNL, copy.CALC_AVG_R,
            copy.CALC_TIMES.format(zone=zone),
        ],
        # Kept explicit (not just relying on {% if %} treating a missing key as falsy):
        # test_state_c_only_open_trades_shows_table_and_n0_cards asserts these are None via
        # direct context access, which raises KeyError on a genuinely missing key.
        "empty_heading": None,
        "empty_body": None,
        "empty_action": None,
        "empty_url": None,
    }
    if not derived:
        last = (
            ImportBatch.objects.for_user(user).order_by("-uploaded_at", "-pk")
            .values_list("pk", flat=True).first()
        )
        context.update(
            empty_heading=copy.TRADES_EMPTY_HEADING,
            empty_body=copy.TRADES_EMPTY_B_BODY if last else copy.TRADES_EMPTY_BODY,
            empty_action=copy.TRADES_EMPTY_B_ACTION if last else copy.TRADES_EMPTY_ACTION,
            empty_url=reverse("import_detail", args=[last]) if last else reverse("imports"),
        )
    return render(request, "journal/trade_list.html", context)


def _batch_label(user):
    """A batch's Account label, read from its executions (ImportBatch has no label column,
    design decision H1). NULL means the batch imported nothing, so no label was recorded."""
    return Subquery(
        Execution.objects.for_user(user)
        .filter(raw_import_row__import_batch=OuterRef("pk"))
        .order_by("pk")
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
        "journal_count": n,
        "shown_token": _shown_signer(user.pk, batch.pk).sign(str(n)),  # posted back as `shown`
        "noted_count": m,
        "journal_list": [
            {
                "symbol": e.opening_execution.symbol,
                "opened_at": e.opening_execution.executed_at,
                "rules": _RULES[e.rules_followed],
                "note": e.note,  # full text: the dialog tells users to copy it from here; "" = none
                "url": None,  # no trade page yet (PR D)
            }
            for e in listed
        ],
        "show_all_url": show_all_url,
        "show_all_text": import_copy.DELETE_SHOW_ALL.format(n=n) if show_all_url else None,
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


def _shown_signer(user_id, pk) -> signing.Signer:
    """Trust boundary for the stale-count guard: the count the user saw travels in the form
    as a signed token bound to user and batch. A plain client-posted count has no upper
    bound, so an inflated value would make delete_import_batch's check never fire; a
    session value gets overwritten by any other tab's GET. A replayed old token can only
    be lower than the real count, which fails safe (StaleConfirm)."""
    return signing.Signer(salt=f"import-delete-shown:{user_id}:{pk}")


def _shown_count(user_id, pk, token) -> int:
    try:
        return int(_shown_signer(user_id, pk).unsign(token or ""))
    except (signing.BadSignature, ValueError):
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
        shown = _shown_count(user.pk, pk, request.POST.get("shown"))
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
