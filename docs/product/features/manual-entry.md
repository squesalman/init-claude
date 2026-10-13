# Manual trade entry: add, close, edit, delete

- **Status:** Accepted (user, 2026-10-13). User rulings applied (three rounds plus the ADR-0008 rulings: AC 40 and AC 36), domain rulings applied (`pnl-and-matching.md` §7, vectors 24-34); [ADR-0008](../../adr/0008-manual-entry.md) is Accepted. Design draft `design/manual-entry.md` is owned by `ux-designer`.
- **Owner:** `product-manager`
- **Covers:** `mvp.md` story 2 (manual trade entry). Supersedes story 2's field list: exit price and exit time are optional here (ruling 1).
- **Depends on:** ADR-0003 §4-§6 (executions are the truth, no trade table), ADR-0004 §3 (pairing key), ADR-0005 (delete semantics, counted confirmation), ADR-0007 (journal entry, RESTRICT FK, stale guard, `PlainDecimalField`), `docs/domain/pnl-and-matching.md` (§1 matcher, §3 R, §7 manual entry, vectors 1-34), `docs/domain/topstep-import.md` (§2 multipliers, §5 fees on the exit fill, §7 roots to verify, vectors T1-T2), `docs/design/manual-entry.md` (UX draft), `journal/importers/topstep.py` (`MULTIPLIERS`, root pattern), `journal/matching.py` (bucket key, line 48)
- **Already built (do not recreate):** `Execution` with `source='manual'`, `broker_execution_id` NULL allowed, `contract_multiplier`, `fees`, `currency`, `broker_account_label`; the matcher; the journal form and `/trades/` Journal control; `UserScopedModelForm`; the import Account datalist.

## Problem

Not every trade comes from a Topstep export. A trader who takes a stock, forex or crypto trade, or a futures trade on another broker, has no way to get it into the journal today, so the one thing this app is for (writing down why) is blocked for them. The manual path has to be faster than skipping it, or they will not use it.

## Users

Retail discretionary traders who (a) trade something Topstep import does not cover, or (b) want to journal a trade right after taking it, before any export exists. Same user as the rest of the MVP; no new role.

## User rulings (2026-10-13, quoted from `docs/README.md` "Recent rulings")

> manual entry (MVP story 2) user rulings: (1) a manual trade may be open when saved (exit price/time optional); a "Close this trade" form adds one full-size exit fill, no partial closes. (2) Contract multiplier: prefill from the importer's root lookup (`MULTIPLIERS`) for known roots; for an unknown root a required "Point value" field with no default (equities type 1). (3) Typo fixes: edit in place for `source=manual` fills only (imported fills stay immutable), keeping the journal entry, plus a delete action with a counted confirmation; needs ADR-0008 to amend ADR-0003 "immutable by convention". (4) Optional collapsed "More details": one Fees number (on the exit fill, like imports) and an Account label with suggestions. Constraints for `architect`: every manual trade needs its own pairing key (NULL `broker_trade_id` would make all manual fills for a label+symbol share one FIFO bucket, `journal/matching.py:48`); which fields lock once a journal entry exists, stop side re-check on edit, delete removes the journal entry first in the same transaction (RESTRICT FK); double-submit guard.

## User rulings applied 2026-10-13 (second round; reply "defaults")

1. **Currency is fixed to USD** for the MVP: read-only, not editable, still stored on every fill (`Execution.currency` NOT NULL). Non-USD manual entry waits for a ruling on `docs/data/follow-ups.md` row 45 plus a minor-unit rounding table (see Out of scope).
2. **Point value for a known root is read-only.**
3. **Rulings, formerly assumptions:** Side and entry time have no default; future times are rejected; fees are 0 or more; the delete confirmation mirrors ADR-0005; a closed trade cannot be reopened.
4. **Last-used point value suggestion: skipped** (conflicts with "no default"); one-line deferred follow-up only.

## User rulings applied 2026-10-13 (third round; user said "hint and go")

1. **Exit before entry (AC 8):** rejected on add, close and edit. Equal times are allowed. Compared as UTC instants (`pnl-and-matching.md` §7.1, vector 32).
2. **DST (AC 21):** a nonexistent and an ambiguous local time are both rejected, never silently picked. The backend must activate `User.timezone` when parsing. An unchanged time field on edit keeps its stored UTC value and is not re-parsed (`pnl-and-matching.md` §7.2, vector 34).
3. **Account label on Close is read-only** (it is part of the matcher bucket); only Fees sits in More details on Close (AC 27).
4. **Fees on add:** the add form always offers Fees; Fees with no exit is a field error; any non-blank value counts, including `0` (AC 29). Editing an open trade shows no exit input and no Fees, only a link to Close; Close is the only way to add an exit (AC 29, 37, 38).
5. **Instrument hint:** the add form shows a short hint near Symbol that only USD-quoted instruments are supported for now (AC 53; practical content of the non-USD deferral, see Out of scope).

No spec point is left PENDING on a user, the domain expert or the PM. Still open elsewhere: ADR-0008 (architect) and the `ux-designer` copy and the user's acceptance of the design draft.

## Slice in one line

An "Add trade" form with five required inputs (symbol, side, quantity, entry price, entry time), an optional exit, and a collapsed "More details". It writes an entry fill (and an exit fill if given). The same matcher derives the trade, so it shows up in `/trades/`, in the stat cards and on the journal page exactly like an imported one.

## User stories

1. **Add a trade.** As a trader, I add a trade I took, open or already closed, with the fewest fields that can compute P&L, so I can journal it the same day.
2. **Close an open trade.** As a trader, I add the exit to a trade I entered earlier ("Close this trade"), so I do not re-enter the trade.
3. **Fix a typo.** As a trader, I edit a manual trade I mistyped, and keep the journal entry I already wrote for it.
4. **Delete a manual trade.** As a trader, I delete a manual trade I should not have added, knowing exactly what else goes with it.

## Design decisions

### What the form writes

- The form writes **fills** (`Execution` rows), never a trade. There is no trade table (ADR-0003 §5). The same `derive_trades()` matcher that handles imports derives the trade, so P&L cannot differ between manual and imported data.
- Add open: one entry fill. Add closed: an entry fill and an exit fill. Close: one exit fill added to the open trade. Edit: the trade's existing fills are updated in place (ruling 3). Delete: the trade's fills are removed.
- Side mapping is the importer's (`topstep-import.md` §5): Long = buy entry, sell exit; Short = sell entry, buy exit. The user sees Long/Short, never buy/sell.
- Every manual fill is `source='manual'`, `broker='manual'`, `broker_execution_id` NULL. The pairing key that keeps each manual trade its own matcher bucket is ADR-0008's job (see "Open questions"). Behavior required by this spec: a manual trade never merges with, or pairs against, any other trade, manual or imported, on the same symbol and account label.
- The trade id is the opening (entry) fill's pk (ADR-0003 §6), so the journal URL is unchanged by close and by edit.

### Fields

| Field | Required to save? | Notes |
|---|---|---|
| Symbol | Yes | Trimmed, uppercased, max 32. Free text, as the user would write it (`AAPL`, `CLZ6`). A short hint nearby says only USD-quoted instruments are supported for now (AC 53). |
| Side (Long / Short) | Yes | No default selected. |
| Quantity | Yes | Greater than 0. Fractional allowed (crypto, forex), up to the column's 10 decimals. |
| Entry price | Yes | A number other than 0 (the same rule as the `Execution` constraints; negative futures prices are real). |
| Entry time | Yes | In the user's time zone (below). No default; a one-tap "Now" shortcut is a UX call, never a prefilled value. |
| Exit price, Exit time | No | Both or neither. Blank = open trade. |
| Currency | No input | Fixed to `USD` for the MVP (ruling 1 above). Shown read-only, not editable, not taken from `User.base_currency`, and stored on every fill. |
| Point value | Depends on symbol | See the point-value rule. |
| More details (collapsed) | No | Fees, Account label. Nothing else. |

The form has no note, no stop, no planned risk and no rules answer. The Journal control on the trade does all of that, so the add form stays at five required inputs.

### Point-value rule (ruling 2)

- Root = the symbol with the trailing `[A-Z][0-9]` stripped, by the importer's own pattern (`(.+)[A-Z][0-9]`). A symbol that does not match has no root.
- **Known root** (a root in the importer's `MULTIPLIERS`, today `CL` = 1000 and `MCL` = 100): the Point value shows the looked-up number, **read-only**. The server uses the lookup and ignores any posted value.
- **Unknown root** (including no root, such as `AAPL`, and today's `ES`, `MNQ`, `GC`): the Point value is a **required** input with **no default and no prefilled number**. Stocks type 1. Futures traders type the value they know.
- Stored on both fills as `contract_multiplier`, so a later contract-spec change never rewrites old P&L (ADR-0003).
- One lookup. The form imports the importer's `MULTIPLIERS`; there is no second table (see out of scope).
- The server decides at submit. Any live prefill when the symbol field loses focus is an enhancement; without JS, submitting with an unknown root re-renders the form showing the required input.

### Time zone

- Entry and exit times are typed in the user's saved time zone (`User.timezone`), the zone name is shown beside the field, and they are stored as UTC. Every time shown back (list, edit form, close form) is rendered in that zone.
- Same-day exit and entry on one date is the normal case; exit must not be earlier than entry (equal is allowed, matching the importer ruling of 2026-09-27). The comparison is on UTC instants after conversion, not on the typed wall-clock text (AC 8).
- The backend activates `User.timezone` when parsing typed times. A local time that does not exist (spring-forward gap) or occurs twice (fall-back overlap) is rejected, never silently resolved (AC 21).
- On edit, a time field the user did not change keeps its stored UTC value and is not re-parsed (compare the posted text with the rendered text of the stored value), so a later change of `User.timezone` cannot make an old trade uneditable.
- Validation order for a time field, so each field shows one error: required, parse, DST gap/overlap, not in the future, then exit not before entry (`pnl-and-matching.md` §7.2).

### More details (ruling 4)

- Collapsed by default on add, close and edit. It opens when a value is already set (edit) or after an error inside it.
- **Fees:** one number, written on the **exit** fill (entry fill = 0), like imports (`topstep-import.md` §5). Blank = 0. Where it is offered: **add** (always, because without JS the form cannot know whether an exit will exist), **close**, and **edit of a closed trade**. **Not offered** on edit of an open trade (no exit fill exists and edit cannot create one). On add, Fees with no exit is a field error and nothing is saved; any non-blank value counts, including `0`. A Fees value is never silently dropped.
- **Account label:** free text, trimmed, max 64, blank allowed (stored `''`), written on every fill of the trade. Suggestions are the user's own existing labels (imports and manual), as the import Account field does. No silent case normalisation. Editable on add and edit. **Not editable on Close:** the label is part of the matcher bucket, so Close shows it read-only and More details there holds only Fees.

### Edit, close, delete

- **Close this trade:** shown only on open **manual** trades. Exit price and exit time are required; Fees sits in More details (alone: the Account label is shown read-only). Size is shown read-only (full size, no partial close). The exit fill copies symbol, label, currency, point value and the pairing key from the trade, takes the opposite side, and carries the full quantity.
- **Edit:** only for `source=manual` trades. The same fields as add, except on an **open** trade: no exit inputs and no Fees (edit never adds a fill), only a link to Close this trade. Close is the only way to add an exit. Fills are updated in place (same ids), so the journal entry stays attached and the journal URL keeps working. No field is locked when a journal entry exists (ADR-0008 §2): every editable field stays editable. A closed trade cannot be reopened by blanking the exit.
- **Delete:** shown only on manual trades. GET shows the confirmation and never deletes. POST only, with CSRF.
  - No journal entry: one ordinary confirm, no checkbox.
  - A journal entry exists: the confirm says so, shows the note in full (so the user can copy it), says whether it has a written note, an answer, and a stop or planned risk amount, and keeps the Delete button disabled until the user ticks "Also delete my journal entry. This can't be undone." The server enforces the tick. This mirrors ADR-0005's import delete so the two dialogs read alike.
  - The journal entry is deleted first, then the fills, in one transaction. If the entry was added or changed in another tab after the confirm rendered, nothing is deleted and the confirm re-renders with the fresh entry (ADR-0007 §7 pattern).

### Where manual trades show up

Same list, same cards, same Journal control as imported trades, no required visual distinction in the data columns (`mvp.md` story 3). Edit and Delete actions exist only on manual trades. Open manual trades are in no stat card (open trades are never counted, `pnl-and-matching.md` §1 and §3).

## Acceptance criteria

Tests reuse existing vectors; do not invent new formulas. Vectors used: domain vectors 1 and 25 (net 48.00 when the trade's total fee 2.00 sits on the exit fill), Topstep T1 and T2 (manual shape: symbol `CLZ6` / `MCLZ6`, fees on the exit fill), journal vectors 6-23 for R, and the manual-entry vectors 24-34 in `pnl-and-matching.md` §5 (24 close, 26 short, 27 unknown root, 28 fee rounding, 29 edit, 30-31 own bucket, 32 time order, 33 two-fill edit, 34 local time to UTC). Copy for every message below is owned by `ux-designer` (final strings in `docs/design/`), not the engineer.

### Add a trade
1. Given a logged-in user, when they open `/trades/` (with or without trades), then there is an "Add trade" control that opens the form. (Placement: `ux-designer`.)
2. Given symbol, side, quantity, entry price and entry time are given (and a point value if the root is unknown), when the form saves, then exactly one `Execution` exists for the trade (`source='manual'`, `broker='manual'`, `broker_execution_id` NULL, user = session user), the user lands where `ux-designer` specifies (destination and flash copy are theirs), and the trade shows in `/trades/` as open with no P&L.
3. Given any of the five required inputs is blank, when the form is submitted, then the page re-renders (200) with an error on each missing field, values kept, and no `Execution` is created. Copy: `ux-designer`.
4. Given Side is not selected, when submitted, then it is an error; there is no default side.
5. Given a long 10 at 100.00, exit 105.00, Fees 2.00 in More details, when saved, then two fills exist (buy entry, sell exit with fees 2.00, entry fees 0), and the derived trade is closed with net P&L 48.00 (vector 1).
6. Given symbol `CLZ6`, Long, quantity 2, entry 80.00, exit 80.15, Fees 8.04, when saved, then the point value is 1000 and net P&L is 291.96 (T1). Given `MCLZ6`, Short, 1, entry 80.00, exit 79.80, Fees 1.52, then the point value is 100 and net P&L is 18.48 (T2).
7. Given exit price without exit time (or the reverse), when submitted, then the missing one is an error and nothing is saved.
8. Given an exit time earlier than the entry time, when submitted on add, close or edit (including moving the entry later than a stored exit), then it is an error on the exit time and nothing is saved. Times are compared as UTC instants after conversion, not as typed wall-clock text: entry 2026-03-08 01:45 and exit 03:15 in `America/New_York` is 30 minutes and saves; entry 2026-11-01 00:45 and exit 02:15 is 2h30 and saves (vector 34). Equal instants save, the trade is a long with the entry fill as opener (vector 32a). Rule and reason: `pnl-and-matching.md` §7.1 (an early exit would sort first, flip the side and orphan the journal entry, vector 32b). Add-closed inserts the entry fill before the exit fill (equal times fall to the pk tie-break; ADR-0008 item 6 owns the mechanics).
9. Given quantity 0 or negative, or not a number, then an error. Given quantity 0.5 (up to 10 decimals), then it saves exactly. Given an entry or exit price of 0, then an error; a negative non-zero price saves.
10. Given numbers with thousands separators (`1,234.50`), then they parse (the journal form's `PlainDecimalField` rules); given text, then the same "not a number" error as the journal form. Given more digits than the column holds, then an error, never a 500.
11. Given a symbol with surrounding spaces and lower case, when saved, then it is stored trimmed and uppercased; given blank or over 32 characters, then an error.
12. Given a manual trade of any kind, then both fills carry currency `USD`; the form has no editable Currency input (the currency is shown read-only); and given a tampered POST naming a currency (or the user's `base_currency` being non-USD), then the stored currency is still `USD`.
12a. **[RESOLVED by user ruling 2026-10-13]** Currency is fixed to USD for the MVP and stays stored on every fill. This keeps manual trades inside what `/trades/` renders today (only the USD group of the Avg R card, follow-ups row 45) and inside the matcher's 2 dp rounding (`journal/matching.py`, no minor-unit table). Non-USD manual entry is deferred (see Out of scope). Given the fills a manual trade writes, then none is non-USD.
13. Given a saved trade, then price, quantity, fees and point value round-trip as exact decimals (for example fees 1.5 reads back as 1.5000). No float anywhere.
14. Given the same rendered form is submitted twice (double click, Back then resubmit), then exactly one trade exists and the second request ends on the same result as the first, not an error page. Given two separately rendered forms with identical content, then two trades exist (no content-based dedupe: two identical scalps are legitimate).
15. Given a database error on save, when submitted, then the form re-renders (200) with the submitted values and the save-failure notice, never a bare 500 (the journal form's AC 14a pattern). Copy: `ux-designer`.

### Point value
16. Given a known root (`CLZ6`), then the Point value shows 1000 read-only. Given a tampered POST with a different point value, then the stored `contract_multiplier` is still 1000.
17. Given an unknown root (`AAPL`, or `ESZ6` while `ES` is unverified), then the Point value input is required, empty, has no default and no placeholder number. Blank is an error; 0 or negative is an error; 1 saves (equities).
18. Given the importer's `MULTIPLIERS` gains a root, then manual entry treats that root as known with no other change (one lookup, tested by importing the same dict).
19. Given a symbol edit that changes known/unknown status, then the rule is re-evaluated on save: known roots take the lookup, unknown roots require the field.

### Time zone
20. Given a user in `America/New_York`, when they enter 2026-03-02 09:30, then the fill's `executed_at` is 2026-03-02 14:30 UTC, and the edit form and list show 09:30 in that zone. A user in `UTC` entering the same text stores 09:30 UTC.
21. Given a local time that does not exist (2026-03-08 02:30 in `America/New_York`; the whole of 02:00-02:59 on that date) or exists twice (2026-11-01 01:30; the whole of 01:00-01:59), then the form rejects it with a message asking the user to check the date and time, and never picks one occurrence. Neighbours save: 2026-03-08 01:59 is 06:59Z, 03:00 is 07:00Z; 2026-11-01 00:59 is 04:59Z, 02:00 is 07:00Z (`pnl-and-matching.md` §7.2, vector 34). The backend must activate `User.timezone` before parsing; with the server zone active the check does not work. Given an edit where the time field is unchanged, then the stored UTC value is kept and the field is not re-parsed (so a trade saved before a time-zone change stays editable); only a changed field is parsed and checked. Cost accepted: a 24/7 (crypto) trader cannot record a trade inside that one hour in local time; they use the nearest valid minute (follow-up only if someone hits it: an explicit first/second occurrence choice).
22. Given an entry or exit time later than now (plus a small clock-skew grace chosen by backend), then it is an error. (User ruling 2026-10-13: future times are rejected.)

### Matching, list, stats and journaling
23. Given a manual open long on symbol `AAPL` and a second manual open long on the same symbol and account label, then `/trades/` shows two open trades; closing one leaves the other open and untouched. Given an imported trade on the same symbol and label, then the manual trade neither merges with nor pairs against it, and the imported trade's P&L is unchanged.
24. Given closed manual trades (all USD, AC 12), then they are counted in Win rate, Total P&L and Avg R exactly like imported ones (same formulas, same sample sizes); given an open manual trade, then it is in none of the cards' `n` or "Left out".
25. Given a manual trade (open or closed), then its row has the same Journal control, opens `/trades/<opening_execution_id>/journal/`, and saving a journal entry works. Given a stop or planned risk on it, then R follows `pnl-and-matching.md` §3 (a manual trade has one entry lot, so a stop is a valid R input; vectors 6-9, 14).
26. Given a manual trade, then no price-sanity or "looks unusual" warning appears anywhere: the app does not check prices against any market and does not comment on the trade (no financial advice).

### More details
27. Given the add, close and edit forms, then "More details" is collapsed by default, is never required, and a form saved without opening it works. It opens if it already holds a value or has an error. Contents: add and edit-closed = Fees and Account label; **close = Fees only** (the Account label is part of the matcher bucket and is shown read-only in the trade header, AC 34); edit-open = Account label only (no Fees, AC 29). Stored fees `0.0000` do not count as "already holds a value".
28. Given Fees blank, then 0 is stored. Given more than 4 decimals, then an error. Given a negative fee, then an error (user ruling 2026-10-13: fees are 0 or more; rebates are out).
29. Given a closed trade, then Fees sit on the exit fill only (entry fill 0). Given the add form, then Fees is always offered in More details (without JS the form cannot know whether an exit will exist) and its help says it is only used with an exit. Given the add form with a non-blank Fees (including `0`) and no exit, then it is a field error, More details opens, and nothing is saved; a Fees value is never silently dropped. Given the close form, then Fees is offered. Given the edit form of an open trade, then no Fees input and no exit inputs are offered, only a link to Close this trade; a tampered POST naming Fees or exit fields there writes nothing.
30. Given an Account label with spaces around it, then it is stored trimmed on every fill; over 64 characters is an error; blank stores `''`.
31. Given the Account field, then it starts blank (never prefilled with the last label), the suggestions are the user's own distinct non-blank labels from their executions (imports and manual, any broker), most recent first, at most 10, free text is still accepted, and case is not normalised. Same rules as `import-account-label.md` (blank-by-default, datalist, max 10, most recent first); the only difference is the broker scope, because a manual trade often belongs to an account the user already labelled on import.

### Close this trade
32. Given an open manual trade, then the row or journal page shows "Close this trade". It is absent on closed manual trades and on every imported trade.
33. Given the close form, then it asks for exit price and exit time (required), shows the size and the Account label read-only, and has Fees alone in More details. There is no quantity, side, point value or account input.
34. Given a valid close, then exactly one exit fill is added: opposite side, full quantity, same symbol, label, currency, point value and pairing key as the trade; the trade becomes closed with P&L per the vectors; the trade id and journal URL are unchanged; an existing journal entry is untouched and still attached.
35. Given an exit time earlier than the entry time (compared in UTC), then it is an error naming the entry time and nothing is saved (as AC 8).
36. Given the trade was already closed (second tab, or a double submit), then no second exit fill is written, the user sees an "already closed" message, and the trade is unchanged.

### Edit
37. Given a manual trade, then Edit is available, the form is prefilled with current values (times in the user's zone), and the validation is the same as add (AC 3-13, 16-22, 28-31), with two differences: on an open trade there are no exit inputs and no Fees (AC 29), and on a closed trade the exit pair is required (AC 41).
38. Given a valid edit, then the trade's existing fills are updated in place: no `Execution` row is added or removed (an edit of an open trade can never add the exit; Close is the only way to add one), ids and `created_at` are unchanged, and `user`, `source` and `broker` are unchanged. Given a closed trade, then both fills are written in one transaction and keep agreeing on symbol, label, point value, quantity and pairing key (vectors 29, 33).
39. Given a trade with a journal entry, when any edit is saved, then the same `JournalEntry` row remains attached, and its note, `rules_followed`, stop and planned risk are unchanged. No field is locked on edit (ADR-0008 §2), so there are no exceptions.
40. Given an edit after which a stored stop sits on the wrong side of the new entry price or side, then the edit is allowed (user ruling 2026-10-13, ADR-0008 §2) and the Avg R card never counts that trade with an invalid R: the trade is left out with its reason (`stop_not_a_risk`), shown on the journal page after the save. The next journal save on that trade fails on the stop field with `stop_not_a_risk` until the user fixes or clears the stop; the re-render keeps everything the user typed, so no journal text is lost.
41. Given a closed manual trade, then the exit cannot be removed by blanking it (no reopen). Given the exit time moved earlier than the entry time, then an error.
42. Given an imported trade (own data), then there is no Edit or Delete control, and a direct request to its edit URL gets the same 404 as a missing id. Imported fills are never writable by this form.

### Delete
43. Given a manual trade, then Delete is available. GET shows the confirmation and deletes nothing; the delete is POST only, with CSRF.
44. Given a trade with no journal entry, then one ordinary confirm (no checkbox) and, on POST, all of the trade's fills are removed; the trade is gone from `/trades/` and the cards on the next read. Same for an open trade.
45. Given a trade with a journal entry, then the confirm states the entry is deleted too, shows its note in full, says whether it has a written note / an answer / a stop or planned risk, and the Delete button is disabled until the box is ticked. Without the tick, the server re-renders the confirm and deletes nothing.
46. Given the journal entry was added or changed in another tab after the confirm rendered, when the user confirms, then nothing is deleted and the confirm re-renders with the fresh entry.
47. Given the delete runs, then the journal entry is deleted before the fills in one transaction; a forced error mid-delete leaves the entry and all fills intact. A bare `execution.delete()` on a journaled execution still raises `RestrictedError` (the FK stays RESTRICT).
48. Given a delete, then no other trade is touched: other manual trades, imported trades and batches on the same symbol and label, and other users' rows are unchanged.

### Tenant isolation
49. Given user B, when they GET or POST user A's edit, close or delete for a trade id, then the response is the same 404 (status, template, text; not byte-identical because CSRF tokens differ per render) as for a missing id, and zero rows change.
50. Given a request body that names `user`, `source`, `broker`, a pairing key, or an execution id, then it is ignored (the forms have no such fields); the user comes from the session.
51. Given user B opens the Account field, then the suggestions contain none of user A's labels.
52. The forms are `UserScopedModelForm`s (so the form-scoping tripwire covers them), every query starts at `for_user(request.user)`, the banned-pattern grep test passes, and the isolation test still enumerates `Execution` and `JournalEntry`.

### Currency hint (numbered last to keep AC 1-52 stable)
53. Given the add form, then a short hint near Symbol says that only USD-quoted instruments are supported for now, and that others (USDJPY, EURGBP, USDT pairs and non-US equities are the examples to name) would be stored and summed as USD. The hint is static text: the app does not inspect the symbol, warn per symbol, or imply that any symbol is USD-quoted (consistent with AC 26 and `pnl-and-matching.md` §7.6). It does not block saving. Copy and exact placement: `ux-designer`. Whether the edit form repeats it: `ux-designer`'s call (the symbol is editable there).

## Empty and edge states

- No trades: the empty state offers "Add trade" next to the import path (placement and wording: `design/manual-entry.md` 3.2). `import-and-list.md` §2 got its manual-entry pass on 2026-10-13. `design/auth-and-trades-list.md` still holds the older copy until `ux-designer` applies the pass there.
- Account column: `import-and-list.md` shows the Account column when the user has more than one distinct label. A manual trade's label counts toward that, so adding one labelled manual trade can make the column appear. Intended; no change to the rule.
- Trade gone between load and save (deleted in another tab): the same 404 as a missing id.
- Open manual trade left open for days: it stays open and counts nowhere; no nagging, no auto-close.
- Fees on add with no exit: field error, More details opens (AC 29). `0` counts as a value.
- Edit of an open trade: no exit and no Fees, a link to Close (AC 29, 38).
- Validation errors: error summary pattern as the journal form, status 200, values kept.
- Coach tone, never shaming: errors say what to fix, not what the user did wrong.

## Out of scope

- **Partial closes.** One full-size exit fill per trade (ruling 1).
- **Multi-leg entry** (scale-ins, several exits) and **flips in one entry**. Every manual trade has exactly one entry lot, so a stop is always a valid R input.
- **Bulk entry**: several trades per submit, paste a table, "duplicate this trade", templates, CSV of manual trades.
- **Editing or deleting imported fills.** Imported fills stay immutable; the fix is re-import, or "Delete this import" (ADR-0005).
- **A growing multiplier table**, a user-managed instrument list, or per-user point-value settings. The importer's `MULTIPLIERS` is the only lookup.
- Reopening a closed trade (removing its exit), moving a fill between trades, changing a trade's source.
- Note, stop, planned risk or rules answer on the add form (the Journal control does these).
- Last-used point value (or last-used anything) prefill or suggestion. Skipped by user ruling 2026-10-13 (conflicts with "no default"). Deferred follow-up, one line: revisit a one-tap last-used point value suggestion only if users complain about retyping it.
- **Non-USD manual entry and an editable Currency field.** Deferred. **Practical content of the deferral (record under follow-ups row 45):** P&L is `(exit - entry) * qty * point_value`, which is in the instrument's quote currency, so with currency fixed to USD it is correct only for USD-quoted instruments (EURUSD, GBPUSD, BTCUSD, US equities, CL). USDJPY, EURGBP, USDT-quoted pairs and non-US equities would be stored and summed as USD though the amount is in another currency. The app does not detect this; the only mitigation until the ruling is the hint (AC 53). Needs (a) a user ruling on `docs/data/follow-ups.md` row 45 (how `/trades/` shows non-USD Avg R groups) and (b) a minor-unit rounding table from `trading-domain-expert` replacing the matcher's 2 dp ceiling (`journal/matching.py`; JPY would be wrong). Trigger: the first non-USD manual-entry request, or the row 45 ruling.
- Autosave drafts, undo, trash (delete is hard, `pg_dump` is the only recovery, as ADR-0005).
- Per-trade time-zone override, FX conversion, pip/lot helpers, market-data price checks, broker connections.
- A "manual" badge or list filter (not needed for MVP, `mvp.md` story 3).
- Any behavior detector, financial advice or signal.

## Rulings (user, 2026-10-13; formerly assumptions)

1. **Currency is USD, fixed** (AC 12, 12a). Read-only, stored on every fill.
2. **Side and entry time have no default** (a wrong silent default writes wrong data). A "Now" shortcut is a UX decision.
3. **Future times are rejected** (AC 22), as a guard against a wrong time zone.
4. **Fees are 0 or more**; rebates (negative fees) are out (AC 28).
5. **Delete confirmation mirrors ADR-0005** (tick box when a journal entry exists), so the two delete dialogs behave the same.
6. **No reopen** of a closed trade; delete-and-re-add loses the journal entry, which the counted confirm makes visible.
7. **Known-root Point value is read-only** (AC 16). Consequence the user accepted: a looked-up value cannot be overridden (for example after a contract spec change); the only fix is the importer table.
8. **Exit before entry is rejected; DST gap and overlap are both rejected** (AC 8, 21). Consequence: a 24/7 trader cannot record a trade inside the one ambiguous or missing hour twice a year in local time.
9. **Close shows the Account label read-only; edit of an open trade has no exit and no Fees; Fees on add are always offered and a non-blank value (even `0`) without an exit is an error** (AC 27, 29, 38).
10. **Add form carries a USD-quoted-only hint** (AC 53).

## Open questions / constraints for other owners

Nothing below is decided here; each goes to its owner before build.

**User rulings needed:** none. AC 8 and AC 21 are resolved (third round, `pnl-and-matching.md` §7.1-§7.2, vectors 32/34). The design doc's four spec questions (section 14) are answered here: Close/Account (AC 27), edit of an open trade (AC 29, 38), Fees on add including `0` (AC 29); the fourth, empty-state wording in `import-and-list.md` §2, is a gap (below), not a PM decision.

**Still open (not PM):** the `ux-designer` copy for the AC 53 hint and for messages above; the user accepting `design/manual-entry.md`; which extra multiplier roots to verify (`topstep-import.md` §7, user).

**`architect` (ADR-0008 is Accepted 2026-10-13 and answers items 1-7 below; kept as the record of what was asked. It amends ADR-0003 §4 "immutable by convention", `journal/models.py` `Execution` docstring, `pnl-and-matching.md` §1 NULL-bucket wording via the domain expert, and ADR-0005's "UPDATE that re-points" ceiling note):**
1. **Per-manual-trade pairing key.** NULL `broker_trade_id` puts every manual fill for a label+symbol in one FIFO bucket (`journal/matching.py:48`), merging separate manual trades. Each manual trade needs its own key. AC 23 is the observable test. Mind the `execution_broker_trade_id_not_blank` CHECK and that manual keys can never collide with a Topstep `Id`.
2. **Which fields lock once a journal entry exists.** Answered: none (ADR-0008 §2).
3. **Stop side re-check on edit.** Answered: allow, and surface via `r_multiple`'s `stop_not_a_risk` (AC 40). Currency cannot change on edit, so `risk_currency` cannot go stale from a manual edit.
4. **Delete order and guard:** journal entry first, then fills, one transaction (RESTRICT FK stays), plus a stale guard for an entry changed after the confirm (AC 46-47).
5. **Double-submit guard.** Manual fills have no dedupe key, and content-based dedupe is wrong (AC 14). Answered (ADR-0008 §4): a signed token on the add form only; Close, Edit and Delete need none (AC 36).
6. **Opener stays the entry fill** so the trade id (opening fill pk) survives edit and close: ordering by `(executed_at, id)` with equal times (AC 8) and an exit time never before entry. ADR-0007 §2 says opener-shift orphans "neither exist today" for NULL-key fills; manual entry makes that live unless item 1 closes it.
7. Endpoint names and URL shapes for add, close, edit, delete (trade id = opening fill pk).

**`trading-domain-expert`:**
Items 1, 2, 4 and 5 are done in `pnl-and-matching.md` §1, §7 and vectors 24-34. Left:
1. ~~Fees on the exit fill~~ Done (§7.3). Edit of an open trade adds no exit; Close does (AC 29, 38).
2. ~~Time entry and DST, exit before entry~~ Done (§7.1-§7.2; AC 8, 21). The §7.2 edit rule (unchanged field not re-parsed) is in AC 21; the mechanism is for `architect`/`backend-engineer`.
3. **Multipliers:** only `CL` and `MCL` are verified, so futures traders on `ES`/`MNQ`/`GC` type the point value themselves. Which roots to verify next is the user's follow-up in `topstep-import.md` §7 (no values added until verified).
4. ~~Forex and crypto~~ Done (§7.6): units with point value 1. The USD-only caveat (USDJPY, EURGBP, USDT pairs, non-US equities) is the practical content of the non-USD deferral (AC 53, Out of scope).
5. ~~`pnl-and-matching.md` §1/§3 rewording and the two-open-longs vector~~ Done (§1, vectors 30-31).
6. **Non-USD (deferred, not for this slice):** the minor-unit table behind `journal/matching.py`'s 2 dp ceiling (JPY), to be done together with the row 45 ruling when non-USD manual entry is picked up (Out of scope).

**`ux-designer`** (writes `docs/design/manual-entry.md`; final copy for every error and flash above):
1. Form layout and field order; the symbol-to-point-value state (known read-only, unknown required, no-JS submit path); whether a "Now" shortcut exists; how the fixed USD currency is shown read-only; the USD-quoted-only hint near Symbol (AC 53); the DST and exit-before-entry messages (AC 8, 21; the draft `TIME_NOT_READABLE` must cover both the nonexistent and the ambiguous case).
2. "Add trade" placement on `/trades/` and in the empty state; post-save destination and flash.
3. "Close this trade": where it lives (row, journal page, or both), the close form.
4. Edit and delete flows (no locked fields, ADR-0008 §2) and a delete dialog that reads like the import delete dialog (ADR-0005, `design/journaling.md` §7).
5. Edit/Delete visible only on manual trades without creating a visual caste system in the list.
6. Mobile layout; copy tone (coach, no price comments).

## Suggested owner agents

`ux-designer` + `trading-domain-expert` (parallel) -> `architect` (ADR-0008, after both) -> `database-engineer` (only if ADR-0008 needs a column or constraint) -> `backend-engineer` (forms, service, views) -> `frontend-engineer` (templates; after backend, same files) -> `qa-engineer` (AC 1-53, vectors 1, T1, T2, 6-34) -> `security-reviewer` (new write endpoints, delete path, tenant checks on trade ids) before release.
