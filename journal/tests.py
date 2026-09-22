"""
The mandatory isolation test (ADR-0003, mvp.md story 1): enumerate every UserOwned
model and assert `for_user()` never returns another user's rows. Minimal by design —
qa-engineer expands coverage (views, URLs, ID-guessing) later.

A new UserOwned subclass added later without a factory registered below fails
`test_user_owned_subclasses_have_factories` loudly, instead of silently being skipped
by the isolation test.
"""

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from journal.models import Execution, ImportBatch, JournalEntry, RawImportRow, UserOwned

User = get_user_model()


def _make_import_batch(user):
    return ImportBatch.objects.create(
        user=user, broker="topstep", filename="f.csv", file_sha256="0" * 64, raw_file=b""
    )


def _make_raw_import_row(user):
    batch = _make_import_batch(user)
    return RawImportRow.objects.create(
        user=user,
        import_batch=batch,
        line_number=1,
        raw={"a": "1"},
        status=RawImportRow.STATUS_IMPORTED,
    )


def _make_execution(user):
    return Execution.objects.create(
        user=user,
        broker="manual",
        symbol="AAPL",
        side=Execution.SIDE_BUY,
        quantity="1",
        price="1",
        currency="USD",
        executed_at=timezone.now(),
        source=Execution.SOURCE_MANUAL,
    )


def _make_journal_entry(user):
    execution = _make_execution(user)
    return JournalEntry.objects.create(user=user, opening_execution=execution)


FACTORIES = {
    ImportBatch: _make_import_batch,
    RawImportRow: _make_raw_import_row,
    Execution: _make_execution,
    JournalEntry: _make_journal_entry,
}


@pytest.mark.django_db
def test_user_owned_subclasses_have_factories():
    missing = [m for m in UserOwned.__subclasses__() if m not in FACTORIES]
    assert not missing, f"No isolation-test factory for: {[m.__name__ for m in missing]}"


@pytest.mark.django_db
def test_for_user_never_returns_another_users_rows():
    user_a = User.objects.create_user(email="a@example.com", password="x")
    user_b = User.objects.create_user(email="b@example.com", password="x")

    for model, factory in FACTORIES.items():
        row = factory(user_a)

        # Membership, not exact-list equality: some factories have side effects that
        # create rows of *other* models in FACTORIES (e.g. JournalEntry's factory also
        # creates an Execution). Asserting "this model has exactly one row at this point
        # in the loop" would silently depend on FACTORIES' dict insertion order — it only
        # happened to pass before because Execution's own turn ran before JournalEntry's.
        # Isolation only requires that `row` is visible to its own user and that user_b's
        # view of this model is empty (user_b never appears in any factory call here).
        assert row in model.objects.for_user(user_a), model.__name__
        assert list(model.objects.for_user(user_b)) == [], model.__name__


@pytest.mark.django_db
def test_default_manager_blocks_unscoped_reads():
    """
    Hardened per code review: tenant isolation was opt-in (nothing stopped a call site
    from using the unscoped default manager). `Model.objects.<read>()` must now fail
    loudly instead of silently returning every user's rows — a call site has to opt OUT
    of scoping via `Model.unscoped`, not opt into it via `.for_user()`.
    """
    for model in FACTORIES:
        with pytest.raises(RuntimeError):
            model.objects.all()
        with pytest.raises(RuntimeError):
            model.objects.filter(pk=1)


@pytest.mark.django_db
def test_create_and_unscoped_escape_hatch_still_work():
    """`.create()` isn't a read and can't leak (user is a required, explicit kwarg), so
    it must keep working. `.unscoped` is the deliberate, reviewed escape hatch — Django's
    internals rely on it (Meta.base_manager_name) for cascades like `user.delete()`."""
    user = User.objects.create_user(email="c@example.com", password="x")

    for model, factory in FACTORIES.items():
        row = factory(user)  # exercises Model.objects.create(...) in every factory
        assert row in model.unscoped.all(), model.__name__


@pytest.mark.django_db
def test_related_manager_still_works_on_a_scoped_parent():
    """
    Reverse-FK traversal (e.g. `import_batch.rows.all()`) is safe even though it doesn't
    go through `.for_user()` explicitly — Django builds a RelatedManager scoped to one
    already-fetched parent row, so it can't leak across users. Without
    `Meta.default_manager_name = "unscoped"`, this used to hit UserScopedManager's raise
    even when the parent itself came from a correctly `.for_user()`-scoped query.
    """
    user = User.objects.create_user(email="d@example.com", password="x")
    row = _make_raw_import_row(user)

    batch = ImportBatch.objects.for_user(user).first()
    assert row in batch.rows.all()

    # The chokepoint itself is unaffected: a direct unscoped query still raises.
    with pytest.raises(RuntimeError):
        ImportBatch.objects.all()


@pytest.mark.django_db
def test_user_delete_cascades_all_owned_rows_in_one_call():
    """
    ADR-0003's "account deletion is one statement" guarantee, previously only verified
    by a comment (RESTRICT + base_manager_name/default_manager_name="unscoped" on
    JournalEntry.opening_execution) and a manual script that was deleted after one run.
    One row per UserOwned model, then user.delete() must remove all of them.
    """
    user = User.objects.create_user(email="e@example.com", password="x")
    rows = {model: factory(user) for model, factory in FACTORIES.items()}

    user.delete()

    for model, row in rows.items():
        assert not model.unscoped.filter(pk=row.pk).exists(), model.__name__
    assert not User.objects.filter(pk=user.pk).exists()
