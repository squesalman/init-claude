# ADR-0005: "Delete this import" (batch delete) semantics

- **Status:** Accepted (user ruling 2026-09-26: option (a), delete journaled entries behind a counted tick box)
- **Date:** 2026-09-26
- **Deciders:** architect (proposed), user (approved 2026-09-26: "a, yes, yes, yes")
- **Depends on:** [ADR-0003](0003-data-model.md) (on_delete rules, `for_user()`), [ADR-0004](0004-topstep-dedupe-and-pairing.md)
  (recovery path for the wrong-label upload), [`import-account-label.md`](../product/features/import-account-label.md)
  (banner + AC 11), [`mvp.md`](../product/features/mvp.md) story 3
- **Resolves:** [`docs/data/follow-ups.md`](../data/follow-ups.md) row 13
- **Blocks:** `backend-engineer` (view + service), `ux-designer` (confirm screen)

## Context

The user approved batch delete for the MVP. Main use: a file was uploaded under the wrong Account
label and the user wants to undo it, then re-upload.

Today's `on_delete` rules make a plain `batch.delete()` wrong:

- `RawImportRow.import_batch` is CASCADE, so raw rows go.
- `Execution.raw_import_row` is SET_NULL, so the batch's executions **stay**, orphaned from their
  provenance. The undo does nothing the user can see.
- `JournalEntry.opening_execution` is RESTRICT, so deleting those executions fails if any trade was
  journaled. CASCADE there would delete notes without asking. That is the reason RESTRICT was chosen.

Realistic timing: the wrong label shows up at upload time (the conflict banner), before any
journaling. The journaled case is rare, but when it does happen it destroys the user's writing, so
it has to be handled explicitly.

## Decision

### 1. What "delete this import" removes

Hard delete, in this order, in one transaction:

1. `JournalEntry` rows whose `opening_execution` is one of this batch's executions (only after the
   user explicitly confirms, see §2).
2. `Execution` rows where `raw_import_row.import_batch = this batch`. Only rows with status
   `imported` have executions. `skipped_duplicate` / `skipped_conflict` / `failed` rows point at nothing.
3. The `ImportBatch`, including `raw_file`. Its `RawImportRow`s go through the existing CASCADE.

Derived trades have no table, so they disappear on the next read. For Topstep, a trade's two legs
always come from one row, so they are always in one batch (ADR-0004 §3). No partial trades are left behind.

**Removes nothing else.** Executions from other batches are untouched. That includes executions an
earlier batch imported that this batch only marked `skipped_duplicate`. Manual executions are untouched
too, and so are journal entries on trades outside this batch.

### 2. Executions that already have a journal entry: **delete with explicit, counted opt-in** (recommended)

| Option | Verdict |
|---|---|
| **A. Block and explain** ("clear the journal on these N trades first") | Rejected. The user ends up deleting the same notes anyway, one trade at a time. That is maximum friction and no safer. |
| **B. Delete all, with an extra confirm that names how many journal entries and notes go** | **Recommended.** |
| **C. Keep journaled executions and delete the rest** | Rejected. It defeats the use case: re-uploading with the right label imports those rows again under the new label, so the journaled trades get counted twice. It also leaves a half-deleted import that the user can't explain. |

B, concretely:

- If the batch has **0** journal entries: one ordinary confirm. No checkbox.
- If it has **N > 0**: the confirm lists them, and the delete button stays disabled until the user
  ticks a required checkbox, *"Also delete my N journal entries (M with written notes). This can't be
  undone."* The server enforces the checkbox too. Without it, it re-renders the confirm and deletes nothing.
- **Stale-count guard:** the form posts back the journal-entry count the user was shown. Inside the
  transaction, the delete in step 1 returns how many rows it deleted. If that is **greater** than the
  count shown (the user journaled in another tab), roll back and re-render the confirm with fresh counts.
  This needs no locks. A journal entry inserted concurrently after step 1 fails on the execution FK,
  so it errors out and is never lost silently.

Why B satisfies "never lose writing silently": the loss is counted, named, listed, and needs a
deliberate extra action. Why B fits "journaling friction is the top risk": the common case (no
journal yet) is one click. The rare case costs one checkbox, not N trips through trade pages.

### 3. Atomicity and tenant scoping

```python
# journal/services.py (backend-engineer) — shape, not final code
def delete_import_batch(user, batch_id, confirmed_journal_count: int) -> None:
    with transaction.atomic():
        batch = ImportBatch.objects.for_user(user).defer("raw_file").get(pk=batch_id)  # DoesNotExist -> 404
        execs = Execution.objects.for_user(user).filter(raw_import_row__import_batch=batch)
        n, _ = JournalEntry.objects.for_user(user).filter(opening_execution__in=execs).delete()
        if n > confirmed_journal_count:
            raise StaleConfirm  # rolls back; view re-renders confirm with fresh counts
        execs.delete()
        batch.delete()  # CASCADE raw rows; SET_NULL on execution is now a no-op
```

- **Every queryset starts from `.for_user(user)`**, and the batch lookup is the ownership gate. Another
  user's id → `DoesNotExist` → 404, with the same response as a missing id. Never `get_object_or_404(ImportBatch, …)`
  and never `_default_manager` (follow-ups row 5).
- `Model.unscoped` is not used in request code. Django's deletion collector uses `_base_manager` for
  cascades, which is fine because it only follows FKs from rows that were already scoped.
- The `save()` cross-tenant guard doesn't apply: `QuerySet.delete()` never calls `save()`. It is not
  needed either, since every row deleted was selected through `for_user`.
- The order (journal → executions → batch) is required. It is what makes RESTRICT pass without
  touching the rule.
- POST only, with the CSRF token. The GET renders the confirm screen and never deletes anything.
- A synchronous request is fine. A batch is at most a few thousand rows (ADR-0003 volume).

### 4. Schema / migration changes: **none**

For `database-engineer`: no `on_delete` change and no migration.

- `JournalEntry.opening_execution` **stays RESTRICT**. It is the tripwire that makes every other
  execution-deleting path handle journal entries explicitly. This ADR handles them in the service,
  not by loosening the FK.
- `Execution.raw_import_row` **stays SET_NULL**. Changing it to CASCADE would make `batch.delete()`
  delete executions implicitly, and then run into RESTRICT anyway. It would also make a future
  "clear raw rows" operation destroy trades. The explicit service is clearer.
- Indexes are already sufficient. `execution.raw_import_row_id` has Django's automatic FK index,
  `journalentry.opening_execution_id` is UNIQUE, and `rawimportrow (import_batch_id, line_number)` is
  UNIQUE with the batch id leading.

### 5. Confirm-screen inputs (for `ux-designer`)

Computed on GET from the same scoped querysets, and all shown to the user:

| Input | Source |
|---|---|
| `filename`, `uploaded_at` (user TZ) | batch |
| `account_label` | distinct `broker_account_label` of the batch's executions (`''` → "no account name") |
| `trade_count` | batch rows with status `imported` (Topstep: 1 row = 1 trade) |
| `other_rows_count` | `row_count - trade_count` (skipped/failed rows, removed with the batch, no trades) |
| `journal_count` (N) | journal entries on this batch's executions |
| `noted_count` (M) | of those, `note <> ''` |
| `journal_list` | per entry: symbol, opened-at (user TZ), rules-followed answer, first ~80 chars of the note, link to the trade. Lets the user copy a note before deleting |

Draft copy (coach tone; ux-designer owns the final wording):

- Title: "Delete this import?"
- Body: "This removes *{trade_count}* trades imported from *{filename}* ({uploaded_at}, {account_label}).
  Your other imports and manual trades stay as they are."
- If N > 0: "You've journaled *{N}* of these trades (*{M}* with written notes). Deleting the import
  deletes those entries too." Then the list, then the required checkbox.
- If `trade_count = 0`: "This import added no trades (every row was skipped or failed). Deleting it
  only removes the upload record."
- After delete: flash "Import deleted. {trade_count} trades removed." and redirect to import history.

### Tests to write first (backend-engineer)

1. Deleting batch → its executions, raw rows, and batch gone; other batch + manual executions intact.
2. Batch with journal entries, no checkbox → nothing deleted.
3. With checkbox and correct count → entries deleted too.
4. Posted count lower than actual → nothing deleted (rollback), confirm re-rendered.
5. **Isolation:** user B posting user A's batch id → 404, zero rows changed.
6. Batch 2 all `skipped_duplicate` → deleting it removes 0 executions; batch 1's trades intact.
7. Regression: bare `execution.delete()` with a journal entry still raises `RestrictedError`.

## Consequences

- **Positive:** one-click undo in the common case; notes can only be lost with a counted, explicit opt-in;
  no schema change; RESTRICT remains the safety net for every other delete path.
- **Accepted:** hard delete, with no undo and no trash. `pg_dump` is the only recovery. The confirm step is the guard.
- **Accepted:** the raw file is deleted with the batch. "Keep raw data" applies to data the user keeps,
  and this is the user saying they don't want it.
- **Accepted (surprise case):** deleting the *original* batch removes the trades, even if a later re-upload
  of the same file exists. That re-upload imported nothing (all `skipped_duplicate`), and the confirm
  shows the true trade count for each batch, so the user sees it before confirming.
- **Future fill-only broker:** a trade may span batches. Deleting one batch then leaves a partial
  (open) trade from the other batch's fills. It stays visible and nothing is dropped. Revisit when such an importer exists.

## Not built

Soft delete / trash / undo. Re-attaching deleted notes on re-upload (matching on `broker_execution_id`).
Note export. Deleting single rows or single trades from a batch. Editing a batch's label in place
(`import-account-label.md` out of scope). Bulk "delete several imports".

## Open question for the user

1. **Approve option B** (delete journaled trades too, behind a counted checkbox)? The alternative is
   A (block until the user clears those journal entries by hand). C is not recommended.
