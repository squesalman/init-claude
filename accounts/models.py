from zoneinfo import available_timezones

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Value
from django.db.models.functions import Lower

# Computed once at import time, not re-cached per call: the set of IANA timezone names
# doesn't change within a running process (it would need a tzdata upgrade + restart), so
# a plain module-level constant is simpler than functools.lru_cache for a value that
# never changes in-process — lru_cache is for values that vary by argument or need
# invalidating; there's neither here.
_AVAILABLE_TIMEZONES = frozenset(available_timezones())


# ISO 4217 alphabetic code shape: exactly three uppercase ASCII letters. Shared by every
# currency column in the project — each pairs `validate_currency_code` (full_clean path)
# with a DB CheckConstraint on `currency__regex=CURRENCY_CODE_REGEX` (plain .save() path).
CURRENCY_CODE_REGEX = r"^[A-Z]{3}$"
validate_currency_code = RegexValidator(
    CURRENCY_CODE_REGEX, "Currency must be a 3-letter uppercase ISO 4217 code, e.g. USD."
)


def validate_timezone(value: str) -> None:
    if value not in _AVAILABLE_TIMEZONES:
        raise ValidationError(f"{value!r} is not a known IANA timezone name.")


class UserManager(BaseUserManager):
    """Email-based manager: no username field exists on this model."""

    use_in_migrations = True

    def get_by_natural_key(self, email: str):
        # Uniqueness is enforced case-insensitively (UniqueConstraint(Lower("email"))
        # below), so login lookup must be too, or a user registered as "Foo@x.com" can't
        # log in typing "foo@x.com". BaseUserManager's default does an exact match.
        #
        # Not `email__iexact=email`: that compiles to UPPER(email) = UPPER(%s), which
        # doesn't match the UNIQUE index built on lower(email) — a seq scan on every
        # login. Annotating Lower(email) and filtering on the annotation compiles to
        # lower(email) = %s, which the index serves directly.
        #
        # Not `email_lower=email.lower()` either (code review, round 7): that lowers the
        # login input in Python while the DB index and the query annotation both lower
        # via Postgres's lower() — and those two disagree for non-ASCII characters under
        # a C/POSIX collation (the default for the postgres:16-alpine image this
        # project's compose.yaml uses). Confirmed live: lower('İstanbul@example.com') in
        # Postgres gives 'istanbul@example.com' (20 chars, plain ascii i), while Python's
        # "İstanbul@example.com".lower() gives 'i̇stanbul@example.com' (21 chars — İ folds
        # to 'i' + a combining dot above under full Unicode case folding). Those don't
        # match character-for-character, so a user could fail to log in with the *exact*
        # email they registered with. Fixed by lowering the login input in SQL too
        # (`Lower(Value(email))`), so both sides of the comparison go through the same
        # (Postgres) lowering rules instead of mixing Python and SQL.
        return self.annotate(email_lower=Lower(self.model.USERNAME_FIELD)).get(
            email_lower=Lower(Value(email))
        )

    def _create_user(self, email: str, password: str | None, **extra_fields):
        # ponytail: known TOCTOU, DB constraint backstops it; view layer should catch
        # IntegrityError alongside ValidationError when the signup view is built.
        if not email:
            # ValidationError, not ValueError (code review): every other invalid-field
            # case here is caught via full_clean() and raises ValidationError, so a
            # caller only needs to catch one exception type from this method.
            raise ValidationError("Email is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        # Bug (code review): validators declared on fields (e.g. validate_timezone on
        # `timezone`) only run via full_clean(), which nothing on this — the only
        # signup path — was calling. Confirmed live: create_user(timezone="Not/A_Real_Zone")
        # saved without error. full_clean() must run before save(), not instead of it —
        # Django doesn't validate on save() by design.
        user.full_clean()
        user.save(using=self._db)
        return user

    def get_or_create(self, defaults=None, **kwargs):
        # Round-7 added a hand-rolled get_or_create()/update_or_create() here to close a
        # full_clean()-bypass gap, but round 8 found the hand-rolling itself was buggy:
        # update_or_create()'s update path did setattr() straight onto the instance, so
        # defaults={"password": "..."} wrote a PLAINTEXT password (the create path
        # correctly used set_password() via _create_user(), the update path didn't);
        # both methods did a case-sensitive self.get(**kwargs) while uniqueness is
        # case-insensitive (UniqueConstraint(Lower("email"))), so an existing
        # "Foo@Example.com" wasn't found by get_or_create(email="foo@example.com") and
        # fell through to create, hitting an uncaught IntegrityError; and neither method
        # had Django's own transaction wrapping/retry-on-race or lookup-suffix stripping
        # (_extract_model_params). Root cause: nothing in this codebase actually calls
        # either method — this was closing a hypothetical gap, and hand-rolling Django's
        # get_or_create/update_or_create semantics correctly is real surface area for
        # zero current benefit. Removed the override entirely; both now refuse to run.
        # If/when a real caller needs this (e.g. an admin-assisted password reset flow),
        # build it correctly at that point, scoped to the actual call shape needed.
        raise NotImplementedError(
            "User.objects.get_or_create() is not supported — use "
            "User.objects.create_user() for creation. If you need get-or-create "
            "semantics, call User.objects.get_by_natural_key()/objects.get() first and "
            "handle DoesNotExist yourself."
        )

    def update_or_create(self, defaults=None, create_defaults=None, **kwargs):
        raise NotImplementedError(
            "User.objects.update_or_create() is not supported — use "
            "User.objects.create_user() for creation and an explicit "
            "user.set_password()/other field updates + user.full_clean() + user.save() "
            "for updates. (In particular, never setattr(user, 'password', raw_value) — "
            "that writes a plaintext password.)"
        )

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model per ADR-0003: email login, no username, per-user timezone,
    display currency default, and the single free-text "my rules" block (story 5).

    Case-insensitive email uniqueness is enforced with a UNIQUE index on lower(email)
    rather than the CITEXT extension — ADR-0003 names this as an acceptable substitute.
    """

    # No explicit max_length: 254 is EmailField's own built-in default (code review,
    # round 6) — restating it here would just be a number someone has to keep in sync
    # with Django's default for no reason.
    email = models.EmailField()
    timezone = models.CharField(max_length=64, default="UTC", validators=[validate_timezone])
    # VARCHAR(3), not ADR-0003's literal CHAR(3) — see journal/models.py's
    # Execution.currency comment and docs/data/schema.md for why.
    base_currency = models.CharField(
        max_length=3, default="USD", validators=[validate_currency_code]
    )
    trading_rules = models.TextField(blank=True, default="")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_lower_uniq"),
            models.CheckConstraint(
                condition=models.Q(base_currency__regex=CURRENCY_CODE_REGEX),
                name="accounts_user_base_currency_iso_format",
            ),
        ]

    def __str__(self) -> str:
        return self.email
