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
| `email` | `VARCHAR(254)` | no | `USERNAME_FIELD`. Case-insensitive uniqueness via `UNIQUE (lower(email))`, **not** the `CITEXT` extension — ADR-0003 §1 names this as an acceptable substitute. Django's `auth.E003` check doesn't recognize expression-based `UniqueConstraint`s, so it's silenced in `config/settings.py` with a comment; the DB guarantee is unaffected and is *stricter* than what the check looks for. |
| `password` | `VARCHAR(128)` | no | Django PBKDF2 hash |
| `timezone` | `VARCHAR(64)` | no, default `'UTC'` | IANA name, validated against `zoneinfo.available_timezones()` at the Python/form layer (no DB-level check — the set changes with tzdata updates) |
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
| `raw_file` | `BYTEA` | no | uploaded bytes verbatim, so a bad row-split/encoding guess is recoverable. **Size-capped per code review** at `MAX_RAW_FILE_BYTES = 10 MiB` (ADR-0003 assumes "tens of KB"; 10 MiB is a generous sanity/abuse guard, not a tight limit). Enforced two ways: `validate_raw_file_size` (Python validator, runs on `full_clean()`) and a DB `CHECK (octet_length(raw_file) <= 10485760)` — `importbatch_raw_file_size_limit` — as the backstop for writes that skip `full_clean()` (e.g. a plain `.create()`). The DB constraint uses `RawSQL`, so Django's `models.W045` check (silenced in `config/settings.py`) correctly notes it isn't pre-validated by `full_clean()` itself; the Python validator covers that path instead. |
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
| `raw` | `JSONB` | no | `{header: cell}`, strings only, no coercion |
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
| `broker_execution_id` | `VARCHAR(128)` | yes | `NULL` for manual entry |
| `broker_account_label` | `VARCHAR(64)` | no, default `''` | verbatim from export; no account table yet |
| `symbol` | `VARCHAR(32)` | no | uppercased, as broker wrote it |
| `side` | `VARCHAR(4)` | no | `CHECK (side IN ('buy','sell'))` |
| `quantity` | `NUMERIC(20,10)` | no | `CHECK (quantity > 0)`. Always positive; direction lives in `side` |
| `price` | `NUMERIC(20,10)` | no | `CHECK (price >= 0)` |
| `contract_multiplier` | `NUMERIC(20,10)` | no, default 1 | point value per contract, stored per fill so a contract-spec change never rewrites old P&L |
| `fees` | `NUMERIC(19,4)` | no, default 0 | total cost of this fill |
| `currency` | `VARCHAR(3)` | no | ISO 4217, applies to `fees` and derived P&L |
| `executed_at` | `TIMESTAMPTZ` | no | UTC in DB, rendered in `user.timezone` |
| `source` | `VARCHAR(8)` | no | `CHECK (source IN ('manual','import'))` |
| `raw_import_row_id` | `BIGINT FK → journal_rawimportrow` | yes | `ON DELETE SET NULL`; `NULL` for manual |
| `created_at` | `TIMESTAMPTZ` | no, `auto_now_add` | |

Constraints/indexes:

- `UNIQUE (user_id, broker, broker_execution_id) WHERE broker_execution_id IS NOT NULL` —
  `execution_broker_dedupe`. This *is* idempotent import; no importer-side locking needed.
  Verified with `EXPLAIN`: a lookup by `(user_id, broker, broker_execution_id)` uses this
  index directly (`Index Scan using execution_broker_dedupe`).
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
| `risk_currency` | `VARCHAR(3)` | yes | required iff `planned_risk_amount` is set |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | no | `auto_now_add` / `auto_now` |

Constraints/indexes:

- `CHECK`: `risk_currency` is set if and only if `planned_risk_amount` is set
  (`journalentry_risk_currency_required_with_amount`).
- `(user_id, rules_followed)` — `journalentry_user_flag_idx`. Covers all three of story 6's
  rule-followed filter states in one index: yes, no, and "not journaled"
  (`WHERE rules_followed IS NULL`, which a composite btree serves directly on the second
  column — verified with `EXPLAIN`: `Index Scan using journalentry_user_flag_idx ... Index
  Cond: ((user_id = 1) AND (rules_followed IS NULL))`).

  ADR-0003's index list separately specified `(user_id) WHERE rules_followed IS NULL` as its
  own partial index. Built initially per the ADR, then **dropped per code review**
  (`journalentry_not_journaled_idx`, removed in `journal/migrations/0005_...`): it was
  redundant once `journalentry_user_flag_idx` existed to cover the yes/no states, since the
  composite index serves the NULL case just as well (confirmed above), and this table is one
  row per trade — cheap either way, but no reason to carry two indexes for one query shape.

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
the one sanctioned read path. `.create()` is separately exempted on the manager — it isn't a
read and can't leak (the `user` FK is `NOT NULL` and always passed explicitly), so blocking it
would break the ordinary `Model.objects.create(user=..., ...)` idiom.

Each `UserOwned` subclass also gets a second manager, `unscoped` (a plain
`models.Manager()`), and sets `Meta.base_manager_name = "unscoped"`. This is the deliberate
escape hatch: Django's internals (the deletion collector, which `user.delete()`'s cascade
relies on) use `_base_manager`, which now resolves to the unrestricted manager instead of the
raising one — without this, hardening `objects` would have silently broken account deletion.
Verified directly: `user.delete()` still removes the user's `ImportBatch`, `RawImportRow`,
`Execution`, and `JournalEntry` rows in one call.

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

## Known gaps (not this task's scope)

- No `Meta.db_table` overrides needed — Django's default naming
  (`<app_label>_<model>` lowercase) already matches ADR-0003's table names exactly.
- `journal/matching.py` (`derive_trades()`) does not exist yet — `backend-engineer`'s task,
  per ADR-0003 follow-up 2.
- No admin registration, views, forms, or API — explicitly out of scope for this task.
