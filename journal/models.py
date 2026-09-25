"""
Schema per docs/adr/0003-data-model.md. Executions are the source of truth; trades are
a pure function of executions computed at read time (journal/matching.py, backend-engineer's
territory) — there is deliberately no `trade` table here.
"""

from django.conf import settings
from django.db import models
from django.db.models.expressions import RawSQL

# ADR-0003 assumes Topstep exports are "tens of KB." 10 MB is a generous cap (roughly
# 100-1000x that) — a sanity/abuse guard against a malformed or hostile upload, not a
# tight limit expected to bind on legitimate files.
MAX_RAW_FILE_BYTES = 10 * 1024 * 1024


class UserScopedManager(models.Manager):
    """
    The one chokepoint every read must go through (ADR-0002/0003, CLAUDE.md).

    Hardened per code review: the default queryset is deliberately unusable, so a call
    site has to opt OUT of scoping (via `Model.unscoped`) rather than opt into it. A
    forgotten `.for_user()` now fails loudly (RuntimeError) instead of silently returning
    every user's rows. `.create()`, `.get_or_create()`, and `.update_or_create()` are the
    exceptions — `.create()` can't leak (the `user` FK is NOT NULL and always passed
    explicitly as a kwarg), and blocking it would break the ordinary
    `Model.objects.create(user=..., ...)` idiom used throughout the app and its tests.

    `get_or_create()`/`update_or_create()` need more than that exemption, though (round-6
    code review — round 5's bypass reintroduced a cross-tenant leak): their *lookup*
    kwargs, not just `defaults`, determine which existing row (if any) gets returned, and
    `defaults` is only applied on create. Confirmed live:
    `Execution.objects.get_or_create(broker="topstep", broker_execution_id="SHARED1",
    defaults=dict(user=user_b, ...))` — `user` only in `defaults`, not the lookup —
    returned user_a's existing matching row with `created=False`, handing user_b user_a's
    execution. No `save()` ever ran on that path, so `UserOwned.save()`'s cross-tenant FK
    guard never got a chance to fire. Fixed by requiring `user=` in the top-level lookup
    kwargs (raises `ValueError` otherwise) and asserting the returned row's `user_id`
    matches afterward (raises `CrossTenantForeignKeyError` if it somehow doesn't — belt
    and suspenders, since the lookup requirement should already make this unreachable).
    """

    def get_queryset(self):
        raise RuntimeError(
            "Blocked: unscoped query via the default manager on a UserOwned model. Use "
            "Model.objects.for_user(user) for a read, or Model.unscoped for a deliberate, "
            "reviewed exception (admin/ops scripts). See journal/models.py:UserScopedManager."
        )

    def for_user(self, user):
        return super().get_queryset().filter(user=user)

    def create(self, **kwargs):
        return super().get_queryset().create(**kwargs)

    def get_or_create(self, defaults=None, **kwargs):
        self._require_user_in_lookup_kwargs("get_or_create", kwargs)
        obj, created = super().get_queryset().get_or_create(defaults=defaults, **kwargs)
        self._assert_belongs_to_user(obj, kwargs["user"])
        return obj, created

    def update_or_create(self, defaults=None, create_defaults=None, **kwargs):
        self._require_user_in_lookup_kwargs("update_or_create", kwargs)
        # create_defaults is Django 5.0+; kwargs shape is identical to get_or_create's
        # otherwise, so no separate comment needed beyond the class docstring above.
        obj, created = super().get_queryset().update_or_create(
            defaults=defaults, create_defaults=create_defaults, **kwargs
        )
        self._assert_belongs_to_user(obj, kwargs["user"])
        return obj, created

    @staticmethod
    def _require_user_in_lookup_kwargs(method_name, kwargs):
        if "user" not in kwargs:
            raise ValueError(
                f"{method_name}() on a UserOwned model requires `user=` in the "
                "top-level lookup kwargs, not only inside `defaults=` — otherwise the "
                "lookup can match (and return) another user's row before `defaults` is "
                "ever applied. See UserScopedManager's docstring."
            )

    @staticmethod
    def _assert_belongs_to_user(obj, user):
        user_id = getattr(user, "pk", user)
        if obj.user_id != user_id:
            raise CrossTenantForeignKeyError(
                f"{type(obj).__name__} (pk={obj.pk}) belongs to user_id={obj.user_id}, "
                f"not the requested user_id={user_id}."
            )


class CrossTenantForeignKeyError(ValueError):
    """Raised when a UserOwned row's FK to another UserOwned row crosses users."""


class UserOwned(models.Model):
    """
    Abstract base for every user-owned table. `user_id` is a plain, non-null column on
    every subclass (not reached only through a parent FK) so a Postgres RLS policy can
    bite on it directly whenever ADR-0002's RLS trigger fires — no schema change needed
    then, per ADR-0003's "Tenant isolation" section.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    objects = UserScopedManager()
    # Explicit, named escape hatch: a plain, unrestricted manager. Used by Django
    # internals — every concrete model inherits both `Meta.base_manager_name` (the
    # deletion collector's cascades, e.g. `user.delete()`, use `_base_manager`) and
    # `Meta.default_manager_name` (reverse-FK RelatedManagers, e.g.
    # `import_batch.rows.all()`, are built off `_default_manager.__class__` — without
    # this, traversing a relation from an already-`.for_user()`-scoped parent row would
    # hit the same raise as a top-level unscoped query, even though it can't leak) from
    # this abstract base's own Meta (see below) — and by any deliberate, reviewed
    # unscoped access. Never call this from request-handling code.
    #
    # Known gap, documented per code review rather than built for: this manager (and the
    # cross-tenant FK guard on save() below) only guards the `save()` path. Django never
    # calls `save()` for `bulk_create`/`bulk_update`/`QuerySet.update()` —
    # `Model.unscoped.bulk_create(...)`, `.bulk_update(...)`, and
    # `Model.unscoped.filter(...).update(...)` all bypass it entirely. No caller does
    # bulk writes yet (no importer in this PR), so no bulk-write guard is built
    # speculatively. Any future bulk-write code (the Topstep importer) must either loop
    # per-row `.save()` or add its own explicit ownership check before calling
    # `bulk_create`/`bulk_update`/`.update()` — see docs/data/schema.md's "Cross-tenant
    # FK integrity" section.
    unscoped = models.Manager()

    class Meta:
        abstract = True
        # Inherited by every concrete subclass via `class Meta(UserOwned.Meta): ...`
        # (Django requires that explicit subclassing to pull in an abstract base's Meta
        # options — a bare `class Meta:` in the child would not see these). Previously
        # copy-pasted into all four subclasses; DRY'd up per code review.
        base_manager_name = "unscoped"
        default_manager_name = "unscoped"

    def save(self, *args, **kwargs):
        # Signature is *args, **kwargs, not a named `update_fields=None` param (code
        # review, round 5): Django's real Model.save() signature is
        # save(force_insert=False, force_update=False, using=None, update_fields=None),
        # all of which can be passed positionally. A named `update_fields` param here
        # would shadow that position — a positional call like
        # save(False, False, None, ["note"]) would put ["note"] into *args instead of
        # this override's `update_fields`, and then `super().save(*args,
        # update_fields=update_fields, **kwargs)` would supply update_fields both
        # positionally (still in args) and as a keyword, raising TypeError. Reading it
        # out of kwargs and forwarding *args/**kwargs unchanged avoids the collision
        # entirely — this override never needs to pass update_fields on, only inspect it.
        update_fields = kwargs.get("update_fields")
        # Perf fix per code review: only re-run the (one SELECT per guarded FK)
        # cross-tenant check when it could actually matter — a full save/create, or an
        # update_fields save that actually touches one of the guarded FK fields. A
        # plain-field update (e.g. `entry.save(update_fields=["note"])`) skips it
        # entirely; the FK columns aren't changing, so there's nothing new to verify.
        if update_fields is None:
            self._check_cross_tenant_fks()
        else:
            touched = set(update_fields)
            # Match both the field name (e.g. "opening_execution") and its attname
            # (e.g. "opening_execution_id") — Django's update_fields accepts either
            # for a FK (round-5 code review: matching only field.name meant
            # save(update_fields=["opening_execution_id"]) skipped the check entirely).
            if any(
                f.name in touched or f.attname in touched
                for f in self._guarded_fk_fields()
            ):
                self._check_cross_tenant_fks()
        super().save(*args, **kwargs)

    def _guarded_fk_fields(self):
        """Every ForeignKey (OneToOneField included — it subclasses ForeignKey) whose
        `related_model` is itself a UserOwned subclass, i.e. every cross-tenant-checked
        relation on this model."""
        for field in self._meta.get_fields():
            if not isinstance(field, models.ForeignKey):
                continue
            related_model = field.related_model
            if isinstance(related_model, type) and issubclass(related_model, UserOwned):
                yield field

    def _check_cross_tenant_fks(self) -> None:
        """
        Root-cause fix for a cross-tenant leak found in code review: nothing checked
        that a UserOwned row's FK targets (RawImportRow.import_batch,
        Execution.raw_import_row, JournalEntry.opening_execution, ...) belong to the
        *same* user as the row itself. Confirmed live:
        `JournalEntry.objects.create(user=user_b, opening_execution=<user_a's execution>)`
        succeeded, and `for_user(user_b)` then surfaced user_a's execution data through
        `entry.opening_execution`.

        Fixed once, here, generically — not per model — since the same shape of bug
        applies to every cross-FK in the schema. Runs on every `save()` call by default
        (not just `full_clean()`, which isn't reliably called — see
        UserManager._create_user's own bug in accounts/models.py), skipped only for
        `update_fields` saves that don't touch a guarded field (see `save()` above).
        Compares `user_id` without loading the full related row, unless Django already
        has the related instance cached in memory (e.g. `JournalEntry.objects.create(
        user=u, opening_execution=execution_instance)` — every factory in
        journal/tests.py does exactly this), in which case it reads `user_id` off that
        instead of issuing a redundant `SELECT` (perf fix, round-6 code review).

        Note: this raises `CrossTenantForeignKeyError` for an actual cross-tenant
        mismatch, but *not* uniformly for every bad FK — a guarded FK pointing at a
        nonexistent row (a dangling/invalid id) surfaces as a plain `IntegrityError` from
        the DB's own FK constraint at INSERT/UPDATE time instead, since
        `related_user_id` comes back `None` and the `is not None` guard below
        deliberately treats "no such row" as "not this check's problem" — the real FK
        constraint already prevents a dangling reference regardless, so the data is
        still protected, just via a different exception type on that path. Not changed
        (round-5 and round-6 code review, both declined for the same reason); documented
        here instead of overclaiming uniform coverage.
        """
        for field in self._guarded_fk_fields():
            related_id = getattr(self, field.attname)
            if related_id is None:
                continue  # nullable FK not set (e.g. Execution.raw_import_row)
            if field.is_cached(self):
                related_user_id = field.get_cached_value(self).user_id
            else:
                related_user_id = (
                    field.related_model.unscoped.filter(pk=related_id)
                    .values_list("user_id", flat=True)
                    .first()
                )
            if related_user_id is not None and related_user_id != self.user_id:
                raise CrossTenantForeignKeyError(
                    f"{type(self).__name__}.{field.name} (pk={related_id}) belongs to "
                    f"user_id={related_user_id}, not this row's user_id={self.user_id}."
                )


class ImportBatch(UserOwned):
    """One row per uploaded file (ADR-0003 §2)."""

    broker = models.CharField(max_length=32)
    filename = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64)
    # max_length gives us Django's built-in MaxLengthValidator for free (BinaryField
    # appends it automatically when max_length is set) — simpler than the hand-written
    # validate_raw_file_size this replaced (code review: don't duplicate what the field
    # already gives you). Runs on full_clean(); the DB CHECK constraint below is the
    # separate, intentional defense-in-depth backstop for writes that skip full_clean()
    # (e.g. a plain .create()) — kept as-is, this is a real trust boundary (file upload).
    raw_file = models.BinaryField(max_length=MAX_RAW_FILE_BYTES)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    row_count = models.IntegerField(default=0)
    imported_count = models.IntegerField(default=0)
    skipped_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)

    class Meta(UserOwned.Meta):
        constraints = [
            # DB-level backstop for the field's max_length validator above — that only
            # runs on full_clean() (e.g. a ModelForm), not on a plain .save()/.create().
            # octet_length() is Postgres-specific, fair game per ADR-0002.
            models.CheckConstraint(
                condition=RawSQL(
                    "octet_length(raw_file) <= %s",
                    (MAX_RAW_FILE_BYTES,),
                    output_field=models.BooleanField(),
                ),
                name="importbatch_raw_file_size_limit",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-uploaded_at"], name="importbatch_user_uploaded_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.broker}:{self.filename}"


class RawImportRow(UserOwned):
    """Verbatim CSV rows, re-parseable (ADR-0003 §3)."""

    STATUS_IMPORTED = "imported"
    STATUS_SKIPPED_DUPLICATE = "skipped_duplicate"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_IMPORTED, "Imported"),
        (STATUS_SKIPPED_DUPLICATE, "Skipped (duplicate)"),
        (STATUS_FAILED, "Failed"),
    ]

    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.CASCADE, related_name="rows"
    )
    line_number = models.IntegerField()
    # Lossless-reparse note (round-6 code review, documented — no importer exists yet
    # to enforce this in): JSONB can re-normalize numeric literals on write (precision/
    # format drift, e.g. trailing zeros or exponent notation), which would violate
    # CLAUDE.md's "keep raw data so parsing can be re-run" guarantee for anything
    # numeric. Any future importer MUST serialize numeric values into this field as
    # strings, never native Python float/int, to stay byte-for-byte lossless. See
    # docs/data/schema.md for the same note.
    raw = models.JSONField()
    # VARCHAR(20), matching ADR-0003 (corrected upstream to VARCHAR(20); the original
    # VARCHAR(16) couldn't fit its own "skipped_duplicate" enum value).
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error = models.TextField(blank=True, default="")

    class Meta(UserOwned.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["import_batch", "line_number"],
                name="rawimportrow_batch_line_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["imported", "skipped_duplicate", "failed"]),
                name="rawimportrow_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.import_batch_id}:{self.line_number}"


class Execution(UserOwned):
    """
    The source of truth. Immutable by convention: append-only, never UPDATEd (ADR-0003 §4).
    Correcting a manual entry deletes and recreates its executions; imported executions are
    never edited (re-import instead).
    """

    SIDE_BUY = "buy"
    SIDE_SELL = "sell"
    SIDE_CHOICES = [(SIDE_BUY, "Buy"), (SIDE_SELL, "Sell")]

    SOURCE_MANUAL = "manual"
    SOURCE_IMPORT = "import"
    SOURCE_CHOICES = [(SOURCE_MANUAL, "Manual"), (SOURCE_IMPORT, "Import")]

    broker = models.CharField(max_length=32)
    broker_execution_id = models.CharField(max_length=128, null=True, blank=True)
    broker_account_label = models.CharField(max_length=64, blank=True, default="")
    symbol = models.CharField(max_length=32)
    side = models.CharField(max_length=4, choices=SIDE_CHOICES)
    quantity = models.DecimalField(max_digits=20, decimal_places=10)
    price = models.DecimalField(max_digits=20, decimal_places=10)
    contract_multiplier = models.DecimalField(max_digits=20, decimal_places=10, default=1)
    fees = models.DecimalField(max_digits=19, decimal_places=4, default=0)
    # VARCHAR(3), not ADR-0003's literal CHAR(3) — see docs/data/schema.md "Money /
    # quantity / time" for why (Django has no native fixed-length char field; Postgres's
    # own docs discourage CHAR(n) generally). Deviation documented, not silent.
    currency = models.CharField(max_length=3)
    executed_at = models.DateTimeField()
    source = models.CharField(max_length=8, choices=SOURCE_CHOICES)
    raw_import_row = models.ForeignKey(
        RawImportRow,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="executions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(UserOwned.Meta):
        constraints = [
            # Idempotent import, per CLAUDE.md and ADR-0003 §4. Partial so manual entries
            # (NULL broker_execution_id) are exempt. Also excludes empty string, not
            # just NULL (round-6 code review): a hand-rolled write path that persisted
            # "" instead of None would otherwise create spurious collisions between
            # unrelated manual entries, since "" IS NOT NULL.
            models.UniqueConstraint(
                fields=["user", "broker", "broker_execution_id"],
                condition=(
                    models.Q(broker_execution_id__isnull=False)
                    & ~models.Q(broker_execution_id="")
                ),
                name="execution_broker_dedupe",
            ),
            models.CheckConstraint(
                condition=models.Q(side__in=["buy", "sell"]), name="execution_side_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="execution_quantity_positive"
            ),
            # No non-negative CheckConstraint on price (round-6 code review, removing
            # execution_price_nonnegative which was here before): futures have traded
            # and settled negative in real markets — WTI crude (CL) settled around
            # -$37.63 on 2020-04-20 — and this app targets futures brokers (Topstep).
            # `quantity > 0` above is still correct; direction lives in `side`, not
            # price's sign.
            models.CheckConstraint(
                condition=models.Q(source__in=["manual", "import"]),
                name="execution_source_valid",
            ),
            # contract_multiplier is the P&L multiplier (point value per contract) — a
            # 0 or negative value would silently corrupt every trade derived from this
            # fill. Same pattern as quantity/price above; was missing (code review).
            models.CheckConstraint(
                condition=models.Q(contract_multiplier__gt=0),
                name="execution_contract_multiplier_positive",
            ),
            # currency had no non-empty guard at all — no CheckConstraint, no
            # full_clean() call site — unlike quantity/price/contract_multiplier above
            # (round-5 code review). CLAUDE.md: "store currency with every amount"; a
            # blank currency on a money-bearing execution violates that silently.
            models.CheckConstraint(
                condition=~models.Q(currency=""), name="execution_currency_not_blank"
            ),
        ]
        indexes = [
            # The matcher's read pattern: a user's fills for one symbol, in time order.
            models.Index(fields=["user", "symbol", "executed_at"], name="execution_user_symbol_ts_idx"),
            # Date-bounded loads / recency (trade list default sort, story 6).
            models.Index(fields=["user", "-executed_at"], name="execution_user_ts_desc_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.symbol} {self.side} {self.quantity}@{self.price}"


class JournalEntry(UserOwned):
    """
    One per trade, keyed by the execution that opened it (ADR-0003 §6) — a derived trade
    has no database id of its own.
    """

    # RESTRICT, not CASCADE and not PROTECT. ADR-0003's manual-entry correction flow is
    # delete-old-execution + insert-new-execution; CASCADE would silently destroy the note
    # + rules_followed flag along with the old execution. RESTRICT raises
    # django.db.models.deletion.RestrictedError on a standalone `execution.delete()`,
    # forcing the correction code to explicitly re-point this row at the new execution (or
    # deliberately delete the journal entry) first — no silent data loss. Unlike PROTECT,
    # RESTRICT still allows account deletion to cascade in one statement (ADR-0003's "one
    # statement" account-deletion guarantee): when a User is deleted, this JournalEntry is
    # already being deleted via its own `user` FK (CASCADE) in the same operation, so
    # Execution's cascade-delete isn't blocked by this relation. PROTECT would raise even
    # in that case, breaking full-account deletion.
    opening_execution = models.OneToOneField(
        Execution, on_delete=models.RESTRICT, related_name="journal_entry"
    )
    note = models.TextField(blank=True, default="")
    # NULL = not yet answered. No default, deliberately (story 4): "journaled" is defined
    # as rules_followed IS NOT NULL.
    rules_followed = models.BooleanField(null=True, blank=True)
    stop_price = models.DecimalField(max_digits=20, decimal_places=10, null=True, blank=True)
    planned_risk_amount = models.DecimalField(max_digits=19, decimal_places=4, null=True, blank=True)
    # VARCHAR(3), not CHAR(3) — see the comment on Execution.currency above.
    risk_currency = models.CharField(max_length=3, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(UserOwned.Meta):
        constraints = [
            # "required iff set" needs both a null check AND a non-blank check on
            # risk_currency (round-5 code review): __isnull=False alone let
            # risk_currency="" through, so planned_risk_amount="100.00",
            # risk_currency="" satisfied the constraint despite being meaningless.
            models.CheckConstraint(
                condition=(
                    models.Q(planned_risk_amount__isnull=True, risk_currency__isnull=True)
                    | (
                        models.Q(planned_risk_amount__isnull=False)
                        & models.Q(risk_currency__isnull=False)
                        & ~models.Q(risk_currency="")
                    )
                ),
                name="journalentry_risk_currency_required_with_amount",
            ),
        ]
        indexes = [
            # Covers all three of story 6's rule-followed filter states (yes/no/not
            # journaled — the last is `WHERE rules_followed IS NULL`, which this
            # composite index serves directly; a separate partial index scoped to just
            # that NULL case, as ADR-0003's list suggested, would be redundant with this
            # one and was dropped per code review).
            models.Index(fields=["user", "rules_followed"], name="journalentry_user_flag_idx"),
        ]

    def __str__(self) -> str:
        return f"journal for execution {self.opening_execution_id}"
