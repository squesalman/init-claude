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


@pytest.mark.django_db
def test_get_or_create_and_update_or_create_are_not_supported():
    """
    Round-7 added a hand-rolled get_or_create()/update_or_create() to close a
    full_clean()-bypass gap; round 8 found the hand-rolling itself was buggy:
    update_or_create()'s update path did setattr() straight onto the instance, so
    defaults={"password": "..."} would have written a PLAINTEXT password; both methods
    did a case-sensitive self.get(**kwargs) lookup while uniqueness is enforced
    case-insensitively, so an existing "Foo@Example.com" wasn't found by
    get_or_create(email="foo@example.com") and fell through to create, hitting an
    uncaught IntegrityError; and neither had Django's own transaction/retry-on-race or
    lookup-suffix stripping. Root cause: nothing in this codebase calls either method —
    removed entirely rather than hand-rolled correctly for a caller that doesn't exist
    yet. Both now refuse to run.
    """
    with pytest.raises(NotImplementedError):
        User.objects.get_or_create(email="x@example.com", defaults={"password": "x"})

    with pytest.raises(NotImplementedError):
        User.objects.update_or_create(email="x@example.com", defaults={"password": "x"})

    assert not User.objects.filter(email="x@example.com").exists()


@pytest.mark.django_db
def test_get_by_natural_key_case_folding_is_db_side_not_python():
    """
    Round-7 code review: get_by_natural_key lowercased the login input with Python's
    str.lower(), while the DB unique index and the query-side annotation both use
    Postgres's lower(). These disagree for non-ASCII characters under a C/POSIX
    collation (the default for the postgres:16-alpine image compose.yaml uses).
    Confirmed live: Postgres's lower('İstanbul@example.com') gives
    'istanbul@example.com' (20 chars, plain ascii i), while Python's
    "İstanbul@example.com".lower() gives 'i̇stanbul@example.com' (21 chars — İ, U+0130,
    folds to 'i' + a combining dot above under Python's full Unicode case folding). Those
    don't match character-for-character, so under the old code a user could fail to log
    in with the *exact* email they registered with. Fixed by lowering the login input in
    SQL too (Lower(Value(email))), so both sides go through the same (Postgres) rules.
    """
    email = "İstanbul@example.com"
    user = User(email=email)
    user.set_password("x")
    user.save()  # bypass full_clean(): EmailValidator's local-part regex is ASCII-only,
    # unrelated to the lowering-consistency bug this test targets

    found = User.objects.get_by_natural_key(email)
    assert found.pk == user.pk


@pytest.mark.django_db
@pytest.mark.parametrize("bad", ["usd", "us"])
def test_base_currency_must_be_three_uppercase_letters(bad):
    """Round-9: validator (full_clean path via create_user) plus DB CHECK backstop."""
    from django.core.exceptions import ValidationError
    from django.db import IntegrityError, transaction

    with pytest.raises(ValidationError):
        User.objects.create_user(email="cur1@example.com", password="x", base_currency=bad)

    user = User.objects.create_user(email="cur2@example.com", password="x")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.filter(pk=user.pk).update(base_currency=bad)
