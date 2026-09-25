"""
The mandatory isolation test (ADR-0003, mvp.md story 1): enumerate every UserOwned
model and assert `for_user()` never returns another user's rows. Minimal by design —
qa-engineer expands coverage (views, URLs, ID-guessing) later.

A new UserOwned subclass added later without a factory registered below fails
`test_user_owned_subclasses_have_factories` loudly, instead of silently being skipped
by the isolation test.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from journal.models import (
    CrossTenantForeignKeyError,
    Execution,
    ImportBatch,
    JournalEntry,
    RawImportRow,
    UserOwned,
)

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
def test_cross_tenant_fk_is_rejected():
    """
    Code review, confirmed live: nothing checked that a UserOwned row's FK to another
    UserOwned row belongs to the same user. Before this fix,
    JournalEntry.objects.create(user=user_b, opening_execution=<user_a's execution>)
    succeeded, and for_user(user_b) then surfaced user_a's execution data through
    entry.opening_execution — a cross-tenant leak through a relation, not a direct
    query. Fixed once, generically, on UserOwned.save() (journal/models.py) rather than
    per model, since the same shape of bug applies to every cross-FK in the schema
    (RawImportRow -> ImportBatch, Execution -> RawImportRow, JournalEntry ->
    opening_execution). This test covers JournalEntry -> Execution, the case found live;
    the mechanism is generic and applies uniformly to the others.
    """
    user_a = User.objects.create_user(email="f@example.com", password="x")
    user_b = User.objects.create_user(email="g@example.com", password="x")
    execution_a = _make_execution(user_a)

    with pytest.raises(CrossTenantForeignKeyError):
        JournalEntry.objects.create(user=user_b, opening_execution=execution_a)

    # Confirms the leak path specifically: no JournalEntry row was created at all, so
    # there's nothing left for for_user(user_b) to surface user_a's execution through.
    assert not JournalEntry.unscoped.filter(opening_execution=execution_a).exists()


@pytest.mark.django_db
def test_save_update_fields_skips_fk_check_unless_a_guarded_field_is_touched():
    """
    Perf fix, code review: the cross-tenant FK check (one SELECT per guarded FK) used
    to re-run on every save(), including a plain field-only update like
    `entry.save(update_fields=["note"])`, where no FK column is even changing. Now it
    only runs on a full save/create (update_fields=None) or an update_fields save that
    actually touches a guarded FK field name.
    """
    user = User.objects.create_user(email="j@example.com", password="x")
    entry = _make_journal_entry(user)

    # A plain-field update_fields save: the check must be skipped entirely.
    entry.note = "updated"
    with patch.object(JournalEntry, "_check_cross_tenant_fks", autospec=True) as mocked:
        entry.save(update_fields=["note"])
    mocked.assert_not_called()
    entry.refresh_from_db()
    assert entry.note == "updated"

    # An update_fields save that *does* touch a guarded FK field: must still run.
    other_execution = _make_execution(user)
    entry.opening_execution = other_execution
    with patch.object(JournalEntry, "_check_cross_tenant_fks", autospec=True) as mocked:
        entry.save(update_fields=["opening_execution"])
    mocked.assert_called_once()

    # Same, but naming the FK's attname (opening_execution_id) instead of its field
    # name — round-5 code review: Django's update_fields accepts either, and matching
    # only field.name meant this variant slipped through unchecked.
    third_execution = _make_execution(user)
    entry.opening_execution = third_execution
    with patch.object(JournalEntry, "_check_cross_tenant_fks", autospec=True) as mocked:
        entry.save(update_fields=["opening_execution_id"])
    mocked.assert_called_once()

    # A full save (update_fields=None, the default): must still run.
    entry.note = "updated again"
    with patch.object(JournalEntry, "_check_cross_tenant_fks", autospec=True) as mocked:
        entry.save()
    mocked.assert_called_once()


@pytest.mark.django_db
def test_save_accepts_django_real_positional_signature():
    """
    Round-5 code review: UserOwned.save(self, *args, update_fields=None, **kwargs) used
    to collide with Django's real Model.save(force_insert, force_update, using,
    update_fields) — all four are positional-capable, so
    save(False, False, None, ["note"]) put ["note"] into *args while this override's
    named update_fields stayed None, then super().save(*args,
    update_fields=update_fields, **kwargs) supplied update_fields both positionally
    (still in args) and as a keyword, raising TypeError. Fixed by not declaring
    update_fields as a named parameter at all.
    """
    user = User.objects.create_user(email="p@example.com", password="x")
    entry = _make_journal_entry(user)

    entry.note = "positional"
    entry.save(False, False, None, ["note"])  # would have raised TypeError before the fix

    entry.refresh_from_db()
    assert entry.note == "positional"


@pytest.mark.django_db
def test_get_or_create_and_update_or_create_work_on_default_manager():
    """
    Round-5 code review: get_or_create()/update_or_create() proxied through the
    raising get_queryset(), contradicting the manager's own docstring claim that
    .create() is the one exception — and directly blocking the idempotent-import
    pattern CLAUDE.md requires ("dedupe by broker + execution id"), whose natural
    implementation is exactly
    Execution.objects.get_or_create(user=..., broker=..., broker_execution_id=...,
    defaults={...}). Neither call can leak: `user` is a required, explicit kwarg.
    """
    user = User.objects.create_user(email="q@example.com", password="x")

    execution, created = Execution.objects.get_or_create(
        user=user,
        broker="topstep",
        broker_execution_id="1:entry",
        defaults=dict(
            symbol="MNQZ5",
            side=Execution.SIDE_BUY,
            quantity="1",
            price="1",
            currency="USD",
            executed_at=timezone.now(),
            source=Execution.SOURCE_IMPORT,
        ),
    )
    assert created

    # Idempotent re-call (the actual importer use case): same row, not created again.
    same_execution, created_again = Execution.objects.get_or_create(
        user=user,
        broker="topstep",
        broker_execution_id="1:entry",
        defaults=dict(
            symbol="MNQZ5",
            side=Execution.SIDE_BUY,
            quantity="1",
            price="1",
            currency="USD",
            executed_at=timezone.now(),
            source=Execution.SOURCE_IMPORT,
        ),
    )
    assert not created_again
    assert same_execution.pk == execution.pk

    execution, updated = Execution.objects.update_or_create(
        user=user,
        broker="topstep",
        broker_execution_id="1:entry",
        defaults={"fees": "1.23"},
    )
    assert str(execution.fees) == "1.23"


@pytest.mark.django_db
def test_journalentry_risk_currency_blank_rejected_when_amount_set():
    """
    Round-5 code review: the CHECK constraint only tested risk_currency__isnull, so
    risk_currency="" satisfied "required iff planned_risk_amount is set" despite being
    meaningless. planned_risk_amount="100.00", risk_currency="" must be rejected.
    """
    user = User.objects.create_user(email="r@example.com", password="x")
    execution = _make_execution(user)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            JournalEntry.objects.create(
                user=user,
                opening_execution=execution,
                planned_risk_amount="100.00",
                risk_currency="",
            )


@pytest.mark.django_db
def test_execution_currency_blank_rejected():
    """
    Round-5 code review: currency had no non-empty guard at all — no CheckConstraint,
    no full_clean() call site — unlike quantity/price/contract_multiplier in the same
    Meta.constraints list. CLAUDE.md: "store currency with every amount."
    """
    user = User.objects.create_user(email="s@example.com", password="x")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Execution.objects.create(
                user=user,
                broker="manual",
                symbol="AAPL",
                side=Execution.SIDE_BUY,
                quantity="1",
                price="1",
                currency="",
                executed_at=timezone.now(),
                source=Execution.SOURCE_MANUAL,
            )


@pytest.mark.django_db
def test_execution_contract_multiplier_must_be_positive():
    """
    contract_multiplier is the P&L multiplier (point value per contract); 0 or negative
    would silently corrupt every trade derived from this fill. Missing CheckConstraint,
    caught in code review — Execution.quantity/price had one in the same Meta.constraints
    list, contract_multiplier didn't.
    """
    user = User.objects.create_user(email="h@example.com", password="x")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Execution.objects.create(
                user=user,
                broker="manual",
                symbol="AAPL",
                side=Execution.SIDE_BUY,
                quantity="1",
                price="1",
                contract_multiplier="0",
                currency="USD",
                executed_at=timezone.now(),
                source=Execution.SOURCE_MANUAL,
            )


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
