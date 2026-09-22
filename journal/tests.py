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

        assert list(model.objects.for_user(user_a)) == [row], model.__name__
        assert list(model.objects.for_user(user_b)) == [], model.__name__
