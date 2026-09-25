"""
The mandatory isolation test (ADR-0003, mvp.md story 1): enumerate every UserOwned
model and assert `for_user()` never returns another user's rows. Minimal by design —
qa-engineer expands coverage (views, URLs, ID-guessing) later.

A new UserOwned subclass added later without a factory registered below fails
`test_user_owned_subclasses_have_factories` loudly, instead of silently being skipped
by the isolation test.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
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
def test_get_or_create_with_user_only_in_defaults_is_rejected():
    """
    Round-6 code review: round 5's bypass let get_or_create()/update_or_create() proxy
    straight to the unscoped queryset, but the *lookup* kwargs (not `defaults`) decide
    which existing row gets returned — `defaults` only applies on create. Confirmed live
    before this fix: Execution.objects.get_or_create(broker="topstep",
    broker_execution_id="SHARED1", defaults=dict(user=user_b, ...)), with `user` only in
    `defaults`, returned user_a's existing matching row with created=False — no save()
    ever ran, so the cross-tenant FK guard on save() never got a chance to fire. Must now
    raise instead of leaking.
    """
    user_a = User.objects.create_user(email="t@example.com", password="x")
    user_b = User.objects.create_user(email="u@example.com", password="x")

    existing = Execution.objects.create(
        user=user_a,
        broker="topstep",
        broker_execution_id="SHARED1",
        symbol="MNQZ5",
        side=Execution.SIDE_BUY,
        quantity="1",
        price="1",
        currency="USD",
        executed_at=timezone.now(),
        source=Execution.SOURCE_IMPORT,
    )

    with pytest.raises(ValueError):
        Execution.objects.get_or_create(
            broker="topstep",
            broker_execution_id="SHARED1",
            defaults=dict(
                user=user_b,
                symbol="MNQZ5",
                side=Execution.SIDE_BUY,
                quantity="1",
                price="1",
                currency="USD",
                executed_at=timezone.now(),
                source=Execution.SOURCE_IMPORT,
            ),
        )

    # No leak occurred: user_a's row is untouched, and nothing new was created for
    # user_b under this broker/broker_execution_id pair.
    existing.refresh_from_db()
    assert existing.user_id == user_a.pk
    assert not Execution.unscoped.filter(
        broker="topstep", broker_execution_id="SHARED1", user=user_b
    ).exists()


@pytest.mark.django_db
def test_get_or_create_with_conflicting_defaults_user_does_not_commit():
    """
    Round-7 addendum: round 6's "require user= in lookup kwargs + check after" fix was
    still insufficient. Confirmed live: Execution.objects.get_or_create(user=user_a,
    broker="topstep", broker_execution_id="X1", defaults=dict(user=user_b, ...)), with
    *no* existing match — Django's own get_or_create() lets defaults["user"] override
    the lookup kwargs' user when building the row to create, so it COMMITS with
    user_id=user_b.pk inside Django's own transaction, and only then does the
    after-the-fact check run and raise — too late, the row already existed (confirmed via
    Execution.unscoped.filter(...) showing the leaked row despite the raise). Fixed by
    validating before calling Django's own get_or_create()/update_or_create() at all: a
    conflicting defaults["user"]/["user_id"] now raises immediately, and nothing commits.
    """
    user_a = User.objects.create_user(email="aa@example.com", password="x")
    user_b = User.objects.create_user(email="bb@example.com", password="x")

    with pytest.raises(ValueError):
        Execution.objects.get_or_create(
            user=user_a,
            broker="topstep",
            broker_execution_id="X1",
            defaults=dict(
                user=user_b,
                symbol="MNQZ5",
                side=Execution.SIDE_BUY,
                quantity="1",
                price="1",
                currency="USD",
                executed_at=timezone.now(),
                source=Execution.SOURCE_IMPORT,
            ),
        )

    # Nothing committed at all, for either user — the row must not exist even briefly.
    assert not Execution.unscoped.filter(
        broker="topstep", broker_execution_id="X1"
    ).exists()


@pytest.mark.django_db
def test_get_or_create_defaults_user_matching_lookup_is_fine():
    """
    Companion to the test above: defaults specifying user/user_id is only a problem when
    it *conflicts* with the lookup kwargs. Specifying the same user in both is a normal,
    harmless call shape and must keep working.
    """
    user = User.objects.create_user(email="cc@example.com", password="x")

    execution, created = Execution.objects.get_or_create(
        user=user,
        broker="topstep",
        broker_execution_id="X3",
        defaults=dict(
            user=user,  # same user, redundant but not conflicting
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
    assert execution.user_id == user.pk


@pytest.mark.django_db
def test_get_or_create_accepts_user_id_lookup_kwarg():
    """
    Round-7 code review: _require_user_in_lookup_kwargs originally only checked for the
    literal key "user", incorrectly rejecting the equally valid user_id= lookup kwarg on
    get_or_create/update_or_create — failed closed (no leak), but blocked a legitimate
    call shape.
    """
    user = User.objects.create_user(email="dd@example.com", password="x")

    execution, created = Execution.objects.get_or_create(
        user_id=user.pk,
        broker="topstep",
        broker_execution_id="X4",
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
    assert execution.user_id == user.pk


@pytest.mark.django_db
def test_cross_tenant_check_skipped_when_user_id_is_none_lets_real_not_null_surface():
    """
    Round-7 code review: without a self.user_id is None guard, a row saved without
    user= set would compare a guarded FK's real owner against self.user_id=None, raising
    a misleading CrossTenantForeignKeyError ("belongs to user_id=5, not this row's
    user_id=None") instead of the DB's own, correct NOT NULL violation on user_id.
    """
    user = User.objects.create_user(email="ee@example.com", password="x")
    execution = _make_execution(user)

    with pytest.raises(IntegrityError) as excinfo:
        JournalEntry(opening_execution=execution).save()

    assert "user_id" in str(excinfo.value)
    assert not issubclass(excinfo.type, CrossTenantForeignKeyError)


@pytest.mark.django_db
def test_execution_price_zero_rejected():
    """
    Round-7 code review: round 6 removed execution_price_nonnegative because negative
    settlement prices are real (WTI 2020-04-20), but that evidence doesn't extend to
    price=0 — $0 is essentially never a valid fill price and is almost certainly
    malformed data, unlike a real negative settlement.
    """
    user = User.objects.create_user(email="ff@example.com", password="x")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Execution.objects.create(
                user=user,
                broker="manual",
                symbol="AAPL",
                side=Execution.SIDE_BUY,
                quantity="1",
                price="0",
                currency="USD",
                executed_at=timezone.now(),
                source=Execution.SOURCE_MANUAL,
            )


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
def test_execution_negative_price_is_allowed():
    """
    Round-6 code review: execution_price_nonnegative (price >= 0) was removed. Futures
    have traded/settled negative in real markets — WTI crude (CL) settled around
    -$37.63 on 2020-04-20 — and this app targets futures brokers (Topstep). quantity > 0
    is still enforced; only price's sign was wrongly constrained.
    """
    user = User.objects.create_user(email="v@example.com", password="x")

    execution = Execution.objects.create(
        user=user,
        broker="manual",
        symbol="CLZ0",
        side=Execution.SIDE_BUY,
        quantity="1",
        price="-37.63",
        currency="USD",
        executed_at=timezone.now(),
        source=Execution.SOURCE_MANUAL,
    )
    execution.refresh_from_db()
    assert execution.price == Decimal("-37.63")


@pytest.mark.django_db
def test_execution_broker_dedupe_exempts_empty_string_like_null():
    """
    Round-6 code review: the partial UniqueConstraint on
    (user, broker, broker_execution_id) only exempted NULL, not empty string — a
    hand-rolled write path persisting "" instead of None would have collided unrelated
    manual entries, since "" IS NOT NULL. Two rows with broker_execution_id="" for the
    same user/broker must coexist, the same as two rows with broker_execution_id=None.
    """
    user = User.objects.create_user(email="w@example.com", password="x")

    first = Execution.objects.create(
        user=user, broker="manual", broker_execution_id="", symbol="AAPL",
        side=Execution.SIDE_BUY, quantity="1", price="1", currency="USD",
        executed_at=timezone.now(), source=Execution.SOURCE_MANUAL,
    )
    second = Execution.objects.create(
        user=user, broker="manual", broker_execution_id="", symbol="AAPL",
        side=Execution.SIDE_BUY, quantity="1", price="1", currency="USD",
        executed_at=timezone.now(), source=Execution.SOURCE_MANUAL,
    )
    assert first.pk != second.pk


@pytest.mark.django_db
def test_cross_tenant_fk_check_uses_cached_instance_without_extra_query():
    """
    Perf fix, round-6 code review: when the related instance is already a live Python
    object on the model being saved (e.g. JournalEntry.objects.create(user=u,
    opening_execution=execution_instance) — every factory in this file does exactly
    this), Django caches it on assignment. The cross-tenant check must read `user_id`
    off that cached instance instead of issuing a redundant SELECT.
    """
    user_a = User.objects.create_user(email="x@example.com", password="x")
    user_b = User.objects.create_user(email="y@example.com", password="x")
    execution_a = _make_execution(user_a)

    with CaptureQueriesContext(connection) as ctx:
        with pytest.raises(CrossTenantForeignKeyError):
            JournalEntry.objects.create(user=user_b, opening_execution=execution_a)

    select_on_execution = [
        q["sql"] for q in ctx.captured_queries
        if "journal_execution" in q["sql"] and q["sql"].strip().upper().startswith("SELECT")
    ]
    assert select_on_execution == [], (
        "cross-tenant check issued a SELECT on journal_execution despite the related "
        "instance already being cached in memory"
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
