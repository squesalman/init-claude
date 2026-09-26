from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from accounts import copy


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
