from zoneinfo import available_timezones

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts import copy
from accounts.models import validate_timezone

# The sorted list the signup template renders as the <datalist> of valid names.
TIMEZONE_NAMES = sorted(available_timezones())


class SignupForm(forms.Form):
    # Plain Form, not a ModelForm: User is not UserOwned, and creation goes through
    # create_user() so the password is hashed and the model is full_clean()ed.
    email = forms.EmailField(
        label="Email",
        max_length=254,
        error_messages={"required": copy.SIGNUP_EMAIL_MISSING, "invalid": copy.SIGNUP_EMAIL_MALFORMED},
        widget=forms.EmailInput(
            attrs={"autocomplete": "email", "autocapitalize": "none", "spellcheck": "false"}
        ),
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        help_text="At least 8 characters.",
        error_messages={"required": copy.SIGNUP_PASSWORD_MISSING},
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        error_messages={"required": copy.SIGNUP_CONFIRM_MISSING},
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    timezone = forms.CharField(
        label="Time zone",
        max_length=64,
        initial="UTC",
        error_messages={"required": copy.SIGNUP_TIMEZONE_INVALID},
        widget=forms.TextInput(attrs={"list": "timezones"}),
    )

    def clean_timezone(self):
        value = self.cleaned_data["timezone"]
        try:
            validate_timezone(value)
        except ValidationError:
            raise ValidationError(copy.SIGNUP_TIMEZONE_INVALID) from None
        return value

    def clean(self):
        cleaned = super().clean()
        password1, password2 = cleaned.get("password1"), cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", copy.SIGNUP_CONFIRM_MISMATCH)
        if password1:
            try:
                User = get_user_model()
                validate_password(password1, user=User(email=cleaned.get("email", "")))
            except ValidationError as exc:
                for err in exc.error_list:
                    self.add_error("password1", copy.PASSWORD_VALIDATOR_COPY.get(err.code, err.message))
        return cleaned

    def save(self):
        """Raises ValidationError or IntegrityError if the email is already registered."""
        data = self.cleaned_data
        with transaction.atomic():
            return get_user_model().objects.create_user(
                email=data["email"], password=data["password1"], timezone=data["timezone"]
            )


class LoginForm(forms.Form):
    # CharField, not EmailField: a malformed address is just a failed login, one message.
    email = forms.CharField(label="Email", error_messages={"required": copy.LOGIN_EMAIL_MISSING})
    password = forms.CharField(
        label="Password", strip=False, error_messages={"required": copy.LOGIN_PASSWORD_MISSING},
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
