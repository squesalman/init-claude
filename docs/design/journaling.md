# Design: per-trade journal page, "My rules", journal link on the trades list, Avg R states

Spec: [`journaling.md`](../product/features/journaling.md) (product; Decisions 1-6 are locked). Formulas and reason codes: [`pnl-and-matching.md`](../domain/pnl-and-matching.md) §3 (R rules, including the 2026-09-30 multi-leg and `risk_currency` rulings, which this doc only refers to). Layout, tokens, `Notice`, `FormField`, error-summary and tone conventions: [`auth-and-trades-list.md`](auth-and-trades-list.md) (sections 4.3, 5, 6, 7, 8) and [`import-account-label.md`](import-account-label.md) (section 3.3 dialog, section 5 tone, section 7 a11y). This doc reuses them and adds only what is new.
Status: final for `frontend-engineer`. Single source for layout and copy of the journal page. It changes three places in the two existing design docs (section 9).

Scope: full page at `/trades/<opening_execution_id>/journal/` (Decision 1), no inline htmx panel. The one htmx use is the small "My rules" save (5.4), with a no-JS fallback.

---

## 1. Rulings on the routed items

| Item | Ruling | Why |
|---|---|---|
| Risk-currency suffix | Plain ISO code ("USD") as a fixed suffix inside the input box, right side. Not a symbol, not editable, not a placeholder. It is tied to the input with `aria-describedby`, and the help text repeats it ("in USD"). Stop price has no suffix (it is a price). | Any ISO currency works (domain §3), so `$` is wrong for EUR. A suffix stays visible while typing; a placeholder does not. Help text repeat means a screen reader hears it once, in context. |
| My rules default | **Collapsed by default, always.** The closed panel shows a preview: the first 3 lines of the rules, or the empty prompt when there are none. Opens automatically only after a rules save error or right after a rules save (so the confirmation is seen). This **differs from the PM recommendation** (open until first save). | An open empty panel pushes the Yes/No question below the fold on a phone on every trade, for anyone who never writes rules. The prompt in the closed panel is still visible every time, so discovery is kept. Friction is the top risk. |
| "Left out: N" | N only, no per-reason breakdown, singular/plural: "Left out: 1 closed trade without usable risk" / "Left out: 4 closed trades without usable risk". Shown whenever N > 0, including when n = 0. Not a link (no filters in this slice). The per-reason explanation lives on each trade's journal page (4.5). | Matches the win rate card's "Excludes N" pattern. The reasons are fixable one trade at a time, on that trade. |
| Save failure copy | "We couldn't save that just now. Your text is still here, so try again in a moment." Only true if the view re-renders the form with the submitted values (DB error and cross-tenant error included), so backend must do that instead of a bare 500. | A trader who typed a long note after a loss must never lose it. Honest and calm, says what is safe. |
| Delete dialog mentions stop/risk | Yes, but in the **body sentence**, not the checkbox: "...along with any stop or planned risk on it." Plus a muted line on each list row that has one: "Has a stop or planned risk amount." Checkbox text unchanged. | The checkbox label is the thing the user agrees to and is already long; the body is where consequences are read. Risk-only entries otherwise look empty ("no written note") and the loss of R inputs would be invisible. |
| Multi-leg stop side-check | Not decided here. Domain §3 (ruling 2026-09-30) says accepted unchecked and R stays null. The form follows whatever that section says; UI copy for the multi-leg stop is in 4.4. | Owned by `trading-domain-expert`. |

Other decisions made here (all inside the spec's locked set):

- After a successful save the page **stays on the journal page** (PRG to the same URL) with a success flash. The bottom secondary action is "Back to trades", not "Cancel", because after a save "Cancel" would be wrong and there is nothing to undo. No unsaved-changes prompt (low stakes; adding one is the first thing to add if people report lost text).
- No "(required)" or asterisks anywhere: nothing is required. One line at the top says so.
- List state labels use "Didn't follow rules" instead of the spec's "Broke rules" (coach tone, no shaming). See section 9.

---

## 2. Flow

```
/trades/  -- row link "Add journal" / state label --> /trades/<id>/journal/  (GET: blank or prefilled)
    ^                                                    |
    |  "Trades" back link / "Back to trades"             +-- Save journal (POST)
    |  (returns to /trades/#trade-<id>)                  |     ok -> 302 same URL + flash "Journal saved."
    +----------------------------------------------------+     errors -> 200, summary + field messages, values kept
                                                         |     all blank, no entry yet -> 200, info notice, nothing created
                                                         |     failure -> 200, values kept, "couldn't save" notice
                                                         +-- My rules panel: Save rules (POST /rules/)
                                                               htmx: swaps the panel only (journal text untouched)
                                                               no JS: 302 back to the journal page + flash
Import delete dialog: entry "View trade" -> /trades/<id>/journal/ (new tab)
```

---

## 3. The page: `/trades/<opening_execution_id>/journal/`

Form width column max 640px (design conventions), same on desktop and mobile with 16px margins. One `<h1>`, `<title>` is "Journal: {symbol}". Page order, top to bottom:

1. Back link
2. Trade summary (header)
3. Intro line
4. My rules panel
5. "Did you follow your rules?" question
6. Note
7. Risk section (collapsible)
8. Actions (Save, Back)

Order rationale: the rules panel must sit directly above the question (spec). The question is one tap, so it goes before the typing field. Risk is progressive disclosure.

### 3.1 Populated, desktop

```
+--------------------------------------------------------------+
| <- Trades                                                    |
|                                                              |
| MNQZ6  (^) Long                          [(+) Win]  +$23.50  |
| Opened Sep 26, 2:31 PM (America/New_York)                    |
| Qty 2  -  19,850.25 to 19,862.00  -  12m  -  Combine 50K     |
|                                                              |
| Nothing here is required. Save whatever you have; you can    |
| come back and change it.                                     |
|                                                              |
| +----------------------------------------------------------+ |
| | My rules                                            Edit | |  <- closed panel
| | Wait for the retest. Max 2 contracts. No trades in the   | |
| | first five minutes.                                      | |
| +----------------------------------------------------------+ |
|                                                              |
| Did you follow your rules on this trade?                     |
| This is what marks a trade as journaled. You can leave it    |
| for later and change your answer any time.                   |
| ( ) Followed my rules      ( ) Didn't follow my rules        |
|                                                              |
| Note                                                         |
| What was your thinking going in, and how did it go? Anything |
| you'd want to remember.                                      |
| +----------------------------------------------------------+ |
| |                                                          | |
| |                                                          | |
| +----------------------------------------------------------+ |
|                                                              |
| > Risk for Avg R (optional)                                  |
|                                                              |
| [ Save journal ]   Back to trades                            |
+--------------------------------------------------------------+
```

Header details:

- Back link "Trades" (left arrow icon, decorative) goes to `/trades/#trade-{opening_execution_id}`. Rows and cards on the list carry `id="trade-{id}"` with `scroll-margin-top` clearing the sticky header. Sort order is not preserved (accepted).
- h1: symbol, then side as icon plus word (same arrow icons as the list).
- Result badge and signed net P&L reuse `result_badge.html` and the list's P&L formatter and `text-gain` / `text-loss` (sign always printed). Open trade: "Open" badge, P&L shows the word "Open".
- Line 2: opened time in the user's zone (zone named once here). Line 3: quantity, entry to exit ("-" plus hidden "not closed yet" if open), duration ("Open" if open), account label only when non-blank (escape it). Multi-entry trades add "{n} entries" after quantity.
- The header is text only, not a link.

Intro line: muted text, one sentence. Not a Notice.

### 3.2 My rules panel

Structure: a bordered card containing a native `<details>` (summary "My rules") plus a preview `<p>` placed **after** the `<details>` and hidden by CSS when it is open (`details[open] + .rules-preview`). No JS is needed to open or close it.

Closed states:

| State | Summary right-hand text | Preview (under the summary) |
|---|---|---|
| Rules saved | "Edit" | First 3 lines of the rules (`line-clamp-3`, `whitespace-pre-line`, escaped) |
| No rules yet | "Add" | "Add your rules so you can check each trade against them." (muted) |

Open state (right-hand text swaps to "Close" by CSS on `details[open]`):

```
| My rules                                                Close |
| Your rules                                                    |
| +-----------------------------------------------------------+ |
| | Wait for the retest. Max 2 contracts. ...                 | |  <- textarea, 5 rows
| +-----------------------------------------------------------+ |
| In your own words. Your rules aren't saved with each answer,  |
| so if you reword them, past answers still reflect the earlier |
| wording.                                          412 / 10,000|
| [ Save rules ]                                                |
```

- The textarea is the edit surface; there is no separate read mode. Saving is explicit ("Save rules"). Closing the panel without saving simply leaves the saved rules unchanged. No Cancel button (closing is the cancel).
- It is its **own `<form>`** (never nested in the journal form), `POST /rules/`, CSRF, one field `trading_rules`, no user id. Placed above the journal form in DOM order.
- The panel has `max-height` on the textarea (about 12 rows) with its own scroll, so long rules never push the journal form down.
- Save rules with JS: `hx-post="/rules/"`, `hx-target` the whole panel, `hx-swap="outerHTML"`. The journal form's typed text is not touched, which is the reason for htmx here. Response is the panel, open, with the status line "Your rules are saved." (`role="status"`, `tabindex="-1"`, receives focus). Next page load shows it closed again.
- Save rules without JS: normal POST; the form carries a hidden `next` (the journal URL; server validates with `url_has_allowed_host_and_scheme`); 302 back with flash "Your rules are saved." Text typed in the journal form is lost in this path (accepted for no-JS).
- Errors: over 10,000 characters gives a field error under the textarea (`aria-invalid`, `aria-describedby`), panel stays open, focus to the message. Save failure shows the rules failure notice inside the panel, text kept.
- Saving an empty textarea is allowed (it clears the rules); the panel then falls back to the "Add" state.
- Saving rules never touches any journal entry (spec AC 26).

### 3.3 "Did you follow your rules on this trade?"

A `<fieldset>` with a `<legend>` (the question) and help text tied by `aria-describedby`. Two native radios, same `name`, **no default**.

```
( ) Followed my rules       ( ) Didn't follow my rules
```

- Each option is a full-width tile on mobile (stacked, min 48px tall) and a side-by-side pair on desktop (min 44px tall, 16px gap). The whole tile is the `<label>` (large target).
- Selected tile: thicker border plus a check icon plus the words. **Both options use the same neutral tokens.** No green for Yes, no red for No; nothing communicates "good" or "bad".
- Unanswered is the state where neither is selected. It is not a third radio. The help text says it may be left for later.
- Once answered, it can only be changed to the other option, not cleared (spec). The help text says "change your answer any time", which stays true.
- Native arrow keys move within the group; Tab enters at the first radio when none is selected.
- Changing only the note leaves the answer as is (AC 7): the form posts the currently checked radio, or none.

### 3.4 Note

- `<label for>` "Note", help text (below the label) tied with `aria-describedby`, `<textarea rows="6">` (5 on desktop is fine), full width, no placeholder.
- **No `maxlength` attribute.** A native limit silently cuts pasted text. The cap is enforced by the counter and the server (10,000 characters after trimming, per spec).
- Counter (Alpine CSP component `charCounter` in `static/js/app.js`, no inline expressions): hidden until the text reaches **9,000 characters**, then shows "9,412 / 10,000" under the field, right-aligned, muted. Over the limit: the counter text changes to the too-long message and the field gets `aria-invalid="true"` (client side only as a hint; the server is the authority).
- The counter's visible text is not a live region. A separate `role="status"` `sr-only` region announces only two moments: first reaching 9,000 ("You're close to the 10,000-character limit.") and going over ("Over the 10,000-character limit by {n}."), plus on blur if still over. Never on every keystroke.
- Length is counted in Unicode code points, and line breaks count as one character. **Backend must normalise `\r\n` to `\n` before the length check** so the browser counter and the server agree (edge case 5).
- Notes are stored trimmed; a whitespace-only note is stored as an empty string. The form does not show any message about it.
- The rules textarea uses the same component with its own copy.

### 3.5 Risk for Avg R (progressive disclosure)

A native `<details>` "Risk for Avg R (optional)". **Open** when either value is saved, when the submitted form has a risk error, or when the trade is multi-leg (so the hint is visible). Closed otherwise.

```
| v Risk for Avg R (optional)                                  |
|   Add a stop or a planned risk to include this trade in      |
|   Avg R. Both are optional.                                  |
|                                                              |
|   Stop price                                                 |
|   [ 19845.50                       ]                         |
|   Your initial stop, as a price. For this long trade it      |
|   sits below your entry of 19,850.25.                        |
|                                                              |
|   Planned risk                                               |
|   [ 62.50                     | USD ]                        |
|   The amount you planned to risk on this trade, in USD,      |
|   before fees.                                               |
|   Planned risk is used for R when both are set.  <- only if both set |
|                                                              |
|   (i) This trade counts in Avg R.                <- status line, 3.6 |
```

- Both inputs: `type="text"`, `inputmode="decimal"`, `autocomplete="off"`, `autocapitalize="none"`, `spellcheck="false"`. No `type="number"` (spinner and scroll-wheel edits, locale surprises).
- Number parsing (backend): plain digits with an optional period. Commas are accepted only as thousands separators (`1,250.50`) and stripped; anything else gets the "enter a number" message. Stop up to 10 decimal places, planned risk up to 4 (column scales). Whitespace trimmed.
- Suffix: `<span id="risk-currency">USD</span>` inside the same bordered box as the input (input has no right border, box has the focus ring). Visible text, contrast AA, not selectable-by-mistake. Input `aria-describedby="risk-help risk-currency"` (plus error id when present). No currency input exists; the value posted is ignored/never read (spec decision 4).
- The stop help text is dynamic by side (single-entry only): "...it sits below your entry of {entry}." (long) or "...above your entry of {entry}." (short). Multi-leg: the multi-leg help replaces it (4.4).
- "Planned risk is used for R when both are set." shows when both fields have a value in the current render (saved or submitted), under the planned risk help.
- Save applies `risk_currency` = trade currency when planned risk is set, cleared when blank (domain §3). Nothing on the page asks for it.

### 3.6 Risk status line (evidence for Avg R)

One line at the bottom of the risk section on GET and after save, only when an entry exists with at least one risk value, or the trade is open. It says whether this trade counts, in plain words. It never switches the input silently (spec). Text by domain reason code:

| Code | Line |
|---|---|
| valid | This trade counts in Avg R. |
| `trade_open` | This trade is still open. R is worked out once it closes. |
| `risk_not_positive` | The saved planned risk isn't more than 0, so this trade is left out of Avg R. Enter a new amount to fix it. |
| `risk_currency_mismatch` | Your saved planned risk is in {risk_currency}, but this trade is in {trade_currency}, so it's left out of Avg R. Save a new amount to update it. |
| `stop_not_a_risk` | Your saved stop isn't on the loss side of this trade's entry price, so it's left out of Avg R. |
| `stop_price_multi_leg` | This trade has more than one entry, so a stop can't give R. Enter planned risk to get R on this trade. |
| `no_risk_input` | (no line; the section intro already covers it) |

Icon is `info`, not `attention`; the line is muted text on the normal surface, not a Notice. It is `role="status"` only when it changes after a save (the flash covers announcement otherwise, so give it no live role on plain GET).

### 3.7 Actions

```
[ Save journal ]        Back to trades
```

- "Save journal" is the one primary button, min 44px. On submit: "Saving..." disabled (label kept when JS is off), using the existing `data-busy` pattern in `app.js`.
- "Back to trades" is a plain secondary-style link to `/trades/#trade-{id}`. No confirm.
- Mobile: the actions bar is `sticky bottom-0` inside the form with `padding-bottom: env(safe-area-inset-bottom)`, surface background and a top border, Save full width, Back below it. Enter in the note textarea inserts a newline (does not submit).
- Enter in the stop or planned risk input submits the form (native).

---

## 4. States

### 4.1 Journal form

| State | What the user sees |
|---|---|
| New (no entry) | Blank note, no radio selected, risk section closed (unless multi-leg), rules panel closed with preview or prompt. No flash. |
| Populated | Saved values prefilled. Risk section open if any risk value saved. Status line (3.6) if applicable. |
| Saved | 302 to the same URL, flash "Journal saved." (variant `success`). Flash region gets `tabindex="-1"` and focus so it is announced (use the `flash_attrs` block in `base.html`). Form shows the saved values. |
| Nothing to save | Only when no entry exists and every field is blank: 200, `info` Notice above the form "Nothing to save yet. Add a note, an answer, or a risk amount." focused. No row created (spec AC 5). |
| Validation error | 200, error summary (`attention` Notice, title "Please fix the items below.", one linked line per problem) focused, per-field messages (4.3), all values kept, risk section open if it has an error. |
| Save failure | 200, values kept, `attention` Notice at the top (focused): "We couldn't save that just now. Your text is still here, so try again in a moment." Same message for the generic cross-tenant form error (spec AC 14); the log line carries user id and model name only. |
| Submitting | Save button "Saving...", disabled. |
| Open trade | Everything works. Header shows "Open". Status line: "This trade is still open. R is worked out once it closes." |
| Multi-leg | Multi-leg stop help (4.4) always shown under the stop field. Risk section open. |
| Loading | Server-rendered, no skeleton. |
| Not found | 404 (4.6). |

### 4.2 My rules panel states

| State | What the user sees |
|---|---|
| Closed, empty | Summary "My rules" + "Add", preview "Add your rules so you can check each trade against them." |
| Closed, saved | Summary + "Edit", preview of the first 3 lines. |
| Open | Label "Your rules", textarea, help, counter (from 9,000), "Save rules". |
| Saving (htmx) | Button "Saving...", disabled, textarea kept. |
| Saved | Panel swapped, open, status "Your rules are saved." focused. |
| Too long | Field message under the textarea, panel open, focus to the message. |
| Failure | Notice inside the panel "We couldn't save your rules just now. Your text is still here, so try again in a moment." text kept. Also shown if the htmx request gets no page back (5xx, network) via the same `data-request-error` pattern the delete dialog uses. |

### 4.3 Field-level messages (shown under the field with an icon, tied by `aria-describedby`, `aria-invalid="true"`; no "Error:" prefix, per auth design 4.3)

| Field | Message |
|---|---|
| Note too long | That note is {n} characters and the limit is {limit}. Shorten it a little. |
| Rules too long | Your rules are {n} characters and the limit is {limit}. Shorten them a little. |
| Stop not a number | Enter a number like 19845.50. Use a period for decimals. |
| Stop too many decimals | Use up to 10 decimal places. |
| Stop wrong side | Your stop should sit on the loss side of your entry price. |
| Planned risk not a number | Enter a number like 62.50. Use a period for decimals. |
| Planned risk too many decimals | Use up to 4 decimal places. |
| Planned risk not positive | Planned risk needs to be more than 0. |
| Number too large | That number is too large. |

The stop wrong-side message applies to single-entry trades with no planned risk set only, as domain §3 defines it.

### 4.4 Multi-leg stop help (replaces the stop help on multi-entry trades)

"This trade has more than one entry, so a stop can't give R. Enter planned risk to get R on this trade." The field stays enabled and the value is saved (spec). No side-check message ever shows here (domain §3).

### 4.5 Where the "why it was left out" evidence lives

Avg R's "Left out: N" is a count only. The journal page of each such trade carries the reason (3.6). There is no breakdown on the list and no per-reason link in this slice.

### 4.6 404

Unknown id, another user's id, and a closing fill's id all return the same page (spec AC 11, 12). Standard shell with h1 "We couldn't find that trade", body "It may have been removed along with an import.", link "Back to trades". Do not say whose trade it is or that it exists.

---

## 5. `/trades/` list changes

### 5.1 Desktop table

New last column (after Net P&L, after Account when that column shows): `<th scope="col">Journal</th>`, not sortable, left aligned. One link per row; it is the only new tab stop per row.

```
... Result       Net P&L    Account       Journal
    (+) Win      +$23.50    Combine 50K   [+] Add journal
    (-) Loss     -$150.00   Combine 50K   [doc] Followed rules
    (=) Breakeven $0.00     Live          [doc] Didn't follow rules
    (o) Open     Open       Live          [pen] Note only
```

Link text is the state label (5.3). The accessible name adds context: visible text plus a visually hidden suffix " for {symbol}, opened {opened}", so a screen reader's links list does not read 177 identical "Add journal". Example accessible name: "Add journal for MNQZ6, opened Sep 26, 2:31 PM".

Links are underlined (not color alone), visible focus ring, no `target`. Each `<tr>` gets `id="trade-{opening_execution_id}"`.

### 5.2 Mobile cards

Each card gets one last line: a full-width link button, min 44px tall, label = state label (with the same icon), separated from the card body by a top border. `<li id="trade-{id}">`. The rest of the card is not a link (one target per card, no nested interactive areas).

```
| CLZ6  Short                 -$150.00 |
| (-) Loss                             |
| Sep 26, 9:10 AM                      |
| 1h 05m - Qty 1 - 80.15 to 80.30      |
| ------------------------------------ |
| [+] Add journal                   >  |
```

### 5.3 State labels (table and cards)

"Journaled" = the answer is set (`rules_followed` not null), as in the spec. "Not yet" = anything else. The distinction is shown by icon, weight and words, never color alone.

| Entry state | Label | Icon | Emphasis |
|---|---|---|---|
| No entry | Add journal | plus | Not yet: muted text (AA), regular weight |
| Answered Yes | Followed rules | document | Journaled: normal text color, medium weight |
| Answered No | Didn't follow rules | document | Journaled: same as Yes (no color difference between the two) |
| Unanswered, note only | Note only | pencil | Not yet |
| Unanswered, risk only | Risk only | pencil | Not yet |
| Unanswered, note and risk | Note and risk | pencil | Not yet |

Icons `plus`, `document`, `pencil` are new entries for `partials/icon.html` (20px, decorative; the words carry the meaning). They are deliberately not the Win/Loss check/minus icons, so the journal column is never read as a result.

No "journaled X of Y" counter and no nudging on the list (gamified-guilt pattern). Filters and sorting by journal state stay out of scope.

### 5.4 Keyboard and mobile

- Table: Tab reaches the journal link after the header sort links, row by row; Enter follows the link. No row-click behaviour, no hidden hover-only affordance.
- Returning with the back link lands at `/trades/#trade-{id}`; the browser Back button also works (pages are `no-store`, the list reloads).
- Mobile: link is the card's last line, 44px target, whole width.

---

## 6. Stat card: Avg R

Card frame, heading, screen-reader label and the `1.40 (n=32)` value format are unchanged (auth design 6.3). New rules for the extra lines:

| State | Value line | Lines under it |
|---|---|---|
| n = 0, no closed trades left out (no closed trades yet, or only open trades) | `— (n=0)` | Help text (new wording, below) |
| n = 0, some closed trades left out | `— (n=0)` | Help text, then "Left out: N closed trades without usable risk" |
| n > 0, N = 0 | `1.40 (n=32)` | none |
| n > 0, N > 0 | `1.40 (n=32)` | "Left out: N closed trades without usable risk" (muted, same style as "Excludes 2 breakeven trades.") |

- The help text shows **only when n = 0** (spec). It is muted text, same frame, no styling that reads as broken.
- Singular: "Left out: 1 closed trade without usable risk".
- Neutral: no grading words, no color by value, no comparison, no trend arrows.
- Per currency group as in the existing stats grouping; card strings still show the group's value only (multi-currency card layout is auth-design open question 6, unchanged).
- Screen reader value is unchanged; the "Left out" line is plain text and is read in order after the value.

New help text (n = 0): "Avg R shows your results in units of what you risked on each trade. Open a trade's journal and add a stop or a planned risk amount to include it. Trades without one are left out, never counted as zero."

### "How these are calculated" (only the Avg R item changes)

New Avg R item: "Avg R: net P&L after fees divided by the risk you entered, averaged over closed trades that have one. Risk is your planned risk amount if you set one, otherwise the distance from your entry to your stop times your size (trades with one entry only). Trades without usable risk are left out, never counted as zero."

The other three items (win rate, total P&L, times) are unchanged.

---

## 7. Import delete dialog: consistency changes

Layout and behaviour of `import-account-label.md` 3.3 are unchanged. Changes:

1. **Risk-only entry**: it counts in N and not in M (spec AC 31). Its row shows "Rules: not answered" with the existing `skip` icon, "(no written note)", and the new muted line "Has a stop or planned risk amount." It is never blank and never an error.
2. **Entry links**: each row's "View trade" link now exists and points to `/trades/<opening_execution_id>/journal/`, `target="_blank"`, `rel="noopener"`, with visually hidden " for {symbol}, opens in a new tab". (The template's "lands with the trade page; entry.url is always None" comment is now stale.)
3. **`DELETE_LIST_HELPER`** replaces the "open the trade first" promise: "Want to keep any of your writing? Copy it from the list, or use View trade to open that trade's journal in a new tab."
4. **Body sentence** mentions stop/risk (rulings, section 1). Checkbox text unchanged.
5. Answer words stay "followed / not followed / not answered" here. The list column uses the longer labels (5.3); the dialog uses them inside a "Rules: ..." phrase, so they do not need to match word for word.

---

## 8. Accessibility

- Every field has a real `<label for>` (or fieldset/legend for the radios). Help text and the currency suffix are tied with `aria-describedby`; error ids are appended, not replaced.
- Errors: same pattern as auth design 4.3. Summary `Notice` (`attention`, `tabindex="-1"`) receives focus on load and links to each field. Each message sits under its field with a decorative icon, `aria-invalid="true"`, no "Error:" prefix, no `role="alert"`. If the error is inside the closed risk `<details>`, the server renders it open so the link target is reachable.
- Focus after save: success flash (focused via `flash_attrs`). After "nothing to save" and save failure: that Notice. After a rules save via htmx: the panel's status line. After a rules validation error: the message under the textarea.
- Radios: fieldset with legend, native arrow keys, selected state is border weight plus check icon plus the words, contrast AA in light and dark, visible focus ring on the tile.
- Details/summary: native, Enter and Space, visible focus ring, 44px summary height, chevron plus text ("Add", "Edit", "Close"), never a color-only cue.
- P&L and result in the header carry sign and word as on the list (no color alone).
- Counter: not live per keystroke; two threshold announcements only (3.4).
- Touch targets 44px (Save, Back link, radio tiles 48px on mobile, summary rows, list links). Text scales to 200% with no horizontal scroll; the sticky action bar must not cover a focused field (use `scroll-padding-bottom` on the form).
- Coach-tone check: no praise or scolding on Yes/No, no red anywhere, no "you should", no advice. The status line describes only what the app did with the numbers.
- `prefers-reduced-motion`: nothing animated.
- Dark mode: uses existing tokens only (`surface`, `surface-raised`, `border`, `text`, `text-muted`, `info`, `attention`, `success`, `gain`, `loss`). No new colors.

---

## 9. Conflicts with existing docs (all resolved 2026-09-30)

Items 1-3 fixed in `auth-and-trades-list.md` and `import-account-label.md` (edits dated 2026-09-30); items 4-5 adopted in `product/features/journaling.md` (decisions 7-8); item 6 done in `docs/README.md`. Kept below for the record. One leftover: the auth 5.1 sketch header row still lacks the Journal column (the column table has it).

1. `auth-and-trades-list.md` 5.1 says "Trade rows are **not links** in this slice ... there is no trade page yet." Now false for the journal link column. Also the sketch header row and column table (section 5.1) need the "Journal" column; `9. Suggested files` needs the new templates.
2. `auth-and-trades-list.md` 6.3 (`AVG_R_HELP`, "which you'll be able to add once trade journaling is available") and 6.4 (`CALC_AVG_R`, "the risk amount you set") are replaced by the strings in section 6 above. Both live in `accounts/copy.py`, not `journal/copy.py`.
3. `import-account-label.md` 5 `DELETE_LIST_HELPER` and the journal-body sentence are replaced (section 7). Its 3.3 "Journal list" row spec needs the "Has a stop or planned risk amount." line.
4. `docs/product/features/journaling.md` uses list labels "Followed rules" / "Broke rules" (AC 3, 4, 30, decision "Where journaling starts"). This doc uses "Didn't follow rules" instead (coach tone; matches the form's "Didn't follow my rules"). PM should update the spec wording. Same spec has no label for an unanswered entry with both a note and a risk value; this doc adds "Note and risk".
5. PM spec section "Save failure" is marked provisional; the string in section 1 replaces it.
6. `docs/README.md` has no row for this doc yet (add under Design, owner `ux-designer`).

---

## 10. Components

| Component | Notes |
|---|---|
| `JournalPage` (`templates/journal/journal_form.html`) | Back link, `TradeSummary`, intro, `RulesPanel`, journal `<form>` (question, note, `RiskSection`, actions). |
| `TradeSummary` (`partials/trade_summary.html`) | Header block 3.1. Reuses `result_badge.html`. |
| `RulesPanel` (`partials/rules_panel.html`) | Card, native details, preview, own form. The htmx swap target; the same partial renders in the page and in the `/rules/` response. |
| `RulesQuestion` (`partials/rules_question.html`) | Fieldset with two radio tiles. |
| `CharCounter` | Alpine CSP `Alpine.data("charCounter")` in `app.js`; used by the note and the rules textarea. Props via `data-` attributes (limit 10000, show-from 9000, texts). |
| `MoneyInput` (`partials/suffix_input.html`) | Text input with a fixed suffix inside one bordered box. Reuse `field.html` for label, error and help. |
| `RiskStatusLine` | 3.6, text by reason code from the same R function the stats use (no second implementation). |
| `JournalLink` (`partials/journal_link.html`) | The list link: state label, icon, sr-only context. Used in `trade_table.html` and `trade_cards.html`. |
| Existing, reused | `Notice` (`success`, `info`, `attention`), `ErrorSummary`, `FormField`, `ResultBadge`, `StatCard`, `ConfirmDialog` and its journal list. |

Icons to add to `icon.html`: `plus`, `document`, `pencil`.

---

## 11. Edge cases

1. Two tabs saving the same trade: second save updates the first row; no message (spec AC 8).
2. Existing entry, user blanks every field and saves: the row is kept (spec). It has no note, no risk and no answer, so the list shows "Add journal" for it. The delete dialog still counts it (accepted in the spec: the form never deletes rows).
3. Trade deleted between load and save (batch deleted): 404 page (4.6); the typed note is lost. Accepted; no draft storage (spec out of scope).
4. Trade currency differs from a saved `risk_currency` (re-import): status line explains (3.6); saving a new amount rewrites the currency.
5. Windows line endings in pasted text: normalise before length check (3.4).
6. Very long rules text: panel textarea scrolls; the closed preview clamps at 3 lines; no layout push.
7. Very long symbol or account label: `break-all` / `break-words`, escaped.
8. Session expires mid-form: the POST redirects to `/login/?next=<journal url>`; the typed note is lost (same as every other form; no autosave in this slice).
9. htmx rules save with an expired session: return `401` with `HX-Redirect` to login (existing build note 1 in the auth design), not a login page swapped into the panel.
10. Open trade with a stop entered: saves, no R until it closes, status line says so.
11. Planned risk saved but the trade flips currency later: status line, left out of Avg R, no fallback to the stop (domain §3).
12. Very small screen (320px): radios stack, risk suffix stays inside the box, sticky bar does not cover the focused field.

---

## 12. Copy table

Ready for the copy modules. Convention: full sentences and finished strings only; one-word column headers ("Journal") live in the template. `{x}` placeholders are `str.format`; `%(name)d` are Django validator params (same style as `ACCOUNT_TOO_LONG`). Strings marked **(accounts)** replace existing keys in `accounts/copy.py`; strings marked **(replace)** replace existing keys in `journal/copy.py`; the rest are new in `journal/copy.py`.

### Page and header

| Key | String |
|---|---|
| JOURNAL_TITLE | Journal: {symbol} |
| JOURNAL_BACK | Trades |
| TRADE_ENTRIES | {n} entries |
| JOURNAL_INTRO | Nothing here is required. Save whatever you have; you can come back and change it. |
| NOT_FOUND_TITLE | We couldn't find that trade |
| NOT_FOUND_BODY | It may have been removed along with an import. |
| NOT_FOUND_LINK | Back to trades |

### My rules

| Key | String |
|---|---|
| RULES_HEADING | My rules |
| RULES_ACTION_ADD | Add |
| RULES_ACTION_EDIT | Edit |
| RULES_ACTION_CLOSE | Close |
| RULES_PROMPT | Add your rules so you can check each trade against them. |
| RULES_LABEL | Your rules |
| RULES_HELP | In your own words. Your rules aren't saved with each answer, so if you reword them, past answers still reflect the earlier wording. |
| RULES_SAVE | Save rules |
| RULES_SAVE_BUSY | Saving... |
| RULES_SAVED | Your rules are saved. |
| RULES_TOO_LONG | Your rules are %(show_value)d characters and the limit is %(limit_value)d. Shorten them a little. |
| RULES_SAVE_FAILED | We couldn't save your rules just now. Your text is still here, so try again in a moment. |

### Question

| Key | String |
|---|---|
| FOLLOWED_LEGEND | Did you follow your rules on this trade? |
| FOLLOWED_HELP | This is what marks a trade as journaled. You can leave it for later and change your answer any time. |
| FOLLOWED_YES | Followed my rules |
| FOLLOWED_NO | Didn't follow my rules |

### Note

| Key | String |
|---|---|
| NOTE_LABEL | Note |
| NOTE_HELP | What was your thinking going in, and how did it go? Anything you'd want to remember. |
| NOTE_TOO_LONG | That note is %(show_value)d characters and the limit is %(limit_value)d. Shorten it a little. |
| COUNTER | {n} / {limit} |
| COUNTER_LIVE_NEAR | You're close to the {limit}-character limit. |
| COUNTER_LIVE_OVER | Over the {limit}-character limit by {n}. |

### Risk

| Key | String |
|---|---|
| RISK_SUMMARY | Risk for Avg R (optional) |
| RISK_INTRO | Add a stop or a planned risk to include this trade in Avg R. Both are optional. |
| STOP_LABEL | Stop price |
| STOP_HELP_LONG | Your initial stop, as a price. For this long trade it sits below your entry of {entry}. |
| STOP_HELP_SHORT | Your initial stop, as a price. For this short trade it sits above your entry of {entry}. |
| STOP_HELP_MULTI_LEG | This trade has more than one entry, so a stop can't give R. Enter planned risk to get R on this trade. |
| RISK_LABEL | Planned risk |
| RISK_HELP | The amount you planned to risk on this trade, in {currency}, before fees. |
| RISK_BOTH_SET | Planned risk is used for R when both are set. |
| STOP_NOT_NUMBER | Enter a number like 19845.50. Use a period for decimals. |
| STOP_DECIMALS | Use up to 10 decimal places. |
| STOP_WRONG_SIDE | Your stop should sit on the loss side of your entry price. |
| RISK_NOT_NUMBER | Enter a number like 62.50. Use a period for decimals. |
| RISK_DECIMALS | Use up to 4 decimal places. |
| RISK_NOT_POSITIVE | Planned risk needs to be more than 0. |
| NUMBER_TOO_LARGE | That number is too large. |

### Risk status line (by reason code)

| Key | String |
|---|---|
| R_STATUS_OK | This trade counts in Avg R. |
| R_STATUS_TRADE_OPEN | This trade is still open. R is worked out once it closes. |
| R_STATUS_RISK_NOT_POSITIVE | The saved planned risk isn't more than 0, so this trade is left out of Avg R. Enter a new amount to fix it. |
| R_STATUS_CURRENCY_MISMATCH | Your saved planned risk is in {risk_currency}, but this trade is in {trade_currency}, so it's left out of Avg R. Save a new amount to update it. |
| R_STATUS_STOP_NOT_A_RISK | Your saved stop isn't on the loss side of this trade's entry price, so it's left out of Avg R. |
| R_STATUS_STOP_MULTI_LEG | This trade has more than one entry, so a stop can't give R. Enter planned risk to get R on this trade. |

### Actions and feedback

| Key | String |
|---|---|
| JOURNAL_SAVE | Save journal |
| JOURNAL_SAVE_BUSY | Saving... |
| JOURNAL_BACK_TO_TRADES | Back to trades |
| JOURNAL_SAVED | Journal saved. |
| JOURNAL_NOTHING_TO_SAVE | Nothing to save yet. Add a note, an answer, or a risk amount. |
| JOURNAL_SAVE_FAILED | We couldn't save that just now. Your text is still here, so try again in a moment. |
| ERROR_SUMMARY_TITLE (existing, reuse) | Please fix the items below. |

### Trades list

| Key | String |
|---|---|
| LIST_JOURNAL_ADD | Add journal |
| LIST_JOURNAL_FOLLOWED | Followed rules |
| LIST_JOURNAL_NOT_FOLLOWED | Didn't follow rules |
| LIST_JOURNAL_NOTE_ONLY | Note only |
| LIST_JOURNAL_RISK_ONLY | Risk only |
| LIST_JOURNAL_NOTE_AND_RISK | Note and risk |
| LIST_JOURNAL_SR_CONTEXT | {state} for {symbol}, opened {opened} |

(The column header "Journal" stays in the template.)

### Avg R card

| Key | String |
|---|---|
| AVG_R_HELP **(accounts)** | Avg R shows your results in units of what you risked on each trade. Open a trade's journal and add a stop or a planned risk amount to include it. Trades without one are left out, never counted as zero. |
| AVG_R_LEFT_OUT_ONE | Left out: 1 closed trade without usable risk |
| AVG_R_LEFT_OUT_MANY | Left out: {n} closed trades without usable risk |
| CALC_AVG_R **(accounts)** | Avg R: net P&L after fees divided by the risk you entered, averaged over closed trades that have one. Risk is your planned risk amount if you set one, otherwise the distance from your entry to your stop times your size (trades with one entry only). Trades without usable risk are left out, never counted as zero. |

### Import delete dialog

| Key | String |
|---|---|
| DELETE_JOURNAL_BODY_ONE **(replace)** | You've journaled 1 of these trades ({noted}). Deleting the import deletes that journal entry too, along with any stop or planned risk on it. |
| DELETE_JOURNAL_BODY_MANY **(replace)** | You've journaled {n} of these trades ({noted}). Deleting the import deletes those journal entries too, along with any stop or planned risk on them. |
| DELETE_LIST_HELPER **(replace)** | Want to keep any of your writing? Copy it from the list, or use View trade to open that trade's journal in a new tab. |
| DELETE_HAS_RISK | Has a stop or planned risk amount. |
| DELETE_VIEW_TRADE | View trade |
| DELETE_VIEW_SR | for {symbol}, opens in a new tab |

Unchanged and reused in the dialog: `DELETE_NO_NOTE` "(no written note)", `RULES_NOT_ANSWERED` "not answered", `DELETE_CHECKBOX`.

---

## 13. Open questions

1. `product-manager`: adopt the label wording changes in section 9 items 4 and 5 (spec edit).
2. `backend-engineer`: confirm the view can re-render the form with submitted values on DB and cross-tenant errors (save-failure copy depends on it), and that the R reason code for the status line comes from the same function as the stats.
3. `frontend-engineer`: confirm the Alpine CSP build supports the `charCounter` component and the `details[open] + .rules-preview` CSS without new dependencies (expected yes; both are plain).
4. Orchestrator or user: after saving, stay on the page (this doc) versus return to `/trades/#trade-<id>` with a flash. Chosen: stay, so a trader can keep adding detail. Flip it if review-ritual use shows people want to move down the list after each save.
