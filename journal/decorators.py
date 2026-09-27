from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse


def is_htmx(request) -> bool:
    """An htmx fragment request. A history-restore miss wants the full page, so it is not."""
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )


def htmx_login_required(view):
    """login_required, except a logged-out htmx request gets 401 + HX-Redirect to the login
    page, so an expired session never swaps the login page into a fragment target."""
    guarded = login_required(view)

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated and is_htmx(request):
            response = HttpResponse(status=401)
            response["HX-Redirect"] = redirect_to_login(request.get_full_path()).url
            return response
        return guarded(request, *args, **kwargs)

    return wrapper
