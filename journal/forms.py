import os
import re
import unicodedata
from datetime import UTC, timedelta
from decimal import Decimal, localcontext

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.forms.utils import from_current_timezone
from django.utils import timezone

from journal import copy, display
from journal.importers.topstep import FILE_NOT_RECOGNISED, MULTIPLIERS, contract_root
from journal.models import MAX_RAW_FILE_BYTES, Execution, JournalEntry, UserOwned
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


class NoControlCharField(forms.CharField):
    """Trimmed, then control/format characters (NUL, zero-width, bidi, inner tab) rejected in
    to_python, before any validator, so this is the field's one message: no max-length or
    Django "Null characters" message alongside it (manual-entry design 12)."""

    def __init__(self, *, control_message, **kwargs):
        super().__init__(**kwargs)
        self.control_message = control_message

    def to_python(self, value):
        value = super().to_python(value)
        if _has_control_characters(value):
            raise ValidationError(self.control_message, code="control_characters")
        return value


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
            digits_only = re.fullmatch(r"[-.,]*\d[-\d.,]*", text[: _MAX_NUMBER_CHARS + 1], re.ASCII)
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


# --- Manual trade entry (ADR-0008 sections 5-7) -----------------------------------------------

MAX_SYMBOL = 32  # Execution.symbol VARCHAR(32)
FUTURE_GRACE = timedelta(minutes=5)  # a browser clock running ahead (ADR-0008 section 6)
_TIME_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]  # minute precision, no offset
_MAX_TIME_CHARS = 32  # a valid value is 16; no strptime on megabytes
_MIN_YEAR = 1970  # security review M2a: no year-1 instant reaches the database
_REQUIRED = {"aria-required": "true"}


def normalize_symbol(text) -> str:
    """Trimmed and uppercased (AC 11). Length is checked after this: "ß".upper() is "SS"."""
    return text.strip().upper() if isinstance(text, str) else ""


def point_value_state(symbol: str) -> tuple[str, str | None, Decimal | None]:
    """Design 5.1: ("pending" | "known" | "unknown", root, looked-up value). A blank, too-long
    or control-character symbol is pending: the symbol's own error says what to fix."""
    if not symbol or len(symbol) > MAX_SYMBOL or _has_control_characters(symbol):
        return "pending", None, None
    root = contract_root(symbol)
    if root in MULTIPLIERS:
        return "known", root, MULTIPLIERS[root]
    return "unknown", root, None


class LocalTimeField(forms.DateTimeField):
    """A minute typed in the active zone, cleaned to UTC (domain section 7.2). The zone is
    User.timezone, activated by UserTimezoneMiddleware, so the help text and the parse use
    the same zone. Only the two input formats: no seconds and no ISO offset, so a typed time
    can't bypass the zone. Order: required, parse, DST gap or overlap, then not in the future."""

    def __init__(self, *, required_message=None, **kwargs):
        super().__init__(
            input_formats=_TIME_FORMATS,
            required=required_message is not None,
            widget=forms.DateTimeInput(
                format=_TIME_FORMATS[0],
                attrs={"type": "datetime-local", "autocomplete": "off",
                       "placeholder": "YYYY-MM-DD HH:MM"},
            ),
            error_messages={"required": required_message, "invalid": copy.TIME_INVALID},
            **kwargs,
        )

    def to_python(self, value):
        if value in self.empty_values:
            return None
        text = str(value).strip()
        if len(text) > _MAX_TIME_CHARS:
            raise ValidationError(self.error_messages["invalid"], code="invalid")
        naive = forms.fields.BaseTemporalField.to_python(self, text)  # input_formats only
        if naive.year < _MIN_YEAR:  # a stored time near year 1 overflows in another zone
            raise ValidationError(copy.TIME_TOO_OLD, code="too_old")
        zone = timezone.get_current_timezone_name()
        try:
            local = from_current_timezone(naive)  # raises on a nonexistent or ambiguous time
        except ValidationError:
            raise ValidationError(
                copy.TIME_NOT_READABLE.format(zone=zone), code="not_readable"
            ) from None
        try:
            return local.astimezone(UTC)
        except OverflowError:  # year 9999 shifted past UTC's range: a future time (design 12)
            raise ValidationError(copy.TIME_FUTURE.format(zone=zone), code="future") from None

    def validate(self, value):
        super().validate(value)
        if value is not None and value > timezone.now() + FUTURE_GRACE:
            zone = timezone.get_current_timezone_name()
            raise ValidationError(copy.TIME_FUTURE.format(zone=zone), code="future")


def _price_field(label, help_text=""):
    return PlainDecimalField(  # NUMERIC(20,10)
        max_digits=20, decimal_places=10, label=label, help_text=help_text,
        not_a_number=copy.PRICE_NOT_NUMBER, too_many_decimals=copy.PRICE_DECIMALS,
    )


def _fees_field(help_text):
    return PlainDecimalField(  # NUMERIC(19,4)
        max_digits=19, decimal_places=4, label=copy.FEES_LABEL, help_text=help_text,
        not_a_number=copy.FEES_NOT_NUMBER, too_many_decimals=copy.FEES_DECIMALS,
    )


def _present(value, message):
    if value is None:  # PlainDecimalField is never required itself
        raise ValidationError(message, code="required")
    return value


def _non_zero(value):
    if value is not None and value == 0:  # execution_price_not_zero; negative is real (AC 9)
        raise ValidationError(copy.PRICE_ZERO, code="zero")
    return value


def _fees(value):
    if value is not None and value < 0:  # user ruling: fees are 0 or more (AC 28)
        raise ValidationError(copy.FEES_NEGATIVE, code="negative")
    return value


MAX_TRADE_VALUE = Decimal(10) ** 15  # NUMERIC(19,4), the money column range


def _check_trade_value(form, name, price, quantity, point_value):
    """abs(price) x quantity x point value stays inside the money range, so no read can
    overflow (security review M2a). Each operand fits NUMERIC(20,10): 60 digits is exact."""
    if None in (price, quantity, point_value):
        return
    with localcontext(prec=60):
        too_large = abs(price) * quantity * point_value >= MAX_TRADE_VALUE
    if too_large:
        form.add_error(name, ValidationError(copy.TRADE_VALUE_TOO_LARGE, code="too_large"))


def _exit_before_entry(entry_at) -> ValidationError:
    return ValidationError(
        copy.EXIT_BEFORE_ENTRY.format(entry=display.opened_with_year(entry_at)),
        code="exit_before_entry",
    )


class _ServiceWrittenForm(UserScopedModelForm):
    """Meta.fields = [] with every input declared: the form validates, the service writes
    (ADR-0008 section 5), so form.save() is never called. Every Execution constraint has a
    field-level check in the subclass, because nothing here runs the model's own checks."""

    class Meta:
        model = Execution
        fields = []

    def _post_clean(self):
        # No model validation. With Meta.fields = [] Django excludes nothing, so model-checking
        # the blank instance would report "cannot be blank" for every input that shares a
        # column's name (symbol, side, quantity, fees) on a valid submit.
        pass

    def _filled(self, name) -> bool:
        return bool(str(self.data.get(self.add_prefix(name)) or "").strip())

    def _set_time_help(self, *names):
        zone = timezone.get_current_timezone_name()
        for name in names:
            self.fields[name].help_text = copy.TIME_HELP.format(zone=zone)


class ManualTradeForm(_ServiceWrittenForm):
    """The add form (edit modes arrive with PR M2b). Build as ManualTradeForm(data,
    user=request.user). cleaned_data is exactly add_manual_trade()'s keywords. No user,
    source, broker, currency, key or execution id field, so a posted value is never read
    (AC 12, 50)."""

    symbol = NoControlCharField(  # zero-width/bidi would look right but miss its root
        label=copy.SYMBOL_LABEL, help_text=copy.SYMBOL_HELP,
        error_messages={"required": copy.SYMBOL_REQUIRED},
        control_message=copy.SYMBOL_CONTROL_CHARACTERS,
        widget=forms.TextInput(attrs={
            "autocomplete": "off", "autocapitalize": "characters", "spellcheck": "false",
            **_REQUIRED,
        }),
    )
    point_value = PlainDecimalField(  # NUMERIC(20,10); only for an unknown root
        max_digits=20, decimal_places=10, label=copy.POINT_VALUE_LABEL,
        help_text=copy.POINT_VALUE_HELP, not_a_number=copy.POINT_VALUE_NOT_NUMBER,
        too_many_decimals=copy.POINT_VALUE_DECIMALS,
    )
    side = forms.ChoiceField(
        label=copy.SIDE_LEGEND, help_text=copy.SIDE_HELP, widget=forms.RadioSelect,
        choices=[("long", copy.SIDE_LONG), ("short", copy.SIDE_SHORT)],  # no default (AC 4)
        error_messages={"required": copy.SIDE_REQUIRED, "invalid_choice": copy.SIDE_REQUIRED},
    )
    quantity = PlainDecimalField(  # NUMERIC(20,10)
        max_digits=20, decimal_places=10, label=copy.QUANTITY_LABEL,
        help_text=copy.QUANTITY_HELP, not_a_number=copy.QUANTITY_NOT_NUMBER,
        too_many_decimals=copy.QUANTITY_DECIMALS,
    )
    entry_price = _price_field(copy.ENTRY_PRICE_LABEL, copy.PRICE_HELP)
    entry_time = LocalTimeField(
        label=copy.ENTRY_TIME_LABEL, required_message=copy.ENTRY_TIME_REQUIRED
    )
    exit_price = _price_field(copy.EXIT_PRICE_LABEL)
    exit_time = LocalTimeField(label=copy.EXIT_TIME_LABEL)
    fees = _fees_field(copy.FEES_HELP_ADD)
    account = NoControlCharField(  # the import form's rules: trimmed, 64, no control characters
        label=copy.ACCOUNT_LABEL, help_text=copy.MANUAL_ACCOUNT_HELP, required=False,
        max_length=64, error_messages={"max_length": copy.ACCOUNT_TOO_LONG},
        control_message=copy.ACCOUNT_CONTROL_CHARACTERS,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Everything per request is set here, before any BoundField exists (J2 lesson).
        self._set_time_help("entry_time", "exit_time")
        for name in ("quantity", "entry_price", "entry_time"):
            self.fields[name].widget.attrs.update(_REQUIRED)
        self._symbol_id = self.auto_id % self.add_prefix("symbol")
        self.symbol_hint_id = f"{self._symbol_id}_currency_hint"
        self.fields["symbol"].widget.attrs["aria-describedby"] = (
            f"{self._symbol_id}_helptext {self.symbol_hint_id}"
        )

        key = self.add_prefix("symbol")
        raw = self.data.get(key) if self.is_bound else self.initial.get("symbol")
        self.pv_symbol = normalize_symbol(raw)
        self.pv_state, self.pv_root, self.pv_lookup = point_value_state(self.pv_symbol)
        if self.pv_state == "unknown":
            self.fields["point_value"].widget.attrs.update(_REQUIRED)
        if self.is_bound:
            self.data = self.data.copy()
            self.data[key] = self.pv_symbol  # the re-render shows the normalized symbol
            typed_for = self.data.get(self.add_prefix("pv_for"))
            if self.pv_state != "unknown" or typed_for != self.pv_symbol:
                # Known root: the lookup wins (AC 16). Unknown root: a value typed for another
                # symbol, or before the input was shown, is dropped, never saved (design 5.3).
                self.data[self.add_prefix("point_value")] = ""
        else:
            self.fields["symbol"].widget.attrs["autofocus"] = True

    def full_clean(self):
        super().full_clean()
        # A widget aria-describedby turns Django's own off, so the error id is added here.
        error = f" {self._symbol_id}_error" if self.has_error("symbol") else ""
        self.fields["symbol"].widget.attrs["aria-describedby"] = (
            f"{self._symbol_id}_helptext {self.symbol_hint_id}{error}"
        )

    def clean_symbol(self):
        symbol = normalize_symbol(self.cleaned_data["symbol"])
        if len(symbol) > MAX_SYMBOL:
            raise ValidationError(copy.SYMBOL_TOO_LONG, code="too_long")
        return symbol

    def clean_point_value(self):
        value = self.cleaned_data["point_value"]
        if value is not None and value <= 0:  # execution_contract_multiplier_positive
            raise ValidationError(copy.POINT_VALUE_NOT_POSITIVE, code="not_positive")
        return value

    def clean_quantity(self):
        value = _present(self.cleaned_data["quantity"], copy.QUANTITY_REQUIRED)
        if value <= 0:  # execution_quantity_positive
            raise ValidationError(copy.QUANTITY_NOT_POSITIVE, code="not_positive")
        return value

    def clean_entry_price(self):
        return _non_zero(_present(self.cleaned_data["entry_price"], copy.ENTRY_PRICE_REQUIRED))

    def clean_exit_price(self):
        return _non_zero(self.cleaned_data["exit_price"])

    def clean_fees(self):
        return _fees(self.cleaned_data["fees"])

    def clean(self):
        cleaned = super().clean()
        price, time = self._filled("exit_price"), self._filled("exit_time")
        # Both or neither (AC 7); one message per field, so never over the other's own error.
        if time and not price and not self.has_error("exit_time"):
            self.add_error("exit_price", ValidationError(copy.EXIT_PRICE_NEEDED, code="needed"))
        if price and not time and not self.has_error("exit_price"):
            self.add_error("exit_time", ValidationError(copy.EXIT_TIME_NEEDED, code="needed"))
        if self._filled("fees") and not (price or time) and not self.has_error("fees"):
            # Any non-blank value, 0 too, is never silently dropped (AC 29).
            self.add_error("fees", ValidationError(copy.FEES_NEEDS_EXIT, code="needs_exit"))
        entry_at, exit_at = cleaned.get("entry_time"), cleaned.get("exit_time")
        if entry_at is not None and exit_at is not None and exit_at < entry_at:
            self.add_error("exit_time", _exit_before_entry(entry_at))  # equal is fine (7.1)

        value = cleaned.pop("point_value", None)
        if self.pv_state == "known":
            cleaned["contract_multiplier"] = self.pv_lookup
        elif self.pv_state == "unknown":
            if value is None and not self.has_error("point_value"):
                needed = copy.POINT_VALUE_NEEDED.format(symbol=self.pv_symbol)
                self.add_error("point_value", ValidationError(needed, code="needed"))
            cleaned["contract_multiplier"] = value
        multiplier, quantity = cleaned.get("contract_multiplier"), cleaned.get("quantity")
        for name in ("entry_price", "exit_price"):
            _check_trade_value(self, name, cleaned.get(name), quantity, multiplier)
        if "fees" in cleaned:
            cleaned["fees"] = cleaned["fees"] or Decimal("0")  # blank = 0 (AC 28)
        return cleaned


class CloseTradeForm(_ServiceWrittenForm):
    """Build as CloseTradeForm(data, user=request.user, instance=<entry fill>, trade=trade).
    Exit price and time are required; Fees alone in More details (AC 33). The service copies
    every shared column from the locked entry fill, never from this form (ADR-0008 section 5)."""

    exit_price = _price_field(copy.EXIT_PRICE_LABEL, copy.PRICE_HELP)
    exit_time = LocalTimeField(
        label=copy.EXIT_TIME_LABEL, required_message=copy.EXIT_TIME_REQUIRED
    )
    fees = _fees_field(copy.FEES_HELP)

    def __init__(self, *args, trade, **kwargs):
        super().__init__(*args, **kwargs)
        self.trade = trade
        self._set_time_help("exit_time")
        for name in ("exit_price", "exit_time"):
            self.fields[name].widget.attrs.update(_REQUIRED)

    def clean_exit_price(self):
        return _non_zero(_present(self.cleaned_data["exit_price"], copy.EXIT_PRICE_REQUIRED))

    def clean_fees(self):
        return _fees(self.cleaned_data["fees"]) or Decimal("0")  # blank = 0

    def clean(self):
        cleaned = super().clean()
        exit_at = cleaned.get("exit_time")
        if exit_at is not None and exit_at < self.trade.opened_at:  # AC 35, compared in UTC
            self.add_error("exit_time", _exit_before_entry(self.trade.opened_at))
        _check_trade_value(self, "exit_price", cleaned.get("exit_price"), self.trade.quantity,
                           self.trade.multiplier)
        return cleaned
