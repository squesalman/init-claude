# ADR-0004: Topstep import: per-account dedupe key and per-row trade pairing

- **Status:** Accepted
- **Date:** 2026-09-26
- **Deciders:** architect (proposed), user (to approve)
- **Amends:** [ADR-0003](0003-data-model.md) §4 (dedupe index, one new column), §3 (one new
  status value), §5 (matcher grouping key)
- **Evidence:** [`docs/domain/topstep-import.md`](../domain/topstep-import.md) §8 (real 177-row
  export, checked by script, no rows reproduced)
- **Blocks:** `database-engineer` (migration spec in [`docs/data/follow-ups.md`](../data/follow-ups.md)
  row 6), `backend-engineer` (importer + `derive_trades()`)

Separate ADR rather than an in-place edit: it adds a column, a status value, and a matcher rule,
which is too much to hide inside ADR-0003's text.

## Context

The domain expert's check of the real TopstepX export found two problems with ADR-0003 as written.

1. **Dedupe key.** `execution_broker_dedupe` is `(user_id, broker, broker_execution_id)`. The
   export has no account column, so we cannot prove TopstepX `Id`s are unique across a user's
   accounts. If they are per-account, a second account's fills are silently dropped as
   `skipped_duplicate`.
2. **Matching.** `topstep-import.md` §5 claimed FIFO over the synthesized executions gives one
   trade per row. It does not. The sample has 12 same-direction, time-overlapping row pairs within
   one contract (11 fully nested). Under `pnl-and-matching.md` §1, a trade runs flat to flat. So FIFO
   **merges** a nested pair into one trade with two re-paired lots (the outer entry pairs with the
   inner exit). The batch total is unchanged. What changes is the trade count, per-trade P&L,
   durations, and which execution opens the trade (the journal-entry key).

   Small correction to the domain doc's framing: it describes this as "re-pairing" only. The
   bigger effect is **fewer trades than rows**. That matters for the overtrading detectors
   (trade counts) as much as for per-trade P&L.

## Decision

### 1. Dedupe key includes the account label: confirmed

```sql
CREATE UNIQUE INDEX execution_broker_dedupe
  ON journal_execution (user_id, broker, broker_account_label, broker_execution_id)
  WHERE broker_execution_id IS NOT NULL AND broker_execution_id <> '';
```

(The `<> ''` term is the existing round-6 condition. It is kept unchanged. §8 of the domain doc
left it out, and that omission is not intended.)

Why I agree with the domain expert: the two failure modes are not symmetric. If the label is left
out, the cost is silent, unrecoverable loss of real fills. If the label is included, the cost is a
visible duplicate batch, and only when the user types a different label for the same file. The
user fixes that by deleting the batch. `broker_account_label` is `NOT NULL DEFAULT ''`, so blank
labels compare equal in the index (they are not NULL-distinct). Every existing row and every
blank-label import keeps exactly today's behaviour.

- **R10 CHECK `execution_import_requires_broker_execution_id`:** unaffected. It constrains only
  the id, and the label may stay `''`. No change.
- **Importer pre-fetch diff** uses the same four fields as the index.
- **Label value:** stored trimmed, case kept, never NULL. Case is not normalised silently.

### 2. Collision guard: confirmed, with a new row status `skipped_conflict`

This guard applies per CSV row. For Topstep, one row means two executions.

| Existing executions for this row's key(s) | Row status | Executions written |
|---|---|---|
| none | `imported` | both |
| both exist, and `symbol`, `side`, `quantity`, `price`, `executed_at` all equal | `skipped_duplicate` | none |
| any leg exists but a field differs, or only one leg exists | `skipped_conflict` | **none** |

A `skipped_conflict` row sets `error` to text shown to the user, for example: "Id already imported
with different contents. If this is a second account, fill in the Account field and re-upload."

**Why a new status and not "`skipped_duplicate` + non-blank `error`":** the row list and the
summary filter on one column. A state encoded across two fields is how "never silently dropped"
(story 3) gets broken later. `'skipped_conflict'` is 16 characters and fits the existing
`VARCHAR(20)`. `rawimportrow_status_valid` derives its values from `_STATUS_CHOICES`, so the change
is one tuple entry plus a regenerated CHECK. `ImportBatch` gets **no new counter**. Conflicts count
toward `skipped_count`, and the summary lists conflict rows by status.

Fees are left out of the comparison on purpose. A changed fee on the same id is more likely a
broker fee adjustment than a second account, and we don't want to cry wolf.

### 3. Matcher: pair within each source row (option b), expressed as data on the executions

**Rule:** executions carry a new nullable column, `broker_trade_id VARCHAR(128)`. When a broker
reports **closed round-trips** (Topstep's `Id`), the importer sets it on both synthesized legs.
`derive_trades()` runs the **existing FIFO routine unchanged**, scoped per:

```
(user, broker_account_label, symbol, broker_trade_id)   -- NULL broker_trade_id = one shared bucket
```

- A Topstep row's two legs form their own bucket. Size in equals size out, so they produce exactly
  one closed trade, opened by that row's `:entry` execution. Overlapping rows can no longer bleed
  into each other.
- Manual entries and any future **fill-only** importer leave `broker_trade_id` NULL. They get plain
  FIFO per `(account, symbol)`, exactly as `pnl-and-matching.md` §1 specifies. No second code path.
- An unbalanced group (should not happen, because the importer writes both legs of a row in one
  transaction) falls out of FIFO as an open trade. It stays visible and is never dropped or raised
  on the read path.
- Account grouping from ADR-0003 L355 is adopted now. It costs nothing and is a no-op when labels
  are blank.

**PnL cross-check (importer):** the importer computes
`(ExitPrice − EntryPrice) × Size × multiplier × side_sign` and compares it to the `PnL` column. A
mismatch marks the row `failed` with a reason, and no executions are written. The domain doc says
"surface as a warning, trust neither". Failing the row does exactly that. Importing a P&L we know
is wrong would not.

**Consequence for "executions are the source of truth":** this rule still holds. Trades are still
a pure function of the `journal_execution` rows. `broker_trade_id` is a broker fact stored on the
fill, like `broker_execution_id`, not a derived trade table. What changes is that the matcher now
respects a pairing the broker already reported instead of re-inventing one. Journal-entry identity
(opening execution) becomes stable per row.

**Why a column and not parsing `{Id}:entry` / `{Id}:exit`, or grouping by `raw_import_row_id`:**
parsing a suffix convention inside the matcher is a hidden format contract. `raw_import_row_id` is
`ON DELETE SET NULL`, so the pairing would vanish if raw rows were ever deleted. A nullable column
is boring and explicit, and adding it is a metadata-only change in Postgres.

A CHECK keeps it NULL-or-non-blank (`broker_trade_id IS NULL OR broker_trade_id <> ''`). Without
it, a stray `''` would put every such execution in one bucket and merge unrelated trades. This
follows the same blank-vs-NULL reasoning as round-6.

### Test vector to add first (before touching the matcher)

Add as **T4** in `topstep-import.md` §6. The ids follow the sample's pattern: an `Id` is assigned
at close, so the inner row that closes first gets the smaller Id.

```python
# Vector T4: two nested same-direction rows, same contract -> two trades, not one
rows = [
  {"Id": "9000000010", "ContractName": "CLZ6", "Type": "Long", "Size": "1",   # inner
   "EnteredAt": "12/19/2026 09:10:00 +00:00", "ExitedAt": "12/19/2026 09:20:00 +00:00",
   "EntryPrice": "80.20", "ExitPrice": "80.10", "PnL": "-100.00", "Fees": "2.00", "Commissions": "1.00"},
  {"Id": "9000000011", "ContractName": "CLZ6", "Type": "Long", "Size": "1",   # outer
   "EnteredAt": "12/19/2026 09:00:00 +00:00", "ExitedAt": "12/19/2026 09:30:00 +00:00",
   "EntryPrice": "80.00", "ExitPrice": "80.50", "PnL": "500.00", "Fees": "2.00", "Commissions": "1.00"},
]
expected_trades = [   # ordered by opened_at
  {"opening": "9000000011:entry", "side": "long", "qty": 1, "entry_price": "80.00", "exit_price": "80.50",
   "gross_pnl": "500.00", "fees": "3.00", "net_pnl": "497.00", "status": "closed"},
  {"opening": "9000000010:entry", "side": "long", "qty": 1, "entry_price": "80.20", "exit_price": "80.10",
   "gross_pnl": "-100.00", "fees": "3.00", "net_pnl": "-103.00", "status": "closed"},
]
# Must NOT produce plain-FIFO's result: ONE trade, qty 2, pairs 80.00->80.10 (+100) and
# 80.20->80.50 (+300), gross 400.00. Batch gross total is 400.00 either way. Assert the trade count.
```

Also add a companion matcher unit test. It uses the same four executions with
`broker_trade_id=None` and must produce the single merged FIFO trade. This proves that fill-only
brokers keep today's behaviour.

## Alternatives rejected

- **(a) Plain FIFO over synthesized executions.** It gives the correct batch total, but the
  trades, trade counts, durations and journal anchors disagree with the broker's own record for
  about 12 of 177 rows. That is exactly the data the behavior detectors read. Traders will compare
  our list to TopstepX's, and a mismatch there destroys trust.
- **Bypass the matcher for imports and build `Trade` straight from the CSV row.** That creates a
  second derivation path, so manual and imported P&L can diverge. It breaks ADR-0003's one-code-path
  decision.
- **Dedupe on content (the domain doc's fallback composite key) instead of `Id`.** Not needed
  unless `Id` re-export stability is disproven (still open, `topstep-import.md` §4).
- **`broker_account` table.** Still deferred per ADR-0003. The free-text label is enough until a
  feature reads accounts.

## Consequences

- **Positive:** a second account can no longer silently eat fills. Conflicts are visible. Imported
  trades match the broker's list one to one. Fill-only brokers are untouched.
- **Accepted risk:** re-uploading the same file with a *different* label inserts a duplicate batch.
  The existing `file_sha256` "you uploaded this before" hint plus batch delete is the fix. No extra
  cross-label logic is built.
- **Accepted risk:** if Topstep ever exports partial closes of one `Id` across several rows, those
  rows share a `broker_trade_id` bucket, and FIFO within the bucket still handles them correctly.
  Nothing is built for that case now.
- **Not built:** account table, label normalisation, a cross-label duplicate detector, an
  "imported with warning" state.

## Open questions

1. **product-manager:** add an optional, non-required "Account" text input on the upload form to
   populate `broker_account_label`? Default blank, trimmed, autocomplete from last-used labels.
   Without it, the label stays `''` for every Topstep row. The dedupe change is then a no-op, and
   the collision guard is the only defence. **Not decided here.**
2. **Still unverified (domain):** whether `Id`s overlap across accounts, and whether they stay
   stable across re-exports. Both need a real second export.
