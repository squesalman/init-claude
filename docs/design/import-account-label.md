# Design: Topstep upload with optional Account, conflict banner, "uploaded before" hint, /imports/ list, import detail, delete import

Spec: [`import-account-label.md`](../product/features/import-account-label.md) (product). Story 3 of [`mvp.md`](../product/features/mvp.md). Data rules: [ADR-0004](../adr/0004-topstep-dedupe-and-pairing.md). Delete semantics: [ADR-0005](../adr/0005-batch-delete.md) (Accepted, option (a), 2026-09-26).
Status: final for `frontend-engineer`. No `[TBD]` markers remain. Open questions are in section 9.
Scope note (user ruling, 2026-09-26): manual trade entry is out of scope for slice 1 (import + list). This doc has no "add a trade" action or "manual trades" wording. When manual entry exists, restore an "add a trade" action in the empty imports list (3.4 case B, section 5 "Empty list") and re-add "manual trades" to the delete-dialog body copy.

`docs/design/` had no other files when this was written, so this doc also fixes the few conventions it needs (see "Conventions used"). A design-system doc should absorb them later.

---

## 1. Decisions

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | Where does Account sit? | **Inline, right under the file picker, as a native `<details>` disclosure.** Collapsed for users who have never used a label. **Open by default** for users who have at least one non-blank label. Also open when re-rendering after a validation error, and when arriving from a delete (section 3.4). | A single-account user sees one quiet line. A multi-account user (the only one who needs it) sees the field already open. No JS needed, keyboard and screen-reader native. |
| 2 | Banner hierarchy, conflicts plus failed rows | **Conflict banner on top** (action needed, has the remedy). Summary under it. **Failed rows get a quiet one-line notice under the summary, not a banner.** Row list defaults to "Needs attention" (conflicts first, then failed) whenever either count is above 0. Max one banner at a time. | Conflicts can cause double counting and have a one-click remedy. Failed rows are about the file and have no action button. Two loud banners would read as an alarm. |
| 3 | Datalist or chips? | **Datalist only** (user ruling). Placeholder reads "Choose or type a name" when the user has past labels. A placeholder is not a value, so nothing is prefilled. | Native, zero JS, keyboard-friendly. Chips are the follow-up if people miss the suggestions. |
| 4 | Delete confirm | **Modal, native `<dialog>`**, body fetched on open via htmx so counts are fresh. Two variants: **simple** (no journal entries, one confirm) and **two-step** (journal entries exist: read the list, then tick the box, then the button enables). No-JS fallback: the same body as a full page at `/imports/<id>/delete/`. | Irreversible, and the counts and list need room. `<dialog>` gives focus trap and Esc for free. |
| 5 | Where can the user delete? | **Three places, one dialog:** the conflict/mismatch banner, the detail page header button, the `/imports/` row menu. | User ruling. Same dialog and same server endpoint everywhere. |
| 6 | Undo | **None.** The dialog says so plainly and offers no fake undo. | ADR-0005: hard delete, `pg_dump` is the only way back. Honest copy beats a soft promise. |

---

## 2. Flow

```
Imports page (/imports/)  = upload form + list of imports
   |  choose file  [+ optional Account]   Upload
   v
POST (multipart) -> sync parse -> 302 to Import detail (/imports/<id>/)
   |                               (PRG: reload never re-submits)
   +- all imported ........................ clean summary
   +- some skipped_duplicate .............. summary, no banner
   +- some skipped_conflict ............... conflict banner + summary
   +- some failed ......................... quiet notice under summary
   +- same file hash seen before .......... hint line (+ banner if label differs)
   |
   +- "Delete this import"  from: banner | detail header | /imports/ row menu
          v
       Confirm dialog
          +- no journal entries ... one step: Delete import
          +- journal entries ...... step 1: read the list
                                    step 2: tick "Also delete my N journal entries ..."
                                            (button enables) -> Delete
          +- server: stale count -> nothing deleted, dialog re-shown with fresh list
          v
       302 /imports/ + flash ("Import deleted...")
          v
       Upload again, Account field open (focused if opened from a banner)
```

Banner, hint and cross-label line are computed on read, so revisiting the detail page later shows the same thing. If the earlier batch is deleted, its hint disappears by itself.

---

## 3. Screens

### 3.1 Imports page: upload form (`/imports/`)

Desktop (form column max 640px; the list below is full width).

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

Disclosure open (default for users with saved labels):

```
| v Trade more than one Topstep account?                                   |
|   Account (optional)                                                     |
|   [ Choose or type a name                        ]  <- datalist, blank   |
|   Only needed if you trade more than one Topstep account.                |
|   Use the same name each time.                                           |
```

Below the form: the imports list (3.5).

Mobile (360px): one column, full-width controls, 44px min touch targets, Upload button full width. File picker uses the native picker.

### 3.2 Import detail (`/imports/<id>/`)

This is both the page the user lands on right after an upload and the page they reach later from the list. Same page, same content.

Header block (always present):

```
+--------------------------------------------------------------------------+
| <- Imports                                                               |
| topstep-export-2026-09-26.csv                     [ Delete this import ] |
| Uploaded Sep 26, 2026, 2:02 PM (America/New_York)                        |
| Broker: Topstep   Account: Combine 50K                                   |
+--------------------------------------------------------------------------+
```

- Title (h1) is the filename. Very long names truncate in the middle, full name in `title` and wrapped in the dialog.
- Upload time is in the user's timezone with the zone name shown, per the project rule (stored UTC).
- **Account line:** the label if non-blank; "no account name" if the batch imported rows with a blank label; "not recorded (nothing was imported)" if the batch imported no executions. `ImportBatch` has no label column, so the label is read from the batch's executions (spec). The third wording is honest about that limit.
- **Delete this import** is a visible outline button in the header (danger token on outline and text, not a filled red button). It is not hidden in an overflow menu here, because there is only one action. It opens the same dialog as the banner button.
- Counts are in the summary card below (Imported, Skipped with duplicate and conflict split, Failed).

Case: conflicts, blank Account (the hard case), one failed row, plus hash hint.

```
+--------------------------------------------------------------------------+
| <- Imports                                                               |
| topstep-export-2026-09-26.csv                     [ Delete this import ] |
| Uploaded Sep 26, 2026, 2:02 PM (America/New_York)                        |
| Broker: Topstep   Account: no account name                               |
|                                                                          |
| [i]  3 rows were not imported                                            |
|      Their IDs match trades you already imported, but the details        |
|      differ. This usually means the file is from a different account.    |
|      Nothing was lost or overwritten.                                    |
|      Delete this import first, then upload again with an Account name,   |
|      so nothing is counted twice.                                        |
|      [ Delete this import ]                                              |
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
| Row  Status               Symbol  Time (America/New_York)  Size  Note    |
| 12   (!) Skipped conflict CLZ6    Sep 3, 9:10 AM           1     Id al...|
| 15   (!) Skipped conflict MNQZ6   Sep 3, 10:42 AM          2     Id al...|
| 21   (!) Skipped conflict MNQZ6   Sep 4, 9:31 AM           1     Id al...|
| 30   (x) Failed           ESZ6    Sep 4, 11:05 AM          1     The P...|
|                                                                          |
| Showing 1-4 of 4                                                         |
+--------------------------------------------------------------------------+
```

Notes:
- DOM order: header, **conflict banner (only if conflicts > 0)**, summary card, row list.
- "Needs attention" only appears if conflict plus failed > 0, and is then the default filter. Otherwise "All" is the default.
- Status column always shows an icon **and** text. Never color alone.
- Conflict rows show symbol, time and size so the trader recognises them (spec). The Note column shows the ADR-0004 `error` text wrapped, max 2 lines with "Show more".
- Pagination: 50 rows per page, "Previous / Next" links that keep the filter (`?status=...&page=2`), footer text "Showing 51-100 of 212". Files are at most a few thousand rows (ADR-0003).
- Banner and two duplicate Delete buttons on one page are fine: both open the same dialog. Focus returns to whichever opened it.

Banner variant, Account filled in:

```
| [i]  3 rows were not imported                                            |
|      Under the Account name 'Combine 50K' those IDs already exist with   |
|      different details. Check that the name is the one you meant.        |
|      Nothing was lost or overwritten.                                    |
|      [ Delete this import ]                                              |
```

Hint with label mismatch (the "adds these trades a second time" case). **Banner-level notice** (same component as the conflict banner), because the fix is the same button and it is where ADR-0004's accepted risk surfaces:

```
| [i]  You uploaded this exact file before                                 |
|      On Sep 12, with Account 'Combine 50K'. Uploading it again with a    |
|      different name adds these trades a second time.                     |
|      [ Delete this import ]   [ View the Sep 12 import ]                 |
```

- Old batch had no label: "On Sep 12, with no Account name."
- Old batch imported zero executions: omit the mismatch banner, show only the plain hint line in the summary.
- Plain hint (same label): one info line inside the summary card, with a link. No banner, no button.
- If both a conflict banner and a mismatch banner ever render: conflict first, mismatch below, and only the conflict banner shows the Delete button.

### 3.3 Confirm dialog: Delete this import

Opened by any "Delete this import" control. `hx-get` fetches the body (counts and journal list are current), then `dialog.showModal()`. Without JS, the control is a link to `/imports/<id>/delete/`, which renders the same body as a full page with a plain form (GET never deletes; POST with CSRF does).

Three variants, chosen server-side from ADR-0005 §5 inputs.

**Variant 1: no journal entries (N = 0).** One step.

```
+----------------------------------------------------------+
| Delete this import?                                [ x ] |
|                                                          |
| topstep-export-2026-09-26.csv                            |
| Uploaded Sep 26, 2026, 2:02 PM  -  Account: Combine 50K  |
|                                                          |
| This removes the 40 trades that came from this file, and |
| the upload record (including 4 rows that were skipped or |
| could not be read).                                      |
| Your other imports stay as they are.                     |
|                                                          |
| This can't be undone.                                    |
|                                                          |
|                 [ Cancel ]   [ Delete import ]           |
+----------------------------------------------------------+
```

**Variant 2: journal entries exist (N > 0).** Two steps inside one dialog: (1) read what will go, (2) tick the box, which enables the button.

```
+----------------------------------------------------------+
| Delete this import?                                [ x ] |
|                                                          |
| topstep-export-2026-09-26.csv                            |
| Uploaded Sep 26, 2026, 2:02 PM  -  Account: Combine 50K  |
|                                                          |
| This removes the 40 trades that came from this file, and |
| the upload record. Your other imports stay as they are.  |
|                                                          |
| You've journaled 3 of these trades (2 with written       |
| notes). Deleting the import deletes those journal        |
| entries too.                                             |
|                                                          |
| Journal entries that will be deleted                     |
| +------------------------------------------------------+ |
| | CLZ6   Sep 3, 9:10 AM    Rules: followed             | |
| |   "Waited for the retest, sized down after the la..."| |
| |   View trade (opens in a new tab)                    | |
| | MNQZ6  Sep 3, 10:42 AM   Rules: not followed         | |
| |   "Chased the second push. Felt behind after the ..."| |
| |   View trade (opens in a new tab)                    | |
| | ESZ6   Sep 4, 11:05 AM   Rules: not answered         | |
| |   (no written note)                                  | |
| |   View trade (opens in a new tab)                    | |
| +------------------------------------------------------+ |
| Want to keep any of your writing? Copy it from the list  |
| or open the trade first.                                 |
|                                                          |
| [ ] Also delete my 3 journal entries (2 with written     |
|     notes). This can't be undone.                        |
|                                                          |
|        [ Cancel ]   [ Delete import and entries ]        |
|                     ^ disabled until the box is ticked   |
+----------------------------------------------------------+
```

**Variant 3: the batch added no trades** (trade count 0; every row was skipped or failed, for example a re-upload of the same file).

```
| This import added no trades (every row was skipped or failed).      |
| Deleting it only removes the upload record. Your trades are not     |
| affected.                                                           |
|                 [ Cancel ]   [ Delete import ]                      |
```

Common rules:

- **"Original batch" line (ADR-0005 accepted surprise).** If the same file (same hash) was uploaded again later and that later upload imported nothing, add above "This can't be undone": "You uploaded this file again on Sep 27, but that upload added nothing new, so these trades exist only through this import. Deleting it removes them." Shown in variants 1 and 2 only.
- **Journal list:** per entry, symbol, opened-at (user tz), the rules-followed answer as icon plus words ("followed", "not followed", "not answered"), first ~80 characters of the note (or "no written note"), and a "View trade" link that opens in a new tab so the dialog and the tick box are not lost. Scroll container `max-height: 40vh`, `tabindex="0"`, `role="region"`, `aria-label="Journal entries that will be deleted"`. First 20 entries render, then a "Show all {N}" button (htmx) if N > 20, so the user can copy any note.
- **Singular and plural:** "1 trade", "1 journal entry (1 with a written note)". If M = 0: "(none with written notes)". Never "0 with notes" in the body sentence; the list still shows "no written note" per entry.
- **Button labels:** Cancel / **Delete import** (variants 1 and 3) / **Delete import and entries** (variant 2). Delete is an outline button in the danger token plus the words, never a filled red slab. The tick box is not styled as an alarm; it uses the normal text color.
- **Focus on open:** variant 1 and 3 focus **Cancel**. Variant 2 focuses the dialog title (`tabindex="-1"`) so a screen reader starts reading at the top. Never focus Delete.
- **Loading:** body shows a 3-line skeleton while `hx-get` runs. The dialog does not open until the body arrives, unless it takes over 300 ms, then it opens with the skeleton and `aria-busy="true"`.
- **Submitting:** `hx-post` carries the CSRF token, the journal count the user was shown, and the tick box value. Button reads "Deleting..." and is disabled. Success: `HX-Redirect` to `/imports/`.
- **Server refused, box not ticked** (JS off, or tampered): dialog re-renders with the inline message "Tick the box to confirm. Nothing was deleted." above the checkbox, checkbox focused.
- **Stale count** (the user journaled more trades in another tab; server rolled back): dialog re-renders in place with a notice at the top, `role="status"`: "Your journal changed while this was open, so nothing was deleted. The list below is up to date." The box is **unticked** and the new N and M are shown. Focus moves to that notice. The user has to read the new list and tick again.
- **Server or network failure:** the dialog stays open with "Something went wrong and nothing was deleted. Try again in a moment." Buttons re-enabled. Variant 2: the tick stays as the user left it.
- **Batch already gone** (double click, other tab): redirect to `/imports/` with flash "That import was already deleted." Not an error.
- **Not owner:** 404, same response as a missing id (isolation).

### 3.4 Imports page after delete: flash and empty state

The flash is `role="status"`, has a close button, and does not auto-dismiss.

Flash text, by what was removed:

| Removed | Flash |
|---|---|
| Trades only | Import deleted. {n} trades removed. Ready when you are. |
| Trades plus journal entries | Import deleted. {n} trades and {N} journal entries removed. Ready when you are. |
| No trades (variant 3) | Import deleted. No trades were affected. Ready when you are. |
| From a banner, appended | If you meant to add an Account name, it's open below. |
| Stale link or double click | That import was already deleted. |

Case A: user has other imports.

```
+--------------------------------------------------------------------------+
| [ok] Import deleted. 40 trades removed. Ready when you are.              |
|      If you meant to add an Account name, it's open below.       [ x ]   |
|                                                                          |
| Import trades                                                            |
| File   [ Choose CSV file... ]                                            |
| v Trade more than one Topstep account?          <- open, input focused   |
|   Account (optional) [ Choose or type a name ]  (focus only if from banner)
| [ Upload ]                                                               |
|                                                                          |
| Your imports                                                             |
| (list, section 3.5)                                                      |
+--------------------------------------------------------------------------+
```

Case B: that was the user's only import (list empty).

```
| [ok] Import deleted. 40 trades removed. Ready when you are.              |
|                                                                          |
| Import trades                                                            |
| (same form, Account disclosure open)                                     |
|                                                                          |
| No imports yet                                                           |
| Your uploads will show up here, with what was imported and what was      |
| skipped.                                                                 |
```

- **Focus after delete:** from a conflict or mismatch banner (`?from=banner`) focus goes to the Account input. From the detail header button or the list row menu, focus goes to the flash (so the result is announced and the user chooses what to do next).
- The browser cannot keep the chosen file, so the file picker is empty. The flash does not promise otherwise.
- Old URL `/imports/<deleted id>/` (bookmark, back button): redirect to `/imports/` with flash "That import was deleted." Someone else's batch id stays a 404.
- If the user now has zero trades, the trades list shows its own standard empty state (owned by the trades-list design).

### 3.5 Imports list (`/imports/`, below the form) and row menu

The list is part of the `/imports/` page. Newest first by upload time. 25 per page with Previous / Next links.

Desktop:

```
+--------------------------------------------------------------------------+
| Your imports                                                             |
|                                                                          |
| Uploaded (America/New_York)  File                     Account   Imported |
|                                                                Skipped  |
|                                                                Failed    |
| ------------------------------------------------------------------------ |
| Sep 26, 2:02 PM   topstep-export-2026-09-26.csv        Combine   40  3  1 |
|                   [i] Needs attention                   50K            [...]|
| Sep 12, 9:15 AM   topstep-export-a.csv                 no name   44  0  0 |
|                                                                        [...]|
+--------------------------------------------------------------------------+
```

Simplified column spec (build this, the sketch above is only for shape):

| Column | Content |
|---|---|
| Uploaded | Date and time in the user's timezone. Zone named once in the column header. |
| File | Filename as a link to `/imports/<id>/`, middle-truncated. Under it, a small "Needs attention" text badge (info icon plus text) if conflicts plus failed > 0. |
| Account | Label, or "no name". A batch that imported nothing shows "-" with visually hidden text "not recorded". |
| Imported / Skipped / Failed | Three number columns, headers spelled out. Zero shown as `0`, not blank. |
| Actions | One button, `aria-label="Actions for {filename}"`. |

Row menu (disclosure pattern, not `role="menu"`, so there is no arrow-key obligation):

```
                                             [ ... ]
                                  +---------------------------+
                                  | View details              |
                                  | Delete this import        |   <- danger text + icon
                                  +---------------------------+
```

- Button has `aria-expanded` and `aria-controls`. Items are a link and a button in normal tab order. Esc closes the menu and returns focus to the button. Clicking outside closes it.
- "Delete this import" opens the same dialog (3.3). Focus returns to the row's menu button after Cancel or Esc.
- The menu items are 44px tall on mobile.

Mobile (360px): each row becomes a card. Line 1: filename (link) and the actions button. Line 2: upload time. Line 3: "Account: Combine 50K". Line 4: "Imported 40, skipped 3, failed 1" (words, not just numbers). "Needs attention" badge under it when it applies.

**Empty state** (no imports): see 3.4 case B. It is also the first-visit state, with the copy "No imports yet".

---

## 4. States

### Upload form

| State | What the user sees |
|---|---|
| Empty (default) | Form as 3.1. Disclosure collapsed if the user has no saved labels, open if they do. Upload enabled (a missing file shows a message on submit, not a disabled button). |
| Populated | File name shown by the browser. Account typed or chosen. |
| Account too long | Live counter from 50 chars ("52 / 64"). At 65+, inline message: "That name is 70 characters and the limit is 64. Shorten it a little." Field gets `aria-invalid="true"` and the message via `aria-describedby`. Never truncated. On server error the field partial is swapped via htmx so the **chosen file stays selected**. Without JS the page re-renders and the file must be re-picked. |
| No file | Inline under file input: "Choose a CSV file to upload." |
| Wrong file type / unreadable | Inline under file input: "This file does not look like a TopstepX export. Check that you exported trades as CSV." (Exact rule comes from the importer.) |
| Loading (parsing, sync) | Upload button becomes "Importing..." and is disabled (`hx-disabled-elt`). File and Account inputs disabled. Polite live region: "Importing your file". Typical files finish in about a second. |
| Server failure | Above the form, not a banner: "We could not finish this import and nothing was saved. Try again in a moment." Form stays filled. `[assumes: whole import is one transaction; confirm with backend, question E]` |

### Import detail

| State | What the user sees |
|---|---|
| Clean success | Header, no banner. Summary "Imported 44, skipped 0, failed 0" and "All 44 rows imported." Row list default filter "All". |
| Only duplicates | No banner. "These rows were already imported earlier, so nothing was added twice." Plain hint if the hash matches. |
| Conflicts | Conflict banner (blank or filled variant), summary, rows under "Needs attention". |
| Failed only | No banner. Quiet notice under summary. Default filter "Needs attention". |
| Conflicts and failed | Conflict banner, summary with the failed notice, list "Needs attention" (conflicts first). |
| Nothing imported (all skipped/failed) | Summary leads with "No new trades were added." plus the relevant banner or notice. Account line reads "not recorded (nothing was imported)". |
| Loading | Server-rendered, no loading state. Filter switches use `hx-get` with `hx-indicator`; table gets `aria-busy="true"` while swapping. |
| Error | Standard app error page for 500s. Nothing on this page writes data, so no "nothing was saved" wording is needed. |
| Empty filter | "No rows with this status." |
| Deleted (owner) | Redirect to `/imports/` with flash "That import was deleted." |
| Not owner | 404. |

### Imports list

| State | What the user sees |
|---|---|
| Empty | "No imports yet" block (3.4 case B). Upload form stays above it. |
| Populated | Table (desktop) or cards (mobile) as in 3.5. |
| Loading | Server-rendered with the page. Paging uses normal links (full page load), so no loading state to design. |
| Error | Standard app error page. The upload form is not shown separately from it. |
| Row menu open / closed | Disclosure list under the button. One menu open at a time. |
| Just deleted | Flash on top (3.4). |

### Confirm dialog

| State | What the user sees |
|---|---|
| Loading | Skeleton, buttons absent or disabled. |
| Ready, simple | Variant 1 or 3, focus on Cancel. |
| Ready, two-step, box unticked | Variant 2, Delete disabled, focus on title. |
| Ready, two-step, box ticked | Delete enabled. Live region says "Delete button is now available". Unticking disables it again and says "Delete button is not available". |
| Submitting | "Deleting..." disabled, Cancel disabled. |
| Stale count | Notice on top, new list, box unticked (see 3.3). |
| Failed | Inline message, dialog stays open, nothing was deleted. |
| Already gone | Redirect with flash. |

---

## 5. Microcopy

Tone: say what happened, say what is or is not lost, say the next step. Never "error", "invalid", "duplicate" as blame, or "you should have". Delete copy is honest about permanence: it says "This can't be undone" once, plainly, and does not use warning icons, exclamation marks, bold red text, or words like "warning", "permanent(ly)", "destroy", "lose your work". (Spec criterion 12 applies to banner and row text; this doc follows it everywhere.)

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
| Conflict row note | "Id already imported with different contents. If this is a second account, fill in the Account field and re-upload." |
| Detail Account line | Account: {label} / Account: no account name / Account: not recorded (nothing was imported) |
| Banner and header button, menu item | Delete this import |
| Dialog title | Delete this import? |
| Dialog subtitle | {filename}, uploaded {date time tz}. Account: {label / no account name / not recorded} |
| Body, variant 1 | This removes the {n} trades that came from this file, and the upload record (including {k} rows that were skipped or could not be read). Your other imports stay as they are. |
| Body, variant 1, k = 0 | This removes the {n} trades that came from this file, and the upload record. Your other imports stay as they are. |
| Body, variant 2 lead | This removes the {n} trades that came from this file, and the upload record. Your other imports stay as they are. |
| Body, variant 2 journal | You've journaled {N} of these trades ({M} with written notes). Deleting the import deletes those journal entries too. |
| List heading | Journal entries that will be deleted |
| List helper | Want to keep any of your writing? Copy it from the list or open the trade first. |
| List row, no note | (no written note) |
| Rules answers | followed / not followed / not answered |
| Checkbox | Also delete my {N} journal entries ({M} with written notes). This can't be undone. (N = 1: "my 1 journal entry (1 with a written note)". M = 0: "(none with written notes)") |
| Body, variant 3 | This import added no trades (every row was skipped or failed). Deleting it only removes the upload record. Your trades are not affected. |
| Original-batch line | You uploaded this file again on {date}, but that upload added nothing new, so these trades exist only through this import. Deleting it removes them. |
| Permanence line, variants 1 and 3 | This can't be undone. |
| Dialog buttons | Cancel / Delete import / (two-step) Delete import and entries |
| Deleting state | Deleting... |
| Box-not-ticked message | Tick the box to confirm. Nothing was deleted. |
| Stale notice | Your journal changed while this was open, so nothing was deleted. The list below is up to date. |
| Dialog failure | Something went wrong and nothing was deleted. Try again in a moment. |
| Live region, tick | Delete button is now available / Delete button is not available |
| Flashes | See the table in 3.4 |
| Empty list | No imports yet. Your uploads will show up here, with what was imported and what was skipped. |
| Row menu button label | Actions for {filename} |

Status labels (text always shown with an icon): Imported, Skipped (duplicate), Skipped (conflict), Failed. Icons: check, equals/skip, info-circle, cross-in-circle. The conflict icon is info, not warning-triangle, on purpose (coach tone). The delete dialog uses no warning icon either.

---

## 6. Components

| Component | Notes |
|---|---|
| `AccountField` | `<details>` + labelled `<input list="account-labels" autocomplete="off">` + `<datalist id="account-labels">` (max 10, most recent first, user's own labels only, this broker) + help text + optional counter (Alpine length check). Django partial so htmx can swap it on validation failure. |
| `Notice` | One component, variants `info`, `success`, `attention`. Icon + title + body + optional action row. `role="status"` for flash, hints and the stale notice. No `role="alert"` anywhere on these screens. Used for conflict banner, mismatch notice, failed notice, hint line, flash, stale notice. |
| `ImportSummary` | Counts row, hint line, failed notice. |
| `ImportHeader` | Filename h1, upload time with zone, broker, Account line, Delete button. |
| `StatusBadge` | Icon + text label, tokens per status. |
| `RowTable` + `FilterTabs` | htmx-driven filters, `aria-current="true"` on the active tab, counts in each label. Dense table, sticky header on desktop, 2-line cards on mobile. Paged, 50 per page. |
| `ImportsTable` | The 3.5 list: table on desktop, cards on mobile, 25 per page. |
| `RowActionsMenu` | Disclosure button (`aria-expanded`, `aria-controls`) with a list holding a link and a button. Alpine for open/close, Esc, outside click, focus return. |
| `ConfirmDialog` | Native `<dialog>`, Alpine only to open/close and to gate the Delete button on the tick. Body from `hx-get`, submit via `hx-post` with `hx-disabled-elt`. Same partial renders in the dialog and in the no-JS full page `/imports/<id>/delete/`. Variants 1, 2, 3 are one template with conditionals. |
| `JournalEntryList` | Scrollable labelled region inside the dialog. Rows: symbol, time, rules answer (icon + word), note snippet, "View trade" link (`target="_blank"`, visually hidden "opens in a new tab"). "Show all {N}" button after 20. |

Color: use semantic tokens for `info`, `success`, `attention` (amber-ish, not red), and `danger` only on the Delete button outline and text and the "Delete this import" menu item. All text and icon contrast meets AA in light and dark mode. Do not use red for conflict rows.

---

## 7. Accessibility

- Every state is conveyed by icon plus text, never color alone (status badges, banners, filter counts, the rules answers in the journal list, the "Needs attention" badge).
- `<details>`/`<summary>` is keyboard operable (Enter, Space). Visible focus ring and 44px hit area.
- Datalist is native, so arrow keys and typing work. Support in screen readers varies, so the field must be fully usable by typing alone. Help text is tied with `aria-describedby`.
- Counter and validation message: `aria-describedby` plus `aria-invalid`. Announce the over-limit message via a `role="status"` region on blur or at 65 chars, not on every keystroke.
- Upload busy state is announced via a polite live region. Buttons keep their labels when disabled.
- Banners are placed before the summary in DOM order, so they are read first. They are not focus traps and never auto-dismiss. Flash has a close button.
- Filter tabs are links or buttons with `aria-current`, not `role="tab"`, to avoid arrow-key tabpanel obligations. Tables have `<caption class="sr-only">` and real `<th scope>`.
- Times are shown in the user's timezone, with the zone named once in the column header or the header block.
- Touch targets at least 44px on mobile. Text scales to 200% without horizontal scroll. Respect `prefers-reduced-motion` (no animation is required).

### Row menu

- Disclosure pattern: a button with `aria-expanded` and `aria-controls`, and a plain list of link/button items in tab order. Enter or Space opens. Esc closes and returns focus to the button. Tab past the last item closes it. The button label names the file ("Actions for topstep-export-a.csv"), so a screen-reader user hears which row they are on.

### Confirm dialog, in general

- Native `<dialog>` with `showModal()`: focus is trapped, the page behind is inert, Esc closes. `aria-labelledby` points to the title, `aria-describedby` to the first body paragraph.
- Focus on open: **Cancel** in the simple variants, the **title** (`tabindex="-1"`) in the two-step variant. Never Delete.
- Focus returns to the control that opened it (banner button, header button, or the row's menu button). If that control no longer exists after a failed delete re-render, focus the page h1.
- The stale and not-ticked messages are `role="status"` and receive focus programmatically when they appear, so they are not missed.

### The two-step confirm (journal entries exist)

It is two deliberate steps, both keyboard and screen-reader reachable, and the server enforces step 2 as well:

1. **Read the list.** A screen reader hears the title, then the body: how many trades go, then "You've journaled N of these trades (M with written notes)". The list is a labelled region in the natural reading order, so it is read before the checkbox is reached. Sighted keyboard users can scroll it (it is focusable, arrow keys scroll) and Tab through the "View trade" links.
2. **Tick the box.** A native `<input type="checkbox">` with a real `<label>` whose text is the full sentence ("Also delete my 3 journal entries (2 with written notes). This can't be undone."). It is a normal control: Space toggles it, its state is announced as checked/unchecked.

The Delete button is `disabled` (native) until the box is ticked. Native `disabled` removes it from the tab order and screen readers announce it as dimmed when browsed. To avoid the user hunting for why, the tick box sits directly above the buttons, and a polite live region announces "Delete button is now available" on tick and "Delete button is not available" on untick. Tab order after the list: checkbox, Cancel, Delete. Cancel always comes first and is always enabled. Enter on the focused checkbox does not submit the form. The button's visual disabled style keeps AA contrast for its text (disabled controls are exempt, but the label must still be legible).

No JS: the button is enabled, and the server rejects an unticked submit with the message in 3.3 (checkbox focused). This keeps the guard real without depending on scripts.

---

## 8. Edge cases

1. **Labels differing only by case or spacing** ("combine 50k" vs "Combine 50K"): stored as typed, treated as different (ADR-0004). Datalist shows both. Out of scope to merge.
2. **Blank label, conflicts, and the user re-uploads without deleting**: same conflict banner again. The non-conflicting rows are `skipped_duplicate`. No double counting.
3. **Mismatch hint, first attempt under a blank label**: "with no Account name".
4. **Hash matches more than one earlier batch**: link to the most recent one that imported at least one execution.
5. **User deletes the batch the hint linked to** and revisits: hint is gone.
6. **Two tabs, both delete**: second lands on the "already deleted" flash.
7. **Very long filename**: middle-truncate in the header, list and card, with the full name in `title`. Wrapped in full inside the dialog.
8. **File with both new rows and conflicts**: the banner says "Delete this import" even though some rows are good. The dialog's trade count makes the consequence visible. That is the reason for the confirm step.
9. **Conflict banner when the user has only one account** (for example a re-exported file with an adjusted price): the "usually" wording covers it.
10. **Zero-row or header-only file**: handled by the file-level message in section 4, not the banner.
11. **Journaled trades in the batch**: two-step confirm (3.3 variant 2). Deleting also removes those journal entries. No undo.
12. **Journal changed while the dialog was open** (another tab): server rolls back, dialog re-shows with fresh list and an unticked box (3.3 stale count). Nothing is lost silently.
13. **Deleting the original batch when a later re-upload of the same file exists**: the trades go, even though the re-upload is still listed. That re-upload imported nothing, so the original's dialog carries the "original-batch line". Deleting the re-upload (variant 3) only removes its record.
14. **Batch with zero imported trades** (all skipped or failed): variant 3, "Your trades are not affected".
15. **Very many journal entries** (dozens): list scrolls in its 40vh region, first 20 shown, "Show all". Note snippets are cut at ~80 characters, so full notes are read on the trade page (link opens in a new tab).
16. **Journal entries with no written note** (only a rules answer): counted in N, shown as "(no written note)", not counted in M.
17. **Deleting the only import**: empty state in 3.4 case B.
18. **Back button after delete**: `/imports/<deleted id>/` redirects with a flash (3.4). The confirm URL for a deleted batch does the same.
19. **Batch page opened by someone else's id**: 404, identical to a missing id.

---

## 9. Open questions

Resolved by the 2026-09-26 rulings: datalist only (was A), `/imports/` list and detail in MVP (was B), delete from banner and row menu (was C), notes semantics (was D, ADR-0005). Still open:

For `backend-engineer`:
- **E.** Is a whole import one transaction, so "nothing was saved" on upload failure is true? Confirm before the frontend ships that wording.
- **F.** Confirm the detail page can compute on read: same hash, earlier batch, and its label (from executions). Also the reverse for the dialog's "original-batch line": later batches with the same hash that imported nothing.
- **G.** htmx partial swap of `AccountField` on a server validation error, so the file input stays selected.
- **H. RESOLVED (user, 2026-09-26: H1).** Accept "not recorded (nothing was imported)" for a batch that imported no executions. No `ImportBatch` label column; no schema change.

For the orchestrator or user:
- **I.** The journal list caps at 20 rows before "Show all". Fine, or show everything always? (Cheap either way.)
- **J.** The two-step dialog button reads "Delete import and entries". Alternative: keep "Delete import" everywhere. I prefer the longer label because the button then says what it does.

## Conventions used (until a design-system doc exists)

- Content column max 640px for forms, full width for tables. 4px spacing scale (4, 8, 12, 16, 24, 32). Type: 14px body, 12px table meta, 20px page title.
- Semantic color tokens by role (`info`, `success`, `attention`, `danger`, `surface`, `border`), each with light and dark values. No hard-coded colors in templates.
- Status is always icon plus text. Amounts and P&L (not on these screens) always carry a sign or a word as well as color.
- Coach tone: name what happened, what is or is not lost, and the next step. Destructive actions say "This can't be undone" once, plainly, with no warning icons or alarm words.
- Destructive confirms: native `<dialog>`, focus starts on Cancel (or the title when the body is long), Delete is an outline button in the `danger` token with a specific label, and when the action removes user-written content it needs a counted tick box listing that content first.
- Row-level actions use a disclosure button with a list of links/buttons, not `role="menu"`.
