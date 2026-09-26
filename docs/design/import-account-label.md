# Design: Topstep upload with optional Account, conflict banner, "uploaded before" hint, delete import

Spec: [`import-account-label.md`](../product/features/import-account-label.md) (product). Story 3 of [`mvp.md`](../product/features/mvp.md). Data rules: [ADR-0004](../adr/0004-topstep-dedupe-and-pairing.md).
Status: draft for `frontend-engineer`. **Delete semantics are TBD pending `docs/adr/0005-batch-delete.md`.** Every place that depends on it is marked `[TBD-0005]`.

`docs/design/` had no files when this was written, so this doc also fixes the few conventions it needs (see "Conventions used"). A design-system doc should absorb them later.

---

## 1. Decisions (answers to the PM's open questions)

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | Where does Account sit? | **Inline, right under the file picker, as a native `<details>` disclosure.** Collapsed for users who have never used a label. **Open by default** for users who have at least one non-blank label. Also open when re-rendering after a validation error, or when arriving from a delete (section 5). | A single-account user sees one quiet line. A multi-account user (the only one who needs it) sees the field already open. No JS needed, keyboard and screen-reader native. The disclosure text names the situation ("more than one Topstep account?") so multi-account users recognise themselves. |
| 2 | Banner hierarchy, conflicts plus failed rows | **Conflict banner on top** (action needed, has the remedy). The summary sits under it. **Failed rows get a quiet one-line notice under the summary, not a banner.** The row list defaults to the "Needs attention" filter (conflicts first, then failed) whenever either count is above 0. | Conflicts can cause double counting if handled wrong and have a one-click remedy. Failed rows are about the file, have no action button, and are already listed with reasons. Two loud banners would read as an alarm. Max one banner at a time. |
| 3 | Datalist or chips? | **Datalist only** (your decision). No chips in MVP. Mitigation for hidden suggestions: when the user has past labels, the placeholder reads "Choose or type a name". A placeholder is not a value, so nothing is prefilled. | Native, zero JS, keyboard-friendly. Chips are the upgrade if usage shows people miss the suggestions (open question A). |
| 4 | Delete confirm | **Modal, using native `<dialog>`**, opened from the banner or the batch page. Server-rendered body (counts are fetched on open via htmx so they are fresh). After delete: redirect to the Imports page with a flash message and the Account field open and focused. | Irreversible, and the counts need room. An inline confirm inside a banner gets lost on mobile. `<dialog>` gives focus trap and Esc for free. |

---

## 2. Flow

```
Imports page (/imports/)
   |  choose file  [+ optional Account]   Upload
   v
POST (multipart) -> sync parse -> 302 to Batch page (/imports/<id>/)
   |                               (PRG: reload never re-submits)
   +- all imported ........................ clean summary
   +- some skipped_duplicate .............. summary, no banner
   +- some skipped_conflict ............... conflict banner + summary
   +- some failed ......................... quiet notice under summary
   +- same file hash seen before .......... hint line (+ extra line if label differs)
   |
   +- "Delete this import" (banner, or batch page menu)
          v
       Confirm dialog -> Delete -> 302 /imports/ + flash ("Import deleted...")
          v
       Upload again, Account field open + focused
```

Banner, hint and cross-label line are computed on read from batch data, so revisiting the batch page later shows the same thing. If the earlier batch is deleted, its hint disappears by itself.

---

## 3. Screens

### 3.1 Imports page: upload form (`/imports/`)

Desktop (content column max 640px; the list below is full width).

```
+--------------------------------------------------------------------------+
| Import trades                                                            |
| Upload your TopstepX trade export (CSV). Nothing is imported twice.      |
|                                                                          |
| Broker      [ Topstep v ]                                                |
|                                                                          |
| File        [ Choose CSV file... ]   no file chosen                      |
|                                                                          |
| > Trade more than one Topstep account?              <- <details> summary |
|                                                                          |
| [ Upload ]                                                               |
+--------------------------------------------------------------------------+
```

Same form with the disclosure open (default for users with saved labels):

```
| v Trade more than one Topstep account?                                   |
|   Account (optional)                                                     |
|   [ Choose or type a name                        ]  <- datalist, blank   |
|   Only needed if you trade more than one Topstep account.                |
|   Use the same name each time.                                           |
```

Below the form: **Recent imports** list (see 3.4).

Mobile (360px): one column, full-width controls, 44px min touch targets, Upload button full width and sticky at the bottom of the form only if the form is taller than the viewport. File picker uses the native picker.

### 3.2 Batch page: result (`/imports/<id>/`)

Case: conflicts, blank Account (the hard case), plus one failed row, plus hash hint.

```
+--------------------------------------------------------------------------+
| <- Imports      topstep-export-2026-09-26.csv      Uploaded Sep 26, 14:02|
|                                                    [ ... menu: Delete ]  |
|                                                                          |
| [i]  3 rows were not imported                                            |
|      Their IDs match trades you already imported, but the details        |
|      differ. This usually means the file is from a different account.    |
|      Nothing was lost or overwritten.                                    |
|      Delete this import first, then upload again with an Account name,   |
|      so nothing is counted twice.                                        |
|      [ Delete this import ]   [ How this works ]  (text link, optional)  |
+--------------------------------------------------------------------------+
| Summary                                                                  |
|  Imported 40    Skipped 3 (duplicate 0, conflict 3)    Failed 1          |
|  [i] You uploaded this exact file on Sep 12.  View that import           |
|                                                                          |
|  [i] 1 row could not be read. See it under "Failed" below.               |
+--------------------------------------------------------------------------+
| Rows   [ Needs attention 4 ] [ All 44 ] [ Imported 40 ]                  |
|        [ Skipped: duplicate 0 ] [ Skipped: conflict 3 ] [ Failed 1 ]     |
|                                                                          |
| Row  Status               Symbol  Time (your tz)   Size  Note            |
| 12   (!) Skipped conflict CLZ6    Sep 3, 09:10     1     Id already...   |
| 15   (!) Skipped conflict MNQZ6   Sep 3, 10:42     2     Id already...   |
| 21   (!) Skipped conflict MNQZ6   Sep 4, 09:31     1     Id already...   |
| 30   (x) Failed           ESZ6    Sep 4, 11:05     1     P&L in the file |
|                                                          does not match  |
|                                                          the prices...   |
+--------------------------------------------------------------------------+
```

Notes:
- Order top to bottom: header, **conflict banner (only if conflicts > 0)**, summary card (counts, hint line, failed notice), row list.
- The "Needs attention" filter only appears if conflict plus failed > 0. It is the default filter then. Otherwise "All" is default.
- The status column always shows an icon **and** the text. Never color alone.
- Conflict rows show symbol, time and size so the trader recognises them (spec). Note column shows the ADR-0004 `error` text, wrapped, max 2 lines with "Show more".

Banner variant, Account filled in:

```
| [i]  3 rows were not imported                                            |
|      Under the Account name 'Combine 50K' those IDs already exist with   |
|      different details. Check that the name is the one you meant.       |
|      Nothing was lost or overwritten.                                    |
|      [ Delete this import ]                                              |
```

Hint with label mismatch (the "adds these trades a second time" case). This is a **banner-level notice** (same component as the conflict banner), because the fix is the same button and it is the one place ADR-0004's accepted risk surfaces:

```
| [i]  You uploaded this exact file before                                 |
|      On Sep 12, with Account 'Combine 50K'. Uploading it again with a    |
|      different name adds these trades a second time.                     |
|      [ Delete this import ]   [ View the Sep 12 import ]                 |
```

- Wording when the old batch had no label: "On Sep 12, with no Account name."
- If the old batch imported zero executions: omit the mismatch banner, show only the plain hint line in the summary.
- Plain hint (same label, or no mismatch): a single info line inside the summary card, with a link. No banner, no button.
- Conflict banner and mismatch banner are effectively exclusive (mismatch means different label, so no collisions). If both ever render, conflict banner first, mismatch below it, and only the conflict banner shows the Delete button once.

### 3.3 Confirm dialog: Delete this import

Opened by any "Delete this import" button. `hx-get` fetches the dialog body so the counts are current, then `dialog.showModal()`.

```
+--------------------------------------------------+
| Delete this import?                        [ x ] |
|                                                  |
| topstep-export-2026-09-26.csv, uploaded Sep 26.  |
|                                                  |
| This will remove:                                |
|   - {n} trades  ({m} with notes)     [TBD-0005]  |
|   - {e} executions and {r} raw rows              |
|                                                  |
| {notes handling sentence}            [TBD-0005]  |
| Other imports are not touched.                   |
|                                                  |
|            [ Cancel ]   [ Delete import ]        |
+--------------------------------------------------+
```

- Focus opens on **Cancel**. Delete is the secondary-weight destructive button (outline in the danger token plus the words "Delete import"), not a big red slab.
- No type-to-confirm in the base design. `[TBD-0005]`: if ADR-0005 says notes are destroyed, add a required checkbox "I understand my notes on these trades will be removed" and keep Delete disabled until checked. If notes survive or the delete is blocked, adjust copy accordingly.
- When `m = 0`, drop "(m with notes)" and the notes sentence entirely. Do not show "0 with notes".
- Counts of executions/raw rows may be dropped from the dialog if too dense. Trades and notes are the ones the user cares about.
- Loading: body shows a skeleton of 3 lines while `hx-get` runs. Delete button disabled until loaded.
- Submit: `hx-post`, button shows "Deleting..." and is disabled. Success: `HX-Redirect` to `/imports/`.
- Failure (server or network): stays open, inline message in the dialog: "Something went wrong and nothing was deleted. Try again in a moment." Buttons re-enabled.
- Batch already gone (double click, other tab): redirect to `/imports/` with flash "That import was already deleted." Not an error.

### 3.4 Imports page after delete: flash and empty states

Case A: user has other imports (the list is not empty).

```
+--------------------------------------------------------------------------+
| [ok] Import deleted. {n} trades removed. Ready when you are.             |
|      If you meant to add an Account name, it's open below.       [ x ]   |
|                                                                          |
| Import trades                                                            |
| File   [ Choose CSV file... ]                                            |
| v Trade more than one Topstep account?          <- open, input focused   |
|   Account (optional) [ Choose or type a name ]                           |
| [ Upload ]                                                               |
|                                                                          |
| Recent imports                                                           |
| Sep 12  topstep-export-a.csv   44 imported   0 skipped   0 failed  [...] |
+--------------------------------------------------------------------------+
```

Case B: that was the user's only import (no batches left, no trades).

```
| [ok] Import deleted. Nothing else was touched.                           |
|                                                                          |
| Import trades                                                            |
| (same form, Account open + focused)                                      |
|                                                                          |
| No imports yet                                                           |
| Your uploads will show up here, with what was imported and what was      |
| skipped. You can also add a trade by hand.       [ Add a trade ]         |
```

- Focus: the flash is `role="status"` (announced politely). Focus moves to the Account input only when the delete came from a conflict or mismatch banner (`?from=banner`); after a delete from the batch menu, focus goes to the flash.
- The browser cannot keep the chosen file, so the file picker is empty. The flash does not promise otherwise.
- Old URL `/imports/<deleted id>/` (bookmark, back button): redirect to `/imports/` with flash "That import was deleted." Not a 404 page for the owner. Someone else's batch id stays a 404 (isolation).
- Trades list, if the user now has zero trades: use the standard trades empty state (owned by the trades-list design, not defined here).
- `[TBD-0005]`: flash copy for "{n} trades removed" may need "{k} trades kept" or similar depending on notes semantics.

---

## 4. States

### Upload form

| State | What the user sees |
|---|---|
| Empty (default) | Form as 3.1. Disclosure collapsed if the user has no saved labels, open if they do. Upload enabled (a missing file shows a message on submit, not a disabled button). |
| Populated | File name shown by the browser. Account typed or chosen. |
| Account too long | Live counter appears from 50 chars ("52 / 64"). At 65+, inline message under the field: "That name is 70 characters and the limit is 64. Shorten it a little." Field gets `aria-invalid="true"` plus the same message linked by `aria-describedby`. Never truncated. Server enforces the same; on server error, the field partial is swapped via htmx so the **chosen file stays selected**. Without JS the full page re-renders and the file must be re-picked. |
| No file | Inline under file input: "Choose a CSV file to upload." |
| Wrong file type / unreadable | Inline under file input: "This file does not look like a TopstepX export. Check that you exported trades as CSV." (Exact rule comes from the importer.) |
| Loading (parsing, sync) | Upload button becomes "Importing..." and is disabled (`hx-disabled-elt`). File and Account inputs disabled. A polite live region says "Importing your file". No spinner-only feedback. Typical files finish in about a second. |
| Server failure | Above the form, not a banner: "We could not finish this import and nothing was saved. Try again in a moment." Form stays filled. `[assumes: whole import is one transaction; confirm with backend]` |

### Batch page

| State | What the user sees |
|---|---|
| Clean success | No banner. Summary "Imported 44, skipped 0, failed 0" and a supportive line: "All 44 rows imported." Row list default filter "All". |
| Only duplicates | No banner. Summary shows skipped (duplicate) count. Plain line: "These rows were already imported earlier, so nothing was added twice." Plain hint appears if the hash matches. |
| Conflicts | Conflict banner (blank or filled variant), summary, rows under "Needs attention". |
| Failed only | No banner. Quiet notice under summary. Default filter "Needs attention". |
| Conflicts and failed | Conflict banner, summary with the failed notice, list "Needs attention" (conflicts first). |
| Nothing imported (all skipped/failed) | Summary leads with "No new trades were added." plus the relevant banner or notice. |
| Loading | Server-rendered, no loading state. Row-list filter switches use `hx-get` with `hx-indicator`, table gets `aria-busy="true"` while swapping. |
| Empty filter | "No rows with this status." |
| Deleted or not owner | Deleted (owner): redirect with flash. Not owner: 404. |

### Confirm dialog: see 3.3 (loading, ready, submitting, failed, already gone).

---

## 5. Microcopy

Tone: say what happened, say nothing was lost, say the next step. Never "error", "invalid", "duplicate" as blame, or "you should have". (Spec criterion 12 applies to banner and row text; this doc follows it for form messages too.)

| Where | Copy |
|---|---|
| Disclosure summary | Trade more than one Topstep account? |
| Field label | Account (optional) |
| Help text | Only needed if you trade more than one Topstep account. Use the same name each time. |
| Placeholder, no saved labels | e.g. Combine 50K |
| Placeholder, has saved labels | Choose or type a name |
| Upload button / busy | Upload / Importing... |
| Conflict banner title | {N} rows were not imported (singular: 1 row was not imported) |
| Conflict body, blank | Their IDs match trades you already imported, but the details differ. This usually means the file is from a different account. Nothing was lost or overwritten. Delete this import first, then upload again with an Account name, so nothing is counted twice. |
| Conflict body, filled | Under the Account name '{label}' those IDs already exist with different details. Check that the name is the one you meant. Nothing was lost or overwritten. |
| Hint, plain | You uploaded this exact file on {date}. View that import |
| Hint, mismatch title | You uploaded this exact file before |
| Hint, mismatch body | On {date}, with Account '{old}'. Uploading it again with a different name adds these trades a second time. |
| Failed notice | {N} row(s) could not be read. See {it/them} under "Failed" below. |
| Failed row note | Reason from the importer, e.g. "The P&L in the file does not match its prices, so this row was left out." (importer owns final text; must avoid "error"/"invalid") |
| Conflict row note | From ADR-0004 `error`: "Id already imported with different contents. If this is a second account, fill in the Account field and re-upload." |
| Banner button | Delete this import |
| Dialog title | Delete this import? |
| Dialog body | This will remove: {n} trades ({m} with notes), {e} executions and {r} raw rows. Other imports are not touched. **`[TBD-0005]` the sentence about notes** |
| Dialog buttons | Cancel / Delete import |
| Dialog failure | Something went wrong and nothing was deleted. Try again in a moment. |
| Flash, deleted | Import deleted. {n} trades removed. Ready when you are. |
| Flash, from banner | ...If you meant to add an Account name, it's open below. |
| Empty list | No imports yet. Your uploads will show up here, with what was imported and what was skipped. |

Status labels (text always shown with an icon): Imported, Skipped (duplicate), Skipped (conflict), Failed. Icons: check, equals/skip, info-circle, cross-in-circle. The conflict icon is info, not warning-triangle, on purpose (coach tone).

---

## 6. Components

| Component | Notes |
|---|---|
| `AccountField` | `<details>` + labelled `<input list="account-labels" autocomplete="off">` + `<datalist id="account-labels">` (max 10, most recent first, user's own labels only, this broker) + help text + optional counter (Alpine, `x-data` with a length check). Django partial so htmx can swap it on validation failure. |
| `Notice` | One component, variants `info`, `success`, `attention`. Icon + title + body + optional action row. `role="status"` for flash and hints, no `role="alert"` (the conflict banner is not an emergency and must not interrupt screen readers). Used for conflict banner, mismatch notice, failed notice, hint line, flash. |
| `ImportSummary` | Counts row, hint line, failed notice. |
| `StatusBadge` | Icon + text label, tokens per status. |
| `RowTable` + `FilterTabs` | htmx-driven filters, `aria-current="true"` on the active tab, counts in each label. Dense table, sticky header on desktop, cards on mobile (each row becomes a 2-line card: status + symbol/time/size, note below). |
| `ConfirmDialog` | Native `<dialog>`, Alpine only to open/close. Body via `hx-get`, submit via `hx-post` with `hx-disabled-elt`. |
| `RecentImportsList` | Date, filename, counts, row menu with Delete. |

Color: use the existing/future semantic tokens for `info`, `success`, `attention` (amber-ish, not red), and `danger` only on the Delete button outline and text. All text and icon contrast meets AA in light and dark mode. Do not use red for conflict rows.

---

## 7. Accessibility

- Every state is conveyed by icon plus text, never color alone (status badges, banners, filter counts).
- `<details>`/`<summary>` is keyboard operable (Enter, Space). The disclosure summary has a visible focus ring and at least a 44px hit area.
- Datalist is native, so arrow keys and typing work. Because datalist support for screen readers varies, the field must be fully usable by typing alone. Help text is tied with `aria-describedby`.
- Counter and validation message: `aria-describedby` plus `aria-invalid`. Announce the over-limit message via a `role="status"` region, not on every keystroke (announce on blur or at 65 chars).
- Upload busy state announced via a polite live region. Buttons keep their labels when disabled.
- Dialog: `<dialog>` modal, `aria-labelledby` on the title, focus starts on Cancel, Esc closes, focus returns to the button that opened it. If it was opened from the banner and the banner is gone after a failed delete, focus the batch page heading.
- Banners are placed before the summary in DOM order, so they are read first. They are not focus traps and have no auto-dismiss. Flash has a close button and does not auto-dismiss.
- Filter tabs are links or buttons with `aria-current`, not `role="tab"`, to avoid arrow-key tabpanel obligations. Table has `<caption class="sr-only">` and real `<th scope>`.
- Times shown in the user's timezone, with the zone shown once in the column header (for example "Time (America/New_York)").
- Touch targets at least 44px on mobile. Text scales to 200% without horizontal scroll. Mobile rows become cards.
- Respect `prefers-reduced-motion` (no animation is required by this design anyway).

---

## 8. Edge cases

1. **Labels differing only by case or spacing** ("combine 50k" vs "Combine 50K"): stored as typed, treated as different (ADR-0004). Datalist shows both. Out of scope to merge. This is what makes the datalist suggestions worth having.
2. **Blank label, conflicts, and the user re-uploads without deleting**: they get the same conflict banner again (rows collide with the first batch). The non-conflicting rows of the new file are `skipped_duplicate`. No double counting. Fine.
3. **Mismatch hint, first attempt was under a blank label**: "with no Account name".
4. **Hash matches more than one earlier batch**: link to the most recent one that imported at least one execution.
5. **User deletes the batch that the hint linked to** and revisits: hint is simply gone.
6. **Two tabs**: second delete lands on the "already deleted" flash.
7. **Very long filename**: truncate in the middle on the header with the full name in `title` and readable in the dialog wrapped.
8. **File with both new rows and conflicts**: the banner says "Delete this import" even though some rows are good. The dialog counts make the consequence visible. This is the reason for the confirm step.
9. **A conflict banner when the user has only one account** (for example re-exported file with adjusted price): the "usually" wording covers it. The banner does not claim certainty.
10. **Zero-row or header-only file**: handled by the file-level message in section 4, not the banner.
11. **Delete of a batch that has trades with notes** `[TBD-0005]`.

---

## 9. Open questions

For the orchestrator / user:
- **A.** Datalist discoverability: acceptable to ship datalist-only and watch for missed suggestions, with chips as the follow-up? (I say yes.)
- **B.** No Imports list or batch page exists in `mvp.md`. This design assumes both (`/imports/` with recent imports, `/imports/<id>/` batch detail, needed for the "link to that batch" hint and for delete). Confirm they are in scope, or say where the summary is shown instead. Minimum viable list: date, filename, three counts, Delete.
- **C.** Delete from the Recent imports list as well as the banner? Design assumes yes (row menu), since it is the same dialog.

For architect (ADR-0005):
- **D.** Notes handling on delete: are notes deleted, kept as orphans, or does delete block? This decides the dialog sentence, the optional acknowledge checkbox, and the flash copy. Also: what is `{n}` (trades derived only from this batch's executions, or trades that mix executions from several batches)?
- **E.** Is the whole import one transaction, so "nothing was saved" on failure is true?

For backend / frontend:
- **F.** Confirm the batch page can compute "same hash, earlier batch, its label" on read (spec says compare with executions' label).
- **G.** htmx partial swap of `AccountField` on a server validation error, so the file input stays selected.

## Conventions used (until a design-system doc exists)

- Content column max 640px for forms, full width for tables. 4px spacing scale (4, 8, 12, 16, 24, 32). Type: 14px body, 12px table meta, 20px page title.
- Semantic color tokens by role (`info`, `success`, `attention`, `danger`, `surface`, `border`), each with light and dark values. No hard-coded colors in templates.
- Status is always icon plus text. Amounts and P&L (not on these screens) always carry a sign or a word as well as color.
- Coach tone: name what happened, that nothing was lost, and the next step.
