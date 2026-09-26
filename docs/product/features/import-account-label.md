# Import: optional "Account" label + conflict handling

Slice of MVP feature 3 ([`mvp.md`](mvp.md) "CSV import from Topstep"). Resolves ADR-0004 open question 1.
Status: user approved the field. **No schema change beyond [ADR-0004](../../adr/0004-topstep-dedupe-and-pairing.md).**

## Problem

The Topstep export has no account column. A trader with two accounts (say a 50K Combine and a funded account)
can end up with fills that share an `Id`. Without a label, the second account's fills could look like
duplicates of the first and get dropped. The trader's P&L would then be wrong with no warning.

Most traders have one account and never need this. So the field must cost them nothing.

## User story

As a trader with more than one Topstep account, I can tag an upload with an account name, so fills from
different accounts are never mistaken for each other. As a trader with one account, I can ignore the field.

## Field behaviour

- One text input on the Topstep upload form, labelled **Account (optional)**, with help text: "Only needed if
  you trade more than one Topstep account. Use the same name each time."
- **Never required.** Blank is valid and stored as `''` (today's behaviour, unchanged).
- Trimmed. Case kept as typed (ADR-0004 §1). Max 64 characters (`broker_account_label VARCHAR(64)`), enforced
  in the form with an inline error, never truncated silently.
- Applies to every execution in that upload.
- **Suggestions, not defaults (recommendation):** the field starts blank. A `<datalist>` offers the user's
  previously used non-blank labels for this broker, most recent first, max 10. Source is the distinct
  `broker_account_label` on the user's own executions, so no new table or column.
  - Why not prefill the last label: a two-account trader who forgets to change a prefilled value tags
    account B's fills as account A. That is a silent error, and this feature exists to prevent those.
    A blank field plus a one-click suggestion is one extra click for multi-account users and zero for everyone else.
  - Not built: a saved-accounts screen, rename or merge of labels, case normalisation (ADR-0004 "Not built").

## Conflict presentation (`skipped_conflict`)

The import summary already counts rows by status. Conflicts count toward "skipped" but are called out
separately, because they need action and a plain duplicate does not.

- **Banner above the summary, only when at least one row is `skipped_conflict`**, coach tone:
  - Account left blank: "N rows were not imported. Their IDs match trades you already imported, but the
    details differ. This usually means the file is from a different account. Nothing was lost or overwritten.
    Add an Account name and upload the file again."
  - Account filled in: "N rows were not imported. Under the Account name '<label>' those IDs already exist
    with different details. Check that the name is the one you meant. Nothing was lost or overwritten."
- Each conflict row in the list shows status "Skipped (conflict)", the row's symbol, time and size (so the
  trader recognises it), and the ADR-0004 `error` text. Filterable by status like the other statuses.
- Wording rules: no "error", "invalid" or "duplicate fraud" language. Say what happened, say nothing was lost,
  say the next step.
- The suggestion is a hint. The app does not know it is a second account, so text says "usually" or "may".

### The re-upload trap (needs a decision)

Rows in the same file that did *not* collide are imported under the blank label. Re-uploading the whole
file with an Account name then imports them a second time under the new label (ADR-0004 accepted risk).

Recommended handling, in this slice: the conflict banner offers **"Delete this import"** (removes the batch,
its executions and its raw rows, with a confirm step), followed by re-upload with the label. The banner
text adds: "Delete this import first, then upload again with an Account name, so nothing is counted twice."

Dependency: batch delete is not in `mvp.md`, and ADR-0004 assumes it exists. If the user does not want it
in the MVP, the fallback is that the conflict banner says only "add an Account name and upload again", and
duplicates from the non-conflicting rows are a known limitation. That fallback is worse for trust.

## "You uploaded this file before" hint (`ImportBatch.file_sha256`)

- Independent of the label. The hash is informational and is not a dedupe key (ADR-0003).
- Shown after upload, in the summary: "You uploaded this exact file on <date>" with a link to that batch.
  Never blocks the upload.
- **Interplay:** if the hash matches a previous batch and the label differs from that batch's label, the
  hint gets an extra line: "That upload used Account '<old>' (or no account). Uploading it again with a
  different name adds these trades a second time." This is the one place the accepted risk from ADR-0004 is
  surfaced. It warns and does not block.
- To show the old batch's label, compare with the label on that batch's executions (`ImportBatch` has no
  label column and none is added). If the old batch imported zero executions, omit the extra line.

## Acceptance criteria

1. Given the upload form, when I open it, then an "Account (optional)" input is present, empty, and
   submitting with it blank succeeds.
2. Given I enter `  Combine 50K  `, when the import runs, then executions store `Combine 50K`
   (trimmed, case kept).
3. Given I enter 65 characters, when I submit, then I see an inline error, nothing is imported, and nothing
   is truncated. Given exactly 64, then it succeeds.
4. Given I previously imported with labels `A` then `B`, when I open the form, then the input suggests `B`
   then `A`, the input itself is still blank, and another user's labels never appear.
5. Given I imported a file with a blank label, and I upload a second account's file (same `Id`s, different
   contents) with a blank label, when the import runs, then the colliding rows are `skipped_conflict`,
   no executions are written for them, and the blank-label banner is shown with the count.
6. Given the same second-account file, when I upload it with Account `B`, then all rows are `imported`
   with no conflicts (into a clean state, after the first attempt's batch is deleted).
7. Given a batch has conflict rows, then the summary shows them under their own status, each with symbol, time,
   size and reason text. No conflict row is missing from the row list.
8. Given a conflict with a filled-in label, then the banner uses the filled-in-label wording and names the label.
9. Given an identical file re-uploaded with the same label, then rows are `skipped_duplicate` (not conflict),
   no banner, and the "uploaded before" hint appears with a link.
10. Given a file with a hash I uploaded before under label `A`, when I upload it under `B`, then the hint
    includes the "adds these trades a second time" line, and the upload still proceeds.
11. Given a batch with conflicts, when I confirm "Delete this import", then its executions and raw rows are
    gone, other batches are untouched, and only the owner can do it (isolation test).
12. Given the banner and row texts, then none contain "error", "invalid" or "you should have".

## Out of scope

- An account table, account rename/merge, label normalisation, per-account dashboards or filters.
- Detecting that two labels mean the same account (the "combine-50k" vs "Combine 50K" typo).
- Blocking or auto-resolving a cross-label re-upload. It only warns.
- Editing the label of an already-imported batch.
- Reading the account from the CSV (the export has none). Other brokers.
- Verifying whether Topstep `Id`s overlap across accounts. That is still open in the domain doc §7.

## Open questions

For `ux-designer` (wireframe to `docs/design/`):
1. Where does the Account input sit so single-account users skip it, for example under a collapsed "More options"
   or inline after the file picker?
2. Banner placement and hierarchy when a batch has both conflicts and failed rows.
3. Is a datalist enough, or should suggestions render as chips under the field?
4. Delete-import confirm: modal or inline confirm? What does the empty state look like afterward?

For the user or orchestrator:
5. Is batch delete in the MVP? Recommended yes (see "re-upload trap"). It is not currently in `mvp.md`.
6. OK with blank-by-default plus suggestions rather than prefilling the last label?

## Suggested owner agents

`ux-designer` (wireframe), `backend-engineer` (form field, importer wiring, banner and hint data, batch delete),
`frontend-engineer` (form, datalist, banner), `qa-engineer` (criteria above; #5, #6 and #11 need a two-account
fixture and an isolation test).
