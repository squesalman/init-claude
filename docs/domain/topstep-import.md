# Topstep CSV import

- **Status:** partially blocked — see §3. Do not treat any column name below as verified.
- **Answers:** `docs/product/features/mvp.md` open question 2
- **Owner:** `trading-domain-expert`
- **Consumers:** `backend-engineer` (importer), `database-engineer` (raw-row storage + dedupe
  index), `architect` (ADR-0003)

## 1. What I can commit to without a sample file

Topstep is a **futures prop-firm evaluation provider**, not a broker with one fixed platform —
accounts have historically run on Rithmic, Tradovate, or Topstep's own "TopstepX" front end
depending on when the account was opened. That means **there is no single canonical "Topstep CSV
format"**; the actual columns depend on which execution platform generated the export, and
Topstep itself may also offer its own performance/statement export on top of that. This is a
real ambiguity, not a detail I'm withholding to be cautious — treat it as load-bearing.

What I'll commit to based on how this class of platform (futures prop firms, Tradovate/Rithmic-
style order/fill exports) generally works:

- **Most likely raw-fill shape**, if the export is an order/fill history (Tradovate- or
  Rithmic-style): one row per fill, columns approximately `account, contract/symbol (e.g.
  "ESZ25"), B/S (buy/sell), qty, fill price, order/fill id, timestamp, commission/fee`. This is
  the shape the fill→trade matcher in `pnl-and-matching.md` expects and is designed for.
- **Also plausible: a closed-trade/statement shape.** Prop-firm dashboards often show P&L
  per round-turn trade directly (since evaluation pass/fail is measured trade-by-trade and
  day-by-day), in which case the export is already `entry time, exit time, entry price, exit
  price, qty, side, P&L` per row — fills pre-matched by the platform, not raw executions.
- **Futures prices are in points, not dollars**, in either shape — see the multiplier problem in
  `pnl-and-matching.md` §4. A raw-fill export will not carry a dollar P&L at all; a closed-trade
  export may carry a pre-computed P&L column that already accounts for the multiplier (in which
  case we should trust *that* column rather than recompute it against an incomplete multiplier
  table — recommendation below).

## 2. Import design that works for either shape (commit to this; verify columns later)

Design the importer so the header row selects the parse path, rather than assuming one shape:

1. **Detect format by header row.** If columns include distinct entry/exit timestamp pairs and a
   P&L column → closed-trade path (import as an already-derived trade, skip fill→trade matching
   entirely for those rows, store the raw row regardless). If columns are single-timestamp,
   single-price, single-side rows → raw-fill path (import as executions, run the FIFO matcher
   from `pnl-and-matching.md`).
2. **Always retain the raw row** (every column, unparsed) alongside whatever we derive, per
   `CLAUDE.md`'s import rule — this is what lets us re-run parsing later once the real column
   mapping is confirmed, without asking the user to re-upload.
3. **Prefer a broker-supplied P&L column over recomputing it** when both a P&L column and points-
   based prices are present — recomputing risks getting the futures multiplier wrong (§4 in
   `pnl-and-matching.md`); the broker's own number is authoritative for that row until proven
   otherwise.

## 3. Dedupe key

Per `CLAUDE.md`: **dedupe by broker + execution id.**

- **If the export is raw fills** and includes a fill/order id column (expected for a
  Tradovate/Rithmic-style export — these platforms do assign one), the dedupe key is
  `(broker="topstep", broker_execution_id=<that id>)`, exactly per the domain rule. This is the
  target and should be used whenever the column exists.
- **If no stable id column exists**, or the export is the closed-trade/statement shape (which may
  only number rows sequentially, with no stable id that survives a re-export), fall back to a
  **composite key**: `(broker="topstep", account, instrument, entered_at_utc, exited_at_utc,
  side, qty, entry_price, exit_price)` for closed-trade rows, or `(broker="topstep", account,
  instrument, executed_at_utc, side, qty, price)` for raw-fill rows. **Flagging the risk
  explicitly:** a composite key can under-dedupe (two genuinely distinct fills at the exact same
  price/qty/timestamp, rare but not impossible in fast markets) or over-dedupe (if the export
  truncates timestamp precision on re-export). This fallback needs verification against a real
  re-exported file — confirm the same trade re-exports with identical values on every column used
  in the composite key before trusting it in production.

## 4. What's needed to close this out

I don't have a real Topstep export to verify against, and I'm not going to guess exact column
names — that risks a confidently-wrong importer spec that `backend-engineer` builds against and
ships. **Escalating to the user rather than guessing:**

- A real sample CSV (a few rows is enough; redact account numbers/PII if needed) from an actual
  Topstep export.
- Which underlying platform generated it (Tradovate / Rithmic / TopstepX), since Topstep has used
  more than one over time and a user with an older export may have a different shape than one
  pulled today.

Once a sample exists, this doc gets a concrete column-mapping table and test vectors (same format
as `pnl-and-matching.md` §5) before `backend-engineer` starts the importer — do not start the
importer against the assumptions above alone.

## 5. Futures contract multiplier table

Blocking dependency shared with `pnl-and-matching.md` §4: if the export uses points-based prices
(no pre-computed P&L column), the importer needs a lookup table of contract root → multiplier
(e.g. `ES`→50, `MES`→5, `NQ`→20, `MNQ`→2, `CL`→1000, `GC`→100) to compute correct P&L. This table
doesn't exist yet. Building it from public specs is possible but every entry should be checked
against a real trade's known P&L before going live — a wrong multiplier silently produces a
plausible-looking but wrong number, which is worse than an obvious import failure.
