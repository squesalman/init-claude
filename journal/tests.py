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
from django.db.models.deletion import RestrictedError
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
def test_save_accepts_one_shot_iterable_update_fields():
    """Round-11 code review: set(update_fields) consumed a generator before it reached
    Model.save(), which then saw an empty update_fields and raised AssertionError."""
    user = User.objects.create_user(email="g@example.com", password="x")
    entry = _make_journal_entry(user)

    entry.note = "generator"
    entry.save(update_fields=(f for f in ["note"]))

    entry.refresh_from_db()
    assert entry.note == "generator"


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


@pytest.mark.django_db
def test_standalone_execution_delete_is_restricted_by_journal_entry():
    """
    Round-8 code review: no test exercised a standalone execution.delete() while a
    JournalEntry still references it via opening_execution (on_delete=RESTRICT) — the
    only existing delete test (above) covers the user.delete() cascade path, where
    RESTRICT never actually fires (it's the same operation deleting both rows
    together). This is the other path: deleting just the execution, on its own, must
    raise RestrictedError, not silently cascade-delete (or orphan) the journal entry.
    """
    user = User.objects.create_user(email="gg@example.com", password="x")
    execution = _make_execution(user)
    entry = JournalEntry.objects.create(user=user, opening_execution=execution)

    with pytest.raises(RestrictedError):
        execution.delete()

    # Neither row was touched: RESTRICT blocks the delete outright.
    assert Execution.unscoped.filter(pk=execution.pk).exists()
    assert JournalEntry.unscoped.filter(pk=entry.pk).exists()


# --- Round-9 code review fixes ------------------------------------------------------


def _make_import_execution(user, **overrides):
    fields = dict(
        user=user, broker="topstep", broker_execution_id="X1", symbol="AAPL",
        side=Execution.SIDE_BUY, quantity="1", price="1", currency="USD",
        executed_at=timezone.now(), source=Execution.SOURCE_IMPORT,
    )
    fields.update(overrides)
    return Execution.objects.create(**fields)


@pytest.mark.django_db
@pytest.mark.parametrize("owner_field", ["user", "user_id"])
def test_owner_only_update_fields_save_still_checks_all_guarded_fks(owner_field):
    """
    Round-9 bug: save(update_fields=["user"]) skipped the cross-tenant FK check because
    only the *guarded FK* names were matched against update_fields. Changing the owner
    of a row whose FK targets stay put is exactly the cross-tenant case. When the owner
    field is being written, every guarded FK must be re-checked, not just listed ones.
    """
    user_a = User.objects.create_user(email="r9a@example.com", password="x")
    user_b = User.objects.create_user(email="r9b@example.com", password="x")

    entry = _make_journal_entry(user_a)
    entry.user = user_b
    with pytest.raises(CrossTenantForeignKeyError):
        entry.save(update_fields=[owner_field])
    assert JournalEntry.unscoped.get(pk=entry.pk).user_id == user_a.pk

    row = _make_raw_import_row(user_a)  # RawImportRow.import_batch
    row.user = user_b
    with pytest.raises(CrossTenantForeignKeyError):
        row.save(update_fields=[owner_field])
    assert RawImportRow.unscoped.get(pk=row.pk).user_id == user_a.pk

    execution = _make_import_execution(
        user_a, raw_import_row=_make_raw_import_row(user_a)
    )  # Execution.raw_import_row
    execution.user = user_b
    with pytest.raises(CrossTenantForeignKeyError):
        execution.save(update_fields=[owner_field])
    assert Execution.unscoped.get(pk=execution.pk).user_id == user_a.pk


@pytest.mark.django_db
@pytest.mark.parametrize("bad_id", [None, ""])
def test_import_execution_requires_broker_execution_id(bad_id):
    """
    Round-9 bug: the partial unique index execution_broker_dedupe exempts NULL and ""
    broker_execution_id, so an import row with a blank id was never deduped — breaking
    the idempotent-import guarantee. source='import' must carry a real id (DB CHECK).
    """
    user = User.objects.create_user(email="r9c@example.com", password="x")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _make_import_execution(user, broker_execution_id=bad_id)

    # Manual entries stay exempt, and a real import id is fine.
    _make_import_execution(user, source=Execution.SOURCE_MANUAL, broker_execution_id=None)
    _make_import_execution(user, broker_execution_id="REAL-1")


@pytest.mark.django_db
@pytest.mark.parametrize("bad", ["usd", "us", "U1D", ""])
def test_execution_currency_must_be_three_uppercase_letters(bad):
    user = User.objects.create_user(email="r9d@example.com", password="x")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _make_import_execution(user, currency=bad)


@pytest.mark.django_db
@pytest.mark.parametrize("bad", ["usd", "us"])
def test_journalentry_risk_currency_must_be_three_uppercase_letters(bad):
    user = User.objects.create_user(email="r9e@example.com", password="x")
    execution = _make_execution(user)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            JournalEntry.objects.create(
                user=user, opening_execution=execution,
                planned_risk_amount="100.00", risk_currency=bad,
            )


def test_currency_validators_reject_bad_codes_on_full_clean():
    from django.core.exceptions import ValidationError

    entry = JournalEntry(risk_currency="usd")
    with pytest.raises(ValidationError) as excinfo:
        entry.clean_fields(exclude=[
            f.name for f in JournalEntry._meta.fields if f.name != "risk_currency"
        ])
    assert "risk_currency" in excinfo.value.message_dict

    execution = Execution(currency="us")
    with pytest.raises(ValidationError) as excinfo:
        execution.clean_fields(exclude=[
            f.name for f in Execution._meta.fields if f.name != "currency"
        ])
    assert "currency" in excinfo.value.message_dict


@pytest.mark.django_db
def test_get_or_create_is_not_offered_on_the_scoped_manager():
    """Round-9: the hand-rolled overrides were removed; the raising get_queryset() makes
    the stock methods fail closed until the importer defines its own upsert."""
    user = User.objects.create_user(email="r9f@example.com", password="x")
    with pytest.raises(RuntimeError):
        Execution.objects.get_or_create(user=user, broker="topstep", broker_execution_id="Z")
    with pytest.raises(RuntimeError):
        Execution.objects.update_or_create(user=user, broker="topstep", broker_execution_id="Z")


# --- Row 6 / ADR-0004: per-account dedupe key, broker_trade_id, skipped_conflict ---


@pytest.mark.django_db
def test_dedupe_key_includes_account_label():
    user = User.objects.create_user(email="r6a@example.com", password="x")
    _make_import_execution(user, broker_execution_id="D1", broker_account_label="A")
    # Same id, different label (a second account): both insert.
    _make_import_execution(user, broker_execution_id="D1", broker_account_label="B")
    _make_import_execution(user, broker_execution_id="D1", broker_account_label="")
    # Same id, same label: rejected by the unique index.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _make_import_execution(user, broker_execution_id="D1", broker_account_label="A")
    # Blank labels compare equal (NOT NULL DEFAULT ''), so today's behaviour is kept.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _make_import_execution(user, broker_execution_id="D1", broker_account_label="")


@pytest.mark.django_db
def test_dedupe_index_still_exempts_null_and_blank_ids_with_a_label():
    user = User.objects.create_user(email="r6b@example.com", password="x")
    for blank in (None, ""):
        for _ in range(2):
            _make_import_execution(
                user, source=Execution.SOURCE_MANUAL, broker="manual",
                broker_execution_id=blank, broker_account_label="A",
            )


@pytest.mark.django_db
def test_broker_trade_id_nullable_but_never_blank():
    user = User.objects.create_user(email="r6c@example.com", password="x")
    assert _make_import_execution(user, broker_execution_id="T1").broker_trade_id is None
    ok = _make_import_execution(user, broker_execution_id="T2", broker_trade_id="ROW-9")
    ok.refresh_from_db()
    assert ok.broker_trade_id == "ROW-9"
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _make_import_execution(user, broker_execution_id="T3", broker_trade_id="")


@pytest.mark.django_db
def test_rawimportrow_status_skipped_conflict_accepted_unknown_rejected():
    user = User.objects.create_user(email="r6d@example.com", password="x")
    batch = _make_import_batch(user)

    def make(line, status):
        return RawImportRow.objects.create(
            user=user, import_batch=batch, line_number=line, raw={"a": "1"}, status=status
        )

    assert make(1, "skipped_conflict").status == "skipped_conflict"
    assert ("skipped_conflict", "Skipped (conflict)") in RawImportRow.STATUS_CHOICES
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make(2, "bogus")
