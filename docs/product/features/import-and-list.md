# Slice 1: sign up, import a Topstep CSV, see trades + 3 stats

Status: draft. First buildable slice of [`mvp.md`](mvp.md) (stories 1, 3, 6, cut down).
Depends on: [ADR-0002](../../adr/0002-stack-revised.md), [ADR-0003](../../adr/0003-data-model.md),
[ADR-0004](../../adr/0004-topstep-dedupe-and-pairing.md), [ADR-0005](../../adr/0005-batch-delete.md).
Formulas: [`pnl-and-matching.md`](../../domain/pnl-and-matching.md) §1-3, [`topstep-import.md`](../../domain/topstep-import.md).

## Problem

A trader cannot judge the app until their own real trades are in it. Today nothing exists between
"sign up" and "see my numbers". This slice is the shortest path to the moment a trader recognises their
own history on screen, with numbers they can check against TopstepX.

## User story

As a trader, I create an account, upload my TopstepX export, and see my trades with win rate, total P&L
and avg R, so I can check that the app matches my broker before I trust it with anything else.

## 1. Slice boundary

**IN**
- Signup, login, logout (email + password).
- Topstep upload flow, Account field, conflict banner, "uploaded before" hint, `/imports/` list and
  detail, delete import. **Already specified in [`import-account-label.md`](import-account-label.md)
  and ADR-0005. Not re-specified here.**
- Trades list (`/trades/`, the landing page after login) with sorting and three filters (date range,
  instrument, win/loss).
- Three stat cards: win rate, total P&L, avg R, each with sample size, recomputed over the filtered set.

**OUT (do not build in this slice)**
- Manual trade entry (story 2). Journal entries, notes, rule-followed flag (story 4). "My rules" (story 5).
- The rule-followed filter (nothing to filter on yet). Note column, "not journaled" column.
- Password reset, email verification, 2FA, social login.
- Per-account stats or filters, charts, CSV export, saved filters, pagination (see §2).
- Any behavioral insight, benchmark, or "good/bad" wording on a number.

Cuttable if the slice runs long: the three filters. Without them the cards are all-time and story 6's
filter criteria stay open. Sorting is not cuttable.

## 2. Trades list

Trades are derived (ADR-0003, ADR-0004 §3). Topstep: one CSV row = one trade.

**Columns (left to right)**

| Column | Content |
|---|---|
| Date | Entry time (`opened_at`) in the user's timezone, e.g. `2026-09-14 09:31`. Zone named once in the column header (e.g. "Date (UTC)"), per the design rule. |
| Instrument | `symbol` as stored, e.g. `CLZ6`. |
| Side | `Long` / `Short`. |
| Qty | Integer or decimal without trailing zeros. |
| Entry | Entry price. |
| Exit | Exit price, or blank for an open trade. |
| Net P&L | Net of fees and commissions, USD, explicit sign, 2 decimals (`+$291.96`, `-$103.00`, `$0.00`). Open trade: the word "Open". |
| Account | **Shown only if the user's trades have more than one distinct account label.** Blank label shows `—`. |

- Price display: at least 2 decimals, trailing zeros beyond that stripped. This never rounds a stored value.
- Net P&L is the stored, already-rounded trade value (`pnl-and-matching.md` §1). No rounding at display.
- No journal, rule-followed or note columns. They arrive with story 4.
- No colour-only signal: the sign is always printed.

**Sort**
- Default: Date, newest first. Tiebreak: opening execution id, descending, so order is stable.
- Sortable by Date, Net P&L, Instrument (click header, toggles asc/desc, server-rendered via GET params).
- Open trades sort last for Net P&L in either direction.
- Unknown or invalid sort params fall back to the default. Never a 500.

**Filters** (GET form, server-rendered, htmx partial swap optional; the URL is the state)
- Date range: from/to, inclusive, compared against the entry date in the user's timezone.
- Instrument: one select, options are the distinct symbols on the user's own trades only.
- Result: All / Wins / Losses. Wins = net P&L > 0, Losses = net P&L < 0. Breakeven trades and open trades
  appear only under All.
- Invalid values are ignored with a short inline note. Never a 500.
- "Clear filters" resets all three.

**Pagination: none.** One page, all trades. Why: the real export is 177 rows, and a few hundred trades
render in one server-side pass with no client state. Pagination adds page controls, sort-and-filter
state across pages, and stat-vs-page confusion for no gain at this size. Trigger to add it: a user
passes ~1,000 trades, or list render passes the §6 budget. (`mvp.md` story 6 says "pagination/requests",
so this is within it.)

**Empty states**
- No imports at all: no stat cards, no table. Heading "No trades yet". Body "Upload your TopstepX
  'Trades' export and your trades show up here." Primary button to the upload page.
- At least one import but zero trades (every row skipped or failed): same heading, body "Your last
  upload didn't add any trades." with a link to that import's detail page.
- Filters match nothing: table replaced by "No trades match these filters." with a "Clear filters" link.
  Stat cards still render, in their n=0 form (§3).

**Multi-account behaviour**
- Labels come from the optional Account field ([`import-account-label.md`](import-account-label.md)).
- The Account column appears only when the user has more than one distinct label (a blank label counts
  as one). Single-account traders never see it.
- Stats span all accounts combined. When the Account column is showing, the cards carry the line
  "Across all accounts." Per-account stats and an account filter are out of scope (same as the
  import spec).

## 3. Stat cards

Three cards above the table, always in this order. Each shows a label, a value string, and the sample
size in the same string. Population: the **currently filtered** trade set, **closed trades only**
(open trades are excluded from all three, `pnl-and-matching.md` §1).

| Card | Rule | Value string | Empty (n=0) |
|---|---|---|---|
| Win rate | wins / (wins + losses); breakeven (`net_pnl == 0`) excluded from numerator and denominator (§2). `n` = wins + losses. Rate quantized once to 2 dp at computation. | `58.33% (n=120)` | `n/a (n=0)` |
| Total P&L | Sum of stored `net_pnl` over closed trades, breakeven included (they add $0). USD, Decimal, 2 dp, explicit sign. `n` = closed trades. | `+$1,234.56 (n=177)`, `-$312.40 (n=177)` | `— (n=0)` |
| Avg R | Mean of per-trade R where R is not null (§3). 2 dp. `n` = trades with non-null R. | `1.40 (n=32)` | `— (n=0)` |

- Thousands separator on P&L. No float anywhere; values are `Decimal` end to end.
- Win rate card, when the filtered set has breakeven trades: second line "Excludes N breakeven
  trades." This explains why its `n` differs from Total P&L's `n`.
- No evaluative words on any card ("good", "poor", "only"), no benchmarks, no comparison to other
  traders. Numbers compare to the trader's own history only.

**Avg R in this slice.** No journal exists, so no trade has R inputs. Every user sees `— (n=0)` on
this card. It stays visible (it teaches what R is and is the target of story 4), and always carries
this help text underneath, in coach tone, with no date and no promise:

> "Avg R shows your results in units of what you risked on each trade. It needs a stop or a planned
> risk amount on the trade, which you'll be able to add once trade journaling is available. Trades
> without one are left out, never counted as zero."

- Once R inputs exist (later slice), the same card shows `1.40 (n=32)` and the help text is shown only
  when `n = 0`. Build the card for both states now so the later slice does not touch it.
- The card never shows `0`, `0.00` or `NaN` for "no data".

Trade-off, stated: a permanent placeholder card is mild clutter for this slice. Kept because `mvp.md`
lists three stats and because it tells the trader up front what R needs. Dropping it is a one-line
template change if the user prefers.

## 4. Auth (email + password)

- Signup form: email, password. **No confirm-password field** (friction). A show-password toggle is
  the typo guard (ux to confirm, open question 3).
- Email: unique case-insensitively (ADR-0003 `CITEXT`), trimmed, valid format.
- Password: Django's default validators (min length, common-password, not all numeric, not similar to
  email). Hashed with Django's default hasher. Never logged.
- Successful signup logs the user in and lands on `/trades/` (the empty state with the upload button).
- Login: email + password. Success goes to `?next=` if it is a safe same-host URL, else `/trades/`.
- Logout: POST only, then redirect to login.
- Anonymous requests to any page that shows user data (trades, imports, upload) redirect to login,
  keeping `?next=`.

**Error copy (coach tone, no blame)**
- Login, wrong password **or** unknown email **or** inactive account: one identical message and status,
  no field-level hint: "That email and password don't match. Check them and try again."
- Signup, invalid email: "Enter a valid email address."
- Signup, weak password: Django's validator messages, shown under the password field.
- Signup, email already registered: see open question 1. Wording until decided: "We couldn't create
  an account with that email. Try logging in, or use a different email."

## 5. Tenant isolation (mandatory test, blocks merge)

One automated test, two users, A has imported a real-shaped file, B is logged in:

- B's `/trades/` shows the empty state, and B's stat cards (with filters cleared and set to any of A's
  symbols) never include A's rows.
- B's instrument filter and Account suggestions (datalist) contain none of A's symbols or labels.
- B requesting A's `/imports/<id>/` gets 404, the same response as a non-existent id.
- B posting delete on A's import id gets 404 and A's rows are unchanged (ADR-0005 test 5).
- B uploading the same file A did imports every row as `imported`, never `skipped_duplicate` or
  `skipped_conflict`, and the "uploaded before" hint does not appear or link to A's batch.
- Every list or count on the page is built from the user-scoped manager (`for_user`), per `CLAUDE.md`.

## 6. Non-functional

- Fixture: a **synthetic** 177-row Topstep file, not the real export (real user data is not committed,
  `topstep-import.md`). Include nested same-direction rows (T4 shape), a short, both roots (`CL`, `MCL`),
  one breakeven and, if the importer allows it, one failed row.
- With 177 trades, `/trades/` (list + three cards) renders in under 1 second end to end on the dev
  setup; the server-side share is expected to be a small fraction of that.
- Query count is constant, not per row (no N+1). QA asserts a fixed maximum with
  `django_assert_max_num_queries`; backend-engineer picks the number.
- Same test at ~1,000 synthetic trades still renders under 1 second. If not, that is the trigger to add
  pagination or move the aggregation to SQL, and it is reported, not silently accepted.
- Stat values are checked against hand-computed results from the reference vectors (`pnl-and-matching.md`
  §5 vectors 1, 5 and `topstep-import.md` T1, T2, T4), not only against the code's own output.

## Acceptance criteria

**Auth**
1. Given a visitor, when they sign up with a new valid email and an acceptable password, then an
   account exists, they are logged in, and they land on `/trades/`.
2. Given an existing email in a different case, when someone signs up with it, then no second account
   is created and the §4 copy is shown.
3. Given a wrong password and given an unknown email, when each logs in, then the response body, status
   and message are identical.
4. Given a logged-in user, when they POST logout, then the session ends; GET on the logout URL does not
   log them out.
5. Given an anonymous request to `/trades/`, `/imports/` or the upload page, then it redirects to login
   with `next` set; and given `next` points to another host, then login ignores it and goes to `/trades/`.
6. Given the database, then no password is stored in plaintext and none appears in logs.

**Import into list**
7. Given a new user with no imports, when they open `/trades/`, then they see the "No trades yet" state
   and the upload button, and no stat cards.
8. Given a valid Topstep file, when the import completes, then the summary offers a link to `/trades/`
   and the trades appear there (upload behaviour: `import-account-label.md`).
9. Given the T4 nested rows, when imported, then the list shows 2 trades, not 1 (ADR-0004).
10. Given a file whose rows were all skipped or failed, when the user opens `/trades/`, then they see
    "Your last upload didn't add any trades." with a link to that import.

**List**
11. Given imported trades, then each row shows the §2 columns, Net P&L is `PnL - Fees - Commissions`
    (T1: `+$291.96`), and times are in the user's timezone with the zone named in the header.
12. Given no sort param, then trades are ordered newest entry first; given sort by Net P&L, then order
    follows P&L, with open trades last in either direction.
13. Given a garbage sort or filter param, then the page renders with defaults and no error.
14. Given filters (date range, instrument, result), then rows and all three cards reflect only the
    matching trades; Wins/Losses exclude breakeven and open trades.
15. Given filters that match nothing, then the "No trades match these filters." state and the n=0 cards
    show.
16. Given one account label (or none), then there is no Account column; given two or more distinct
    labels, then the column shows and the cards carry "Across all accounts."

**Stat cards**
17. Given net P&L `+50, -30, 0, +10, -10, 0, +5` (vector 5), then win rate shows `60.00% (n=5)`, Total
    P&L shows `+$25.00 (n=7)`, and the win-rate card shows "Excludes 2 breakeven trades."
18. Given only breakeven trades, then win rate shows `n/a (n=0)`, never `0%` and never an error.
19. Given no closed trades in the filtered set, then Total P&L shows `— (n=0)` and Avg R shows `— (n=0)`.
20. Given open trades, then they appear in the list as "Open" and are excluded from all three cards.
21. Given any user in this slice, then Avg R shows `— (n=0)` with the help text from §3 underneath, and
    the text has no date, no "coming soon", and no advice.
22. Given the card strings, then none contain "good", "bad", "poor", "should", or a comparison to other
    traders.
23. Given a negative total, then the string is `-$312.40` (sign printed) and the amount is a `Decimal`
    computed without float at any step.

**Isolation and performance**
24. The §5 isolation test exists, is in CI, and passes.
25. Given the §6 fixture, then the list plus cards meet the §6 time and query-count budgets.

## Out of scope

See §1. Also: editing or deleting a single trade, column chooser, row detail page, timezone settings
page, account/profile page, "remember me", login throttling UI.

## Open questions

Owners in brackets.

1. **Signup cannot be fully non-leaking without email verification.** Login can and does hide whether
   an email exists (AC 3). Signup has to tell the visitor the email is taken, or it silently fails. Hiding
   it properly needs a "you already have an account" email, which needs email sending, which mvp.md puts
   out of scope. Recommendation: accept signup enumeration for now, keep login non-leaking, revisit when
   email exists. Needs the user's call. [orchestrator to ask user; security-reviewer to confirm risk]
2. **How does `user.timezone` get set?** It defaults to `UTC`, and this slice has no settings page. A
   trader in another zone sees UTC times until it is set. Options: hidden field filled from the browser at
   signup with fallback UTC (no extra field), or a later settings page. Recommendation: browser-detected
   hidden field. [architect / ux-designer]
3. **Show-password toggle vs confirm field**, given there is no password reset. A typo at signup locks the
   user out with no self-service recovery. Recommendation: toggle, plus prioritising password reset before
   any non-author user. [ux-designer; user for the reset priority]
4. **Instrument filter granularity:** full contract (`CLZ6`) or root (`CL`)? Spec uses the stored
   symbol because nothing else is defined. [trading-domain-expert]
5. **Small-sample hint on cards** (e.g. a note when n is low). Sample size is shown, but no threshold is
   defined, so none is specced. [behavior-analyst]
6. **Login throttling / lockout.** Not specced here. [security-reviewer]
7. **Trade date basis:** list uses entry time. A trade entered late evening can belong to the next
   `TradeDay` (`topstep-import.md` §1). Fine while there is no day-based grouping. Revisit if a day view
   is added. [trading-domain-expert]
8. **Design gap:** signup, login, trades list, stat cards have no wireframes (docs index: "Not designed
   yet"). This spec sets content and copy; layout is open. [ux-designer]

## Suggested owner agents

`ux-designer` (signup/login, list, cards; `docs/design/`), `architect` (timezone source, sort/filter over
derived trades, query budget), `backend-engineer` (auth views, list view, stats service),
`frontend-engineer` (templates, cards, sort/filter form), `qa-engineer` (AC 1-25, synthetic fixture,
isolation test), `security-reviewer` (auth, `next` redirect, isolation, before any second user).
