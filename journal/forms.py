import os
import re
import unicodedata
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from journal import copy, display
from journal.importers.topstep import FILE_NOT_RECOGNISED
from journal.models import MAX_RAW_FILE_BYTES, JournalEntry, UserOwned
from journal.services import FILE_TOO_LARGE
from journal.stats import RISK_NOT_POSITIVE, STOP_NOT_A_RISK, stop_on_loss_side


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


FALLBACK_FILENAME = "import.csv"


def safe_filename(name: str) -> str:
    """Display-only name: basename (either slash), no control/format characters, <= 255.
    Never used as a storage path. Blank after cleaning -> FALLBACK_FILENAME (no empty link)."""
    base = os.path.basename(name.replace("\\", "/"))
    clean = "".join(c for c in base if not _has_control_characters(c))[:255]
    return clean if clean.strip() else FALLBACK_FILENAME


# --- Journaling (ADR-0007 section 5) ------------------------------------------------------------

TEXT_LIMIT = 10_000  # note and rules, in code points after \r\n -> \n and trimming (spec)


class NormalizedTextField(forms.CharField):
    """\r\n -> \n before CharField trims and MaxLengthValidator counts, so the browser counter
    and the server agree. No maxlength attribute: a native limit silently cuts pasted text
    (design 3.4). Django's NUL-character validator stays on."""

    def __init__(self, **kwargs):
        super().__init__(max_length=TEXT_LIMIT, required=False, widget=forms.Textarea, **kwargs)

    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace("\r\n", "\n")
        return super().to_python(value)

    def widget_attrs(self, widget):
        attrs = super().widget_attrs(widget)
        attrs.pop("maxlength", None)
        return attrs


# Plain digits, optional period, optional leading minus (ruling 1); commas only as thousands
# separators. ASCII only: \d would also match other scripts' digits.
# Linear: \d+ and \d* must not sit either side of an optional dot (quadratic on a non-match).
_PLAIN_NUMBER = re.compile(r"-?(?:\d+(?:\.\d*)?|\.\d+)", re.ASCII)
_MAX_NUMBER_CHARS = 40  # longest valid value: 20 digits, sign, point, thousands commas
_GROUPED_NUMBER = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d*)?", re.ASCII)
_NUMBER_INPUT = {
    "inputmode": "decimal", "autocomplete": "off", "autocapitalize": "none", "spellcheck": "false",
}


class PlainDecimalField(forms.DecimalField):
    """Design 3.5 parsing, then DecimalField's own checks: DecimalValidator enforces the
    column's max_digits/decimal_places and rejects (never rounds) anything that doesn't fit,
    so a value can't reach NUMERIC and fail there. Text input, not type=number."""

    def __init__(self, *, max_digits, decimal_places, not_a_number, too_many_decimals, **kwargs):
        super().__init__(
            max_digits=max_digits,
            decimal_places=decimal_places,
            required=False,
            widget=forms.TextInput(attrs=_NUMBER_INPUT),
            error_messages={
                "invalid": not_a_number,
                "max_decimal_places": too_many_decimals,
                "max_digits": copy.NUMBER_TOO_LARGE,
                "max_whole_digits": copy.NUMBER_TOO_LARGE,
            },
            **kwargs,
        )

    def to_python(self, value):
        text = "" if value in self.empty_values else str(value).strip()
        if not text:
            return None
        if len(text) > _MAX_NUMBER_CHARS:  # no number regex on megabytes (ReDoS)
            digits_only = re.fullmatch(r"[-.,]*\d[-\d.,]*", text, re.ASCII)
            code = "max_digits" if digits_only else "invalid"
            raise ValidationError(self.error_messages[code], code=code)
        if not (_PLAIN_NUMBER.fullmatch(text) or _GROUPED_NUMBER.fullmatch(text)):
            raise ValidationError(self.error_messages["invalid"], code="invalid")
        return Decimal(text.replace(",", ""))

    def prepare_value(self, value):
        # A saved NUMERIC comes back at column scale (49.5000000000): prefill without the
        # zeros. Same value, so saving the untouched form stores the same number.
        return f"{value.normalize():f}" if isinstance(value, Decimal) else value


class YesNoField(forms.TypedChoiceField):
    """Two radios, no default; nothing posted cleans to None (unanswered)."""

    def __init__(self, **kwargs):
        super().__init__(
            choices=[("true", copy.FOLLOWED_YES), ("false", copy.FOLLOWED_NO)],
            coerce=lambda v: v == "true",
            empty_value=None,
            required=False,
            widget=forms.RadioSelect,
            **kwargs,
        )

    def prepare_value(self, value):
        return {True: "true", False: "false"}.get(value, value)  # prefill from the saved bool


class JournalEntryForm(UserScopedModelForm):
    """Build as JournalEntryForm(data, instance=entry_or_new, user=request.user, trade=trade).
    No user, opening_execution or risk_currency field, so a posted value is never read (AC
    13); the save service derives the currency (domain section 3, vector 21)."""

    rules_followed = YesNoField(label=copy.FOLLOWED_LEGEND, help_text=copy.FOLLOWED_HELP)
    note = NormalizedTextField(
        label=copy.NOTE_LABEL, help_text=copy.NOTE_HELP,
        error_messages={"max_length": copy.NOTE_TOO_LONG},
    )
    stop_price = PlainDecimalField(  # NUMERIC(20,10)
        max_digits=20, decimal_places=10, label=copy.STOP_LABEL,
        not_a_number=copy.STOP_NOT_NUMBER, too_many_decimals=copy.STOP_DECIMALS,
    )
    planned_risk_amount = PlainDecimalField(  # NUMERIC(19,4)
        max_digits=19, decimal_places=4, label=copy.RISK_LABEL,
        not_a_number=copy.RISK_NOT_NUMBER, too_many_decimals=copy.RISK_DECIMALS,
    )

    class Meta:
        model = JournalEntry
        fields = ["rules_followed", "note", "stop_price", "planned_risk_amount"]

    def __init__(self, *args, trade, **kwargs):
        super().__init__(*args, **kwargs)
        self.trade = trade
        # Per-trade help lives on the fields, so every BoundField (GET, and validation on a
        # POST, which builds them all before the view runs) carries it, and the rendered
        # input's aria-describedby points at it (design 3.5, 8).
        side_help = copy.STOP_HELP_LONG if trade.direction == "long" else copy.STOP_HELP_SHORT
        self.fields["stop_price"].help_text = (
            copy.STOP_HELP_MULTI_LEG
            if trade.entry_lot_count > 1
            else side_help.format(entry=display.price(trade.avg_entry_price))
        )
        self.fields["planned_risk_amount"].help_text = copy.RISK_HELP.format(
            currency=trade.currency
        )

    def clean_planned_risk_amount(self):
        value = self.cleaned_data["planned_risk_amount"]
        if value is not None and value <= 0:
            raise ValidationError(copy.RISK_NOT_POSITIVE, code=RISK_NOT_POSITIVE)
        return value

    def clean(self):
        """The stop check runs only for a single-entry trade with planned risk blank on this
        submission (domain section 3; vectors 9, 19, 20): the same predicate R uses."""
        cleaned = super().clean()
        stop = cleaned.get("stop_price")
        if (
            stop is not None
            and self.trade.entry_lot_count == 1
            and cleaned.get("planned_risk_amount") is None
            and not self.has_error("planned_risk_amount")
            and not stop_on_loss_side(self.trade, stop)
        ):
            wrong_side = ValidationError(copy.STOP_WRONG_SIDE, code=STOP_NOT_A_RISK)
            self.add_error("stop_price", wrong_side)
        return cleaned


class RulesForm(forms.Form):
    """A plain Form on the User row, not UserOwned (ADR-0006 rule 1 does not apply). It never
    takes a user id: the view writes request.user only."""

    trading_rules = NormalizedTextField(
        label=copy.RULES_LABEL, help_text=copy.RULES_HELP,
        error_messages={"max_length": copy.RULES_TOO_LONG},
    )
