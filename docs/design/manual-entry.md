# Design: manual trade entry (add, close, edit, delete)

Spec: [`manual-entry.md`](../product/features/manual-entry.md) (product; 52 ACs plus 12a; user rulings 2026-10-13 are final). Layout, tokens, `Notice`, `FormField`, error-summary and tone conventions: [`auth-and-trades-list.md`](auth-and-trades-list.md) (4.3, 5, 7, 8). Page pattern, radio tiles, `details` disclosures, sticky action bar, `charCounter`/`data-busy`: [`journaling.md`](journaling.md) (3, 8, 12a). Delete dialog, Account datalist, tone: [`import-account-label.md`](import-account-label.md) (3.1, 3.3, 5, 7). Where this doc and the merged code differ on conventions, follow the merged code (follow-ups rows 42, 43).
Status: **Draft for `frontend-engineer`.** Not buildable until ADR-0008 is Accepted and the PENDING items in section 14 are ruled. Everything marked **[ADR-0008]** or **[PENDING]** is a slot, not a decision. Route and endpoint names are placeholders (ADR-0008 item 7 owns them).

Aim: a trader logs an open trade in about 20 seconds (five inputs, one tap on Save), and fixing or removing a manual trade is never scary.

---

## 1. Decisions by area

| Area | Decision |
|---|---|
| Form | One page, one column (max 640px, `max-w-form`). Order: Symbol, Point value (read-only or input), Side, Quantity, Entry price, Entry time, Exit (optional pair), More details (collapsed: Fees, Account). Only the five required inputs plus the two exit fields are visible. |
| Required marking | No asterisks. Optional things say "(optional)". One intro line names the five required inputs. Form is `novalidate`; required inputs carry `aria-required="true"`. Server messages are the only validation. |
| Side | Two radio tiles (Long, Short) from the journal radio-tile style, side by side at every width, 48px tall on mobile. No default. Neutral tokens: no green/red. Arrow icons match the list (decorative). |
| Entry/exit time | `<input type="datetime-local">`, no default, no server prefill. Zone named in the help text. Optional "Use current time" button (Alpine, hidden without JS) fills the field with now, floored to the minute, formatted in the user's saved zone (not the browser's). |
| Currency | Never an input. Prices and Fees carry a fixed "USD" suffix (`field.html` `suffix`, `aria-hidden`) and say "in USD" in their help text. The intro line says amounts are in US dollars. Because the app cannot tell a USD-quoted symbol from another, a one-sentence hint sits under the Symbol field on add and edit (not close): `SYMBOL_CURRENCY_HINT`, section 4.2. User ruling 2026-10-13 ("hint and go"); non-USD waits on follow-ups row 45 plus a minor-unit rounding table. |
| Point value (no JS) | Initial render shows no input, only a muted line: it is worked out from the symbol on save. Known root: a read-only `<dl>` row (not a disabled input). Unknown root: re-render at 200 with the required empty input and an error in the summary. The server never shows an editable box for a known root. |
| Point value (htmx, optional) | `change` on Symbol swaps `#point-value` with the right state. Full-page submit works identically without it. |
| Fees on add **[PENDING PM, section 14 item 3]** | Always offered (without JS the server cannot know whether an exit will exist). Help says it is only used with an exit. Fees with no exit gives a field error. This departs from AC 29's "no Fees input is offered" on an open trade, which only holds for edit-open and close. |
| Open trade | Shown everywhere as today ("Open"). Manual open trades get "Close this trade" on the journal page. No nagging, no reminder. |
| Close **[PENDING PM, section 14 item 1]** | Own page `/trades/<id>/close/`: exit price, exit time, size read-only, Fees in More details. Account label is shown read-only in the trade header and is not editable: AC 27 says More details on close holds Fees and Account label, but AC 34 copies the label onto the exit fill and the label is part of the matcher bucket. |
| Edit | Same form, prefilled, times in the user's zone. Open trade: no exit, no Fees, a link to Close. Closed trade: exit required. Locked fields render as read-only text with a reason line **[ADR-0008 slot, section 7.2]**. |
| Delete | Same dialog shell and `deleteConfirm` behavior as import delete, sibling partial. Tick box only when a journal entry exists. The entry is shown in full. |
| Entry points | "Add trade" on `/trades/` header (secondary button beside "Import trades") and in both empty states. Edit, Close, Delete live only in the journal page header, for manual trades. List rows stay identical for every trade: no new per-row tab stop, no badge. |
| After save | Add and Edit and Close land on the trade's journal page with a flash (the journal is the point). Delete lands on `/trades/` with a flash. |
| 404 | One page for missing id, other user's id, imported trade on an edit/close/delete URL, and a trade deleted in another tab. Body text reworded (section 12, replace). |

---

## 2. Flow

```
/trades/  [Add trade] ---------------> /trades/new/  (GET: blank form)
(empty state: [Import trades] [Add trade])        |
                                                  +-- Save trade (POST)
                                                        ok     -> 302 /trades/<id>/journal/ + flash "Trade added. ..."
                                                        errors -> 200, summary + field messages, values kept
                                                        unknown point value -> 200, Point value input shown, summary
                                                        failure -> 200, values kept, "couldn't save" notice
                                                        same form token twice -> same 302 as the first (no 2nd trade)

/trades/<id>/journal/  (manual trades only: Close this trade | Edit trade | Delete trade)
   |-- Close this trade (open only) -> /trades/<id>/close/ -> POST ok -> 302 journal page + flash "Trade closed."
   |        already closed (other tab) -> 302 journal page + info flash "That trade was already closed..."
   |-- Edit trade   -> /trades/<id>/edit/  -> POST ok -> 302 journal page + flash "Trade updated."
   `-- Delete trade -> dialog (htmx) or /trades/<id>/delete/ (no JS)
                         POST ok -> 302 /trades/ + flash "Trade deleted." / "Trade and journal entry deleted."
Imported trade, or someone else's, on /close/ /edit/ /delete/ -> the one 404 page.
```

---

## 3. Entry points

### 3.1 `/trades/` header (populated)

```
Trades                              [ Import trades ]  [ Add trade ]
```

- "Import trades" stays the primary button (`btn-primary`). "Add trade" is `btn-secondary`, same size, DOM order after Import. `flex flex-wrap gap-3`; under 640px both are full width, stacked, 44px, Import first.
- Link to `{% url 'trade_add' %}`. Hidden nowhere: it shows in every state where the header shows the Import button.

### 3.2 Empty states (`trades_empty.html`)

| State | Heading | Body | Actions |
|---|---|---|---|
| A. No imports, no trades | No trades yet | Upload your TopstepX 'Trades' export, or add a trade by hand. Your trades show up here. | [Import trades] primary, [Add trade] secondary |
| B. Imports, none produced a trade | No trades yet | Your last upload didn't add any trades. (unchanged) | [See what happened to that upload] primary, [Add trade] secondary |

Both buttons in one row on desktop, stacked full width on mobile. The empty-state primary stays the existing link (`empty_url`); the secondary Add trade link is a constant in the template.

### 3.3 Journal page header: trade actions (manual trades only)

Placed directly under the trade summary, above the intro line. Not inside the journal form. Imported trades show nothing here (no hint text, no disabled controls).

```
MNQZ6  Long                                  (o) Open   Open
Opened Sep 26, 2:31 PM (America/New_York)
Qty 2 - 19,850.25 to -  - Open
Close this trade     Edit trade     Delete trade
```

- Three plain text links in a `role="group" aria-label="Trade actions"`, `flex flex-wrap gap-x-4`, each `min-h-[44px] items-center inline-flex`, underlined (not color alone). Delete uses the `danger` text token plus the word. Order: Close this trade (open trades only), Edit trade, Delete trade.
- No icons on these links; that keeps them on one line at 360px.
- "Delete trade" is a `delete_link.html` instance (hx-get into `#delete-dialog-body`, `data-opens-dialog`), with a new `label` parameter so it does not print "Delete this import". It needs the page to include `delete_dialog.html` (one per page).

---

## 4. Add form: `/trades/new/`

`<title>` "Add a trade". h1 "Add a trade". Back link "Trades" (to `/trades/`, one link, no CSS pair needed: there is no row to return to).

### 4.1 Desktop, blank

```
+--------------------------------------------------------------+
| <- Trades                                                    |
| Add a trade                                                  |
| Symbol, side, quantity, entry price and entry time are all   |
| you need. Everything else can wait. Amounts are in US        |
| dollars (USD).                                               |
|                                                              |
| Symbol                                                       |
| [ AAPL                                  ]                    |
| For example AAPL or CLZ6.                                    |
| For now we only support USD-quoted instruments; others, like |
| USDJPY, EURGBP, USDT pairs or non-US stocks, would be saved  |
| as USD and show the wrong P&L.                               |
|                                                              |
| Point value  (muted, no input yet)                           |
| We'll fill this in from the symbol when you save. If we      |
| don't know it, we'll ask.                                    |
|                                                              |
| Side                                                         |
| Long means you bought first. Short means you sold first.     |
| [ (^) Long ]  [ (v) Short ]       <- radio tiles, none chosen |
|                                                              |
| Quantity                                                     |
| [ 10                                    ]                    |
| Shares, contracts or units. Decimals are fine, like 0.5.     |
|                                                              |
| Entry price                                                  |
| [ 100.00                           | USD ]                   |
| Price per unit, in USD.                                      |
|                                                              |
| Entry time                                                   |
| [ 2026-03-02  09:30 AM             ]  [ Use current time ]   |
| In your time zone, America/New_York.                         |
|                                                              |
| Exit (optional)                         <- fieldset legend   |
| Leave both blank if the trade is still open. You can add     |
| the exit later with Close this trade.                        |
| Exit price                                                   |
| [                                  | USD ]                   |
| Exit time                                                    |
| [                                  ]  [ Use current time ]   |
| In your time zone, America/New_York.                         |
|                                                              |
| > More details (optional)               <- <details>, closed |
|                                                              |
| [ Save trade ]   Cancel                                      |
+--------------------------------------------------------------+
```

"Use current time" renders `hidden` and Alpine un-hides it; without JS it does not exist.

More details open:

```
| v More details (optional)                                    |
|   Fees                                                       |
|   [                                | USD ]                   |
|   Total fees for the whole trade, in USD. Only used when     |
|   the trade has an exit.                                     |
|                                                              |
|   Account (optional)                                         |
|   [ Choose or type a name                ]  <- datalist      |
|   Only if you track more than one account. Use the same     |
|   name each time.                                            |
```

### 4.2 Field spec

| Field (`name`) | Control | Attributes | Server notes |
|---|---|---|---|
| Symbol (`symbol`) | `type="text"` | `autocomplete="off"`, `autocapitalize="characters"`, `spellcheck="false"`, `aria-required`, no `maxlength` (a native cap silently cuts pastes), `autofocus` on a fresh GET with no errors | Trim, uppercase, 32 max (AC 11). Re-rendered value is the normalized one. |
| Symbol currency hint (no `name`, not a field) | `<p id="id_symbol_currency_hint" class="mt-1 text-text-muted">` directly under the Symbol help line, before any error message | Same style as every other help line (muted token, AA contrast, no icon, no border, not a `Notice`). Static `SYMBOL_CURRENCY_HINT`, always visible: not collapsed, not dismissible, not a tooltip, not a live region, not in the error summary, and not cleared or changed by an error re-render. Shown on **add and edit**; **not on close** (no Symbol input there). On edit it is omitted when `symbol` is in `locked_fields` (7.2): no input, nothing to describe. The Symbol input's `aria-describedby` lists, in order: help id, hint id, error id (same help-then-error convention as 10.2). `field.html` renders one `help_text` paragraph today, so give it an optional `hint` / `hint_id` parameter (second `<p>`), and have the form add the hint id to the widget's `aria-describedby` (Django only emits the help and error ids). Backend only passes `mode`; no new context key. |
| Side (`side`) | fieldset + 2 radios | legend "Side", help tied by `aria-describedby`; no `checked` | Required. Long = buy entry. |
| Quantity (`quantity`) | `type="text"` | `inputmode="decimal"`, `autocomplete="off"`, `autocapitalize="none"`, `spellcheck="false"`, `aria-required` | `PlainDecimalField` rules (AC 9, 10). |
| Entry price (`entry_price`) | same, with `suffix=USD` | same | Non-zero; negative allowed. |
| Entry time (`entry_time`) | `type="datetime-local"` | `aria-required`, `autocomplete="off"`, `placeholder="YYYY-MM-DD HH:MM"` (only seen if a browser falls back to text) | Server accepts `YYYY-MM-DDTHH:MM` and `YYYY-MM-DD HH:MM`, interpreted in the user's zone. |
| Point value (`point_value`) | see 5 | `type="text"`, `inputmode="decimal"`, no placeholder, no default, `aria-required` | Required only for an unknown root. |
| Exit price / time (`exit_price`, `exit_time`) | as entry | not `aria-required` on add | Both or neither (AC 7). |
| Fees (`fees`) | `type="text"`, `suffix=USD` | `inputmode="decimal"` | 0 or more, 4 dp; blank = 0. |
| Account (`account`) | text + datalist | as the import Account input, blank by default | Max 64, trimmed, control characters rejected (existing copy). |
| Hidden `form_token` | per-render token | | Double-submit guard (ADR-0008 item 5). |
| Hidden `pv_for` | normalized symbol the Point value input was rendered for | only present in the unknown-root state | See 5.3. |

Known ceiling: `inputmode="decimal"` has no minus key on iOS, though negative prices are valid (AC 9). Same ceiling as the journal stop field; those users can paste or use a hardware keyboard.

### 4.3 Time fields and "Use current time"

- Help text under each time field: "In your time zone, {zone}." (the user's `timezone`, same name the list header shows). One help line, tied by `aria-describedby`.
- **Use current time** (`type="button"`, `btn-secondary`, 44px, to the right of the input on desktop, full width below it on mobile). Behavior: Alpine CSP component `nowButton` in `static/js/app.js` (no inline expressions), props in `data-target` (the input id) and `data-zone` (the user's zone). On click it sets the input to now **floored to the minute**, formatted in `data-zone` with `Intl.DateTimeFormat` as `YYYY-MM-DDTHH:MM`, dispatches `input`, and sets a polite `role="status"` sr-only text "Set to {time}." Floored so the future-time check (AC 22) never rejects it. Never prefills on load. Without JS the button is `hidden` and absent from the accessibility tree.
- Backend supplies edit/re-render values as `YYYY-MM-DDTHH:MM` in the user's zone (context keys in 13).
- DST gap/overlap and future-time rejection: server-side only (sections 12, 14).

### 4.4 Actions

- "Save trade" primary, `data-busy="Saving..."` (existing global handler), 44px. "Cancel" is a link to `/trades/` (no confirm; nothing is saved until Save). Same sticky mobile `action-bar` as the journal page: Save full width, Cancel below, `env(safe-area-inset-bottom)`, form `scroll-padding-bottom`.
- Enter in any single-line input submits (native). Accepted: required-field errors keep values.

---

## 5. Point value

### 5.1 States (one partial, `partials/point_value.html`, id `point-value`)

| State (`pv_state`) | When | What renders |
|---|---|---|
| `pending` | Add form with no symbol yet (first GET, or symbol blank) | Muted block: label "Point value", text `POINT_VALUE_PENDING`. No input. |
| `known` | Symbol has a root in `MULTIPLIERS` | `<dl class="field"><div><dt>Point value</dt><dd>1,000</dd></div></dl>` plus help `POINT_VALUE_KNOWN_HELP` ("Set from the symbol (CL). It can't be changed here."). Not an input, not disabled, not posted. Value formatted with thousands separators. |
| `unknown` | Symbol has no root or the root is not in `MULTIPLIERS` | `FormField` "Point value": empty text input, `aria-required`, help `POINT_VALUE_HELP`, hidden `pv_for`. No placeholder number, no default. |

Edit form: the state is always `known` or `unknown` (the symbol exists); `unknown` prefills the stored value.

**Where QA can observe AC 16 ("shows 1000 read-only") without JS:** a known root on the add form saves without the value ever being shown, so the value is observable (a) on the edit form for that trade, (b) on an add re-render after any other validation error with `CLZ6` typed, and (c) in the htmx response for `GET point-value?symbol=CLZ6`. Tests assert those three, not a "known" state on the first add GET (that is always `pending`).

### 5.2 Without JS (the working path)

1. GET renders `pending` (add) or the real state (edit).
2. User submits. Server resolves the root.
   - Known: saves with the looked-up value, ignoring any posted `point_value`. The user never typed one, so nothing is dropped.
   - Unknown, input not shown yet or left blank: 200 re-render, all values kept, nothing saved, state `unknown`, error in the summary and under the field: `POINT_VALUE_NEEDED` ("We don't have a point value for {symbol} yet. Enter one; for shares, use 1."). The error summary receives focus as for any validation error; the link jumps to the Point value input.
3. User fills it in and resubmits.

### 5.3 Symbol changed after the input was shown (wrong-data guard)

A value typed for one symbol must not be saved for another (1 on `AAPL` then `ESZ6` would be off by the multiplier). The `unknown` state carries hidden `pv_for` = the normalized symbol it was rendered for. If the symbol at submit differs from `pv_for`, the server ignores the posted point value, clears it, and re-renders `unknown` with `POINT_VALUE_NEEDED`. If the new symbol is known, it saves with the lookup (AC 16, 19).

### 5.4 htmx enhancement (optional; everything above still works without it)

- On the Symbol input: `hx-get="{% url 'trade_point_value' %}"` (placeholder, ADR-0008 item 7), `hx-trigger="change"`, `hx-target="#point-value"`, `hx-swap="outerHTML"`, `hx-sync="this:replace"`. The request carries the input's own `symbol`; edit passes nothing else.
- The response is `point_value.html` for the new state plus an out-of-band update of a persistent `<p id="point-value-live" role="status" class="sr-only" hx-swap-oob="innerHTML">` (the swapped block is replaced wholesale, so it cannot announce itself; `innerHTML`, not `true`, so only the text changes and the live region element itself persists). Live text: `POINT_VALUE_LIVE_KNOWN`, `POINT_VALUE_LIVE_ASK`, or empty for `pending`.
- Do **not** set `data-focus-after-swap` and do not move focus: the user is already on Side. A keyboard user who tabs away before the swap lands is told by the live region; if they miss it, the submit path above catches it.
- A blank symbol swaps back to `pending`.
- Expired session: this endpoint returns 401 **without** `HX-Redirect` (an exception to auth design 9 note 1), so htmx ignores it and the typed form is not thrown away; the later Save redirects to login as for any form.
- Any network error: nothing changes; no error shown (the submit path is the fallback).

---

## 6. Open trade and "Close this trade"

### 6.1 Open-trade state

An open manual trade looks like any open trade: "Open" badge, P&L "Open", no exit price, "Open" duration (journal 3.1). It is in no stat card. Its journal page adds the trade-actions group (3.3). Nothing else: no age badge, no reminder, no auto-close.

### 6.2 Close page: `/trades/<id>/close/`

`<title>` "Close {symbol}". One back link "Trade" (to the journal page, no CSS pair); Cancel goes to the same place. Order: trade summary (reuse `trade_summary.html`; shows side, entry price, entry time, account label read-only), intro, form.

```
+--------------------------------------------------------------+
| <- Trade                                                     |
| MNQZ6  Long                                  (o) Open   Open |
| Opened Sep 26, 2:31 PM (America/New_York)                    |
| Qty 2 - 19,850.25 to - - Open - Combine 50K                  |
|                                                              |
| Close this trade                                             |
| Add the exit for this trade. It closes the whole position,   |
| so there's no quantity to enter.                             |
|                                                              |
| Size                                                         |
| 2 (the whole position)                  <- read-only <dl>    |
|                                                              |
| Exit price                                                   |
| [                                  | USD ]                   |
| Price per unit, in USD.                                      |
|                                                              |
| Exit time                                                    |
| [                                  ]  [ Use current time ]   |
| In your time zone, America/New_York.                         |
|                                                              |
| > More details (optional)       <- contains Fees only        |
|                                                              |
| [ Close trade ]   Cancel                                     |
+--------------------------------------------------------------+
```

- Fields: `exit_price` and `exit_time` both required (`aria-required`), Fees in More details. No quantity input, no side, no point value, no account input.
- GET on a trade that is already closed: no form; 302 to the journal page with the info flash `ALREADY_CLOSED`. Imported trade or not found: the one 404.
- POST of the same rendered form twice: second returns the same redirect as the first (`form_token`). POST from a different render after the trade closed: no second fill, info flash `ALREADY_CLOSED`.
- Exit before entry **[PENDING AC 8/35]**: error under Exit time `EXIT_BEFORE_ENTRY`, which names the entry time, so the user does not need to scroll up.

---

## 7. Edit: `/trades/<id>/edit/`

Title "Edit {symbol}". Same page frame as add. Intro `EDIT_INTRO`. When a journal entry exists, one `info` line under the intro: `EDIT_KEEPS_JOURNAL` (so the fear "will I lose my note?" is answered up front). Submit "Save changes". Cancel returns to the journal page.

### 7.1 Variants

| Trade | Visible fields |
|---|---|
| Open **[PENDING PM, section 14 item 2]** | Symbol, Point value, Side, Quantity, Entry price, Entry time, More details (**Account only, no Fees**). In place of the exit group: a muted line `EDIT_OPEN_NOTE` with a link "Close this trade" to the close page. No exit inputs (AC 38: an edit never adds a fill). Derived from AC 29 and AC 38; the spec does not say it in so many words. |
| Closed | Everything in add, with the exit pair **required** (legend "Exit", no "(optional)", no "leave blank" help). More details opens when fees are above 0 or the account label is non-blank (stored fees are `0.0000` by default, which must not count as "set"). A blank exit gives `EXIT_PRICE_REQUIRED_CLOSED` / `EXIT_TIME_REQUIRED_CLOSED`: a closed trade cannot be reopened (AC 41). |

Point value follows section 5 with the real state from the first render. Changing the symbol to a different known/unknown status re-evaluates on save (AC 19), including the `pv_for` guard.

### 7.2 Locked fields **[ADR-0008 slot]**

ADR-0008 decides which fields lock once a journal entry exists. Presentation, so the decision is a data change only:

- Context key `locked_fields`: a set of field names. A locked field renders as the same read-only `<dl>` row used for a known Point value (label, current value in the user's formats, no input, nothing posted), followed by the reason line `LOCKED_REASON`, tied to the row with `aria-describedby`.
- Locked rows keep their position in the form. Nothing is "editable then discarded" (AC 39).
- Proposed `LOCKED_REASON` (final wording waits for the ADR): "Locked because this trade has a journal entry."
- If ADR-0008 locks nothing, the key is an empty set and no UI changes.

### 7.3 Stop side re-check **[ADR-0008 slot, AC 40]**

- Option (a), refused at edit time: field error under Entry price (or Side): `STOP_RECHECK_REFUSED`. Nothing saved, values kept.
- Option (b), allowed: no new copy. The journal page's existing risk status line (`R_STATUS_STOP_NOT_A_RISK`) explains it after the redirect.

---

## 8. Delete

### 8.1 Dialog and page

Same shell as import delete: `delete_dialog.html` (included once on the journal page, **outside both the rules form and the journal form**, because it contains its own `<form method="dialog">` and forms cannot nest), `hx-get` into `#delete-dialog-body`, `showModal()` from `app.js`, `deleteConfirm` Alpine component, same focus rules. No-JS: the link goes to `/trades/<id>/delete/`, which renders the same body as a full page with a plain form (GET never deletes; POST + CSRF does). `delete_confirm.html` hardcodes `batch` and the import URLs, so build a **sibling partial** `journal/partials/trade_delete_confirm.html` (and `trade_delete.html` for the page) that reuses the same markup classes, `x-data="deleteConfirm"`, `data-cancel`, `data-request-error` and the same focus attributes. Only the body content differs.

Context for the sibling partial mirrors the import one so `app.js` needs no change: `page`, `notice`, `body`, `journal_body`, `checkbox_label`, `delete_button`, `shown_token`.

### 8.2 Variant 1: no journal entry (open or closed)

```
+----------------------------------------------------------+
| Delete this trade?                                 [ x ] |
|                                                          |
| MNQZ6  Long                                              |
| Opened Sep 26, 2026, 2:31 PM (America/New_York)          |
|                                                          |
| This removes this trade from your trades and your stats. |
| Your other trades stay as they are.                      |
|                                                          |
| This can't be undone.                                    |
|                                                          |
|                  [ Cancel ]   [ Delete trade ]           |
+----------------------------------------------------------+
```

An open trade says `MANUAL_DELETE_BODY_OPEN` ("This removes this open trade from your trades. ...") instead of `MANUAL_DELETE_BODY`, in both variants (no stats mention; an open trade is in none). Focus starts on Cancel.

### 8.3 Variant 2: a journal entry exists

```
+----------------------------------------------------------+
| Delete this trade?                                 [ x ] |
|                                                          |
| MNQZ6  Long                                              |
| Opened Sep 26, 2026, 2:31 PM (America/New_York)          |
|                                                          |
| This removes this trade from your trades and your stats. |
| Your other trades stay as they are.                      |
|                                                          |
| This trade has a journal entry. Deleting the trade       |
| deletes that entry too, along with any stop or planned   |
| risk on it.                                              |
|                                                          |
| Journal entry that will be deleted                       |
| +------------------------------------------------------+ |
| | Rules: followed                                      | |
| | "Waited for the retest, sized down after the la..."  | |   <- note in full
| | Has a stop or planned risk amount.                   | |
| +------------------------------------------------------+ |
| Want to keep your writing? Copy it from the box above.   |
|                                                          |
| [ ] Also delete my journal entry (with a written note).  |
|     This can't be undone.                                |
|                                                          |
|        [ Cancel ]   [ Delete trade and entry ]           |
|                     ^ disabled until the box is ticked   |
+----------------------------------------------------------+
```

- The entry block is a `role="region" tabindex="0" aria-label="Journal entry that will be deleted"` scroll container (`max-h-[40vh]`), note in full, escaped, `break-words`. Shows: rules answer (icon + words "followed / not followed / not answered"), note or "(no written note)", and "Has a stop or planned risk amount." when there is one. No "View trade" link (the user came from that trade's page).
- Checkbox label ends "(with a written note)" or "(no written note)". Delete is an outline `btn-danger` with the words; never filled red. Tab order: entry region, checkbox, Cancel, Delete. Focus on open: the title (`tabindex="-1"`). Live region on tick/untick: existing strings.
- Server enforces the tick (same refusal message as import: `DELETE_NOT_TICKED`, checkbox focused).
- **Stale** (entry added or changed after the dialog rendered): nothing deleted, dialog re-renders with the fresh entry, box unticked, notice `MANUAL_DELETE_STALE` (role="status", focused).
- **Failure** (5xx/network): existing `DELETE_FAILED` inline, dialog stays open.
- Success: htmx `HX-Redirect` / plain 302 to `/trades/` with flash `MANUAL_DELETED` or `MANUAL_DELETED_WITH_ENTRY`; focus goes to the flash (`flash_attrs` block, added to `trade_list.html`).
- Trade already gone (other tab) or not owner: the one 404. The htmx GET that fails falls back to the no-JS page (existing `htmx:responseError` handler), which then shows the 404.

---

## 9. States

### Add / close / edit form

| State | What the user sees |
|---|---|
| Empty (add) | Blank form, no side chosen, no time, Point value `pending`, More details closed. Symbol focused. No flash. |
| Populated (edit) | Values prefilled, times in the user's zone as `YYYY-MM-DDTHH:MM`, More details open if Fees/Account hold a value. |
| Validation error | 200, error summary (`attention`, focused, title "Please fix the items below.", one linked line per problem), field messages under fields, all values kept (nothing is cleared). More details opens if it holds an error. |
| Unknown point value | Same as validation error; the Point value input is now present, empty, with `POINT_VALUE_NEEDED`. |
| Save failure (DB, cross-tenant form error) | 200, values kept, `attention` Notice at the top, focused: `MANUAL_SAVE_FAILED`. Never a bare 500. |
| Submitting | Save button "Saving...", disabled (existing `data-busy`; restored on `pageshow`). |
| Double submit | JS on: button disabled. Same token twice (Back then resubmit, no JS double click): second request ends on the same redirect and flash as the first. Two separate renders with identical content: two trades (AC 14). |
| Saved | 302 + flash (section 12), focus on the flash. |
| Close: already closed | 302 to the journal page, info flash `ALREADY_CLOSED`. |
| Loading | Server-rendered, no skeleton. |
| Not found / foreign / imported | The one 404. |
| Session expired | POST redirects to `/login/?next=<form url>`; typed values are lost (same as every form, no autosave). |

### Delete dialog

Loading, ready (variant 1, 2), box ticked/unticked, submitting ("Deleting..." disabled, Cancel disabled), stale, not ticked, failed, gone: as import delete (`import-account-label.md` section 4, Confirm dialog), with the trade copy in section 12.

### `/trades/`

Empty A and B as 3.2. Populated: header with both buttons. A just-added trade is in the list (open, no P&L) on the next read.

---

## 10. Accessibility

### 10.1 Focus order (add)

Skip link, nav, Back link, (error summary or flash when present, focused on load), Symbol, Point value input (only in `unknown`), Side radio group (one stop; arrows move within), Quantity, Entry price, Entry time, Use current time, Exit price, Exit time, Use current time, More details summary, (Fees, Account when open), Save, Cancel. Close and Edit: same order minus absent fields. Journal page: the trade-actions links sit after the summary and before the rules panel.

### 10.2 Wiring

- Every control has a real `<label for>`; Side is a `fieldset` with `legend`, help via `aria-describedby`, errors appended to it (like `rules_question.html`; errors inside the fieldset).
- Errors: summary + per-field message with decorative icon, `aria-invalid="true"`, `aria-describedby` (help id then error id), no "Error:" prefix, no `role="alert"`. The summary links to field ids; Side's link targets the tile container id (`id_side`). Errors inside a closed More details open it.
- The USD suffix is `aria-hidden`; the help text says "in USD".
- Symbol's `aria-describedby` = help id, `id_symbol_currency_hint`, then the error id when present, so a screen reader hears the hint on focus.
- Read-only values (known Point value, Size, locked fields) are `<dl>` rows, not disabled inputs, so they are readable and reachable by screen readers and cannot be mistaken for editable fields.
- Live regions: `#point-value-live` (htmx path), the "Use current time" status. Neither speaks on every keystroke.
- Date/time: native `datetime-local` is keyboard and screen-reader operable; the zone is in the help text, tied by `aria-describedby`.
- P&L and results in the journal header keep sign + word + icon. Nothing on these forms encodes meaning by color alone.
- Dialog: as import delete (focus trap, Esc, focus returns to the opener; focus to h1 if the opener is gone).

### 10.3 Targets and layout

44px minimum on: all inputs (existing input height), radio tiles (48px mobile), Use current time, Save, Cancel link, More details summary, trade-actions links, dialog buttons. Mobile (360px and 320px): single column, Side tiles side by side (each at least 150px wide at 320px), Use current time below its input at full width, sticky action bar must not cover a focused field (`scroll-padding-bottom`). 200% text zoom: no horizontal scroll. `prefers-reduced-motion`: nothing animated. Dark mode: existing tokens only.

---

## 11. Edge cases

1. **Symbol with surrounding spaces or lower case:** stored trimmed and uppercase; re-render shows the normalized value.
2. **`MNQZ6`, `ESZ6`, `GC...`:** unknown today, so the user types the point value (they know it). Verified roots grow only via the importer table.
3. **Symbol edited after an error re-render** that already showed the point value: `pv_for` guard (5.3).
4. **Fees on add with no exit [PENDING PM]:** field error `FEES_NEEDS_EXIT`; the More details panel opens so the message is reachable.
5. **Entry equals exit time:** saves (AC 8). Exit earlier: error **[PENDING]**.
6. **Future time:** error `TIME_FUTURE`. "Use current time" never triggers it (floored).
7. **Nonexistent or repeated local time (DST):** rejected for now with `TIME_NOT_READABLE` **[PENDING AC 21]**.
8. **Very long symbol or account:** `break-all` / `break-words`, escaped everywhere.
9. **Two tabs:** edit saved in one, stale form saved in the other: last write wins (same as the journal, no message). Delete in one tab, anything in the other: the one 404.
10. **Edit changes known/unknown point value:** re-evaluated on save (AC 19); journal entry kept (AC 39).
11. **Open manual trade left for days:** no reminder (spec).
12. **Account column** appears if a manual label makes a second distinct label (spec; no change).
13. **Browser without `datetime-local`:** falls back to a text input with the `YYYY-MM-DD HH:MM` placeholder; server accepts both formats.
14. **Mixed currency:** impossible; there is no input and the server forces USD.
15. **A non-USD symbol typed anyway** (USDJPY, EURGBP, a USDT pair, a non-US stock): nothing blocks or warns on save; the hint under Symbol is the only signal and it is always shown. Saved as USD. Revisit with follow-ups row 45.

---

## 12. Copy table

Convention: finished strings, `{x}` is `str.format`. **Module** says where the key lives, because `trades()` binds `accounts.copy` and the journal views bind `journal.copy` (follow-ups row 42); a template reads only keys from the module its view binds. Every string is neutral: no price comments, no advice, no praise or scolding.

### List and entry points

| Key | String | Module |
|---|---|---|
| ADD_TRADE | Add trade | `accounts/copy.py` |
| TRADES_EMPTY_BODY **(replace)** | Upload your TopstepX 'Trades' export, or add a trade by hand. Your trades show up here. | `accounts/copy.py` |
| TRADE_ACTIONS_LABEL | Trade actions | `journal/copy.py` |
| TRADE_CLOSE | Close this trade | `journal/copy.py` |
| TRADE_EDIT | Edit trade | `journal/copy.py` |
| TRADE_DELETE | Delete trade | `journal/copy.py` |
| NOT_FOUND_BODY **(replace)** | It may have been deleted, or removed along with an import. | `journal/copy.py` |

(`TRADES_EMPTY_B_BODY`, `TRADES_EMPTY_HEADING`, `IMPORT_TRADES`, `NOT_FOUND_TITLE`, `NOT_FOUND_LINK` unchanged.)

Name check: a grep of both `copy.py` files found no clash with the new keys below except `NUMBER_TOO_LARGE`, `ACCOUNT_LABEL`, `ACCOUNT_TOO_LONG`, `ACCOUNT_CONTROL_CHARACTERS` and the `DELETE_*`/`RULES_*` keys, which are deliberate reuses. If row 42 ever merges the two namespaces, keep every key unique across both files.

### Page chrome (all `journal/copy.py`)

| Key | String |
|---|---|
| ADD_TITLE | Add a trade |
| ADD_INTRO | Symbol, side, quantity, entry price and entry time are all you need. Everything else can wait. Amounts are in US dollars (USD). |
| CLOSE_TITLE | Close {symbol} |
| CLOSE_INTRO | Add the exit for this trade. It closes the whole position, so there's no quantity to enter. |
| EDIT_TITLE | Edit {symbol} |
| EDIT_INTRO | Fix a typo or a wrong number. |
| EDIT_KEEPS_JOURNAL | Your journal entry stays with this trade when you save. |
| EDIT_OPEN_NOTE | This trade is still open, so there's no exit to edit. Add the exit with Close this trade. |
| BACK_TO_TRADE | Trade |
| SAVE_ADD / SAVE_CLOSE / SAVE_EDIT | Save trade / Close trade / Save changes |
| SAVE_BUSY | Saving... |
| MANUAL_CANCEL | Cancel |

### Labels and help

| Key | String |
|---|---|
| SYMBOL_LABEL / SYMBOL_HELP | Symbol / For example AAPL or CLZ6. |
| SYMBOL_CURRENCY_HINT | For now we only support USD-quoted instruments; others, like USDJPY, EURGBP, USDT pairs or non-US stocks, would be saved as USD and show the wrong P&L. (add and edit only; temporary, remove when non-USD lands, follow-ups row 45) |
| SIDE_LEGEND / SIDE_HELP | Side / Long means you bought first. Short means you sold first. |
| SIDE_LONG / SIDE_SHORT | Long / Short |
| QUANTITY_LABEL / QUANTITY_HELP | Quantity / Shares, contracts or units. Decimals are fine, like 0.5. |
| ENTRY_PRICE_LABEL / PRICE_HELP | Entry price / Price per unit, in USD. |
| ENTRY_TIME_LABEL / TIME_HELP | Entry time / In your time zone, {zone}. |
| NOW_BUTTON / NOW_SET | Use current time / Set to {time}. |
| EXIT_LEGEND_OPTIONAL | Exit (optional) |
| EXIT_HELP_OPTIONAL | Leave both blank if the trade is still open. You can add the exit later with Close this trade. |
| EXIT_LEGEND_CLOSED | Exit |
| EXIT_PRICE_LABEL / EXIT_TIME_LABEL | Exit price / Exit time |
| SIZE_LABEL / SIZE_VALUE | Size / {quantity} (the whole position) |
| POINT_VALUE_LABEL | Point value |
| POINT_VALUE_PENDING | We'll fill this in from the symbol when you save. If we don't know it, we'll ask. |
| POINT_VALUE_KNOWN_HELP | Set from the symbol ({root}). It can't be changed here. |
| POINT_VALUE_HELP | What one full point of price move is worth for one unit, in USD. For shares, use 1. |
| POINT_VALUE_LIVE_KNOWN | Point value for {root} is {value}. |
| POINT_VALUE_LIVE_ASK | We don't have a point value for {symbol}. Enter one below. For shares, use 1. |
| MORE_SUMMARY | More details (optional) |
| FEES_LABEL | Fees |
| FEES_HELP_ADD | Total fees for the whole trade, in USD. Only used when the trade has an exit. |
| FEES_HELP | Total fees for the whole trade, in USD. |
| ACCOUNT_LABEL | Account (optional) (existing key, reused) |
| MANUAL_ACCOUNT_HELP | Only if you track more than one account. Use the same name each time. |
| MANUAL_ACCOUNT_PLACEHOLDER | e.g. Main account (no saved labels; the existing `ACCOUNT_PLACEHOLDER_EMPTY` says "Combine 50K", which is Topstep-specific). With saved labels the existing `ACCOUNT_PLACEHOLDER_SAVED` ("Choose or type a name") is reused. |

### Field errors (shown under the field and in the summary; summary title reuses `ERROR_SUMMARY_TITLE`)

| Key | String |
|---|---|
| SYMBOL_REQUIRED | Enter the symbol, for example AAPL or CLZ6. |
| SYMBOL_TOO_LONG | Use 32 characters or fewer. |
| SIDE_REQUIRED | Choose Long or Short. |
| QUANTITY_REQUIRED | Enter how many you traded. |
| QUANTITY_NOT_NUMBER | Enter a number like 10 or 0.5. Use a period for decimals. |
| QUANTITY_NOT_POSITIVE | Quantity needs to be more than 0. |
| QUANTITY_DECIMALS / PRICE_DECIMALS | Use up to 10 decimal places. |
| ENTRY_PRICE_REQUIRED | Enter your entry price. |
| PRICE_NOT_NUMBER | Enter a number like 100.25. Use a period for decimals. |
| PRICE_ZERO | Enter a price other than 0. |
| EXIT_PRICE_NEEDED | Add an exit price, or clear the exit time. |
| EXIT_TIME_NEEDED | Add an exit time, or clear the exit price. |
| EXIT_PRICE_REQUIRED | Enter the exit price. (close mode, both exit fields blank or the price blank) |
| EXIT_TIME_REQUIRED | Enter the exit time. (close mode) |
| EXIT_PRICE_REQUIRED_CLOSED | A closed trade needs an exit price. (edit-closed mode only) |
| EXIT_TIME_REQUIRED_CLOSED | A closed trade needs an exit time. (edit-closed mode only) |
| ENTRY_TIME_REQUIRED | Enter when you entered the trade. |
| TIME_INVALID | Enter a date and time like 2026-03-02 09:30. (an unparseable entry or exit time, e.g. the text fallback) |
| TIME_FUTURE | That time hasn't happened yet in {zone}. Check the date, time and time zone. |
| TIME_NOT_READABLE **[PENDING AC 21]** | We couldn't use that time in {zone}. Check the date and time, then try again. |
| EXIT_BEFORE_ENTRY **[PENDING AC 8/35]** | The exit time can't be before the entry time ({entry}). |
| POINT_VALUE_NEEDED | We don't have a point value for {symbol} yet. Enter one; for shares, use 1. |
| POINT_VALUE_NOT_NUMBER | Enter a number like 1000. Use a period for decimals. |
| POINT_VALUE_NOT_POSITIVE | Point value needs to be more than 0. |
| POINT_VALUE_DECIMALS | Use up to 10 decimal places. (the column is `NUMERIC(20,10)`, same as quantity and price; `docs/data/schema.md`) |
| FEES_NOT_NUMBER | Enter a number like 2.50. Use a period for decimals. |
| FEES_NEGATIVE | Fees can't be negative. Enter 0 or more. |
| FEES_DECIMALS | Use up to 4 decimal places. |
| FEES_NEEDS_EXIT | Fees apply once a trade is closed. Add an exit, or clear Fees. |
| NUMBER_TOO_LARGE | That number is too large. (existing key) |
| ACCOUNT_TOO_LONG, ACCOUNT_CONTROL_CHARACTERS | Existing keys, reused. |
| STOP_RECHECK_REFUSED **[ADR-0008 item 3, option (a) only]** | Your saved stop ({stop}) would sit on the wrong side of this entry price. Change the stop in the journal first, or choose a different price. |

### Flashes and notices

| Key | Variant | String |
|---|---|---|
| TRADE_ADDED | success | Trade added. Journal it now, or come back later. |
| TRADE_CLOSED | success | Trade closed. |
| TRADE_UPDATED | success | Trade updated. |
| ALREADY_CLOSED | info | That trade was already closed, so nothing was added. |
| MANUAL_SAVE_FAILED | attention Notice | We couldn't save that just now. Your entries are still here, so try again in a moment. |

### Locked fields **[ADR-0008 slot]**

| Key | String |
|---|---|
| LOCKED_REASON | Locked because this trade has a journal entry. (proposed; final wording after the ADR) |

### Delete

| Key | String |
|---|---|
| MANUAL_DELETE_TITLE | Delete this trade? |
| MANUAL_DELETE_SUBTITLE | {symbol} {side}, opened {opened} ({zone}) |
| MANUAL_DELETE_BODY | This removes this trade from your trades and your stats. Your other trades stay as they are. |
| MANUAL_DELETE_BODY_OPEN | This removes this open trade from your trades. Your other trades stay as they are. |
| MANUAL_DELETE_JOURNAL_BODY | This trade has a journal entry. Deleting the trade deletes that entry too, along with any stop or planned risk on it. |
| MANUAL_DELETE_LIST_HEADING | Journal entry that will be deleted |
| MANUAL_DELETE_LIST_HELPER | Want to keep your writing? Copy it from the box above. |
| MANUAL_DELETE_CHECKBOX_NOTE | Also delete my journal entry (with a written note). This can't be undone. |
| MANUAL_DELETE_CHECKBOX_NO_NOTE | Also delete my journal entry (no written note). This can't be undone. |
| MANUAL_DELETE_BUTTON / MANUAL_DELETE_BUTTON_ENTRY | Delete trade / Delete trade and entry |
| MANUAL_DELETE_STALE | Your journal entry changed while this was open, so nothing was deleted. The entry below is up to date. |
| MANUAL_DELETED | Trade deleted. (success flash) |
| MANUAL_DELETED_WITH_ENTRY | Trade and journal entry deleted. (success flash) |

Reused unchanged: `DELETE_PERMANENT`, `DELETE_CANCEL`, `DELETE_BUSY`, `DELETE_NOT_TICKED`, `DELETE_FAILED`, `DELETE_LIVE_ENABLED`, `DELETE_LIVE_DISABLED`, `DELETE_NO_NOTE`, `DELETE_HAS_RISK`, the `RULES_*` answer words.

Import delete body: no change. "Your other imports stay as they are." stays; manual trades belong to no import, so the sentence is true as it is.

---

## 13. Components and new context keys

### Components

| Component | Notes |
|---|---|
| `ManualTradePage` (`templates/journal/trade_form.html`) | One template for add, close, edit (`mode`). Back link, header (summary on close/edit), intro, journal-kept note, form. |
| `PointValue` (`partials/point_value.html`) | The three states; the htmx swap target; plus the OOB live region. |
| `SideTiles` (`partials/side_tiles.html`) | Copy of `rules_question.html` structure with Long/Short and arrow icons. |
| `TimeField` (`partials/time_field.html`) | `field.html` + help + `nowButton` hook. |
| `ReadOnlyValue` (`partials/readonly_value.html`) | `<dl>` row: label, value, optional help. Used by Point value (known), Size, locked fields. |
| `TradeActions` (`partials/trade_actions.html`) | 3.3. |
| `TradeDeleteConfirm` (`partials/trade_delete_confirm.html`, page `trade_delete.html`) | 8. |
| `AccountInput` (`partials/account_input.html`) | Extract the inner `.field` block of `account_field.html` (label, input, datalist, errors, counter, help); the import wrapper keeps its `<details id="account-details">` and `id="account-field"`; the manual form calls it inside More details with its own label/help/placeholder. Same `accountField` Alpine component, same datalist id. |
| Existing, reused | `Notice`, `ErrorSummary`, `FormField` (`suffix`), `trade_summary.html`, `delete_dialog.html`, `delete_link.html` (+ new `label` parameter), `deleteConfirm`, `accountField`, `.action-bar`, `.disclosure`, `.radio-tile`. |

New Alpine component: `nowButton` only (`static/js/app.js`). New icons: none.

### New context keys (names for backend to supply)

Form pages (add, close, edit): `mode` ("add" | "close" | "edit"); `form`; `zone`; `pv_state` ("pending" | "known" | "unknown"); `pv_root`; `pv_value_display` (formatted, known state); `pv_symbol` (normalized symbol the unknown input is for, rendered into `pv_for`); `more_open`; `show_fees` (false only for edit-open); `show_exit` (false for edit-open); `exit_required` (edit-closed and close); `form_token`; `account_labels`, `account_placeholder` (all brokers, per AC 31); `has_journal` (edit); `locked_fields` (set; empty until ADR-0008); `trade` (same keys the journal page passes to `trade_summary.html`, including `is_open`) for close and edit; `size_display` (close); `entry_time_display` (for `EXIT_BEFORE_ENTRY`).
Form class: locked fields (7.2) must be **excluded from the form** for that request, not rendered as disabled inputs. Otherwise their absence from the POST reads as a blank required field. Read-only rows (known Point value, Size, locked fields) carry no input name.
Field values: `entry_time` and `exit_time` bound as `YYYY-MM-DDTHH:MM` in the user's zone.
Journal page: `trade.is_manual` (bool, from the opening execution's source) and `trade.is_open`. The template builds the action URLs from `{% url %}` and `trade.id`.
Delete page and partial: same names as the import version (`page`, `notice`, `body`, `journal_body`, `checkbox_label`, `delete_button`, `shown_token`) plus `entry` (`rules`, `note`, `has_risk`), `trade`.
Point value endpoint (placeholder `trade_point_value`): GET `symbol`, returns `point_value.html` and the OOB live region; context `pv_state`, `pv_root`, `pv_value_display`, `pv_symbol`.
List page: none (the Add link is a constant `{% url %}`).
Flash attrs: add `{% block flash_attrs %} tabindex="-1" autofocus{% endblock %}` to `trade_list.html` so the delete and (future) list flashes get focus.

---

## 14. Changes to other docs, spec questions, open questions

### Other design docs (mine; not applied unless noted)

| Doc | Change |
|---|---|
| `auth-and-trades-list.md` 5.4 A | Replace "the one and only action ... no 'add by hand' option or link" with the two-button empty state (3.2). Same for 5.1's header sketch (add "Add trade") and "Former Q4" (now superseded by this doc). **Not applied** (waiting for the user to accept this draft). |
| `import-account-label.md` header scope note | "restore 'add a trade' in the empty imports list" is not wanted: manual trades do not belong to an import. Import delete body unchanged. **Not applied.** |
| `journaling.md` 4.6 | 404 body reworded (section 12). **Not applied.** |
| `docs/README.md` | Add a Design row for `manual-entry` (owner `ux-designer`, Draft). Until the three edits above are applied, `auth-and-trades-list.md` 5.4 A ("no add by hand option") contradicts this doc; note it under "Known conflicts" or apply the edit once the user accepts this draft. Owner: orchestrator. |

### Spec points for `product-manager` (one line each)

1. **Close form and Account label:** AC 27 says More details on add, close and edit holds Fees and Account label, but AC 34 copies the label onto the exit fill and the label is part of the matcher bucket, so an editable label on close could split the pair. This design shows the label read-only in the header and puts only Fees in More details on close. Confirm, and amend AC 27.
2. **Edit of an open trade shows no exit and no Fees**, derived from AC 29 and AC 38 ("no `Execution` row added or removed"). Adding the exit is Close's job. Confirm.
3. **Fees on add:** AC 29 says no Fees input is offered on an open trade. On the add form that state is unknowable before submit without JS, so Fees is always offered there and rejected with `FEES_NEEDS_EXIT` when posted without an exit. Edit-open and close behave per AC 29. Confirm. Open sub-question: does a posted `0` count as "a Fees value"? This design says any non-blank value is rejected; relax if you prefer.
4. **Empty-state wording:** `import-and-list.md` section 2 needs the same manual-entry pass (`TRADES_EMPTY_BODY` replaced).

### Needs another owner

- `architect` (ADR-0008): route/endpoint names (placeholders above: `trade_add`, `trade_close`, `trade_edit`, `trade_delete`, `trade_point_value`); lock set (7.2); stop re-check (7.3); form-token mechanics; exit-before-entry opener ordering.
- `trading-domain-expert`: AC 8 (exit before entry), AC 21 (DST). Copy is drafted and marked PENDING.
- `backend-engineer`: re-render-with-values on every failure; the `pv_for` guard; token replay returns the same redirect; the 401-without-redirect exception on the point-value endpoint; `copy` binding (row 42).

### Open questions

1. **Cut candidate:** "Use current time". Small (one Alpine component), but cuttable if scope is tight. The form works without it.
2. **A close shortcut on list rows** for open manual trades (second link in the Journal cell) was left out to keep rows identical and tab stops down. Revisit only if users cannot find Close.
3. **Symbol suggestions** (a datalist of the user's recent symbols) were left out (YAGNI; the Account datalist pattern would carry it later).
4. After Add, destination is the journal page. If people backfill many trades in one sitting and find the extra Back click costly, revisit with an "Add another" link on that flash.

---

## 15. Frontend deliverables (build these and nothing else)

Backend and ADR-0008 must exist first; templates are built against the context keys in section 13.

- [ ] `trade_list.html`: "Add trade" `btn-secondary` after Import (3.1); `flash_attrs` block.
- [ ] `trades_empty.html`: secondary "Add trade" button in states A and B (3.2).
- [ ] `trade_actions.html` + included in `journal_form.html` under the summary for `trade.is_manual` (3.3); `delete_dialog.html` included on that page; `delete_link.html` gains `label`.
- [ ] `trade_form.html` (add/close/edit by `mode`) per sections 4, 6, 7: field order, `field.html` with `suffix=USD` on price and Fees, `novalidate`, `aria-required`, `datetime-local`, error summary, sticky `action-bar`, `data-busy`, hidden `form_token`.
- [ ] Symbol currency hint (4.2): second `<p id="id_symbol_currency_hint">` under the Symbol help via an optional `hint`/`hint_id` on `field.html`; `aria-describedby` on Symbol = help, hint, error ids; `copy.SYMBOL_CURRENCY_HINT`; rendered for `mode` add and edit, never close, omitted if `symbol` is locked; test asserts the text, the id in `aria-describedby`, and absence on close.
- [ ] `side_tiles.html` (no default, side by side, 48px mobile, neutral tokens).
- [ ] `point_value.html` three states, `readonly_value.html`, hidden `pv_for`; Symbol input htmx attrs (5.4), `#point-value-live` OOB region; no focus move.
- [ ] `time_field.html` + `nowButton` in `app.js` (CSP, `data-` props, floor to minute, zone from `data-zone`, status text; button `hidden` without JS).
- [ ] `account_input.html` extracted from `account_field.html` (import page output unchanged; its tests still pass); used in More details.
- [ ] More details `<details class="disclosure">` open by `more_open`; contents per mode (add: Fees + Account; close: Fees; edit-closed: Fees + Account; edit-open: Account).
- [ ] Locked-field row rendering from `locked_fields` (7.2), wired but empty until ADR-0008.
- [ ] `trade_delete_confirm.html` + `trade_delete.html` (8): two variants, entry region, tick gate via `deleteConfirm`, stale/not-ticked/failed notices, no-JS page.
- [ ] `trade_not_found.html`: unchanged markup, body from the replaced key.
- [ ] Copy: every string from section 12 via `copy.*` from the right module; no English literals in the new templates.
- [ ] Tailwind bundle rebuilt if any new class is missing from `static/css/app.css` (check `input-suffix-box`, `radio-tile`, `action-bar`, `disclosure` already present).

States to verify by hand (light and dark, 320px, 200% zoom): add blank, validation error (each required field), unknown point value (no JS and htmx), known root, symbol changed after prompt, exit pair error, fees without exit, future time, save failure, submitting, edit open and closed, edit with journal (info line), close (valid, already closed, exit before entry), delete variant 1 and 2, stale, not ticked, failed, no-JS delete page, 404 for imported/foreign/deleted, empty states A and B, mobile sticky bar over a focused field, Side tiles at 320px.

Focus and aria summary: flash/summary/Notice focused on load; field errors `aria-invalid` + `aria-describedby`; Side by fieldset/legend; read-only values as `<dl>`; USD suffix `aria-hidden`; live regions only for the point-value swap and the time button; no `role="alert"`.
