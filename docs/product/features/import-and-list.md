# Slice 1: sign up, import a Topstep CSV, see trades + 3 stats

**Layout and copy: `docs/design/auth-and-trades-list.md` is the source of truth; if this spec and the design disagree, the design wins for layout/copy and this spec wins for behavior.** (User ruling 2026-09-26.) Stat card strings and the empty-state text are kept inline here and are identical in both docs.

Status: final for build (user rulings of 2026-09-26 applied) (filters out, no pagination, PM card strings, visible time zone field, confirm-password, signup email leak accepted, login throttling deferred). First buildable slice of [`mvp.md`](mvp.md) (stories 1, 3, 6, cut down).
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
- Trades list (`/trades/`, the landing page after login) with sorting. No filters (see below).
- Three stat cards: win rate, total P&L, avg R, each with sample size, over all of the user's closed
  trades.

**OUT (do not build in this slice)**
- Manual trade entry (story 2). Journal entries, notes, rule-followed flag (story 4). "My rules" (story 5).
- All list filters (user ruling 2026-09-26). Note column, "not journaled" column.
- Password reset, email verification, 2FA, social login, login throttling (follow-ups row 18).
- Per-account stats, charts, CSV export, saved filters, pagination (see §2).
- Any behavioral insight, benchmark, or "good/bad" wording on a number.

**Deferred to the next slice: `mvp.md` story 6's filter criteria, explicitly not met by slice 1.**
Story 6 stays open on these until then:
- Filterable by date range.
- Filterable by instrument.
- Filterable by rule-followed (yes / no / not journaled). Also needs journaling (story 4).
- Filterable by win/loss.
- Stat cards recomputed over the *currently filtered* set. In slice 1 the cards cover all closed trades.

Sorting (date, Net P&L, instrument) stays in slice 1.

## 2. Trades list

Trades are derived (ADR-0003, ADR-0004 §3). Topstep: one CSV row = one trade.

**Columns, headers, date format, blank-label display, Duration/Result, mobile layout:** defined only in
[`auth-and-trades-list.md`](../../design/auth-and-trades-list.md) §5.1 (desktop column spec), §5.2
(mobile) and §5.3 (account label). Not repeated here. Domain rules this spec keeps:

- Row = one derived trade. Its time is the entry time (`opened_at`), shown in the user's timezone
  (`user.timezone`). Stored in UTC.
- Net P&L is the stored, already-rounded trade value, net of fees and commissions
  (`pnl-and-matching.md` §1). Same formatter as the Total P&L card. No rounding at display.
- Price and quantity display never round a stored value; display is formatting only.
- An open trade has no exit and no P&L number; how it is shown is in the design doc §5.1.
- Account label = the opening execution's `broker_account_label`. The Account column appears only under
  the rule in "Multi-account behaviour" below.
- No journal, rule-followed or note columns. They arrive with story 4.

**Sort** (behavior; header markup and URL shape are in design §5.1)
- Default: opened time, newest first. Tiebreak: opening execution id, descending, so order is stable.
- Sortable by opened time, Net P&L, symbol (click header toggles asc/desc, server-rendered via GET params).
- Open trades sort last for Net P&L in either direction.
- Unknown or invalid sort params fall back to the default. Never a 500.

**Filters: none in this slice** (moved to the next slice, see §1). The list always shows all the
user's trades, and the cards cover all of them.

**Pagination: none.** One page, all trades. Why: the real export is 177 rows, and a few hundred trades
render in one server-side pass with no client state. Pagination adds page controls, sort
state across pages, and stat-vs-page confusion for no gain at this size. Trigger to add it: a user
passes ~1,000 trades, or list render passes the §6 budget. (`mvp.md` story 6 says "pagination/requests",
so this is within it.)

**Empty states**
- No imports at all: no stat cards, no table. Heading "No trades yet". Body "Upload your TopstepX
  'Trades' export and your trades show up here." Primary button to the upload page.
- At least one import but zero trades (every row skipped or failed): same heading, body "Your last
  upload didn't add any trades." with a link to that import's detail page.
- Trades exist but none are closed (only open trades): the table shows them, the cards render in their
  n=0 form (§3).

**Multi-account behaviour**
- Labels come from the optional Account field ([`import-account-label.md`](import-account-label.md)).
- The Account column appears only when the user has more than one distinct label (a blank label counts
  as one). Single-account traders never see it. How a blank label is displayed: design §5.3.
- Stats span all accounts combined. When the Account column is showing, the cards carry the line
  "Across all accounts." Per-account stats and an account filter are out of scope (same as the
  import spec).

## 3. Stat cards

Three cards above the table, always in this order. Each shows a label, a value string, and the sample
size in the same string. Population: **all of the user's closed trades** (no filters exist in this
slice; open trades are excluded from all three, `pnl-and-matching.md` §1).

| Card | Rule | Value string | Empty (n=0) |
|---|---|---|---|
| Win rate | wins / (wins + losses); breakeven (`net_pnl == 0`) excluded from numerator and denominator (§2). `n` = wins + losses. Rate quantized once to 2 dp at computation. | `58.33% (n=120)` | `n/a (n=0)` |
| Total P&L | Sum of stored `net_pnl` over closed trades, breakeven included (they add $0). USD, Decimal, 2 dp, explicit sign. `n` = closed trades. | `+$1,234.56 (n=177)`, `-$312.40 (n=177)` | `— (n=0)` |
| Avg R | Mean of per-trade R where R is not null (§3). 2 dp. `n` = trades with non-null R. | `1.40 (n=32)` | `— (n=0)` |

- Thousands separator on P&L. No float anywhere; values are `Decimal` end to end.
- Win rate card, when the user has breakeven trades: second line "Excludes N breakeven
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

- Signup form: email, password, **confirm password**, time zone. Confirm-password is the typo guard
  (no password reset exists). **No show-password toggle** (user ruling 2026-09-26). Login has one
  password field.
- Email: unique case-insensitively (ADR-0003 `CITEXT`), trimmed, valid format.
- Password: Django's default validators (min length, common-password, not all numeric, not similar to
  email). Password and confirm must match exactly. Hashed with Django's default hasher. Never logged.
- **Time zone: a visible field on the signup form**, prefilled with a browser-detected guess
  (`Intl.DateTimeFormat().resolvedOptions().timeZone`), falling back to `UTC` without JS. The user can
  change it at signup. Required; must be a valid IANA zone name (validated server-side); saved to
  `user.timezone`. Not a hidden field. There is no settings page in this slice, so a wrong zone cannot be
  fixed later (see out of scope).
- Successful signup logs the user in and lands on `/trades/` (the empty state with the upload button).
- Login: email + password. Success goes to `?next=` if it is a safe same-host URL, else `/trades/`.
- Logout: POST only, then redirect to login.
- Anonymous requests to any page that shows user data (trades, imports, upload) redirect to login,
  keeping `?next=`.

**Error copy and placement:** all strings live in the design doc (signup: §4.1 "Wording" table; login:
§4.2 table; form error presentation: §4.3). Not repeated here. Behavior this spec requires:
- Login, wrong password **or** unknown email **or** inactive account: one identical message and status,
  no field-level hint (design §4.2 "Bad credentials").
- Signup, invalid email, weak password (Django validator failure), passwords differ, unknown time zone:
  each shows its own message from design §4.1, next to its field.
- Signup, email already registered (any letter case): no account is created, and **one generic
  form-level message** is shown (design §4.1 "Email cannot be used"), not attached to the Email field
  and not saying "already registered", "taken" or "in use".

**Accepted risk (user ruling 2026-09-26):** signup reveals whether an email is registered, because a
successful signup and the duplicate-email message differ. Login stays non-leaking (AC 3). Fixing signup needs email
verification, which is out of scope. Logged as `docs/data/follow-ups.md` row 17, trigger: before signup
is opened to strangers.

**Login throttling** is deferred to before any non-author account exists (`follow-ups.md` row 18). Not
built or specced in this slice.

## 5. Tenant isolation (mandatory test, blocks merge)

One automated test, two users, A has imported a real-shaped file, B is logged in:

- B's `/trades/` shows the empty state, and B's stat cards never include A's rows (also after B imports
  a file of their own: the cards count only B's trades).
- B's Account suggestions (datalist) contain none of A's labels.
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
1. Given a visitor, when they sign up with a new valid email, an acceptable password typed twice
   identically, and a valid time zone, then an account exists, they are logged in, and they land on
   `/trades/`.
2. Given an existing email in a different case, when someone signs up with it, then no second account
   is created and the single generic form-level message is shown (exact string: design §4.1 "Email
   cannot be used"; not under the Email field, per design §4.1 and §4.3).
3. Given a wrong password and given an unknown email, when each logs in, then the response body, status
   and message are identical (exact string: design §4.2 "Bad credentials").
4. Given a logged-in user, when they POST logout, then the session ends; GET on the logout URL does not
   log them out.
5. Given an anonymous request to `/trades/`, `/imports/` or the upload page, then it redirects to login
   with `next` set; and given `next` points to another host, then login ignores it and goes to `/trades/`.
6. Given the database, then no password is stored in plaintext and none appears in logs.
7. Given the signup page, then the time zone is a visible, editable field prefilled with the browser's
   zone (or `UTC` without JS), and a submitted valid zone is stored in `user.timezone` and used for the
   trades list; an unknown zone name is rejected with the message in design §4.1 "Time zone missing /
   unknown" and no account is created.
8. Given a signup where password and confirm differ, then no account is created and the §4 mismatch
   message is shown (exact string and placement under Confirm password: design §4.1 "Confirm mismatch");
   the signup page has no show-password toggle.

**Import into list**
9. Given a new user with no imports, when they open `/trades/`, then they see the "No trades yet" state
   and the upload button, and no stat cards.
10. Given a valid Topstep file, when the import completes, then the summary offers a link to `/trades/`
    and the trades appear there (upload behaviour: `import-account-label.md`).
11. Given the T4 nested rows, when imported, then the list shows 2 trades, not 1 (ADR-0004).
12. Given a file whose rows were all skipped or failed, when the user opens `/trades/`, then they see
    "Your last upload didn't add any trades." with a link to that import.

**List**
13. Given imported trades, then each row shows the columns defined in design §5.1, Net P&L is
    `PnL - Fees - Commissions` (T1: `+$291.96`, format per design §5.1 column 9), and times are in the
    user's timezone (header and date format: design §5.1 column 1).
14. Given no sort param, then trades are ordered newest entry first; given sort by Net P&L, then order
    follows P&L, with open trades last in either direction.
15. Given a garbage sort param, then the page renders with the default sort and no error.
16. Given one account label (or none), then there is no Account column; given two or more distinct
    labels, then the Account column shows (design §5.1 column 10, §5.3; blank label displays as defined
    there) and the cards carry "Across all accounts."

**Stat cards**
17. Given net P&L `+50, -30, 0, +10, -10, 0, +5` (vector 5), then win rate shows `60.00% (n=5)`, Total
    P&L shows `+$25.00 (n=7)`, and the win-rate card shows "Excludes 2 breakeven trades."
18. Given only breakeven trades, then win rate shows `n/a (n=0)`, never `0%` and never an error.
19. Given the user has no closed trades (none, or only open trades), then Total P&L shows `— (n=0)` and Avg R shows `— (n=0)`.
20. Given open trades, then they appear in the list marked open (design §5.1 columns 6-9) and are
    excluded from all three cards.
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
page (so a wrong zone chosen at signup cannot be changed in this slice), account/profile page,
"remember me", login throttling.

## Open questions

Owners in brackets.

1. ~~Signup email leak.~~ **CLOSED 2026-09-26 (user ruling):** accepted for now, login stays
   non-leaking. Logged as `follow-ups.md` row 17 (§4 "Accepted risk").
2. ~~How does `user.timezone` get set?~~ **CLOSED 2026-09-26 (user ruling):** visible field on signup,
   prefilled from the browser, user can change it (§4, AC 7). No settings page in this slice.
3. ~~Show-password toggle vs confirm field.~~ **CLOSED 2026-09-26 (user ruling):** confirm-password
   field, no show-password toggle (§4, AC 8).
4. **Instrument filter granularity** (for the next slice, when filters are built): full contract
   (`CLZ6`) or root (`CL`)? Not needed for slice 1; sorting uses the stored symbol.
   [trading-domain-expert]
5. **Small-sample hint on cards** (e.g. a note when n is low). Sample size is shown, but no threshold is
   defined, so none is specced. [behavior-analyst]
6. ~~Login throttling / lockout.~~ **CLOSED 2026-09-26 (user ruling):** deferred to before any
   non-author account exists, `follow-ups.md` row 18.
7. **Trade date basis:** list uses entry time. A trade entered late evening can belong to the next
   `TradeDay` (`topstep-import.md` §1). Fine while there is no day-based grouping. Revisit if a day view
   is added. [trading-domain-expert]
8. ~~Design gap.~~ **ANSWERED 2026-09-26:** wireframes now exist in
   [`docs/design/auth-and-trades-list.md`](../../design/auth-and-trades-list.md). Per the top-of-file
   ruling, the design owns layout and copy; this spec owns behavior.

## Suggested owner agents

`ux-designer` (signup/login, list, cards; `docs/design/`), `architect` (sort over derived trades, query
budget), `backend-engineer` (auth views, list view, stats service),
`frontend-engineer` (templates, cards, sort headers, signup form with time zone field), `qa-engineer` (AC 1-25, synthetic fixture,
isolation test), `security-reviewer` (auth, `next` redirect, isolation, before any second user).
