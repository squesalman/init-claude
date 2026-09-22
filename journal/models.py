"""
Schema per docs/adr/0003-data-model.md. Executions are the source of truth; trades are
a pure function of executions computed at read time (journal/matching.py, backend-engineer's
territory) — there is deliberately no `trade` table here.
"""

from django.conf import settings
from django.db import models


class UserScopedManager(models.Manager):
    """
    The one chokepoint every read must go through (ADR-0002/0003, CLAUDE.md).

    Hardened per code review: the default queryset is deliberately unusable, so a call
    site has to opt OUT of scoping (via `Model.unscoped`) rather than opt into it. A
    forgotten `.for_user()` now fails loudly (RuntimeError) instead of silently returning
    every user's rows. `.create()` is the one exception — it can't leak (the `user` FK is
    NOT NULL and always passed explicitly), and blocking it would break the ordinary
    `Model.objects.create(user=..., ...)` idiom used throughout the app and its tests.
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
    # internals (each concrete model below routes `Meta.base_manager_name` here, so the
    # deletion collector's cascades — e.g. `user.delete()` — aren't blocked by the raise
    # above) and by any deliberate, reviewed unscoped access. Never call this from
    # request-handling code.
    unscoped = models.Manager()

    class Meta:
        abstract = True


class ImportBatch(UserOwned):
    """One row per uploaded file (ADR-0003 §2)."""

    broker = models.CharField(max_length=32)
    filename = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64)
    raw_file = models.BinaryField()
    uploaded_at = models.DateTimeField(auto_now_add=True)
    row_count = models.IntegerField(default=0)
    imported_count = models.IntegerField(default=0)
    skipped_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)

    class Meta:
        base_manager_name = "unscoped"
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
    raw = models.JSONField()
    # VARCHAR(20), matching ADR-0003 (corrected upstream to VARCHAR(20); the original
    # VARCHAR(16) couldn't fit its own "skipped_duplicate" enum value).
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error = models.TextField(blank=True, default="")

    class Meta:
        base_manager_name = "unscoped"
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

    class Meta:
        base_manager_name = "unscoped"
        constraints = [
            # Idempotent import, per CLAUDE.md and ADR-0003 §4. Partial so manual entries
            # (NULL broker_execution_id) are exempt.
            models.UniqueConstraint(
                fields=["user", "broker", "broker_execution_id"],
                condition=models.Q(broker_execution_id__isnull=False),
                name="execution_broker_dedupe",
            ),
            models.CheckConstraint(
                condition=models.Q(side__in=["buy", "sell"]), name="execution_side_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="execution_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(price__gte=0), name="execution_price_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=["manual", "import"]),
                name="execution_source_valid",
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
    risk_currency = models.CharField(max_length=3, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        base_manager_name = "unscoped"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(planned_risk_amount__isnull=True, risk_currency__isnull=True)
                    | models.Q(planned_risk_amount__isnull=False, risk_currency__isnull=False)
                ),
                name="journalentry_risk_currency_required_with_amount",
            ),
        ]
        indexes = [
            # story 6's "not journaled" filter, and the far more common predicate than a
            # specific yes/no value — kept as its own partial index per ADR-0003's list.
            models.Index(
                fields=["user"],
                name="journalentry_not_journaled_idx",
                condition=models.Q(rules_followed__isnull=True),
            ),
            # story 6's yes/no split of the same filter, covering the non-NULL states.
            models.Index(fields=["user", "rules_followed"], name="journalentry_user_flag_idx"),
        ]

    def __str__(self) -> str:
        return f"journal for execution {self.opening_execution_id}"
