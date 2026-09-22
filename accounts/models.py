from zoneinfo import available_timezones

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower


def validate_timezone(value: str) -> None:
    if value not in available_timezones():
        raise ValidationError(f"{value!r} is not a known IANA timezone name.")


class UserManager(BaseUserManager):
    """Email-based manager: no username field exists on this model."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

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

    email = models.EmailField(max_length=254)
    timezone = models.CharField(max_length=64, default="UTC", validators=[validate_timezone])
    base_currency = models.CharField(max_length=3, default="USD")
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
        ]

    def __str__(self) -> str:
        return self.email
