# Journaling: per-trade entry, "My rules", and Avg R

- **Status:** Ready to build (owner decisions recorded 2026-09-30; UX doc `docs/design/journaling.md` is the single source for layout and copy; see "Open, routed")
- **Owner:** `product-manager`
- **Covers:** `mvp.md` story 4 (journal entry per trade) and story 5 ("My rules" free text). Also lights up the Avg R stat card from story 6, because R inputs are typed on the journal entry.
- **Depends on:** ADR-0003 §1 and §6 (`User.trading_rules`, `JournalEntry`), ADR-0005 (delete dialog), ADR-0006 (forms), `docs/domain/pnl-and-matching.md` §3 (R rules, vectors 6-23), `docs/design/journaling.md` (layout and copy, single source), `docs/design/auth-and-trades-list.md` (list, stat cards; partly superseded by the journaling design, its §9)
- **Already built (do not recreate):** `JournalEntry` model with `opening_execution` (1:1, RESTRICT), `note`, `rules_followed` (nullable), `stop_price`, `planned_risk_amount`, `risk_currency`, both CHECK constraints. `User.trading_rules` column. `/trades/` list with 3 stat cards (Avg R stubbed at `— (n=0)` in `journal/stats.py`). Import delete dialog that counts and lists journal entries.

## Problem

P&L says what happened, not why. The trader has to write down their reasoning and say whether they kept their own rules, at the moment it is cheapest to do so: right after looking at the trade. If that takes more than a few seconds, they stop doing it.

## Slice in one line

From a row on `/trades/`, open a small journal form for that trade. Nothing is required except that "did you follow your rules" is only counted once answered. Optional stop or planned risk feeds Avg R. One free-text "My rules" block sits on the same screen.

## User stories

1. **Journal a trade.** As a trader, I open any trade from my list and write a note and answer "Did you follow your rules?" so I can review my own thinking later.
2. **Add risk for R.** As a trader, I can optionally enter a stop or a planned risk amount on that same form so the Avg R card can show my results in units of what I risked.
3. **Keep my rules in view.** As a trader, I keep one free-text "My rules" block that I can read and edit right where I answer the yes/no question.
4. **Edit later.** As a trader, I can reopen a trade and change anything I wrote.

## Design decisions

### Where journaling starts

- Each `/trades/` row gets one journal control: a new last "Journal" column in the table and a full-width link on each mobile card (`docs/design/journaling.md` §5). Its label is the state: "Add journal" (no entry), "Followed rules" / "Didn't follow rules" (answered), or, when `rules_followed` is unanswered, "Note only" / "Risk only" / "Note and risk". Rows and cards carry `id="trade-<id>"` so "Back to trades" returns to the same row.
- The control opens `/trades/<opening_execution_id>/journal/` (the trade id is the opening execution's pk, ADR-0003 §6). This is also the target follow-ups row 32 is waiting for (the delete dialog's `entry.url`).
- One form does both create and edit. There is no separate "new" and "edit" screen. GET shows current values or blanks. POST creates the entry if none exists, else updates it.
- One entry per opening execution, enforced by the existing UNIQUE. Two tabs saving at once: the second save updates the first row (get-or-create semantics), never a 500.
- v1 is a full page that works without JS. An inline htmx panel under the row is the friction-reducing follow-on (decision 1, separate PR) and must not change the URL or the fields.

### Fields

| Field | Required to save? | Notes |
|---|---|---|
| Rules followed (Yes / No radios) | No | No default selected. Required only to count as journaled (`rules_followed IS NOT NULL`, ADR-0003 §6). Once answered it can be changed Yes<->No, not returned to unanswered. |
| Note | No | Plain text, trimmed on save, max 10,000 characters. `\r\n` is normalised to `\n` before the length check (so the browser counter and the server agree); length is counted in Unicode code points. Same rule for the rules block. |
| Stop price | No | Optional R input. |
| Planned risk amount | No | Optional R input. Wins over stop for R (domain §3). |
| Risk currency | Not typed | Not shown as an input. Set to the trade's currency whenever planned risk is set, cleared when it is blank. Shown as a fixed ISO-code suffix inside the amount input ("USD"), per the UX ruling. |

Why nothing is required: a note saved mid-trade must not be blocked by a question the trader cannot answer yet. Cost: some entries will sit unanswered. The list state label makes that visible without nagging.

Saving a form where every field is blank creates no row (no empty entries in the delete dialog counts). Blanking every field on an existing entry keeps the row and shows it as unanswered; deleting an entry is out of scope.

### Risk inputs: validation (form level, per domain §3)

- Planned risk must be greater than 0: "Planned risk needs to be more than 0."
- Stop on a single-entry trade (one entry lot, any number of partial exits) must satisfy `(entry_price - stop_price) * side_sign > 0`, i.e. long: below entry, short: above, equal rejected. Violation message: "Your stop should sit on the loss side of your entry price." (domain §3 code `stop_not_a_risk`; vector 18 for the single-entry shape, vector 14 for the violation). The check is skipped when planned risk is set on the same save.
- Stop on a multi-leg trade (two or more entry lots): ruled by `trading-domain-expert` 2026-09-30. The side-check does not apply. The stop is accepted and stored unchecked, with the inline hint "Enter planned risk to get R on this trade." R is `stop_price_multi_leg` whenever no usable planned risk exists, even if the stop looks like it sits on the profit side (vector 19). `stop_not_a_risk` is never returned for a multi-leg trade.
- Both set: both saved. The stop is inert (never checked, never used for R) while `planned_risk_amount` is set (vector 9, vector 20). A hint under the fields: "Planned risk is used for R when both are set."
- Currency is derived, not typed (vector 21): on save, `risk_currency` = the trade's currency when planned risk is set, cleared when the amount is blank.
- If an existing value later becomes unusable (for example a re-import changes the trade's currency, vector 22), the trade is left out of Avg R with reason `risk_currency_mismatch` and the form shows the reason in plain words. The form never silently switches to the stop.
- Open trade: journal and risk inputs are allowed (a trader can journal at entry). R stays null (`trade_open`) until the trade closes.

### "My rules" and how it relates to `rules_followed`

- One free-text block, stored in `User.trading_rules`. No structure, no titles, no list of rules, no versions.
- Shown on the journal form as a collapsible panel above the Yes/No question, labelled "My rules". **Collapsed by default, always** (owner decision, UX ruling). The closed panel previews the first 3 lines of the rules, or the prompt when there are none. It opens only right after a rules save or a rules error. (This replaces the earlier "open until first save" recommendation.)
- Edited in place on the same screen via a small separate form (`POST /rules/`, updates `request.user` only). There is no settings page in this slice. Saving rules does not touch any journal entry.
- Relationship to the flag: self-report only. `rules_followed` is the trader's Yes/No against whatever the block said when they answered. There is no link to a named rule, no check that the trade matches the text, and no snapshot of the text. Editing the rules never changes past answers (already true: the flag is stored on the entry).
- Honest limit to state in the UI help text and the spec: because the text is not versioned, a past "Yes" was judged against an earlier wording the app no longer has.
- Empty rules block: the Yes/No question still works. The closed panel shows the prompt "Add your rules so you can check each trade against them." No blocking.
- Max length 10,000 characters.

### How Avg R lights up

Compute-on-read, per currency group (existing `journal/stats.py` grouping), following `docs/domain/pnl-and-matching.md` §3 and vectors 6-23 exactly. This spec adds no formula.

- For each closed trade in the currency group, look up its journal entry by `opening_execution_id` and derive R with the domain precedence (planned risk wins, no fallback when planned risk is unusable).
- Per-trade R: quantize once to 4 dp (`ROUND_HALF_EVEN`). Avg R: mean of those stored 4 dp values, quantized once to 2 dp with `ROUND_HALF_EVEN` (exact ties go to the even digit; vector 23 gives 0.1250 -> 0.12, and `ROUND_HALF_UP` would fail it). Never average display values.
- `n` = closed trades in the currency group with non-null R. A trade with no usable risk is never counted as 0.
- `Left out: N` = closed trades in the same group with null R for any reason (`no_risk_input`, `risk_not_positive`, `risk_currency_mismatch`, `stop_not_a_risk`, `stop_price_multi_leg`). So `n + N` = the group's closed trades. Open trades (`trade_open`) are in neither `n` nor N.
- Card value: `1.40 (n=32)` (mvp.md story 6). With n=0 it stays `— (n=0)`. Never `0.00 (n=0)`.
- Exclusion transparency: below the value, one quiet line "Left out: N closed trades without usable risk" (singular "1 closed trade"), shown whenever N > 0 including when n = 0. N only, no per-reason breakdown on the list; the reason for each trade is on that trade's journal page (`docs/design/journaling.md` §3.6, §4.5).
- Help text on the card is replaced by the new wording in `docs/design/journaling.md` §6 (old "...once trade journaling is available" wording goes). Show help only when n=0.
- The card follows the same trade set as the other cards (all trades, no filters in this slice).
- Tone: neutral. No grading adjectives, no color by threshold, no comparison to anyone else.

## Acceptance criteria

Tests use the domain vectors 6-23 in `pnl-and-matching.md` where noted; do not invent new formulas.

### Entry point and create/edit
1. Given a logged-in user with a trade in `/trades/`, when the page loads, then that row has a journal control labelled "Add journal" and it links to `/trades/<opening_execution_id>/journal/`.
2. Given no entry exists, when the user opens the link, then the form shows an empty note, neither Yes nor No selected, empty stop and planned risk, and the My rules panel.
3. Given the user submits only a note, when the form saves, then one `JournalEntry` exists with that note, `rules_followed IS NULL`, the user stays on the journal page (302 to the same URL) with the flash "Journal saved.", and the list row shows "Note only". Submitting only a risk value shows "Risk only"; a note plus a risk value with no answer shows "Note and risk".
4. Given the user submits Yes, when the form saves, then `rules_followed = True` and the list row shows "Followed rules". Submitting No gives `False` and "Didn't follow rules". Both answered labels use the same neutral styling (no color difference).
5. Given the user submits every field blank on a trade with no entry, when the form saves, then no `JournalEntry` row is created, the page re-renders (200) with the notice "Nothing to save yet. Add a note, an answer, or a risk amount.", and nothing changes.
5a. Given a successful save, when the page reloads, then the form shows the saved values and a bottom action "Back to trades" that links to `/trades/#trade-<id>` (no "Cancel").
6. Given an entry exists, when the user reopens the form, then all saved values are prefilled, and saving changes updates the same row (still exactly one entry for that opening execution).
7. Given an entry with `rules_followed = True`, when the user edits only the note, then `rules_followed` stays True.
8. Given two tabs open on the same trade with no entry, when both save, then one row exists and neither request returns a 500.
9. Given a note with only whitespace, when saved, then it is stored as an empty string.
10. Given a note over 10,000 characters after `\r\n` is normalised to `\n` and the text is trimmed, when submitted, then the form re-renders with a field error and nothing is saved. Given a note of exactly 10,000 characters using Windows line endings that is 10,000 or fewer after normalisation, then it saves.

### Tenant and identity
11. Given user B, when they request user A's `/trades/<id>/journal/` (GET or POST), then the response is 404 identical to a missing id, and no row changes.
12. Given an execution id that is not the opening execution of any derived trade (a closing fill), when requested, then 404.
13. Given the POST tries to name any other `opening_execution` in the body, then the URL's trade is used and the body value is ignored (the form has no execution field).
14. The isolation test still enumerates `JournalEntry`; the form is a `UserScopedModelForm` and the view catches `CrossTenantForeignKeyError` with a generic form error and a log line with user id and model name only (follow-ups row 26).
14a. Backend requirement: on a database error or a cross-tenant error during save, the view re-renders the form (status 200) with the submitted values kept and the notice "We couldn't save that just now. Your text is still here, so try again in a moment." There is never a bare 500 on this path. The same holds for `POST /rules/` (rules text kept, its own notice). Test: force a DB error on save and assert 200, the notice, and the submitted note in the response.

### Risk inputs and R
15. Given planned risk of 0 or negative, when saved, then the form rejects it with the message above and saves nothing (reason code `risk_not_positive` in stats if such a value ever exists; never `planned_risk_not_positive`).
16. Given a single-entry long trade at 50.00, no planned risk, and stop 50.00 or above, when saved, then the stop is rejected with the loss-side message (vector 14). Given a short at 50.00 and stop 49.50, same. Given the same stop with planned risk also set, then it saves (stop inert, vector 9). Given a single-entry trade with partial exits, then the check uses the one entry lot's price (vector 18).
17. Given planned risk 59.00 on a USD trade, when saved, then `risk_currency = 'USD'`; given 40.00 on a EUR trade, then `'EUR'` (vector 21). Given planned risk is cleared, then `risk_currency` is cleared too and the CHECK constraints hold. The form has no currency input.
18. Given a multi-leg trade with only a stop, when saved, then it saves unchecked (also when the stop is on the profit side of the entries, vector 19), the hint "Enter planned risk to get R on this trade." shows, and the trade is left out of Avg R with `stop_price_multi_leg` (vector 10a). Given the same multi-leg trade with planned risk also set, then R is computed from planned risk (vectors 10b, 20).
19. Given vector 6 (stop 49.50, single entry), then that trade's R is 2.3600. Given vectors 7 to 16, 18, and 20, then R and reason codes match the vectors exactly, and the stats code reuses those vectors as tests.
20. Given trades with R values [2.3600, none, -1.0400, 2.0000], when `/trades/` loads, then the Avg R card reads `1.11 (n=3)` (vector 17) and "Left out: 1 closed trade without usable risk".
21. Given one currency group with 4 closed trades (two with R 0.2000 and 0.0500, two with no risk input), then the card reads `0.12 (n=2)` and "Left out: 2 closed trades without usable risk" (vector 23; a HALF_UP implementation shows 0.13 and fails). Given an added open trade with planned risk, then nothing changes: open trades are in neither `n` nor N.
22. Given no trade has a usable risk input, then the card reads `— (n=0)` and never `0.00`.
23. Given a trade whose stored `risk_currency` no longer matches the trade currency (vector 22), when stats compute, then it is left out with `risk_currency_mismatch` and the stop is not used as a fallback (vector 11).
24. Given an open trade with a stop or planned risk entered, then it does not count in `n` or N, and after it closes the same entry counts without re-entry.
25. Given trades in two currencies, then Avg R, n, and N are computed per currency group, with no mixing, and `n + N` equals each group's closed trades.

### My rules
26. Given any user, when they open a journal form, then the My rules panel is collapsed. With no rules it previews the prompt (summary action "Add"); with rules it previews the first 3 lines (summary action "Edit"). The Yes/No question is usable either way. Given a rules save or a rules error, then the panel renders open.
27. Given the user saves rules text, then `User.trading_rules` holds it, it appears in the panel on every trade's journal form, and no `JournalEntry` row changes (compare `updated_at` and `rules_followed` before and after).
28. Given rules text over 10,000 characters, then rejected with a field error.
29. Given user B, then `POST /rules/` only ever changes B's own row (no user id in the request).
30. Given the user answers Yes on trade 1, then edits the rules, then answers No on trade 2, then trade 1 still reads Yes.

### List state and existing flows
31. Given entries in each state (none, note only, risk only, note and risk, Yes, No), then the new "Journal" column (table) and the last line of each card show, respectively, "Add journal", "Note only", "Risk only", "Note and risk", "Followed rules", "Didn't follow rules". The link's accessible name adds " for {symbol}, opened {opened}", it is keyboard reachable, and the state is never shown by color alone. Rows and cards carry `id="trade-<id>"`.
32. Given a batch with a journal entry that has only stop and risk (no note, no answer), when the import delete dialog opens, then that entry counts in N, does not count in M, and appears in the list with "Rules: not answered", "(no written note)", and the muted line "Has a stop or planned risk amount." (never a blank or an error). The dialog body sentence also mentions "any stop or planned risk" (`docs/design/journaling.md` §7); the checkbox text is unchanged.
33. Given the trade page now exists, then each dialog list row's "View trade" link points to `/trades/<id>/journal/` (new tab, `rel="noopener"`) and `DELETE_LIST_HELPER` uses the UX wording (follow-ups row 32; `docs/design/journaling.md` §7).
34. Given a form with no changes saved, then the delete dialog counts are unchanged (no row is created by viewing).

## Effects on existing flows

- **Import delete (ADR-0005):** entries are counted as `journal_count` if a row exists, even risk-only. `noted_count` uses non-whitespace notes. Because this spec trims notes and never creates all-blank rows, the counts match what the user typed. The list shows the full note, the answer (now including "not answered"), and a working link. The body sentence mentions "any stop or planned risk" and risk-bearing rows get a muted "Has a stop or planned risk amount." line; the checkbox text is unchanged (UX doc §7). The signed max-pk guard needs no change. Note: the delete dialog's existing "journal_list" and its template comment that `entry.url` is always None become stale.
- **`/trades/` list:** adds the "Journal" column / card link and state labels (`docs/design/journaling.md` §5). This changes `auth-and-trades-list.md` §5.1, whose "trade rows are not links" and column list are now stale (UX doc §9 items 1-2; not edited here). It does not add rule-followed or note columns, filters, or sorting on them (story 6, next slice).
- **Stats:** `compute_stats` currently takes trades only. It needs the journal entries (one scoped query by `opening_execution_id`, ordered read as in follow-ups row 27). Design section 6.3 help text needs replacing.
- **Orphaned entries** (ADR-0003 negative consequence): a re-import that shifts a trade's opener would orphan its entry. This slice does not detect or surface them.
- **Copy/tone:** coach tone. The form's radio labels are "Followed my rules" / "Didn't follow my rules"; list labels are "Followed rules" / "Didn't follow rules". No praise or scolding, no advice. All strings come from `docs/design/journaling.md` §12.

## Empty and edge states

- No trades: unchanged (design 5 empty state). No journal control.
- Trade with no entry: "Add journal".
- Entry with only risk inputs: "Risk only". Note and risk, no answer: "Note and risk".
- Rules block empty: the closed-panel prompt (above). Rules block long: closed preview clamps at 3 lines, the open textarea scrolls, the form is not pushed down.
- Validation errors: the error summary pattern from design 4.3 (page re-renders, status 200), values kept.
- Save failure: "We couldn't save that just now. Your text is still here, so try again in a moment." (final, UX doc §1). Implies criterion 14a: re-render with submitted values, no bare 500.
- Windows line endings in pasted text: normalised before the length check (criterion 10).
- Trade gone (batch deleted between load and save): 404 page.

## Out of scope

- Manual trade entry, behavior detectors, filters, pagination, list columns for note/rule-followed, sort on journal fields.
- Emotion, mood, tilt, tags, screenshots, attachments.
- Structured or named rules, rule history/versioning, per-trade rule linkage, checking a trade against the rules text, AI or automated review of notes.
- Deleting or clearing an entry with one action, note export, autosave drafts.
- Detecting or repairing orphaned entries.
- Password reset etc. (unchanged).
- Suggesting the last-used planned risk (owner decision 5: not now). Stays in `plan.md` under Later; if added, it must be a one-tap suggestion beside the field, never a prefilled value, and only when the currency matches.
- Any financial advice, signals, or mental-health wording.

## Open questions and routing

- `trading-domain-expert`: all three earlier questions are ruled (2026-09-30, `pnl-and-matching.md` §3, vectors 18-23). (a) The stop side-check applies to single-entry trades only; multi-leg with a stop always gives `stop_price_multi_leg`. (b) `risk_currency` is derived from the trade currency on save and cleared when the amount is blank; a later mismatch gives `risk_currency_mismatch` with no fallback to the stop. (c) The reason code is `risk_not_positive`; `planned_risk_not_positive` in `docs/data/follow-ups.md` row 14 is a typo (that file needs a one-word fix by its owner, the orchestrator; not edited here).
- `ux-designer`: form layout (page and inline), state labels, My rules panel, "Left out" copy, new help text for the Avg R card, delete dialog copy tweaks.
- `architect`/`database-engineer`: none expected. Optional: the `planned_risk_amount > 0` CHECK (follow-ups row 14).

## Suggested owner agents

`ux-designer` (form, labels, copy) -> `backend-engineer` (view, `UserScopedModelForm`, stats join, `/rules/`) and `frontend-engineer` (templates, list control, htmx follow-on) -> `qa-engineer` (criteria 1-34 including 5a and 14a, and vectors 6-23) -> `security-reviewer` (new write endpoints, tenant checks on the trade id) before release.

## Decisions (owner, 2026-09-30)

1. Full page at `/trades/<opening_execution_id>/journal/` first; the inline htmx panel is a later, separate PR.
2. Yes/No is not required to save. Journaled = `rules_followed` answered.
3. "My rules" lives only as a collapsible panel on the journal form with in-place edit (`POST /rules/`). No settings page.
4. `risk_currency` is derived from the trade, not asked for.
5. No "use last planned risk" suggestion in this slice.
6. 10,000-character cap on note and on rules.
7. "My rules" panel is collapsed by default, always (previews the first 3 lines or the prompt; opens only after a rules save or error). Replaces the earlier "open until first save" recommendation (owner: "both recommended", UX ruling).
8. After saving a journal entry the user stays on the journal page with the flash "Journal saved."; the bottom action is "Back to trades" (not "Cancel").

## Open, routed

- Resolved 2026-09-30 by `trading-domain-expert`: multi-leg stop side-check (none, stored unchecked), reason-code name, currency derivation.
- Resolved 2026-09-30 by `ux-designer` (`docs/design/journaling.md`): risk-currency suffix (ISO code inside the input), rules panel default (collapsed), "Left out: N" (count only), save-failure copy, delete-dialog stop/risk mention (in the body sentence and a per-row line, not the checkbox).
- `backend-engineer` (confirm): re-render with submitted values on DB and cross-tenant errors (criterion 14a); R reason for the status line comes from the same function as the stats; `\r\n` -> `\n` before the length check.
- `frontend-engineer` (confirm): Alpine CSP `charCounter` and `details[open] + .rules-preview` CSS need no new dependency.
- No open [GUESS] items remain in this spec.
