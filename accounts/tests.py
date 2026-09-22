"""
Regression tests for accounts/models.py's custom User model. Added per code review:
the case-insensitive login fix (UserManager.get_by_natural_key using __iexact) had
previously been verified only by a throwaway script run manually and then deleted —
no persisted test caught a regression. Going forward, verification scripts prove a fix
once; tests here are what keeps it fixed.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

User = get_user_model()


@pytest.mark.django_db
def test_get_by_natural_key_is_case_insensitive():
    """
    Email uniqueness is enforced case-insensitively (UniqueConstraint(Lower('email'))),
    so login lookup must be too — otherwise a user registered as Foo@Example.com can't
    log in typing foo@example.com. Covers the exact scenario from the code review.

    Note: UserManager.create_user() runs normalize_email(), which lowercases the domain
    but preserves the local part's case — so "Foo@Example.com" is actually stored as
    "Foo@example.com". That's standard Django behavior, not this fix; asserted explicitly
    below so this test doesn't silently rely on an assumption about it.
    """
    created = User.objects.create_user(email="Foo@Example.com", password="x")
    assert created.email == "Foo@example.com"

    assert User.objects.get_by_natural_key("foo@example.com").pk == created.pk
    assert User.objects.get_by_natural_key("FOO@EXAMPLE.COM").pk == created.pk
    assert User.objects.get_by_natural_key("Foo@example.com").pk == created.pk


@pytest.mark.django_db
def test_create_user_rejects_invalid_timezone():
    """
    Code review, confirmed live: create_user()/create_superuser() (the only signup
    path) never called full_clean(), so validate_timezone on the `timezone` field never
    ran — create_user(timezone="Not/A_Real_Zone") saved without error. Fixed by calling
    full_clean() in _create_user() before save().
    """
    with pytest.raises(ValidationError):
        User.objects.create_user(
            email="badtz@example.com", password="x", timezone="Not/A_Real_Zone"
        )

    assert not User.objects.filter(email="badtz@example.com").exists()


@pytest.mark.django_db
def test_get_by_natural_key_does_not_match_a_different_email():
    User.objects.create_user(email="foo@example.com", password="x")

    with pytest.raises(User.DoesNotExist):
        User.objects.get_by_natural_key("bar@example.com")


@pytest.mark.django_db
def test_email_uniqueness_is_case_insensitive_at_db_level():
    """
    The DB constraint (accounts_user_email_lower_uniq, a UNIQUE index on lower(email))
    is what get_by_natural_key's lookup relies on to ever return at most one row. Prove
    the constraint itself rejects a case-variant duplicate, not just that create_user()
    happens to catch it — so this bypasses full_clean() (create_user() now calls it,
    which catches the same violation earlier as ValidationError; see the test below) and
    saves directly, the way any write path that skips full_clean() would.
    """
    User.objects.create_user(email="Foo@Example.com", password="x")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            duplicate = User(email="foo@example.com")
            duplicate.set_password("y")
            duplicate.save()  # deliberately not full_clean() — see docstring


@pytest.mark.django_db
def test_create_user_rejects_case_variant_duplicate_email_via_full_clean():
    """
    Companion to the DB-level test above: create_user()'s normal path (which now calls
    full_clean(), per the code-review fix below) catches the same violation earlier, as
    ValidationError, before ever reaching the DB.
    """
    User.objects.create_user(email="Bar@Example.com", password="x")

    with pytest.raises(ValidationError):
        User.objects.create_user(email="bar@example.com", password="y")
