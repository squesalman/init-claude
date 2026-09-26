from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from accounts import copy
from accounts.forms import TIMEZONE_NAMES, LoginForm, SignupForm


def home(request):
    return redirect("trades" if request.user.is_authenticated else "login")


@sensitive_post_parameters("password1", "password2")
def signup(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = form.save()
        except (ValidationError, IntegrityError):
            # Duplicate email (any case), including a lost race on the DB constraint.
            # One generic form-level message, never attached to the Email field.
            form.add_error(None, copy.SIGNUP_EMAIL_UNUSABLE)
        else:
            login(request, user)
            messages.success(request, copy.SIGNUP_WELCOME)
            return redirect(settings.LOGIN_REDIRECT_URL)
    return render(
        request,
        "accounts/signup.html",
        {"form": form, "timezones": TIMEZONE_NAMES, "error_summary_title": copy.SIGNUP_ERROR_SUMMARY_TITLE},
    )


def _safe_next(request, value):
    ok = value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return value if ok else ""


@sensitive_post_parameters("password")
def login_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    next_url = _safe_next(request, request.POST.get("next") or request.GET.get("next"))
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        # ModelBackend runs the hasher for unknown emails and rejects inactive users, so
        # unknown email, wrong password and inactive all land here with the same result.
        user = authenticate(request, username=form.cleaned_data["email"], password=form.cleaned_data["password"])
        if user is None:
            form.add_error(None, copy.LOGIN_BAD_CREDENTIALS)
        else:
            login(request, user)
            return redirect(next_url or settings.LOGIN_REDIRECT_URL)
    return render(
        request,
        "accounts/login.html",
        {
            "form": form,
            "next": next_url,
            "login_required_notice": copy.LOGIN_REQUIRED if next_url else None,
            # Design 4.3: both forms use the one summary title (held under SIGNUP_ in copy.py).
            "error_summary_title": copy.SIGNUP_ERROR_SUMMARY_TITLE,
        },
    )


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, copy.LOGOUT_FLASH)
    return redirect(settings.LOGOUT_REDIRECT_URL)
