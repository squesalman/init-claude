# MVP: Journaling + Basic P&L Stats

- **Status:** draft, ready for downstream specs (domain/behavior N/A this slice, ux, architect, db, backend, frontend)
- **Depends on:** `docs/adr/0002-stack-revised.md` (Django + Postgres + htmx, compute-on-read, no worker)

## Overview

The MVP is a journal, not an analyst. A user logs trades (manually or via Topstep CSV
import), writes a free-text reasoning note per trade, self-flags whether they followed
their own rules (yes/no, checked against one free-text "my rules" field they maintain),
and reviews everything in a sortable/filterable trade list with basic P&L stats (win
rate, total P&L, average R-multiple). No behavior detection, no emotion tagging, no
screenshots, no structured rule sets, no broker besides Topstep — all deferred to v2 or
later, not silently built now.

Every user's data is fully isolated (hard domain rule, not an MVP nicety).

## User stories

### 1. Account and login

**Problem:** Multiple traders use the app; each must only ever see their own data.

**User story:** As a trader, I sign up with email + password and log in, so my journal
is mine alone.

**Acceptance criteria:**
- A visitor can create an account with email + password; email must be unique.
- Passwords are hashed (Django's default hasher); never stored or logged in plaintext.
- A logged-in user can log out.
- Every page that shows trade/journal data requires login; unauthenticated requests
  redirect to login.
- User A cannot view, edit, filter into, or infer the existence of User B's trades,
  journal entries, or rules text via any URL, ID guess, or API response.
- One automated test enumerates every user-owned model and asserts a second user's
  session cannot retrieve its rows (per ADR-0001's user-scoping chokepoint).

**Out of scope:** password reset/forgot-password flow, email verification, 2FA, social
login. (Assumption — flagged below; add password reset before this has real users
locking themselves out.)

**Suggested owners:** `backend-engineer` (auth views), `security-reviewer` (isolation
audit before release).

---

### 2. Manual trade entry

**Problem:** Not every trade comes from a broker export; the user needs a fast manual
path so journaling never blocks on import support.

**User story:** As a trader, I can manually add a trade with the essentials, so I can
journal something the same day I take it.

**Acceptance criteria:**
- A form captures: instrument/symbol, side (long/short), quantity, entry price, entry
  time, exit price, exit time, currency. (Exact field list and derived-P&L mechanics are
  `trading-domain-expert` territory — see open questions.)
- Quantity and price are stored as `NUMERIC(20,10)`, never float; money (P&L, fees) as
  `NUMERIC(19,4)` with an ISO 4217 currency column, per ADR-0002.
- Submitted times are interpreted in the user's configured timezone and stored as UTC;
  displayed back in the user's timezone.
- A saved trade appears immediately in the trade list (no async processing, no queue —
  compute-on-read per ADR-0001).
- Required fields are the minimum needed to compute P&L and show the trade in the list;
  nothing beyond that is required (journaling-friction principle).

**Out of scope:** editing/deleting executions after the fact beyond simple correction of
the manual entry itself; multi-leg or partial-fill entry (that's the import path's job).

**Suggested owners:** `trading-domain-expert` (field list, P&L calc), `backend-engineer`,
`frontend-engineer` (form), `database-engineer` (schema).

---

### 3. CSV import from Topstep

**Problem:** Re-typing every trade is the #1 journaling-friction killer for anyone who
already gets a broker statement.

**User story:** As a trader using Topstep, I can upload my Topstep export and have my
trades appear without manual re-entry.

**Acceptance criteria:**
- User uploads a Topstep-format CSV; the app parses it synchronously within the request
  (no background job, per ADR-0001) and shows a result summary (rows imported, rows
  skipped, rows failed with reasons).
- Import is idempotent: re-uploading the same file (or an overlapping export) creates no
  duplicate trades — dedupe key is broker + execution/trade id, per `CLAUDE.md`.
- Raw imported rows are retained so parsing can be re-run without re-uploading, per
  `CLAUDE.md`'s "keep raw data" import rule.
- Malformed or unrecognized rows are reported to the user, not silently dropped and not
  a hard failure of the whole file.
- Imported trades are scoped to the importing user only and appear in their trade list
  alongside manual entries with no visual distinction required for MVP.
- User can delete an import batch (with confirmation) to undo an import, e.g. a file
  uploaded under the wrong Account label. Added after ADR-0004: its recovery path for the
  duplicate-batch risk depends on this. Semantics for executions that already have a
  journal entry are an open architect decision (see `docs/data/follow-ups.md` row 13).
  Spec: `docs/product/features/import-account-label.md`.

**Out of scope:** any broker other than Topstep; scheduled/automatic re-import; email or
API-based import.

**Suggested owners:** `trading-domain-expert` (exact Topstep column mapping, fill→trade
matching — see open questions), `backend-engineer` (importer), `database-engineer`
(raw-row storage + dedupe index).

---

### 4. Journal entry per trade

**Problem:** Raw P&L numbers don't teach anything; the reasoning behind a trade is what
a trader needs to review later.

**User story:** As a trader, I can attach a note and a rule-followed flag to any trade,
so I can review my own thinking later.

**Acceptance criteria:**
- Each trade (manual or imported) has exactly one journal entry with two fields: a
  free-text note (reasoning) and a required yes/no "did I follow my rules" flag.
- The yes/no flag has no default that silently counts as an answer — the user must pick
  one before the entry counts as journaled. (Trade itself can exist un-journaled; the
  list shows which trades are missing a journal entry.)
- The note is optional; the yes/no flag is the one required structured field, matching
  locked scope.
- Journal entries can be edited after creation.
- The yes/no flag is a plain self-assessment against whatever the user's free-text "my
  rules" field currently says — there is no per-trade link to a specific named rule, and
  no validation that the trade actually matches the rules text (self-report only).

**Out of scope:** emotion tagging, mood/tilt fields, structured or named rule lists,
per-trade linkage to a specific rule, screenshots/attachments, tagging/categorization
beyond the yes/no flag.

**Suggested owners:** `ux-designer` (low-friction entry flow, ideally inline on the trade
row via htmx), `backend-engineer`, `frontend-engineer`.

---

### 5. "My rules" free-text field

**Problem:** Traders think of their rules as a personal document, not a database of
discrete named rules — forcing structure here is scope creep the MVP doesn't need to
test the core idea.

**User story:** As a trader, I keep one free-text block of "my rules" that I can update
any time, so my own yes/no journaling has something to be judged against.

**Acceptance criteria:**
- Each user has exactly one free-text "my rules" field, editable at any time, with no
  required format or structure.
- Editing "my rules" does not retroactively change past trades' yes/no flags.
- The field is visible from the journal-entry UI so the user can reference it while
  self-assessing (doesn't have to be memorized).

**Out of scope:** multiple named rules, rule categories, per-trade rule linkage,
enforcement/validation against the rules text.

**Suggested owners:** `backend-engineer`, `frontend-engineer`.

---

### 6. Trade list / dashboard

**Problem:** A pile of journaled trades is only useful if the trader can find and review
patterns manually — this is the review surface for the whole MVP.

**User story:** As a trader, I can see all my trades in one sortable, filterable list
with their notes, rule-flag, and basic stats, so I can review my history.

**Acceptance criteria:**
- List shows, per trade: date/time (user's timezone), instrument, side, quantity, entry
  price, exit price, P&L, rule-followed flag (or "not journaled" if absent), and note
  (truncated with full view on demand).
- Sortable by date, P&L, and instrument at minimum.
- Filterable by at least: date range, instrument, rule-followed (yes/no/not journaled),
  win/loss.
- List and filters work via standard server-rendered pagination/requests (htmx partial
  updates per ADR-0001) — no client-side SPA state.
- List only ever shows the logged-in user's trades (ties back to story 1's isolation
  test).
- Above the list, three account-level stats are shown, recomputed on read from the
  currently filtered set: **win rate**, **total P&L**, **average R-multiple**.
- Each stat shows its sample size (e.g., "avg R: 1.4 (n=32)") so a small or filtered
  sample isn't mistaken for a stable number, per the "insights are evidence-backed"
  product principle — even though this isn't a behavioral insight, the sample-size
  discipline still applies.
- R-multiple excludes trades that lack the data needed to compute it (see open
  questions) rather than treating them as zero or excluding them silently — the stat's
  sample size reflects only trades that had risk data.

**Out of scope:** any behavioral insight, streak detection, charts/graphs beyond the
three numbers above (a chart is a v2 nicety, not required to ship this), CSV export,
saved filter presets.

**Suggested owners:** `ux-designer` (list/filter layout), `trading-domain-expert` (win
rate / P&L / R-multiple formulas — must live in `docs/domain/`), `backend-engineer`
(query + compute-on-read implementation), `database-engineer` (indexes for sort/filter
at expected volume), `frontend-engineer` (templates + htmx).

## Non-goals (explicitly out of scope for this MVP — do not build early)

- Emotion tagging or mood/tilt fields of any kind.
- Structured or named rule sets, or per-trade linkage to a specific rule.
- Any behavior detector: revenge trading, overtrading, tilt, rule-break clustering, or
  any other pattern/insight beyond the three plain P&L stats. Detection is v2, full
  stop.
- Screenshot or file attachments on trades or journal entries.
- Any broker import besides Topstep.
- Password reset, email verification, 2FA, social login (see story 1 assumption).
- CSV/data export, saved filters, charts/graphs.
- Background jobs / async import — everything computes synchronously on read or on
  request, per ADR-0001.

## Open questions

These need answers from a spec owner before the dependent story can be implemented;
routing per `CLAUDE.md`'s delegation rules rather than left for an engineer to invent:

1. **R-multiple formula and required inputs** (story 2, 6) — R-multiple needs a defined
   "risk" (e.g., stop-loss distance or a risk amount entered per trade). This isn't in
   the locked field list. Route to `trading-domain-expert`: either (a) add an optional
   risk/stop field to trade entry so R can be computed for trades that provide it, or
   (b) defer average R-multiple to v2 if this app doesn't want to ask for a stop.
   Assumption used above: (a), field is optional, trades without it are excluded from
   the R-multiple average and reflected in its sample size.
2. **Topstep CSV format** (story 3) — exact columns, whether Topstep exports raw fills
   or already-closed trades, and the dedupe key it provides. Route to
   `trading-domain-expert` to produce the mapping and test vectors before
   `backend-engineer` builds the importer.
3. **Win rate tie-breaking** (story 6) — how a $0 net P&L trade counts (win, loss, or
   excluded from the denominator). Route to `trading-domain-expert`; needs to live in
   `docs/domain/` alongside the P&L formula.
4. **P&L computation / fill→trade matching** (stories 2, 3, 6) — "executions are the
   source of truth; trades are derived" is a hard domain rule, but the actual matching
   algorithm (FIFO/LIFO, partial fills, multi-leg trades) isn't specified. Route to
   `trading-domain-expert`; this blocks both entry stories and the dashboard stats.

## Judgment calls made without asking (flagging for sanity-check)

- Assumed password reset/email verification are out of scope for MVP — reasonable for a
  small/trusted user base but should be confirmed before opening this to strangers.
- Assumed dashboard stats recompute over the *currently filtered* trade list rather than
  always showing all-time numbers — seemed more useful for review, but it's a UX choice
  the user didn't explicitly lock.
- Assumed R-multiple needs an optional risk/stop input not mentioned in the locked scope
  (see open question 1) rather than silently dropping the "average R-multiple"
  requirement — flagging instead of picking silently since it adds a field, which
  conflicts with the journaling-friction principle if done carelessly.
