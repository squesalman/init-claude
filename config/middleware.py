from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.http import HttpResponse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers

from journal.models import MAX_RAW_FILE_BYTES

# The largest file we accept plus room for the other form fields and multipart framing.
# Prod still needs Caddy `request_body max_size` (about 11 MB) once compose.prod.yaml exists
# (follow-ups row 24), so an oversized body is refused before it reaches Django at all.
MAX_REQUEST_BYTES = MAX_RAW_FILE_BYTES + 1024 * 1024


class RequestBodyLimitMiddleware:
    """First in MIDDLEWARE: refuse an oversized body before anything parses request.POST
    (CsrfViewMiddleware does). Plain responses; nothing from the request is echoed."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        length = request.META.get("CONTENT_LENGTH", "")
        if length.isdigit():
            if int(length) > MAX_REQUEST_BYTES:
                return HttpResponse("Request too large.", status=413, content_type="text/plain")
        elif request.method == "POST" and request.content_type == "multipart/form-data":
            return HttpResponse("Length required.", status=411, content_type="text/plain")
        return self.get_response(request)


class NoStoreWhenAuthenticatedMiddleware:
    """`Cache-Control: private, no-store` on every response to a logged-in user, so no view
    has to remember never_cache (follow-ups row 19). Anonymous responses are untouched."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            add_never_cache_headers(response)
        return response


class UserTimezoneMiddleware:
    """Show times in the logged-in user's zone (CLAUDE.md: stored UTC, displayed local).
    Anonymous requests get the default (UTC). Must run after AuthenticationMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        try:
            if user is not None and user.is_authenticated:
                timezone.activate(ZoneInfo(user.timezone))
            else:
                timezone.deactivate()
        # A bad stored name must not 500. OSError: a directory name such as "America".
        except (ZoneInfoNotFoundError, ValueError, OSError):
            timezone.deactivate()
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()  # never leak this user's zone into the thread's next request
