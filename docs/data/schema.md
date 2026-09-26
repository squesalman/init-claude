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
| `email` | `VARCHAR(254)` | no | `254` is `EmailField`'s own built-in default, not an explicit `max_length=` on the field (**round-6 cleanup**: dropped an explicit `max_length=254` that just restated Django's default — `makemigrations` confirms no schema change resulted). `USERNAME_FIELD`. Case-insensitive uniqueness via `UNIQUE (lower(email))`, **not** the `CITEXT` extension — ADR-0003 §1 names this as an acceptable substitute. Django's `auth.E003` check doesn't recognize expression-based `UniqueConstraint`s, so it's silenced in `config/settings.py` with a comment; the DB guarantee is unaffected and is *stricter* than what the check looks for. Login (`UserManager.get_by_natural_key`) filters on `.annotate(email_lower=Lower("email")).get(email_lower=Lower(Value(raw_email)))`, **not** `email__iexact=value` — `__iexact` compiles to `UPPER(email) = UPPER(%s)`, which doesn't match this index and forces a seq scan on every login attempt (verified with `EXPLAIN`, round-2 code review: `iexact` → `Seq Scan on accounts_user`; the `Lower()` annotation → `Index Scan using accounts_user_email_lower_uniq`). **Also not** `email_lower=email.lower()` (round-7 code review — that was this method's shape between rounds 2 and 7): lowering the login input in *Python* while the index and the annotation both lower in *SQL* disagrees for non-ASCII characters under a C/POSIX collation (the default for the `postgres:16-alpine` image `compose.yaml` uses). Confirmed live: Postgres's `lower('İstanbul@example.com')` gives `'istanbul@example.com'` (20 chars, plain ASCII `i`), while Python's `"İstanbul@example.com".lower()` gives `'i̇stanbul@example.com'` (21 chars — `İ`, U+0130, folds to `i` + a combining dot above under Python's full Unicode case folding). These don't match character-for-character, so a user could fail to log in with the *exact* email they registered with. Fixed by lowering the login input in SQL too (`Lower(Value(email))`), so both sides of the comparison go through the same (Postgres) lowering rules instead of mixing Python and SQL. `UserManager._create_user()`'s missing-email guard raises `ValidationError`, not `ValueError` (**round-4 code review**, for consistency — every other invalid-field case in `_create_user()` is caught via `full_clean()` and raises `ValidationError`; a caller only needs to catch one exception type). **`get_or_create()`/`update_or_create()` are explicitly unsupported on `UserManager`**, not hand-rolled: round 7 added an override to close a `full_clean()`-bypass gap (Django's defaults construct-and-save a `User` directly on the create path without calling `full_clean()`, silently bypassing `validate_timezone` — confirmed live), but **round 8 found the hand-rolling itself was buggy** — `update_or_create()`'s update path did `setattr()` straight onto the instance, so `defaults={"password": "..."}` would have written a **plaintext password** (the create path correctly used `set_password()`, the update path didn't); both methods did a case-sensitive `self.get(**kwargs)` lookup while uniqueness is case-insensitive, so an existing `"Foo@Example.com"` wasn't found by `get_or_create(email="foo@example.com")` and fell through to create, hitting an uncaught `IntegrityError`; and neither had Django's own transaction wrapping/retry-on-race or lookup-suffix stripping. Root cause: nothing in this codebase calls either method — this was closing a hypothetical gap, and hand-rolling Django's `get_or_create`/`update_or_create` semantics correctly is real surface area for zero current benefit. Removed entirely; both now raise `NotImplementedError` pointing callers at `create_user()` for creation and an explicit `set_password()` + `full_clean()` + `save()` for updates. If/when a real caller needs this (e.g. an admin-assisted password reset flow), build it correctly at that point, scoped to the actual call shape needed. |
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
| `raw_file` | `BYTEA` | no | uploaded bytes verbatim, so a bad row-split/encoding guess is recoverable. **Size-capped per code review** at `MAX_RAW_FILE_BYTES = 10 MiB` (ADR-0003 assumes "tens of KB"; 10 MiB is a generous sanity/abuse guard, not a tight limit). Enforced two ways: `models.BinaryField(max_length=MAX_RAW_FILE_BYTES)` — Django's built-in `MaxLengthValidator`, appended automatically by `BinaryField` when `max_length` is set, runs on `full_clean()` — and a DB `CHECK (length(raw_file) <= 10485760)` — `importbatch_raw_file_size_limit` — as the backstop for writes that skip `full_clean()` (e.g. a plain `.create()`). **Simplified in round-4 code review**: originally a hand-written `validate_raw_file_size` validator function, which duplicated exactly what `max_length=` already gives for free; dropped in favor of the built-in. **Root-cause fixed in round-8 code review**: the DB constraint originally used `RawSQL("octet_length(raw_file) <= %s", ...)`, which Django's checker can't introspect, hence a `models.W045` warning silenced in `config/settings.py`. Replaced with `LessThanOrEqual(Length("raw_file"), MAX_RAW_FILE_BYTES)` — Postgres's `length(bytea)` returns byte count, identical to `octet_length(bytea)`, but expressed as an ORM expression Django *can* verify — so `models.W045` no longer fires at all, and `SILENCED_SYSTEM_CHECKS` no longer needs it (only `auth.E003` remains). `LessThanOrEqual` (from `django.db.models.lookups`) rather than a `__lte` lookup shortcut, because `length` isn't a lookup registered on `BinaryField` by default (unlike `CharField`/`TextField`) — constructing the `Lookup` class directly avoids registering a new lookup globally for one constraint. |
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
| `status` | `VARCHAR(20)` | no | `CHECK (status IN ('imported','skipped_duplicate','failed','skipped_conflict'))` — `rawimportrow_status_valid`. `skipped_conflict` added in migration `0003` (ADR-0004 §2): id already imported with different contents; the row's `error` says why; no executions written. Matches ADR-0003's `VARCHAR(20)` (corrected upstream from an original `VARCHAR(16)` that couldn't fit its own `'skipped_duplicate'` enum value). **Round-8 code review**: the constraint's allowed-values list is derived from the same `STATUS_CHOICES` tuple the field's `choices=` uses (`[c[0] for c in _STATUS_CHOICES]`), not a second hardcoded list — the two could otherwise drift independently. This (and the equivalent fix for `Execution.side`/`source` below) required moving the `*_CHOICES` tuples to module-level constants: a model's nested `Meta` class can't see names defined in the *enclosing* model class's own body (confirmed empirically — Python's class-scope rule, not a Django quirk), only module globals, so a `CheckConstraint` inside `Meta` literally cannot reference a class attribute like `STATUS_CHOICES` directly. The model class attributes of the same name (`RawImportRow.STATUS_CHOICES`, `Execution.SIDE_CHOICES`, etc.) still exist and work exactly as before for any external reference — they just point at the module constants now. |
| `error` | `TEXT` | no, default `''` | shown to the user, never silently dropped |

Constraints: `UNIQUE (import_batch_id, line_number)`; `CHECK (status IN (...))` above — code review
caught that `status` had `choices=` (Python-only) but no DB-level enum constraint, unlike
`Execution.side`/`source` in the same PR. Added for parity.

Index: `(user_id)` — `rawimportrow_user_idx`, added round-8 code review as a direct
replacement for the automatic single-column FK index removed via `db_index=False` on
`UserOwned.user` — see "Tenant isolation" below for why this table specifically needed a
compensating index where the other three didn't.

## `journal_execution` (`UserOwned`) — source of truth

Immutable by convention (never `UPDATE`d; corrections delete+recreate).

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `broker` | `VARCHAR(32)` | no | `'topstep'` or `'manual'` |
| `broker_execution_id` | `VARCHAR(128)` | yes | `NULL` for manual entry. The dedupe `UniqueConstraint` below (`execution_broker_dedupe`) exempts both `NULL` **and** empty string — see that constraint's note |
| `broker_account_label` | `VARCHAR(64)` | no, default `''` | verbatim from export; no account table yet. Part of the dedupe key (ADR-0004 §1); blank labels compare equal |
| `broker_trade_id` | `VARCHAR(128)` | yes | broker-reported round-trip id (Topstep row `Id`), set on both legs; `NULL` for manual / fill-only imports. Matcher bucket key (ADR-0004 §3). `CHECK (broker_trade_id IS NULL OR broker_trade_id <> '')` — `execution_broker_trade_id_not_blank` (a stray `''` would merge unrelated trades) |
| `symbol` | `VARCHAR(32)` | no | uppercased, as broker wrote it |
| `side` | `VARCHAR(4)` | no | `CHECK (side IN ('buy','sell'))`. Derived from `SIDE_CHOICES` the same way as `status` above (round-8 code review) — see that note |
| `quantity` | `NUMERIC(20,10)` | no | `CHECK (quantity > 0)`. Always positive; direction lives in `side` |
| `price` | `NUMERIC(20,10)` | no | No non-negative `CHECK`. **Removed round-6 code review** (was `CHECK (price >= 0)` — `execution_price_nonnegative`): futures have traded/settled negative in real markets (WTI crude, CL, settled around -$37.63 on 2020-04-20), and this app targets futures brokers (Topstep). `quantity > 0` below is still correct and unaffected — direction lives in `side`, not price's sign. `CHECK (price <> 0)` — `execution_price_not_zero`, **added round-7 code review**: that same negative-price evidence doesn't extend to `price = 0` — $0 is essentially never a valid fill price and is almost certainly malformed data, unlike a real negative settlement |
| `contract_multiplier` | `NUMERIC(20,10)` | no, default 1 | point value per contract, stored per fill so a contract-spec change never rewrites old P&L. `CHECK (contract_multiplier > 0)` — `execution_contract_multiplier_positive`, added round-3 code review: it's the P&L multiplier, so 0 or negative would silently corrupt every derived trade, and `quantity`/`price` in the same constraints list already had this protection while this column didn't |
| `fees` | `NUMERIC(19,4)` | no, default 0 | total cost of this fill |
| `currency` | `VARCHAR(3)` | no | ISO 4217, applies to `fees` and derived P&L. `CHECK (currency <> '')` — `execution_currency_not_blank`, added round-5 code review: unlike `quantity`/`price`/`contract_multiplier` in the same constraints list, `currency` had no non-empty guard at all (no `CheckConstraint`, no `full_clean()` call site on this write path), so a money-bearing execution could be saved with `currency=""`, silently violating CLAUDE.md's "store currency with every amount" |
| `executed_at` | `TIMESTAMPTZ` | no | UTC in DB, rendered in `user.timezone` |
| `source` | `VARCHAR(8)` | no | `CHECK (source IN ('manual','import'))`. Derived from `SOURCE_CHOICES` the same way as `status` above (round-8 code review) — see that note |
| `raw_import_row_id` | `BIGINT FK → journal_rawimportrow` | yes | `ON DELETE SET NULL`; `NULL` for manual |
| `created_at` | `TIMESTAMPTZ` | no, `auto_now_add` | |

Constraints/indexes:

- `UNIQUE (user_id, broker, broker_account_label, broker_execution_id) WHERE
  broker_execution_id IS NOT NULL AND broker_execution_id != ''` — `execution_broker_dedupe`.
  **Widened from 3 to 4 columns in migration `0003` (ADR-0004 §1)** so the same broker id in
  two accounts is not a duplicate; the `WHERE` is unchanged. Rolling `0003` back restores the
  3-column index and fails if rows differing only by label exist. The `EXPLAIN` note below
  was measured on the 3-column form; not re-measured on the 4-column one. This *is* idempotent import; no
  importer-side locking needed. Verified with `EXPLAIN`: a lookup by
  `(user_id, broker, broker_execution_id)` used this index directly (`Index Scan using
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
| `opening_execution_id` | `BIGINT FK → journal_execution`, `UNIQUE` | no | `ON DELETE RESTRICT` (not `CASCADE`). `OneToOneField` — this *is* the trade id. **Changed in code review**: `CASCADE` let a trade correction (delete-old-execution + insert-new-execution, per ADR-0003 §4) silently destroy the note + `rules_followed` flag with no recovery. `RESTRICT` raises `RestrictedError` on a standalone `execution.delete()` while a `JournalEntry` still points at it, forcing the correction code to explicitly re-point (`UPDATE opening_execution_id`) or deliberately delete the entry first. `PROTECT` was considered and rejected: it raises unconditionally, which would also block full account deletion (ADR-0003's "one statement" guarantee) — `RESTRICT` specifically allows deletion when the protecting row is being deleted in the same cascade (verified: `user.delete()` still removes the execution and its journal entry together in one call). **Test gap closed round-8**: until then, no test exercised a *standalone* `execution.delete()` while a `JournalEntry` still references it — the only delete test covered the `user.delete()` cascade path, where `RESTRICT` never actually fires (it's the same operation deleting both rows together). `test_standalone_execution_delete_is_restricted_by_journal_entry` covers the other path directly: `execution.delete()` on its own raises `RestrictedError`, and neither row is touched. |
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
  longer existed even at that point, and doesn't now either (see "Migration history" below
  for the full squash history). The drop is simply baked into `0001_initial.py`, which never
  creates the index at all, rather than living in its own migration.

## Tenant isolation

Every table above except `accounts_user` inherits `journal.models.UserOwned`: a plain
`user_id BIGINT NOT NULL FK → accounts_user ON DELETE CASCADE`, plus
`UserScopedManager.for_user(user)` as the one query chokepoint. `ON DELETE CASCADE` on every
FK to `user` means account deletion is one `DELETE FROM accounts_user WHERE id = ?`.

**`user` is `db_index=False` — round-8 code review.** A plain `ForeignKey` gets Django's
automatic single-column index by default; that's redundant on three of the four models,
which each already have an explicit composite index leading with `user`
(`execution_user_symbol_ts_idx`, `importbatch_user_uploaded_idx`,
`journalentry_user_flag_idx` — a leading-column btree already serves a plain `user_id = ?`
lookup just as well as a dedicated single-column index would). **`RawImportRow` is the
exception**: it has no composite index leading with `user` (its only other index is the
`(import_batch, line_number)` unique constraint, which doesn't help a plain `user_id`
filter) — ADR-0003's own index list never specified one here either, so it was silently
relying on the automatic FK index alone. Blindly applying `db_index=False` to the shared
abstract field would have left this one table with *no* index on `user_id` at all, a real
regression the round's premise didn't account for — caught by checking, not assumed.
Compensated with an explicit `models.Index(fields=["user"], name="rawimportrow_user_idx")`
on `RawImportRow` specifically. Verified with `EXPLAIN` on all four tables (`WHERE user_id
= ?`): every one uses an index scan, none fall back to a sequential scan —
`execution_user_ts_desc_idx`, `importbatch_user_uploaded_idx`, `journalentry_user_flag_idx`,
and the new `rawimportrow_user_idx` respectively.

**Hardened per code review** (isolation was previously opt-in — nothing stopped a call site
from using the unscoped default manager and leaking cross-user data): `UserScopedManager`'s
`get_queryset()` now raises `RuntimeError` unconditionally, so `Model.objects.all()`,
`.filter()`, `.get()`, etc. all fail loudly instead of silently returning every user's rows.
`.for_user(user)` bypasses the raise (it calls `super().get_queryset()` directly) and remains
the one sanctioned read path. `.create()` is the only other exemption: it isn't a read and
can't leak (the `user` FK is `NOT NULL` and always passed explicitly), so blocking it would
break the ordinary `Model.objects.create(user=..., ...)` idiom.

`get_or_create()`/`update_or_create()` are **not** overridden and fail closed
(`RuntimeError` from the raising `get_queryset()`). Hand-rolled versions leaked cross-tenant
rows through `defaults=` across three review rounds and were removed (round 9); the Topstep
importer will define its exact upsert, idempotent on `(user, broker, broker_execution_id)`,
when it is written.

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

**Guards `self.user_id is None` too — added round-7 code review**: without this, a row saved
without `user=` set compared a guarded FK's real owner against `self.user_id=None`, raising a
misleading `CrossTenantForeignKeyError` ("belongs to user_id=5, not this row's
user_id=None") instead of ever reaching the DB's own, correct `NOT NULL` violation on
`user_id`. The check now returns immediately when `self.user_id is None`, letting the real
constraint surface instead — confirmed live with a `JournalEntry(opening_execution=<valid
execution>)` saved with no `user`: raises a plain `IntegrityError` naming `user_id`, not
`CrossTenantForeignKeyError`.

**Not uniformly `CrossTenantForeignKeyError`** — corrected wording, round-6 code review: an
actual cross-tenant mismatch raises `CrossTenantForeignKeyError` (a `ValueError` subclass), but
a guarded FK pointing at a **nonexistent** row (a dangling/invalid id) surfaces as a plain
`IntegrityError` from the DB's own FK constraint at INSERT/UPDATE time instead — the lookup
comes back with no `user_id` to compare, so this check treats "no such row" as "not this
check's problem" and lets the DB's real FK constraint be the one to reject it. Not changed
(declined in both round 5 and round 6, same reasoning): the data is still protected either way,
just via a different exception type on that one path, and the fix would be a bigger design
change for no additional safety.

**Form/view handling — flagged, round-7 addendum, nothing built.** `CrossTenantForeignKeyError`
is raised only inside `save()`, so Django's `ModelForm.is_valid()`/`full_clean()` machinery
won't catch it — a future form-based edit that ends up with a cross-tenant-mismatched instance
would surface this as an unhandled 500, not a graceful form error. No form code exists yet in
this PR, so nothing is built for that here; the class's own docstring now carries a note for
whoever builds the edit forms later: catch this explicitly and translate it into a validation
error.

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
- `test_get_or_create_is_not_offered_on_the_scoped_manager` (round-9) asserts both methods
  raise `RuntimeError` on `Model.objects`.
- `test_owner_only_update_fields_save_still_checks_all_guarded_fks` (round-9) asserts
  `save(update_fields=["user"])` / `["user_id"]` re-checks every guarded FK (JournalEntry,
  RawImportRow, Execution) — an owner write moves what all FKs must match.
- `test_import_execution_requires_broker_execution_id` (round-9): `source='import'` with a
  NULL/blank id is rejected by `execution_import_requires_broker_execution_id`.
- Currency format tests (round-9): `usd`/`us`/`U1D`/`` rejected on `Execution.currency`,
  `JournalEntry.risk_currency`, `User.base_currency` (DB CHECK and field validator).
- `test_cross_tenant_check_skipped_when_user_id_is_none_lets_real_not_null_surface`
  (round-7) asserts a `JournalEntry` saved with no `user` raises a plain `IntegrityError`
  naming `user_id`, not a misleading `CrossTenantForeignKeyError`.
- `test_execution_price_zero_rejected` (round-7) asserts `IntegrityError` on `price=0`.
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
- `test_standalone_execution_delete_is_restricted_by_journal_entry` (round-8) creates a
  `JournalEntry` referencing an `Execution`, calls `execution.delete()` directly (not via
  `user.delete()`), and asserts `RestrictedError` — the delete-path test gap this round
  found: the existing cascade test never actually exercises `RESTRICT` firing.

`accounts/tests.py`: `test_get_by_natural_key_case_folding_is_db_side_not_python` (round-7)
constructs a user with a non-ASCII local part (bypassing `full_clean()`, same pattern as
`test_email_uniqueness_is_case_insensitive_at_db_level`) and asserts `get_by_natural_key`
still finds it with the exact stored email.
`test_get_or_create_and_update_or_create_are_not_supported` (round-8, replaces four
round-7 tests that exercised the since-removed hand-rolled implementation — see the
`email` column note above) asserts both raise `NotImplementedError` and that nothing is
created.

`config/tests.py` (new, round-5) covers `SECRET_KEY`'s fail-closed logic — necessarily via
subprocess, since it's import-time settings behavior that can't be re-exercised once a test
process has already imported settings once: `test_secret_key_required_when_debug_false_and_unset`,
`test_secret_key_falls_back_to_dev_default_when_debug_true`,
`test_secret_key_from_env_used_when_debug_false`, plus (round-7 addendum)
`test_secret_key_rejects_the_env_example_placeholder_when_debug_false` /
`test_secret_key_placeholder_falls_back_to_dev_default_when_debug_true`, plus (round-8)
`test_secret_key_rejects_django_insecure_prefix_when_debug_false` /
`test_secret_key_django_insecure_prefix_falls_back_to_dev_default_when_debug_true` /
`test_secret_key_real_value_not_starting_with_django_insecure_still_boots`.

### Migration history

`journal`'s migrations were squashed to a single `0001_initial.py` twice: once in round-4
code review, and again in a **final pre-merge squash** after round-6 landed (this note
reflects that second, current squash — the round-4 note above is kept for history but is no
longer the live state).

Between the two squashes, `journal` picked up `0002` (round-5: `default_manager_name` +
manager changes) and `0003` (round-6: dropped `execution_price_nonnegative`, widened
`execution_broker_dedupe`'s exemption to empty string as well as `NULL`) — three migrations
total, none of which had ever been applied to a real/shipped database on this branch, so
there was no reason to carry any of that churn into permanent history. Squashed back down to
one `0001_initial.py` reflecting the final schema: reset the local dev DB (`docker compose
down -v` — this session's own container, no real data), deleted `0001`–`0003`, and
regenerated a fresh `0001_initial.py` from the current models.

Verified after this final squash: fresh `migrate` from zero applies `0001_initial.py`
cleanly (including all of round 6's changes), `makemigrations --check --dry-run` reports no
changes, the full test suite still passes (25/25), and `psql \d` on both `journal_execution`
and `journal_journalentry` confirms the final constraint set landed correctly — notably
`execution_broker_dedupe`'s widened `WHERE broker_execution_id IS NOT NULL AND NOT
(broker_execution_id = '' AND broker_execution_id IS NOT NULL)`, no
`execution_price_nonnegative`, `execution_currency_not_blank` present, and
`journalentry_risk_currency_required_with_amount`'s blank-string exclusion present.
`accounts` was not touched either time — still a single migration, no churn to squash.

That was intended to be the last squash before merge, but round-7 added one more
`CheckConstraint` (`execution_price_not_zero`) after it — per explicit instruction, folded
directly into the existing `0001_initial.py` rather than adding a new migration file (same
"nothing has shipped to a real DB yet" rule as every squash above), using the same
reset-dev-DB-and-regenerate mechanism. `journal` and `accounts` still have exactly one
migration each; round-7's constraint is simply already inside `0001_initial.py`, never its own
file. Verified the same way as every prior squash: fresh `migrate` from zero, `makemigrations
--check --dry-run` clean, full test suite passing (37/37), `psql \d journal_execution`
confirms `execution_price_not_zero` present.

Round-8 did the same fold again: `execution_price_not_zero`'s removal of `db_index=True` on
`user`, the new `rawimportrow_user_idx`, and the `Length()`-based `raw_file` constraint all
landed inside the same `0001_initial.py`, no new migration file, same reset-and-regenerate
mechanism. Verified identically: fresh `migrate` from zero, `makemigrations --check
--dry-run` clean, full test suite passing (38/38), `psql \d` on all four `journal_*` tables
confirms the final shape — notably `importbatch_raw_file_size_limit` now reads
`CHECK (length(raw_file) <= 10485760)` (was `octet_length(...)` via `RawSQL`), and `EXPLAIN`
on a plain `WHERE user_id = ?` against all four tables shows an index scan, not a
sequential scan, confirming the `db_index=False` change didn't quietly regress anything.

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
- **Round-7 addendum**: emptiness wasn't the only bad `SECRET_KEY` value. `.env.example`
  shipped `DJANGO_SECRET_KEY=dev-only-change-me` — a value that looks real enough that
  someone copying `.env.example` to `.env` could miss updating it, and the round-5 check only
  verified the var was non-empty, not that it differed from the known placeholder. If ops set
  `DEBUG=false` and never touched that line, the app would boot in "production mode" using a
  key that's public in this repo's git history. Fixed two ways: renamed the placeholder to
  something unmistakably fake (`DJANGO_SECRET_KEY=CHANGE-ME-run-get_random_secret_key`), and
  added an explicit check in `settings.py` that rejects that exact known placeholder value the
  same way it rejects emptiness — `DEBUG=False` with `DJANGO_SECRET_KEY` still set to the
  placeholder raises `ImproperlyConfigured`, same as if it were unset; `DEBUG=True` still
  falls back to the dev default either way. Verified via subprocess in `config/tests.py`, same
  pattern as the empty-value tests.
- **Round-8 code review**: broadened again — the one-exact-string check missed the more
  realistic leftover-default scenario. Any value starting with `"django-insecure-"` (Django's
  own `startproject` default-key prefix) is now rejected the same way when `DEBUG=False`;
  someone who never touches `SECRET_KEY` at all and copies the auto-generated
  `startproject` value into prod is more likely than someone leaving the literal
  `CHANGE-ME` placeholder in place. Verified via subprocess in `config/tests.py`: the
  prefix is rejected under `DEBUG=False`, still falls back under `DEBUG=True`, and a real
  key not matching either bad pattern still boots normally.
- Root-cause fixed, not silenced (**round-8 code review**): `models.W045` used to be in
  `SILENCED_SYSTEM_CHECKS` because `ImportBatch.raw_file`'s size CHECK used `RawSQL`, which
  Django's checker can't introspect. Replaced with `Length()` (see the `raw_file` column note
  above) — an ORM expression Django *can* verify — so the warning no longer fires at all.
  `SILENCED_SYSTEM_CHECKS` now holds only `auth.E003` (still a genuine false positive, not
  worth a custom system check for one remaining item).
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


## Round-9 additions (migrations `journal/0002`, `accounts/0002`)

- Every currency column has `CHECK (col ~ '^[A-Z]{3}$')` (`execution_currency_iso_format`,
  `journalentry_risk_currency_iso_format`, `accounts_user_base_currency_iso_format`) plus a
  `RegexValidator` (`accounts.models.validate_currency_code`) for the `full_clean()` path.
- `execution_import_requires_broker_execution_id`: `CHECK (source <> 'import' OR
  (broker_execution_id IS NOT NULL AND broker_execution_id <> ''))`. Closes the hole where
  the partial unique index `execution_broker_dedupe` exempted blank ids, so an import row
  without one was never deduped.
- Rollback: `migrate journal 0001` and `migrate accounts 0001` (constraints and validators
  only; no data rewrite). Verified up/down/up on the dev DB.
