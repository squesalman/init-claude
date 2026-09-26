from django.utils.cache import add_never_cache_headers


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
