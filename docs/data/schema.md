# Data dictionary — MVP schema

Source of truth for the model: `docs/adr/0003-data-model.md`. This document records what
was actually built, any corrections found while implementing, and the indexing rationale
for the MVP dashboard (`docs/product/features/mvp.md` story 6). Built by `database-engineer`
in `accounts/models.py` and `journal/models.py`; migrations in `accounts/migrations/` and
`journal/migrations/`.

Two Django apps, four user-owned tables, one user table. No `trade` table — trades are a
pure function of executions (`journal/matching.py`, not built in this task; `backend-engineer`'s
territory per ADR-0003 §5).

## `accounts_user`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `BIGINT` (identity) | no | PK |
| `email` | `VARCHAR(254)` | no | `254` is `EmailField`'s own built-in default, not an explicit `max_length=` on the field (**round-6 cleanup**: dropped an explicit `max_length=254` that just restated Django's default — `makemigrations` confirms no schema change resulted). `USERNAME_FIELD`. Case-insensitive uniqueness via `UNIQUE (lower(email))`, **not** the `CITEXT` extension — ADR-0003 §1 names this as an acceptable substitute. Django's `auth.E003` check doesn't recognize expression-based `UniqueConstraint`s, so it's silenced in `config/settings.py` with a comment; the DB guarantee is unaffected and is *stricter* than what the check looks for. Login (`UserManager.get_by_natural_key`) filters on `.annotate(email_lower=Lower("email")).get(email_lower=value.lower())`, **not** `email__iexact=value` — `__iexact` compiles to `UPPER(email) = UPPER(%s)`, which doesn't match this index and forces a seq scan on every login attempt (verified with `EXPLAIN`, round-2 code review: `iexact` → `Seq Scan on accounts_user`; the `Lower()` annotation → `Index Scan using accounts_user_email_lower_uniq`). `UserManager._create_user()`'s missing-email guard raises `ValidationError`, not `ValueError` (**round-4 code review**, for consistency — every other invalid-field case in `_create_user()` is caught via `full_clean()` and raises `ValidationError`; a caller only needs to catch one exception type). |
| `password` | `VARCHAR(128)` | no | Django PBKDF2 hash |
| `timezone` | `VARCHAR(64)` | no, default `'UTC'` | IANA name, validated against `accounts.models._AVAILABLE_TIMEZONES` — `frozenset(zoneinfo.available_timezones())` computed once at import time, not re-scanned per call (no DB-level check — the set changes only with a tzdata upgrade + process restart, hence a plain module constant rather than a `CHECK` or a per-call cache). Actually enforced on the only signup path: `UserManager._create_user()` calls `full_clean()` before `save()` — **fixed in code review, round 3**: it previously only constructed and saved the model directly, so `validate_timezone` (a field validator, which only runs via `full_clean()`) never ran; `create_user(timezone="Not/A_Real_Zone")` saved without error. |
| `base_currency` | `VARCHAR(3)` | no, default `'USD'` | ISO 4217. Display default only; never overrides an amount's own currency column |
| `trading_rules` | `TEXT` | no, default `''` | The entire "my rules" feature (mvp.md story 5). No versioning — past journal entries' `rules_followed` flags are unaffected by later edits, by construction |
| `is_active`, `is_staff`, `is_superuser`, `last_login`, `date_joined` | Django defaults | | |

## `journal_importbatch` (`UserOwned`)

One row per uploaded file. `user_id` present per `UserOwned` (RLS-ready).

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `broker` | `VARCHAR(32)` | no | `'topstep'` today |
| `filename` | `VARCHAR(255)` | no | as uploaded |
| `file_sha256` | `VARCHAR(64)` | no | informational only, not a dedupe key |
| `raw_file` | `BYTEA` | no | uploaded bytes verbatim, so a bad row-split/encoding guess is recoverable. **Size-capped per code review** at `MAX_RAW_FILE_BYTES = 10 MiB` (ADR-0003 assumes "tens of KB"; 10 MiB is a generous sanity/abuse guard, not a tight limit). Enforced two ways: `models.BinaryField(max_length=MAX_RAW_FILE_BYTES)` — Django's built-in `MaxLengthValidator`, appended automatically by `BinaryField` when `max_length` is set, runs on `full_clean()` — and a DB `CHECK (octet_length(raw_file) <= 10485760)` — `importbatch_raw_file_size_limit` — as the backstop for writes that skip `full_clean()` (e.g. a plain `.create()`). **Simplified in round-4 code review**: originally a hand-written `validate_raw_file_size` validator function, which duplicated exactly what `max_length=` already gives for free; dropped in favor of the built-in. The DB constraint uses `RawSQL`, so Django's `models.W045` check (silenced in `config/settings.py`) correctly notes it isn't pre-validated by `full_clean()` itself; the field's `max_length` validator covers that path instead. |
| `uploaded_at` | `TIMESTAMPTZ` | no, `auto_now_add` | |
| `row_count`, `imported_count`, `skipped_count`, `failed_count` | `INTEGER` | no, default 0 | result summary (mvp.md story 3) |

Index: `(user_id, uploaded_at DESC)` — import history.

## `journal_rawimportrow` (`UserOwned`)

Verbatim CSV rows, re-parseable. `user_id` denormalized here rather than reached only via
`import_batch_id`, so an RLS policy can bite on it directly (ADR-0003 §"Tenant isolation").

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `import_batch_id` | `BIGINT FK → journal_importbatch` | no | `ON DELETE CASCADE` |
| `line_number` | `INTEGER` | no | 1-based |
| `raw` | `JSONB` | no | `{header: cell}`, strings only, no coercion. **Lossless-reparse note, round-6 code review, document-only — no importer exists yet**: `JSONB` can re-normalize numeric literals on write (precision/format drift — trailing zeros, exponent notation), which would violate CLAUDE.md's "keep raw data so parsing can be re-run" guarantee for anything numeric. Any future importer **must** serialize numeric values into this field as strings, never native Python `float`/`int`, to stay byte-for-byte lossless. Matching comment on the field in `journal/models.py`. |
| `status` | `VARCHAR(20)` | no | `CHECK (status IN ('imported','skipped_duplicate','failed'))` — `rawimportrow_status_valid`. Matches ADR-0003's `VARCHAR(20)` (corrected upstream from an original `VARCHAR(16)` that couldn't fit its own `'skipped_duplicate'` enum value). |
| `error` | `TEXT` | no, default `''` | shown to the user, never silently dropped |

Constraints: `UNIQUE (import_batch_id, line_number)`; `CHECK (status IN (...))` above — code review
caught that `status` had `choices=` (Python-only) but no DB-level enum constraint, unlike
`Execution.side`/`source` in the same PR. Added for parity.

## `journal_execution` (`UserOwned`) — source of truth

Immutable by convention (never `UPDATE`d; corrections delete+recreate).

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `broker` | `VARCHAR(32)` | no | `'topstep'` or `'manual'` |
| `broker_execution_id` | `VARCHAR(128)` | yes | `NULL` for manual entry. The dedupe `UniqueConstraint` below (`execution_broker_dedupe`) exempts both `NULL` **and** empty string — see that constraint's note |
| `broker_account_label` | `VARCHAR(64)` | no, default `''` | verbatim from export; no account table yet |
| `symbol` | `VARCHAR(32)` | no | uppercased, as broker wrote it |
| `side` | `VARCHAR(4)` | no | `CHECK (side IN ('buy','sell'))` |
| `quantity` | `NUMERIC(20,10)` | no | `CHECK (quantity > 0)`. Always positive; direction lives in `side` |
| `price` | `NUMERIC(20,10)` | no | No non-negative `CHECK`. **Removed round-6 code review** (was `CHECK (price >= 0)` — `execution_price_nonnegative`): futures have traded/settled negative in real markets (WTI crude, CL, settled around -$37.63 on 2020-04-20), and this app targets futures brokers (Topstep). `quantity > 0` below is still correct and unaffected — direction lives in `side`, not price's sign |
| `contract_multiplier` | `NUMERIC(20,10)` | no, default 1 | point value per contract, stored per fill so a contract-spec change never rewrites old P&L. `CHECK (contract_multiplier > 0)` — `execution_contract_multiplier_positive`, added round-3 code review: it's the P&L multiplier, so 0 or negative would silently corrupt every derived trade, and `quantity`/`price` in the same constraints list already had this protection while this column didn't |
| `fees` | `NUMERIC(19,4)` | no, default 0 | total cost of this fill |
| `currency` | `VARCHAR(3)` | no | ISO 4217, applies to `fees` and derived P&L. `CHECK (currency <> '')` — `execution_currency_not_blank`, added round-5 code review: unlike `quantity`/`price`/`contract_multiplier` in the same constraints list, `currency` had no non-empty guard at all (no `CheckConstraint`, no `full_clean()` call site on this write path), so a money-bearing execution could be saved with `currency=""`, silently violating CLAUDE.md's "store currency with every amount" |
| `executed_at` | `TIMESTAMPTZ` | no | UTC in DB, rendered in `user.timezone` |
| `source` | `VARCHAR(8)` | no | `CHECK (source IN ('manual','import'))` |
| `raw_import_row_id` | `BIGINT FK → journal_rawimportrow` | yes | `ON DELETE SET NULL`; `NULL` for manual |
| `created_at` | `TIMESTAMPTZ` | no, `auto_now_add` | |

Constraints/indexes:

- `UNIQUE (user_id, broker, broker_execution_id) WHERE broker_execution_id IS NOT NULL AND
  broker_execution_id != ''` — `execution_broker_dedupe`. This *is* idempotent import; no
  importer-side locking needed. Verified with `EXPLAIN`: a lookup by
  `(user_id, broker, broker_execution_id)` uses this index directly (`Index Scan using
  execution_broker_dedupe`). **Extended round-6 code review** to also exclude empty string,
  not just `NULL`: a hand-rolled write path persisting `""` instead of `None` would
  otherwise create spurious collisions between unrelated manual entries, since `""` is
  `IS NOT NULL`.
- `(user_id, symbol, executed_at)` — `execution_user_symbol_ts_idx`. The FIFO matcher's read
  pattern: one user's fills for one symbol, oldest first. Verified with `EXPLAIN` on
  `WHERE user_id = ? ORDER BY symbol, executed_at`.
- `(user_id, executed_at DESC)` — `execution_user_ts_desc_idx`. Date-bounded loads and the
  trade list's default recency sort (mvp.md story 6). Verified with `EXPLAIN` on
  `WHERE user_id = ? ORDER BY executed_at DESC`.

### On story 6's "P&L" sort/filter specifically

There is **no SQL column or index for P&L** — by ADR-0003 §5, P&L doesn't exist until
`derive_trades()` runs in Python over a user's executions (compute-on-read; sorting/filtering
by P&L happens after matching, in Python, not pushed into SQL). The index that actually bounds
this cost is `execution_user_symbol_ts_idx` above: it's what makes "load one user's fills,
grouped by symbol, in time order" — the input `derive_trades()` needs — an index scan instead
of a sequential scan. There is nothing further to index for P&L itself without materializing a
`trade` table, which ADR-0003 explicitly defers until a measured >300ms dashboard.

## `journal_journalentry` (`UserOwned`)

One per trade, keyed by the execution that opened it.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `opening_execution_id` | `BIGINT FK → journal_execution`, `UNIQUE` | no | `ON DELETE RESTRICT` (not `CASCADE`). `OneToOneField` — this *is* the trade id. **Changed in code review**: `CASCADE` let a trade correction (delete-old-execution + insert-new-execution, per ADR-0003 §4) silently destroy the note + `rules_followed` flag with no recovery. `RESTRICT` raises `RestrictedError` on a standalone `execution.delete()` while a `JournalEntry` still points at it, forcing the correction code to explicitly re-point (`UPDATE opening_execution_id`) or deliberately delete the entry first. `PROTECT` was considered and rejected: it raises unconditionally, which would also block full account deletion (ADR-0003's "one statement" guarantee) — `RESTRICT` specifically allows deletion when the protecting row is being deleted in the same cascade (verified: `user.delete()` still removes the execution and its journal entry together in one call). |
| `note` | `TEXT` | no, default `''` | optional reasoning |
| `rules_followed` | `BOOLEAN` | yes, no default | `NULL` = not yet answered. "Journaled" ≡ `rules_followed IS NOT NULL` |
| `stop_price` | `NUMERIC(20,10)` | yes | R-multiple input |
| `planned_risk_amount` | `NUMERIC(19,4)` | yes | R-multiple input |
| `risk_currency` | `VARCHAR(3)` | yes | required (non-null **and** non-blank) iff `planned_risk_amount` is set |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | no | `auto_now_add` / `auto_now` |

Constraints/indexes:

- `CHECK`: `risk_currency` is set (non-null and non-blank) if and only if
  `planned_risk_amount` is set (`journalentry_risk_currency_required_with_amount`).
  **Tightened round-5 code review**: the original constraint only tested
  `risk_currency__isnull`, so `planned_risk_amount="100.00", risk_currency=""` satisfied
  it despite being meaningless — `""` is not null but isn't a currency either. Added
  `~Q(risk_currency="")` to the "amount set" branch.
- `(user_id, rules_followed)` — `journalentry_user_flag_idx`. Covers all three of story 6's
  rule-followed filter states in one index: yes, no, and "not journaled"
  (`WHERE rules_followed IS NULL`, which a composite btree serves directly on the second
  column — verified with `EXPLAIN`: `Index Scan using journalentry_user_flag_idx ... Index
  Cond: ((user_id = 1) AND (rules_followed IS NULL))`).

  ADR-0003's index list separately specified `(user_id) WHERE rules_followed IS NULL` as its
  own partial index. Built initially per the ADR, then **dropped per code review**
  (`journalentry_not_journaled_idx`): it was redundant once `journalentry_user_flag_idx`
  existed to cover the yes/no states, since the composite index serves the NULL case just as
  well (confirmed above), and this table is one row per trade — cheap either way, but no
  reason to carry two indexes for one query shape. **Corrected round-6 code review**: this
  used to cite `journal/migrations/0005_...` for where the drop happened — that file no
  longer exists after the round-4 migration squash (only `0001`–`0003` exist now; see
  "Migration history" below). The drop is simply baked into the squashed `0001_initial.py`,
  which never creates the index at all, rather than living in its own migration.

## Tenant isolation

Every table above except `accounts_user` inherits `journal.models.UserOwned`: a plain
`user_id BIGINT NOT NULL FK → accounts_user ON DELETE CASCADE`, plus
`UserScopedManager.for_user(user)` as the one query chokepoint. `ON DELETE CASCADE` on every
FK to `user` means account deletion is one `DELETE FROM accounts_user WHERE id = ?`.

**Hardened per code review** (isolation was previously opt-in — nothing stopped a call site
from using the unscoped default manager and leaking cross-user data): `UserScopedManager`'s
`get_queryset()` now raises `RuntimeError` unconditionally, so `Model.objects.all()`,
`.filter()`, `.get()`, etc. all fail loudly instead of silently returning every user's rows.
`.for_user(user)` bypasses the raise (it calls `super().get_queryset()` directly) and remains
the one sanctioned read path. `.create()`, `.get_or_create()`, and `.update_or_create()` are
separately exempted on the manager. `.create()` is unconditionally safe — it isn't a read and
can't leak (the `user` FK is `NOT NULL` and always passed explicitly as a kwarg), so blocking
it would break the ordinary `Model.objects.create(user=..., ...)` idiom.

`get_or_create`/`update_or_create` are **not** unconditionally safe, and took two rounds to
get right:

- **Round-5**: missed in the original hardening — they proxied through the raising
  `get_queryset()`, contradicting the manager's own docstring claim that `.create()` was the
  one exception, and directly blocked the idempotent-import pattern CLAUDE.md requires
  ("dedupe by broker + execution id"). Bypassed the same way as `.create()`.
- **Round-6**: that bypass reintroduced a cross-tenant leak. `get_or_create`'s *lookup* kwargs
  (not `defaults`) decide which existing row gets returned — `defaults` only applies on
  create. Confirmed live: `Execution.objects.get_or_create(broker="topstep",
  broker_execution_id="SHARED1", defaults=dict(user=user_b, ...))`, with `user` only in
  `defaults`, returned user_a's existing matching row with `created=False`, handing user_b
  user_a's execution — no `save()` ever ran on that path, so `UserOwned.save()`'s
  cross-tenant FK guard never fired. Fixed by requiring `user=` in the top-level lookup
  kwargs (raises `ValueError` if it's missing or only in `defaults`) and asserting the
  returned row's `user_id` matches afterward (raises `CrossTenantForeignKeyError` as a
  belt-and-suspenders check, since the lookup requirement should already make a mismatch
  unreachable). The natural, now-safe importer call:
  `Execution.objects.get_or_create(user=..., broker=..., broker_execution_id=...,
  defaults={...})`.

Each `UserOwned` subclass also gets a second manager, `unscoped` (a plain
`models.Manager()`). `Meta.base_manager_name = "unscoped"` and `Meta.default_manager_name =
"unscoped"` are declared **once**, on `UserOwned`'s own abstract `Meta`, and every concrete
model inherits them via `class Meta(UserOwned.Meta): ...` — **DRY'd up in round-4 code
review**: these two lines were previously copy-pasted into all four concrete models'
`Meta` bodies. Verified this inheritance actually resolves correctly (Django requires the
explicit `class Meta(UserOwned.Meta)` subclassing — a bare `class Meta:` in a concrete model
would not see the abstract base's options) by checking `_meta.base_manager_name` /
`_meta.default_manager_name` / `_meta.abstract` on all four live model classes after the
change: all four report `abstract=False` (Django resets `abstract` on an abstract base's own
`Meta` right after building it, specifically so concrete subclasses of `class
Meta(UserOwned.Meta)` don't inherit `abstract=True`) and both manager names as `"unscoped"`.

Two separate Django internals needed the manager-name pair, found in two rounds of code review:

- `base_manager_name`: the deletion collector (which `user.delete()`'s cascade relies on) uses
  `_base_manager`. Without this, hardening `objects` would have silently broken account
  deletion.
- `default_manager_name`: a reverse-FK `RelatedManager` (e.g. `import_batch.rows.all()`) is
  built off `_default_manager.__class__`. Without this, traversing a relation from an
  already-`.for_user()`-scoped parent row hit the same `RuntimeError` as a top-level unscoped
  query — even though it can't leak (the manager is bound to one specific, already-scoped
  parent instance, not a fresh query). Found in round-2 review after `base_manager_name` alone
  shipped in round 1.

Both route to the same unrestricted `unscoped` manager; `Model.objects.<read>()` (the explicit
`UserScopedManager`) still raises regardless of either setting.

Verified directly: `user.delete()` still removes the user's `ImportBatch`, `RawImportRow`,
`Execution`, and `JournalEntry` rows in one call; `batch.rows.all()` on a
`.for_user()`-fetched `ImportBatch` works, while `ImportBatch.objects.all()` still raises.

**Cross-tenant FK integrity — added round-3 code review.** `.for_user()` and the hardened
manager only stop a query from crossing users; they never stopped a *write* from linking two
rows across users through a FK. Confirmed live before the fix:
`JournalEntry.objects.create(user=user_b, opening_execution=<user_a's execution>)` succeeded,
and `for_user(user_b)` then surfaced user_a's execution data through
`journal_entry.opening_execution` — a leak through a relation, not a direct query, so the
manager hardening above didn't catch it.

Fixed once, generically, on `UserOwned.save()` rather than per model, since the same shape of
bug applies to every cross-FK in the schema (`RawImportRow.import_batch`,
`Execution.raw_import_row`, `JournalEntry.opening_execution`). `save()` introspects
`self._meta.get_fields()` for every `ForeignKey` (`OneToOneField` included — it's a `ForeignKey`
subclass) whose `related_model` is itself a `UserOwned` subclass, and compares that related
row's `user_id` against `self.user_id`. Runs on every full `save()`/`create()` call, not just
`full_clean()` — `full_clean()` isn't reliably called (see `accounts_user.timezone` above for
exactly that failure mode on a different model). Nullable cross-FKs
(`Execution.raw_import_row`) are skipped when unset.

**Not uniformly `CrossTenantForeignKeyError`** — corrected wording, round-6 code review: an
actual cross-tenant mismatch raises `CrossTenantForeignKeyError` (a `ValueError` subclass), but
a guarded FK pointing at a **nonexistent** row (a dangling/invalid id) surfaces as a plain
`IntegrityError` from the DB's own FK constraint at INSERT/UPDATE time instead — the lookup
comes back with no `user_id` to compare, so this check treats "no such row" as "not this
check's problem" and lets the DB's real FK constraint be the one to reject it. Not changed
(declined in both round 5 and round 6, same reasoning): the data is still protected either way,
just via a different exception type on that one path, and the fix would be a bigger design
change for no additional safety.

**Perf**: fetches the related row's `user_id` via `.unscoped` (a
`.values_list("user_id", flat=True)` lookup, not a full-row fetch) — **unless** Django already
has the related instance cached in memory (round-6 code review: `field.is_cached(self)`),
e.g. `JournalEntry.objects.create(user=u, opening_execution=execution_instance)` — every
factory in `journal/tests.py` does exactly this — in which case it reads `user_id` off the
cached instance directly and skips the query entirely. Verified with
`CaptureQueriesContext`: a cross-tenant `create()` with a cached related instance is caught
with **zero** SQL queries (no `SELECT` for the check, no `INSERT` since the exception fires
first).

**Perf fix, round-4 code review**: `save()` accepts `update_fields` (read out of `**kwargs`,
not a named parameter — see below) and only re-runs the check (one `SELECT` per guarded FK)
when `update_fields is None` (a full save/create) or when the `update_fields` list actually
includes one of the guarded FK field names. A plain-field update like
`entry.save(update_fields=["note"])` skips the check entirely — no FK column is changing, so
there's nothing new to verify. Verified in `journal/tests.py` (below) by mocking
`_check_cross_tenant_fks` and asserting it's not called for a `note`-only `update_fields` save,
but is called for both an `update_fields=["opening_execution"]` save and a full save.
**Extended round-5 code review**: matching only checked `field.name` (e.g.
`"opening_execution"`), but Django's `update_fields` also accepts a FK's `attname` (e.g.
`"opening_execution_id"`) — `save(update_fields=["opening_execution_id"])` skipped the check
entirely before this fix. Now matches both.

**Signature bug, round-5 code review**: `save()` was declared as
`save(self, *args, update_fields=None, **kwargs)`, which collides with Django's real
`Model.save(force_insert=False, force_update=False, using=None, update_fields=None)` — all
four of Django's params are positional-capable. A call like `save(False, False, None,
["note"])` (valid against Django's real signature, if deprecated in 5.2 in favor of keywords)
put `["note"]` into this override's `*args` while its own named `update_fields` stayed `None`,
then `super().save(*args, update_fields=update_fields, **kwargs)` supplied `update_fields`
both positionally (still sitting in `args`) and as a keyword, raising `TypeError`. Fixed by not
declaring `update_fields` as a named parameter at all — it's read via
`kwargs.get("update_fields")`, and `*args, **kwargs` are forwarded to `super().save()`
unchanged.

**Known gap, documented rather than built for (round-4 code review, extended round-5)**: this
guard is `save()`-only. Django never calls `save()` for `bulk_create`/`bulk_update`/
`QuerySet.update()`, so `Model.unscoped.bulk_create(...)`, `.bulk_update(...)`, and
`Model.unscoped.filter(...).update(...)` all bypass it entirely — there is no protection
against a cross-tenant FK inserted or changed via any of these. No caller does bulk writes yet
(no importer exists in this PR), so no bulk-write guard is built speculatively. **Any future
bulk-write code (the Topstep importer) must either loop per-row `.save()` or add its own
explicit ownership check before calling `bulk_create`/`bulk_update`/`.update()`.** This is also
called out as a comment directly on `UserOwned.unscoped` in `journal/models.py`.

**Admin known gap, documented round-5 code review, nothing registered yet**:
`Meta.default_manager_name = "unscoped"` means Django admin's `ModelAdmin.get_queryset()`
(which reads `_default_manager`) would show every tenant's rows the moment any `UserOwned`
model is registered in `journal/admin.py` or `accounts/admin.py` — both are currently empty
stubs. Registering one requires an explicit `get_queryset()` override on that `ModelAdmin`,
scoped to the current request's user. Flagged with a comment in both `admin.py` files; not
built now since nothing is registered.

RLS is not enabled (ADR-0002's trigger — before any non-author account exists — hasn't fired).
Every table already carries the plain `user_id` column a policy would need, so enabling it
later is additive DDL, no migration of existing columns.

Verified in `journal/tests.py`:
- `test_user_owned_subclasses_have_factories` / `test_for_user_never_returns_another_users_rows`
  enumerate `UserOwned.__subclasses__()` and assert `for_user(user_a)` never returns
  `user_b`'s rows, for all four models. A model added later without a registered factory
  fails loudly instead of being silently skipped.
- `test_default_manager_blocks_unscoped_reads` asserts `Model.objects.all()`/`.filter()`
  raise `RuntimeError` for all four models.
- `test_create_and_unscoped_escape_hatch_still_work` asserts `.create()` and `.unscoped`
  still function normally.
- `test_related_manager_still_works_on_a_scoped_parent` asserts reverse-FK traversal
  (`batch.rows.all()`) works on a `.for_user()`-scoped parent, while a direct
  `ImportBatch.objects.all()` still raises.
- `test_user_delete_cascades_all_owned_rows_in_one_call` creates one row per `UserOwned`
  model, calls `user.delete()`, and asserts zero rows remain across all of them — a persisted
  test for the "one statement" account-deletion invariant that was previously only checked by
  a manual script and a code comment.
- `test_cross_tenant_fk_is_rejected` asserts `CrossTenantForeignKeyError` on the exact
  `JournalEntry`/`opening_execution` scenario confirmed live above, and that no row was
  created at all.
- `test_save_update_fields_skips_fk_check_unless_a_guarded_field_is_touched` mocks
  `_check_cross_tenant_fks` and asserts it's skipped for a `note`-only `update_fields`
  save, but still runs for an `update_fields=["opening_execution"]` save, an
  `update_fields=["opening_execution_id"]` save (the attname variant, round-5), and a
  full save.
- `test_save_accepts_django_real_positional_signature` calls
  `entry.save(False, False, None, ["note"])` — Django's real, deprecated-but-still-valid
  positional `save()` signature — and asserts it doesn't raise `TypeError`.
- `test_get_or_create_and_update_or_create_work_on_default_manager` exercises the exact
  idempotent-import shape CLAUDE.md requires directly on `Model.objects`.
- `test_get_or_create_with_user_only_in_defaults_is_rejected` (round-6) reproduces the exact
  live leak — `user` only in `defaults`, not the lookup kwargs — and asserts `ValueError`
  now, plus that user_a's row is untouched and nothing was created for user_b.
- `test_journalentry_risk_currency_blank_rejected_when_amount_set` and
  `test_execution_currency_blank_rejected` assert `IntegrityError` on
  `risk_currency=""` (with `planned_risk_amount` set) and `currency=""` respectively.
- `test_execution_negative_price_is_allowed` (round-6) asserts a `price="-37.63"`
  execution saves and round-trips correctly, now that `execution_price_nonnegative` is
  gone.
- `test_execution_broker_dedupe_exempts_empty_string_like_null` (round-6) asserts two
  executions with `broker_execution_id=""` for the same user/broker coexist.
- `test_cross_tenant_fk_check_uses_cached_instance_without_extra_query` (round-6) asserts
  via `CaptureQueriesContext` that a cross-tenant `create()` with a cached related
  instance raises with zero SQL queries.
- `test_execution_contract_multiplier_must_be_positive` asserts `IntegrityError` on
  `contract_multiplier=0`.

`config/tests.py` (new, round-5) covers `SECRET_KEY`'s fail-closed logic — necessarily via
subprocess, since it's import-time settings behavior that can't be re-exercised once a test
process has already imported settings once: `test_secret_key_required_when_debug_false_and_unset`,
`test_secret_key_falls_back_to_dev_default_when_debug_true`,
`test_secret_key_from_env_used_when_debug_false`.

### Migration history

`journal`'s migrations were squashed to a single `0001_initial.py` in round-4 code review
(the prior `0001`–`0007` reflected four rounds of review churn — an index added then
dropped, `on_delete` changed twice, `Meta` options redeclared then DRY'd up — none of which
had ever been applied to a real/shipped database on this branch, so there was no reason to
carry it into permanent history). Verified: fresh `migrate` from zero applies the squashed
`0001_initial.py` cleanly, `makemigrations --check --dry-run` reports no changes, and the DDL
inspected via `psql \d journal_execution` afterward is byte-for-byte the same shape as before
the squash. `accounts` was not touched — its single migration had no churn to squash.

**Not re-squashed since** (round-5 added `0002`, round-6 added `0003`) — deliberate, per
explicit direction: squash everything into one clean `0001_initial.py` again in a dedicated
final pass right before merge, not after every review round. `journal` currently has three
migrations (`0001`–`0003`); this note exists so a future reference to "the squashed
migration" doesn't assume `0001` alone still reflects the full current schema.

## Money / quantity / time — confirmed as built

- Money (`fees`, `planned_risk_amount`): `NUMERIC(19,4)` + a 3-char currency column next to it.
- Price/quantity (`quantity`, `price`, `contract_multiplier`, `stop_price`): `NUMERIC(20,10)`.
- All timestamps: `TIMESTAMPTZ` (`USE_TZ = True`), stored UTC, rendered in `user.timezone`
  (rendering is `backend-engineer`/`frontend-engineer` territory, not built here).
- No `FloatField` anywhere in `accounts/models.py` or `journal/models.py`.

**Deviation, flagged per code review**: every currency column (`accounts_user.base_currency`,
`journal_execution.currency`, `journal_journalentry.risk_currency`) is `VARCHAR(3)`, not
ADR-0003's literal `CHAR(3)`. Django's `CharField` always maps to `varchar` regardless of
`max_length` — there's no built-in fixed-length char field, so matching `CHAR(3)` exactly
would mean a custom `Field` subclass overriding `db_type()` for a 3-byte column, with no
functional upside: Postgres's own documentation recommends `varchar(n)`/`text` over `char(n)`
in general, because `char(n)` pads values with trailing spaces and that padding is a
long-standing source of surprise (`'USD' = 'USD '` behavior, `rstrip`-on-read semantics).
`VARCHAR(3)` stores and compares exactly the 3-letter ISO 4217 codes this schema needs, with
none of that padding behavior. Kept as documented deviation rather than "fixed" to a type
Postgres itself steers people away from.

## Application configuration (`config/settings.py`)

Not part of the schema, but tracked here since this doc has become the running log for this
task's code-review fixes:

- `DEBUG`, `ALLOWED_HOSTS`, and (**round-5 code review**) `SECRET_KEY` all fail closed on a
  missing/misconfigured env var, rather than silently defaulting to something permissive.
  `SECRET_KEY` was the odd one out: it fell back to a hardcoded, publicly-committed dev value
  unconditionally, so a prod `.env` missing `DJANGO_SECRET_KEY` would silently boot with a key
  anyone reading the public repo could use to forge sessions/CSRF tokens/password-reset tokens.
  Now: if `DJANGO_SECRET_KEY` is unset, the dev fallback is used only when `DEBUG=True`;
  `DEBUG=False` with no `DJANGO_SECRET_KEY` raises `ImproperlyConfigured` at import time,
  refusing to start. Verified via subprocess in `config/tests.py` (see the "Tenant isolation"
  test list above) for all three cases: unset+`DEBUG=False` (raises), unset+`DEBUG=True` (dev
  fallback), and set+`DEBUG=False` (uses the real value). Required reordering `DEBUG` above
  `SECRET_KEY` in the file, since the fail-closed check needs to know `DEBUG` first.
- `UserManager._create_user()` has a known, accepted TOCTOU: two concurrent signups with the
  same email can both pass the pre-save uniqueness check before either commits. Not fixed —
  the DB `UniqueConstraint(Lower("email"))` already prevents the actual duplicate row
  regardless (one of the two will hit `IntegrityError`); the gap is only that `full_clean()`'s
  `ValidationError` isn't guaranteed to be what the loser sees. Marked with a `# ponytail:`
  comment on `_create_user()` — the eventual signup view needs to catch `IntegrityError`
  alongside `ValidationError`, but there's no view yet to catch anything in.

## Known gaps (not this task's scope)

- No `Meta.db_table` overrides needed — Django's default naming
  (`<app_label>_<model>` lowercase) already matches ADR-0003's table names exactly.
- `journal/matching.py` (`derive_trades()`) does not exist yet — `backend-engineer`'s task,
  per ADR-0003 follow-up 2.
- No admin registration, views, forms, or API — explicitly out of scope for this task. See
  the "Admin known gap" note under Tenant isolation above for what registering a `UserOwned`
  model will require.
