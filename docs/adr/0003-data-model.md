# ADR-0003: Core data model (executions, derived trades, journal)

- **Status:** Accepted; amended by [ADR-0004](0004-topstep-dedupe-and-pairing.md) (dedupe key, `broker_trade_id`, `skipped_conflict`, matcher grouping)
- **Date:** 2026-09-22
- **Deciders:** architect (proposed), user (to approve)
- **Depends on:** [ADR-0002](0002-stack-revised.md) (Django + Postgres, `NUMERIC` money, compute-on-read,
  `for_user()` chokepoint, RLS deferred), [`docs/product/features/mvp.md`](../product/features/mvp.md)
- **Blocks:** `database-engineer` (DDL, migrations, indexes → `docs/data/`), `backend-engineer`
  (importer, stats)

## Context

ADR-0002 follow-up 3 asked for the data model and required it to confirm three things: the
compute-on-read position, the `NUMERIC` column rules, and a plain `user_id` on every user-owned
table so RLS stays a one-migration change. All three are confirmed below.

The MVP needs seven things to exist: a user (email login, timezone, one free-text rules block),
manual trade entry, Topstep CSV import that is idempotent and keeps raw data, executions as the
source of truth, trades derived from them, one journal entry per trade, and a filtered trade list
with three stats.

### The one hard constraint that shapes everything

`trading-domain-expert` is **still writing** `docs/domain/` (fill→trade matching algorithm, Topstep
CSV columns, R-multiple inputs, win-rate tie-breaking — the four open questions in `mvp.md`). That
directory is empty at the time of writing. So this model must be **matcher-agnostic**: it stores the
fields any FIFO/LIFO/average-cost matcher needs and takes no position on which one runs. Every place
this ADR had to guess ahead of that spec is marked **[GUESS]** and listed again under
"Assumptions made ahead of `docs/domain/`".

### Volume (from ADR-0002, unchanged)

Thousands of executions per user, tens to low hundreds of users. A single user's entire execution
history is a few megabytes. This is the justification for compute-on-read, and the number to
re-check before anyone materializes anything.

## Decision

Six tables. Two Django apps: `accounts` (user) and `journal` (everything else).

```
accounts_user ──┬── journal_importbatch ──< journal_rawimportrow ──? journal_execution
                ├──< journal_execution ────────────────────────────────┐
                └──< journal_journalentry ──1:1── (opening execution) ─┘

trades = f(executions)   -- a pure function, not a table
```

### 1. `accounts_user` — custom user model from day one

Swapping `AUTH_USER_MODEL` later is the single most painful migration in Django, and we already know
we need email login, a timezone, and the rules blob. Subclasses `AbstractUser` with `username`
removed.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `email` | `CITEXT NOT NULL UNIQUE` | `USERNAME_FIELD`. `CITEXT` so `A@x.com` can't register twice (needs the `citext` extension; a `UNIQUE` index on `lower(email)` is an acceptable substitute) |
| `password` | `VARCHAR(128) NOT NULL` | Django PBKDF2 hash |
| `timezone` | `VARCHAR(64) NOT NULL DEFAULT 'UTC'` | IANA name, validated against `zoneinfo.available_timezones()` |
| `base_currency` | `CHAR(3) NOT NULL DEFAULT 'USD'` | ISO 4217; display default only, never overrides an amount's own currency |
| `trading_rules` | `TEXT NOT NULL DEFAULT ''` | **the entire "my rules" feature** (story 5) |
| `is_active`, `is_staff`, `is_superuser`, `last_login`, `date_joined` | Django defaults | |

`trading_rules` is a column, not a table. "Exactly one free-text field per user, no structure"
(story 5) is a column. No versioning: story 5's only history requirement — editing the rules must
not retroactively change past yes/no flags — is satisfied for free because the flag is stored on the
journal entry, not computed from the rules text.

### 2. `journal_importbatch` — one row per uploaded file

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `user_id` | `BIGINT NOT NULL REFERENCES accounts_user` | |
| `broker` | `VARCHAR(32) NOT NULL` | `'topstep'` today. Plain string, no broker table |
| `filename` | `VARCHAR(255) NOT NULL` | as uploaded, for the user's benefit |
| `file_sha256` | `CHAR(64) NOT NULL` | informational: lets the UI say "you uploaded this file before". **Not** the dedupe key and **not** unique |
| `raw_file` | `BYTEA NOT NULL` | the uploaded bytes, verbatim |
| `uploaded_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `row_count`, `imported_count`, `skipped_count`, `failed_count` | `INTEGER NOT NULL DEFAULT 0` | the result summary story 3 must display |

`raw_file` holds the original bytes so parsing can be re-run even if the **row splitting** or the
encoding guess was wrong — which the per-row table below cannot recover from. Files are tens of KB;
the duplication with `rawimportrow` is deliberate and cheap. No `FileField`/object storage: a
`BYTEA` column is backed up by the same `pg_dump` as everything else, with no second storage system
to secure or scope per user.

### 3. `journal_rawimportrow` — verbatim rows, re-parseable

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `user_id` | `BIGINT NOT NULL REFERENCES accounts_user` | denormalized for `for_user()` + RLS |
| `import_batch_id` | `BIGINT NOT NULL REFERENCES journal_importbatch ON DELETE CASCADE` | |
| `line_number` | `INTEGER NOT NULL` | 1-based, as in the file |
| `raw` | `JSONB NOT NULL` | the CSV row as `{header: cell}`, strings only, **no coercion** |
| `status` | `VARCHAR(20) NOT NULL` | `imported` / `skipped_duplicate` / `failed` (+ `skipped_conflict`, ADR-0004) |
| `error` | `TEXT NOT NULL DEFAULT ''` | why it failed, shown to the user (story 3: never silently dropped) |

`UNIQUE (import_batch_id, line_number)`.

`raw` is `JSONB` of strings, not typed columns: it is evidence, not data. Coercion happens in the
parser, so a parser fix can be re-run over stored rows. This is the table the "rows failed with
reasons" UI reads.

### 4. `journal_execution` — the source of truth

Immutable by convention: append-only, never `UPDATE`d. Correcting a manual entry deletes and
recreates its executions; imported executions are never edited (re-import instead).

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `user_id` | `BIGINT NOT NULL REFERENCES accounts_user` | |
| `broker` | `VARCHAR(32) NOT NULL` | `'topstep'`, or `'manual'` for hand entry |
| `broker_execution_id` | `VARCHAR(128) NULL` | broker's fill/trade id; `NULL` for manual entry |
| `broker_account_label` | `VARCHAR(64) NOT NULL DEFAULT ''` | the account/whatever string the export carries, kept verbatim. **[GUESS]** — no `account` table; see below |
| `symbol` | `VARCHAR(32) NOT NULL` | as the broker wrote it (`MNQZ5`), uppercased. No instrument table |
| `side` | `VARCHAR(4) NOT NULL CHECK (side IN ('buy','sell'))` | a fill is buy/sell. long/short is a property of the *derived* trade |
| `quantity` | `NUMERIC(20,10) NOT NULL CHECK (quantity > 0)` | always positive; direction lives in `side` |
| `price` | `NUMERIC(20,10) NOT NULL CHECK (price >= 0)` | |
| `contract_multiplier` | `NUMERIC(20,10) NOT NULL DEFAULT 1` | point value per contract (MNQ = 2, ES = 50). Stored per fill so a contract-spec change can never retroactively rewrite old P&L. `1` is correct for equities. **[GUESS]** |
| `fees` | `NUMERIC(19,4) NOT NULL DEFAULT 0` | total cost of this fill (commission + exchange + clearing), positive = charged. **[GUESS]**: one column, not a breakdown |
| `currency` | `CHAR(3) NOT NULL` | ISO 4217, applies to `fees` and to P&L derived from this fill |
| `executed_at` | `TIMESTAMPTZ NOT NULL` | UTC in the DB (`USE_TZ = True`), rendered in `user.timezone` |
| `source` | `VARCHAR(8) NOT NULL CHECK (source IN ('manual','import'))` | |
| `raw_import_row_id` | `BIGINT NULL REFERENCES journal_rawimportrow ON DELETE SET NULL` | provenance; `NULL` for manual |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

**Idempotent import is this one index** (superseded: ADR-0004 adds `broker_account_label` to the key):

```sql
CREATE UNIQUE INDEX execution_broker_dedupe
  ON journal_execution (user_id, broker, broker_execution_id)
  WHERE broker_execution_id IS NOT NULL;
```

Partial, so manual entries (`NULL` id) are exempt. `user_id` is in the key both because two users'
brokers may hand out colliding ids and because it aligns the index with every scoped query. The
importer's job is then trivial: pre-fetch the user's existing `broker_execution_id`s for that broker,
diff in Python to produce accurate imported/skipped counts, insert the rest. The index is the
guarantee (and the race protection), the diff is only for the counts.

Fields the matcher gets: instrument, side, quantity, price, timestamp, fees, multiplier, currency.
That is the union of what FIFO, LIFO, and average-cost all need. No column here assumes which one runs.

### 5. No `trade` table — trades are a pure function of executions

```python
# journal/matching.py  — signature only; the algorithm is docs/domain/'s call
def derive_trades(executions: Sequence[Execution]) -> list[Trade]: ...
```

`Trade` is a frozen dataclass, never a row: `opening_execution_id`, `symbol`, `direction`
(long/short), `quantity`, `avg_entry_price`, `avg_exit_price | None`, `opened_at`, `closed_at | None`,
`gross_pnl`, `fees`, `net_pnl`, `currency`, `is_open`, `execution_ids: list[int]`.

**Confirming ADR-0002's compute-on-read:** no `trade` table, no triggers, no jobs, nothing to keep in
sync. Read path for the trade list and the three stats:

1. `Execution.objects.for_user(request.user).order_by("symbol", "executed_at")` — the user's whole
   history, or a date-bounded superset.
2. `derive_trades()` in Python.
3. Filter, sort, paginate, and aggregate the resulting list.

Filtering and sorting happen **after** matching, not in SQL, because a trade's P&L and direction
don't exist until the fills are paired. A date filter must not be pushed into the SQL `WHERE` naively
— it would cut a trade's fills in half; bound the query generously (or by symbol), match, then filter
trades by `closed_at`.

<!-- ponytail: loads the user's full execution history per page view. Ceiling ~tens of thousands of
     executions per user; at ~5k fills this is single-digit ms of Python. Upgrade path is ADR-0002's:
     index, then materialize a trade table at write time, then a worker — each gated on a measured
     >300ms dashboard, not on a hunch. -->

Why not materialize now, given ADR-0002 explicitly allows it as step 2: a materialized `trade` table
buys nothing at this volume and costs the one thing we can't test away — a second source of truth
that can silently disagree with the executions. It also has to be rebuilt every time
`docs/domain/`'s matching algorithm changes, and that algorithm **is not written yet**. Materializing
a derivation whose definition is still in flux is the textbook premature optimization. Revisit on
measurement.

Mixed currencies: stats are computed **grouped by currency** and rendered as one block per currency
(Topstep users will have exactly one). No FX conversion, per ADR-0002.

### 6. `journal_journalentry` — one per trade, keyed by the opening execution

The identity problem: a derived trade has no database id, but a journal entry has to attach to
something stable. It attaches to the **execution that opened the trade** — the one fact about a trade
that no matcher can invent or reassign without the underlying fill changing.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `user_id` | `BIGINT NOT NULL REFERENCES accounts_user` | |
| `opening_execution_id` | `BIGINT NOT NULL UNIQUE REFERENCES journal_execution ON DELETE CASCADE` | = the trade id. `OneToOneField` |
| `note` | `TEXT NOT NULL DEFAULT ''` | optional reasoning (story 4) |
| `rules_followed` | `BOOLEAN NULL` | `NULL` = **not yet answered**. See below |
| `stop_price` | `NUMERIC(20,10) NULL` | R-multiple input, optional. **[GUESS]** |
| `planned_risk_amount` | `NUMERIC(19,4) NULL` | R-multiple input, optional. **[GUESS]** |
| `risk_currency` | `CHAR(3) NULL` | required iff `planned_risk_amount` is set (`CHECK`) |
| `created_at`, `updated_at` | `TIMESTAMPTZ NOT NULL` | |

`rules_followed` is nullable and has **no default**, which is exactly story 4's requirement that no
default silently counts as an answer. "Journaled" is defined as `rules_followed IS NOT NULL` — one
SQL predicate, so story 6's yes/no/not-journaled filter is free, and a user can save a note inline
without being forced to answer the flag in the same keystroke (journaling-friction principle). This
is a deliberate reading of story 4's "required": required *to count as journaled*, not required *to
save a row*.

The two risk columns live here because this is already the per-trade user-annotation table with the
same key; a separate table for two nullable numbers would be a join for nothing. Open question 1 in
`mvp.md` will pick stop-distance **or** risk-amount; both are nullable, and deleting the loser is a
one-line migration.

If a re-import ever shifts which fill opens a trade, the affected entries become orphaned rather than
silently mis-attached — detectable (a journal entry whose `opening_execution_id` is not any derived
trade's opener) and repairable. That is the accepted cost of not having a trade table; it is strictly
better than the alternative failure mode, which is a note attached to the wrong trade.

### Tenant isolation — confirming ADR-0002

Every table except `accounts_user` carries a plain, non-null `user_id BIGINT` — including
`rawimportrow`, where it is denormalized rather than reached through `import_batch_id`, precisely so
an RLS policy can bite on it directly. So:

```sql
-- one migration, whenever the ADR-0002 trigger fires. No schema change needed.
ALTER TABLE journal_execution ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant ON journal_execution
  USING (user_id = current_setting('app.user_id')::bigint);
```

All four `journal` models inherit one abstract base:

```python
class UserOwned(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    objects = UserScopedManager()   # .for_user(user) -> filter(user=user)
    class Meta: abstract = True
```

`ON DELETE CASCADE` from `user` on every FK, so account deletion is one statement. The mandatory
isolation test enumerates `UserOwned.__subclasses__()` — so a new model is covered the moment it is
added, and a model that skips the base fails the test by omission.

### Index list (initial; `database-engineer` owns the final set)

| Table | Index | For |
|---|---|---|
| `execution` | `UNIQUE (user_id, broker, broker_account_label, broker_execution_id) WHERE broker_execution_id IS NOT NULL AND <> ''` (ADR-0004) | idempotent import |
| `execution` | `(user_id, symbol, executed_at)` | the matcher's read pattern |
| `execution` | `(user_id, executed_at DESC)` | date-bounded loads, recency |
| `rawimportrow` | `UNIQUE (import_batch_id, line_number)` | re-parse, error display |
| `importbatch` | `(user_id, uploaded_at DESC)` | import history |
| `journalentry` | `UNIQUE (opening_execution_id)` | one entry per trade |
| `journalentry` | `(user_id) WHERE rules_followed IS NULL` | "not journaled" filter |

### Money and quantity — confirming ADR-0002

Every money column is `NUMERIC(19,4)` and sits next to an ISO 4217 currency column (`fees`+`currency`,
`planned_risk_amount`+`risk_currency`). Every price/quantity column is `NUMERIC(20,10)`. No floats
anywhere: `DecimalField` ↔ `Decimal`, and derived P&L is computed in `Decimal` and rounded once,
explicitly, with a stated rounding mode. All timestamps are `TIMESTAMPTZ` stored UTC, rendered in
`user.timezone`.

## Alternatives considered

### A materialized `trade` table, maintained at write time — rejected for now

ADR-0002's own step 2, so it needs a reason, not a shrug. It would make P&L sortable in SQL and the
journal FK unambiguous. Rejected because (a) at thousands of fills per user the Python matcher is
milliseconds, (b) the matching algorithm it would materialize **is still being written**, so the
table would need a full rebuild on every spec revision, and (c) it introduces a second source of
truth that can drift from the executions — the exact failure the "executions are the source of truth"
rule exists to prevent. Trigger to revisit: a measured >300 ms dashboard on a realistic dataset.

### A `trade` table that users write to directly — rejected

Contradicts the domain rule outright. Named only because manual entry (story 2) makes it tempting:
the form *looks* like a trade. It writes two executions instead (an entry fill and an exit fill), and
the same matcher derives the trade. One code path for manual and imported data, so P&L can't differ
between them.

### `instrument` and `broker_account` tables — deferred

The normalized answer, and where this ends up once multi-account Topstep users or per-instrument
multipliers need managing. Skipped because MVP has no feature that reads them: `symbol` and
`contract_multiplier` on the fill are what P&L needs, and storing the multiplier per fill is
*more* correct than a lookup (contract specs change; historical P&L must not). `broker_account_label`
is kept verbatim so the data to backfill a real table exists whenever one is wanted.

### Tags / custom fields / EAV — deliberately not built

My standing brief says "design for schema evolution — users will want custom tags", and `mvp.md`
lists tagging as an explicit non-goal. Both are satisfied by building nothing: when v2 asks, it's
`tags TEXT[]` (or `JSONB` for custom fields) on `journalentry` with a GIN index — one migration, no
tables, no EAV. An EAV schema now would be three tables and a join for a feature nobody has scoped.

### Storing only raw rows, or only the raw file — rejected

Cheaper either way. Keeping just rows loses the ability to fix a bad row-split or encoding guess;
keeping just the file loses per-row error reporting and the execution→origin link. Both together are
one extra `BYTEA` column on a table with one row per upload.

### Integer surrogate keys vs UUIDs — `BIGSERIAL`

UUIDs would stop id-guessing, but tenant isolation is not allowed to depend on unguessable ids (that
is the `for_user()` chokepoint's job, and story 1 tests it), and `BIGSERIAL` indexes better. Not a
security decision.

### `journal_entry` keyed by a synthetic `trade_key` hash — rejected

A deterministic hash of (user, symbol, opening broker execution id) would survive a database rebuild.
Rejected: it is a hand-rolled key that still breaks under exactly the same re-import shift, while
giving up FK integrity and `ON DELETE CASCADE`. A FK to the opening execution is the same idea with
the database enforcing it.

## Consequences

### Positive

- Confirms all three of ADR-0002's asks: compute-on-read (no trade table), `NUMERIC` money/price
  rules, `user_id` on every user-owned table.
- One source of truth, enforced structurally: there is no other table P&L could be read from.
- Idempotent import is a single partial unique index, not importer logic that can be forgotten.
- Manual and imported trades share one code path end to end.
- Nothing in the schema commits to a matching algorithm, so `docs/domain/` can land FIFO, LIFO, or
  average-cost without a migration.
- Six tables, no join tables, no lookup tables, no EAV. `database-engineer` can write the DDL from
  the tables above without interpreting prose.
- RLS is one DDL migration whenever ADR-0002's trigger fires.

### Negative / accepted risks

- **Trade identity is derived, so it can shift.** A backfilled earlier fill can change which
  execution opens a trade and orphan its journal entry. Mitigated by detectability (orphans are
  queryable) and by the alternative being worse (a note silently attached to the wrong trade). Add an
  orphan check to the trade list when a real import produces one.
- **Sorting and filtering happen in Python, not SQL.** Correct, since P&L doesn't exist before
  matching, but it means pagination loads more than one page's worth. Named ceiling and upgrade path
  in the comment above.
- **`contract_multiplier` is a guess.** If Topstep's export carries point value or per-contract P&L
  directly, this column may be redundant — or wrong if the importer defaults it to 1 for futures.
  Blocked on open question 2.
- **One `fees` column, not a breakdown.** If a user wants commission separated from exchange fees,
  that is a column split later. Total fees is what net P&L needs.
- **`rules_followed` nullable** means "required" is enforced in the UI and in the definition of
  journaled, not by `NOT NULL`. Deliberate; see above.
- **Raw file bytes live in Postgres**, growing the backup. At tens of KB per import this is noise;
  move to object storage if someone uploads yearly exports daily.
- **No `account` dimension.** A Topstep user trading two funded accounts sees them merged in one
  trade list, and — worse — the matcher may pair a fill from one account against another. If the
  export carries an account column, revisit before release: matching must group by
  `(symbol, broker_account_label)`, which is a matcher change, not a schema change. **Resolved by ADR-0004.**
- **Two R-multiple columns where the spec will want one.** Costs a one-line migration to clean up.

## Assumptions made ahead of `docs/domain/`

`docs/domain/` was empty when this was written. Each item is a **[GUESS]** above; each is a column
change at worst, not a redesign.

1. **`contract_multiplier` on the execution** — assumed futures P&L needs a point value and that it
   must be stored per fill, not looked up. Defaults to 1 (correct for equities).
2. **A single `fees` column per fill** — assumed the matcher wants total cost per fill, not a
   commission/exchange/clearing breakdown.
3. **`side` is `buy`/`sell` on the fill; long/short is a property of the derived trade** — assumed,
   because a fill has no direction of its own. If Topstep exports already-closed trades with a
   long/short column, the importer maps it to two fills.
4. **Topstep may export closed trades rather than raw fills** (open question 2). Accommodated without
   a schema change: the importer synthesizes two executions per row with derived ids
   (`<broker_trade_id>:entry`, `<broker_trade_id>:exit`), which keeps them unique and keeps the
   dedupe index working. `database-engineer` and `backend-engineer` should not need to change the
   schema whichever way the answer lands.
5. **R-multiple inputs are `stop_price` and/or `planned_risk_amount` on the journal entry**
   (open question 1), both optional; trades without them are excluded from the average and reflected
   in its sample size, per `mvp.md`.
6. **`broker_account_label` exists in the export.** (Wrong for Topstep: no account column; see ADR-0004 open question 1.) Stored verbatim, unused by MVP. Empty string if
   the export has no such column.
7. **Win-rate tie-breaking (open question 3) needs no schema support** — it is a predicate over
   derived `net_pnl`. Confirmed safe to leave to `docs/domain/`.

## Follow-ups

1. `database-engineer`: DDL + initial migrations + the index list above; data dictionary in
   `docs/data/`. Postgres-specific types are fair game (ADR-0002 follow-up 4). Needs the `citext`
   extension, or swap to a `lower(email)` unique index.
2. `backend-engineer`: `UserOwned` base + `UserScopedManager.for_user()`, the mandatory isolation
   test over `UserOwned.__subclasses__()`, and `derive_trades()` as a pure function in
   `journal/matching.py` — implementing `docs/domain/`'s algorithm and its test vectors, not
   inventing one.
3. `trading-domain-expert`: the seven assumptions above are the review list. Items 1–4 are the ones
   that could cost a column.
4. Revisit materialization only on a **measured** >300 ms dashboard, per ADR-0002.
