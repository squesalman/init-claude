import os
import unicodedata

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from journal import copy
from journal.importers.topstep import FILE_NOT_RECOGNISED
from journal.models import MAX_RAW_FILE_BYTES, UserOwned
from journal.services import FILE_TOO_LARGE


class UserScopedModelForm(forms.ModelForm):
    """
    Base for every ModelForm on a UserOwned model (ADR-0006 Decision 2). Build it as
    `Form(..., user=request.user)`. `user` must never be in Meta.fields: it is set here.
    Every FK dropdown is narrowed to the user's rows (as for_user() does, on top of any
    existing filter), and any User dropdown to the current user, so another tenant's id
    is an ordinary "invalid choice" instead of a cross-tenant write.
    """

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.user_id is None:
            self.instance.user = user
        elif self.instance.user_id != user.pk:
            raise PermissionDenied  # instance was not fetched via for_user()
        for field in self.fields.values():
            qs = getattr(field, "queryset", None)
            if qs is not None and issubclass(qs.model, UserOwned):
                field.queryset = qs.filter(user=user)  # = for_user(), keeps any narrower filter
            elif qs is not None and issubclass(qs.model, get_user_model()):
                field.queryset = qs.filter(pk=user.pk)


def _has_control_characters(text: str) -> bool:
    return any(unicodedata.category(c) in ("Cc", "Cf") for c in text)


def _reject_control_characters(value: str) -> None:
    # Runs after CharField's trim, so only inner tabs, line breaks etc. are left to catch.
    if _has_control_characters(value):
        raise ValidationError(copy.ACCOUNT_CONTROL_CHARACTERS)


class UploadForm(forms.Form):
    """Topstep upload (design import-account-label 3.1). A plain Form with no
    ModelChoiceField, so it is exempt from UserScopedModelForm (ADR-0006 rule 1)."""

    broker = forms.ChoiceField(label=copy.BROKER_LABEL, choices=[("topstep", "Topstep")])
    file = forms.FileField(
        label=copy.FILE_LABEL,
        error_messages={
            "required": copy.FILE_MISSING,
            "invalid": copy.FILE_MISSING,
            "missing": copy.FILE_MISSING,
            "empty": FILE_NOT_RECOGNISED,
        },
    )
    account = forms.CharField(  # trimmed by CharField, case kept (ADR-0004 section 1)
        label=copy.ACCOUNT_LABEL,
        help_text=copy.ACCOUNT_HELP,
        required=False,
        max_length=64,
        error_messages={"max_length": copy.ACCOUNT_TOO_LONG},
        validators=[_reject_control_characters],
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > MAX_RAW_FILE_BYTES:  # before anything calls .read()
            raise ValidationError(FILE_TOO_LARGE)
        return upload


def safe_filename(name: str) -> str:
    """Display-only name: basename (either slash), no control/format characters, <= 255.
    Never used as a storage path."""
    base = os.path.basename(name.replace("\\", "/"))
    return "".join(c for c in base if not _has_control_characters(c))[:255]
