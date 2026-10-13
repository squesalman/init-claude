# P&L, fill→trade matching, win rate, and R-multiple

- **Status:** accepted for MVP implementation
- **Answers:** `docs/product/features/mvp.md` open questions 1, 3, 4
- **Owner:** `trading-domain-expert`
- **Consumers:** `architect` (ADR-0003 data model), `database-engineer`, `backend-engineer`, `qa-engineer`

All amounts below use `Decimal`, never float, per `CLAUDE.md` and ADR-0002. Money is
`NUMERIC(19,4)` + currency; price/qty are `NUMERIC(20,10)`.

---

## 1. Fill → trade matching algorithm

**Method: FIFO, per user + broker + account + instrument (+ `broker_trade_id` bucket, see below).** Not configurable in MVP (LIFO/average-cost
are a v2 toggle at most, and only if a user asks — do not build the switch speculatively).

**Bucket by `broker_trade_id` (ADR-0004 §3).** When a broker reports closed round-trips (Topstep),
the importer sets `broker_trade_id` on both legs of a row, and FIFO runs unchanged inside each
`(user, broker, account, instrument, broker_trade_id)` bucket, so each row is exactly one trade.
`broker` is in the key (ADR-0008 §1, user ruling 2026-10-13) so a `broker_trade_id` from one source
can never pair with the same string from another (for example a Topstep `Id` that happens to look
like a manual key). Executions with `broker_trade_id = NULL` (a future fill-only broker importer;
**not** manual entry, see below) share one bucket per `(user, broker, account, instrument)`, so two
brokers' fills never merge even on the same label and symbol, and get the plain FIFO below,
including merging nested same-direction lifecycles into one flat-to-flat trade. Everything in this
doc after this paragraph describes FIFO within one bucket.

**Manual entry gets its own key per trade (user ruling 2026-10-13; the key's format is
ADR-0008's).** A manual trade is one entry fill plus at most one same-size exit fill, and both
carry a pairing key that no other trade has. Domain consequence: a manual trade is its own
bucket, so it never merges with, pairs against, or is paired by another manual trade or any
imported fill, even on the same account label and symbol. Every manual trade therefore has
exactly one entry lot, and no manual trade can be a scale-in, partially closed, or a flip. See §7
for the manual-entry rules and vectors 24-34.

### Model

- An **execution** (fill): `(id, user_id, account_id, instrument, side [buy/sell],
  qty, price, fee, currency, executed_at_utc, broker, broker_execution_id)`. Imported fills are
  immutable. Manual fills may be edited in place (user ruling 2026-10-13, ADR-0008 Accepted); the
  matcher is a pure function of the current fills, computed on read, so an edit simply changes
  the next derivation.
- A **position** per `(user, account, instrument)` is a FIFO queue of **open lots**:
  `(opening_execution_id, side, remaining_qty, price, fee_per_unit_remaining, opened_at)`.
- A **trade** is one flat→flat lifecycle of that position: it starts when qty goes from 0 to
  non-zero and ends when qty returns to 0. Every partial close, scale-in, and scale-out inside
  that lifecycle belongs to the same trade. A trade that has not returned to 0 is an **open
  trade**.

### Processing one execution against the queue

1. If the queue is empty or the execution's side matches the queue's side (adding to the
   position — a scale-in): push a new lot. No P&L realized.
2. If the execution's side is opposite the queue's side (a close or partial close): consume lots
   **oldest first**. For each lot consumed:
   - `matched_qty = min(lot.remaining_qty, execution.remaining_qty)`
   - Realize a **matched pair**: `(entry_price = lot.price, exit_price = execution.price,
     qty = matched_qty, entry_fee = allocate(lot, matched_qty), exit_fee =
     allocate(execution, matched_qty))`.
   - Reduce `lot.remaining_qty` and `execution.remaining_qty` by `matched_qty`. Drop the lot if
     it reaches 0.
3. **Flip (long→short or short→long in one execution):** if the execution's qty exceeds the
   total open qty on the opposite side, step 2 closes every existing lot (ending the current
   trade — position returns to exactly 0), and the execution's leftover qty opens a **new lot on
   the new side**, starting a new trade. Both trades record the same execution timestamp as their
   boundary (close of trade N, open of trade N+1). This is two trades sharing one execution row,
   split by matched quantity.
4. Fee allocation is **pro-rata by quantity** within an execution: `fee_per_unit =
   execution.fee / execution.qty`; each matched pair gets `fee_per_unit * matched_qty` from both
   the opening execution and the closing execution. Rounding: allocate with full precision, round
   only the final trade-level sum (`ROUND_HALF_EVEN`, to the currency's minor unit) — never round
   per-lot and re-sum.

### Trade-level P&L

```
gross_pnl   = Σ over matched pairs of (exit_price - entry_price) * qty * side_sign * multiplier
side_sign   = +1 for long (opened with a buy), -1 for short (opened with a sell)
net_pnl     = gross_pnl - Σ (entry_fee + exit_fee) over all matched pairs in the trade
```

`multiplier` = contract/point multiplier (see §4). 1 for stocks, forex (in quote-currency pip
terms — see note below), and crypto.

### Worked example — scale-in, two partial closes, one trade

Stock, multiplier 1, USD.

| # | Side | Qty | Price | Fee |
|---|------|-----|-------|-----|
| 1 | Buy  | 100 | 10.00 | 1.00 |
| 2 | Buy  | 50  | 10.20 | 0.50 |
| 3 | Sell | 80  | 10.50 | 0.80 |
| 4 | Sell | 70  | 10.60 | 0.70 |

FIFO matching:

- Sell #3 (80) consumes 80 of lot #1's 100 → matched pair A: entry 10.00, exit 10.50, qty 80,
  entry_fee = 1.00 × 80/100 = 0.80, exit_fee = 0.80 × 80/80 = 0.80.
  `pnl_A = (10.50-10.00)*80 - 0.80 - 0.80 = 40.00 - 1.60 = 38.40`
- Lot #1 has 20 left (fee remaining 0.20).
- Sell #4 (70): first 20 consumes the rest of lot #1 → matched pair B: entry 10.00, exit 10.60,
  qty 20, entry_fee = 0.20 (all that's left), exit_fee = 0.70 × 20/70 = 0.20.
  `pnl_B = (10.60-10.00)*20 - 0.20 - 0.20 = 12.00 - 0.40 = 11.60`
- Remaining 50 of Sell #4 consumes all of lot #2 → matched pair C: entry 10.20, exit 10.60,
  qty 50, entry_fee = 0.50 (all), exit_fee = 0.70 × 50/70 = 0.50.
  `pnl_C = (10.60-10.20)*50 - 0.50 - 0.50 = 20.00 - 1.00 = 19.00`

Position is now flat (150 bought, 150 sold) → **one trade**, `net_pnl = 38.40+11.60+19.00 =
69.00`.

Cross-check via averages: avg entry = (100×10.00+50×10.20)/150 = 10.0667, avg exit =
(80×10.50+70×10.60)/150 = 10.5467, gross = (10.5467-10.0667)×150 = 72.00, total fees =
1.00+0.50+0.80+0.70 = 3.00, net = 72.00-3.00 = **69.00**. Matches.

### Worked example — flip in one execution

Futures-style, multiplier 1 for simplicity, fees 0.

| # | Side | Qty | Price |
|---|------|-----|-------|
| 1 | Buy  | 10  | 100.00 |
| 2 | Sell | 15  | 105.00 |

Sell #2 closes all 10 of lot #1 (matched pair: entry 100, exit 105, qty 10 →
`pnl = (105-100)*10 = 50.00`) → **Trade 1 closes, net_pnl = 50.00**. The leftover 5 of Sell #2
opens a new short lot at 105.00 → **Trade 2 opens** (short, qty 5, still open), same
execution timestamp as Trade 1's close.

### Edge cases

- **Open trades:** qty ≠ 0 at end of available executions. No `exit_price`, no `net_pnl`.
  Excluded from win-rate, total P&L, and R-multiple denominators — shown in the trade list as
  "open," not as a loss or a zero.
- **Zero-qty / cancelled fills:** never enter matching (should not exist as executions; if a
  broker export contains a cancelled order, that row does not correspond to a fill and is not
  imported as an execution at all).
- **Same-timestamp fills:** if two executions share `executed_at_utc` (batch fills), process in
  the order the broker export lists them. In code that order is the execution pk, ascending
  (callers pass `order_by("executed_at", "id")`; `id` follows file order). Needs revisiting if a
  broker's export is provably unordered. Manual fills have NULL `broker_execution_id`, so pk is
  their only tie-break: a manual entry fill must be inserted before its exit fill (§7.1).
- **Multi-account:** matching is scoped per `(user, account, instrument)`. MVP doesn't have an
  explicit multi-account UI, but the matching key includes `account` from day one so it's not a
  retrofit — if Topstep exports one account per file this is a no-op.
- **Corrections/cancels after import:** imported fills are not editable; re-running the parser
  against retained raw rows (per `CLAUDE.md`), or deleting the import (ADR-0005), is the only
  supported fix path if an imported fill was wrong. Manual fills are corrected by editing them in
  place (§7.4).

---

## 2. Win-rate tie-breaking ($0 net P&L)

**Rule:** a trade is a **breakeven** when `net_pnl == 0` exactly (Decimal equality — `NUMERIC` is
exact, so no epsilon/threshold is needed here, unlike float systems).

**Win rate = wins / (wins + losses).** Breakeven trades are counted in the trade list and in
total P&L (they contribute $0), but **excluded from both the numerator and the denominator** of
win rate. This is the standard retail-journal convention (a $0 trade is neither a win nor a loss)
and avoids silently deflating win rate by counting flat trades as losses.

This is a **named, configurable definition**, not a hidden default — store it as a named rule
(`BREAKEVEN_ELIGIBLE_EXCLUDED` in code) so a future per-user preference (e.g., "count breakeven as
loss") is a config change, not a rewrite.

### Worked example

Trades with net P&L: `+50, -30, 0, +10, -10, 0, +5`

- Wins: `+50, +10, +5` → 3
- Losses: `-30, -10` → 2
- Breakeven: `0, 0` → 2 (excluded from ratio)
- **Win rate = 3 / (3+2) = 60%**, sample size shown as `n=5` (wins+losses), not `n=7`.
- Total P&L (unaffected by the tie-break rule) = 50-30+0+10-10+0+5 = **+25**.

### Edge cases

- All trades breakeven → win rate denominator is 0 → display "n/a (n=0)", never `0%` or a
  division error.
- A trade with `net_pnl` that rounds to 0.00 at the currency's minor unit but is non-zero before
  rounding: compare the **stored, already-rounded** `net_pnl` value (rounding happens once, at
  computation time, per §1) — do not re-derive a fuzzier "near zero" band. If a future behavior
  spec wants a fuzzy breakeven band (e.g., "|net_pnl| < $1"), that's a distinct, explicitly-named
  threshold layered on top, not a change to this exact-zero rule.

---

## 3. R-multiple

**Inputs live on the trade's `JournalEntry`** (ADR-0003 §6, `docs/data/schema.md`), not on the
execution. All three columns are optional: `stop_price` (`NUMERIC(20,10)`), `planned_risk_amount`
(`NUMERIC(19,4)`), `risk_currency` (ISO 4217; DB CHECK: set iff `planned_risk_amount` is set). The
entry hangs off the trade's **opening execution**, so any trade, manual or imported, can carry R
inputs. Nothing is captured from a broker export; the user types them on the journal entry.

All arithmetic is `Decimal`. Never float.

### Terms

- **Entry lot(s):** the open lots created by the trade's entry executions (§1). A trade with **one**
  entry lot is *single-entry* (partial closes are fine; they do not add entry lots). A trade with
  **two or more** entry lots (a scale-in) is *multi-leg*. A flip's leftover qty is one lot, and its
  qty is the lot's qty, not the whole execution's qty.
- **Trade currency:** the `currency` of the trade's executions (one currency per trade).
- `trade_qty` = total entry qty = sum of entry-lot qty. `multiplier` = the opening lot's
  `contract_multiplier`.

### Choosing the risk amount (in order)

1. **`planned_risk_amount` set** -> `risk_amount = planned_risk_amount`. **It wins over
   `stop_price` when both are set.** Reason: it is the trader's own statement of money at risk, so
   it is correct for every trade shape (scale-ins, multi-leg, any multiplier). `stop_price` stays
   stored and visible, just unused for R. No blending, no fallback (below).
2. **Else `stop_price` set and the trade is single-entry** ->
   `risk_amount = |entry_price - stop_price| * trade_qty * multiplier`, with `entry_price` = the
   entry lot's price.
3. **Else `stop_price` set and the trade is multi-leg** -> `R = null`, reason
   `stop_price_multi_leg`. With several entry prices, "distance from entry" is ambiguous, and a
   stop that moved between scale-ins cannot be reconstructed. **For multi-leg trades,
   `planned_risk_amount` is the only valid R input.** The UI should say "Enter planned risk to get
   R on this trade."
4. **Else** (neither set) -> `R = null`, reason `no_risk_input`.

**User ruling 2026-09-26: precedence and no-fallback confirmed ("KEEP").**

The precedence and the multi-leg rule are named definitions (`R_RISK_PRECEDENCE =
planned_over_stop`), not hidden defaults, so a per-user preference later is a config change.

```
R = net_pnl / risk_amount       # net_pnl per §1 (after fees); risk_amount excludes fees
```

**Rounding:** per-trade `R` is quantized once to 4 dp (`ROUND_HALF_EVEN`). Average R is the
arithmetic mean of those stored values, quantized once to 2 dp. Do not average display values.

### Validity checks (each failure gives `R = null` plus a reason code, never 0 or infinity)

| Condition | R | Reason code |
|---|---|---|
| Trade is open (no `net_pnl`, §1) | null | `trade_open` |
| `planned_risk_amount <= 0` | null | `risk_not_positive` |
| `risk_currency != trade currency` | null | `risk_currency_mismatch` |
| Stop-derived risk: `(entry_price - stop_price) * side_sign <= 0` (stop equals entry, or is on the profit side of entry) | null | `stop_not_a_risk` |
| Multi-leg, stop only | null | `stop_price_multi_leg` |
| Neither input | null | `no_risk_input` |

- **Reason codes are exactly the strings in the table above** (ruling 2026-09-30): the code for
  `planned_risk_amount <= 0` is `risk_not_positive`. `planned_risk_not_positive` (used once in
  `docs/data/follow-ups.md` row 14) is a typo and must not appear in code.
- **Evaluation order** (first hit wins, so each trade has exactly one reason): `trade_open` ->
  planned risk set? (`risk_not_positive`, then `risk_currency_mismatch`, else valid) -> stop set?
  (multi-leg: `stop_price_multi_leg`; single-entry: `stop_not_a_risk` or valid) -> `no_risk_input`.
- **Stop side-check applies to single-entry trades only** (ruling 2026-09-30). "Single-entry" is
  by entry lots (Terms above), so a trade with one entry lot and any number of partial exits is
  single-entry: `entry_price` is that lot's price and the check is
  `(entry_price - stop_price) * side_sign > 0`, violation gives `stop_not_a_risk`. A flip's
  leftover lot counts as one lot at the execution's price. For a **multi-leg** trade the stop is
  never used for R, so no side-check is defined: there is no single entry price to compare to, an
  average entry would invent one, and a stop set before a later scale-in may legitimately sit
  above a later entry. The result is `stop_price_multi_leg` even when the stop looks like it is
  on the profit side (`stop_not_a_risk` is never returned for multi-leg). The form may accept and
  store such a stop unchecked. A stop is also never checked when `planned_risk_amount` is set
  (planned wins, the stop is inert).
- **`risk_currency` is derived, not typed** (ruling 2026-09-30). On save, when
  `planned_risk_amount` is set the app sets `risk_currency` = the trade's currency (one
  currency per trade, from its executions); when the amount is blank both are cleared, which
  satisfies the DB CHECK. Any ISO currency works: a EUR trade with planned risk 40.00 stores
  `risk_currency = EUR`, and R is computed in the EUR group with no FX. The amount is read as
  "in the trade's currency". Nothing is special about non-USD. A mismatch can therefore only arise
  later (a re-import changes the trade's currency) or from legacy rows, and then the rule below
  applies.
- **Currency mismatch:** no FX conversion (ADR-0002). `risk_currency` compared to the trade
  currency, case-sensitive ISO codes. A mismatch gives `R = null`. **No fallback to `stop_price`
  when `planned_risk_amount` is set but unusable** (mismatch or not positive). A silent fallback
  would show a number the trader did not intend. `stop_price` has no currency, so it never
  mismatches.
- **Zero risk:** `stop_price == entry_price` and `planned_risk_amount == 0` are both rejected at
  form validation ("stop must be on the loss side of entry" / "risk must be greater than 0"). The
  computation guard above is the backstop, because entry prices can change after a re-import.
- **Stop on the wrong side** (long: stop above entry; short: stop below entry): treated like zero
  risk. `stop_price` means the **initial** stop. A trailed stop that now sits on the profit side
  is not the initial risk.
- **Losing and breakeven trades:** R is computed normally, so it can be negative or exactly 0.
  Breakeven (§2) does not change R.
- **Short trades:** same formulas. The absolute value in step 2 removes the side branch, and the
  validity check uses `side_sign` (+1 long, -1 short).
- **Average R** = mean of stored `R` over closed trades in the current filtered set where
  `R is not null`. Display `avg R: 1.40 (n=32)`, where `n` counts trades with non-null R (mvp.md
  story 6). Trades with null R are excluded, never counted as 0.
  **Which trades count** (ruling 2026-09-30): within one currency group (no FX), only **closed**
  trades whose R is non-null (valid risk, positive, R computed). Open trades are in neither `n`
  nor "Left out". **"Left out: N"** = closed trades in the same group with `R = null`, for any
  reason (`no_risk_input`, `risk_not_positive`, `risk_currency_mismatch`, `stop_not_a_risk`,
  `stop_price_multi_leg`). So `n + N = closed trades in the group`. Missing or unusable risk is
  never treated as 0 (that would drag the mean toward 0 and fabricate a result). If `n = 0`
  show `— (n=0)`, never `0.00`. Rounding: mean of the stored 4 dp R values, quantized once to
  2 dp with `ROUND_HALF_EVEN` (exact ties round to the even digit, see Vector 23).
- **Which imported trades get R:** every Topstep row is one trade with one entry lot (ADR-0004 §3,
  bucketed by `broker_trade_id`), so both inputs work on Topstep imports. Manual trades are
  always single-entry too (one entry fill, own pairing key, §1 and §7), so both inputs work on
  them. Multi-leg trades come only from merged FIFO, which means executions with
  `broker_trade_id = NULL` (a future fill-only importer). For those, only `planned_risk_amount`
  works.

### Worked example (stop-derived, single-entry)

Long 100 shares @ 50.00, stop @ 49.50, exit @ 51.20, total fees $2.00, USD.

```
risk_amount = |50.00 - 49.50| * 100 * 1 = 50.00
net_pnl     = (51.20 - 50.00) * 100 - 2.00 = 120.00 - 2.00 = 118.00
R           = 118.00 / 50.00 = 2.3600
```

Same trade with `planned_risk_amount = 59.00`, `risk_currency = USD` (and the stop still set):
`risk_amount = 59.00` (planned wins), `R = 118.00 / 59.00 = 2.0000`.

### Worked example (multi-leg)

The scale-in trade from §1 (net 69.00, two entry lots at 10.00 and 10.20). With only
`stop_price = 9.80`: `R = null` (`stop_price_multi_leg`). With `planned_risk_amount = 30.00 USD`:
`R = 69.00 / 30.00 = 2.3000`.

---

## 4. Instrument multiplier note

`multiplier` in the P&L formula above is 1 for stocks/forex/crypto in MVP. **Futures need a
contract multiplier (a dollar value per point that differs by root, for example ES vs MES; no value is stated here because only `CL` and `MCL` are verified) that is not derivable from a fill row
alone** — it depends on a reference table keyed by contract root symbol. Topstep is a futures
prop firm, so this directly affects the importer (see `docs/domain/topstep-import.md`). This
reference table is now partly resolved: `CL` = 1000 and `MCL` = 100 are verified against a real
export (`docs/domain/topstep-import.md` §2). Other roots are still unverified and the importer
rejects unknown roots rather than defaulting to 1. The multiplier is stored per execution
(ADR-0003), so historical P&L does not change if a contract spec changes.

---

## 5. Test vectors

Reference fixtures for `qa-engineer` and for unit tests backing the matcher. Format: input
executions → expected trade(s). All amounts `Decimal`; `multiplier=1` unless noted.

```python
# Vector 1: simple single-entry/single-exit. Fees are split 1.00 + 1.00 here (a generic
# two-fill shape). The manual-entry shape, all fees on the exit fill, is vector 25; net is the same.
executions = [
    {"side": "buy",  "qty": 10, "price": "100.00", "fee": "1.00", "ts": "2026-01-01T14:30:00Z"},
    {"side": "sell", "qty": 10, "price": "105.00", "fee": "1.00", "ts": "2026-01-01T15:00:00Z"},
]
expected_trade = {
    "side": "long", "qty": 10, "entry_price": "100.00", "exit_price": "105.00",
    "gross_pnl": "50.00", "fees": "2.00", "net_pnl": "48.00", "status": "closed",
}

# Vector 2: scale-in + two partial closes -> one trade (worked example in §1)
executions = [
    {"side": "buy",  "qty": 100, "price": "10.00", "fee": "1.00", "ts": "T0"},
    {"side": "buy",  "qty": 50,  "price": "10.20", "fee": "0.50", "ts": "T1"},
    {"side": "sell", "qty": 80,  "price": "10.50", "fee": "0.80", "ts": "T2"},
    {"side": "sell", "qty": 70,  "price": "10.60", "fee": "0.70", "ts": "T3"},
]
expected_trade = {"side": "long", "qty": 150, "net_pnl": "69.00", "status": "closed"}

# Vector 3: flip long -> short in one execution -> two trades
executions = [
    {"side": "buy",  "qty": 10, "price": "100.00", "fee": "0.00", "ts": "T0"},
    {"side": "sell", "qty": 15, "price": "105.00", "fee": "0.00", "ts": "T1"},
]
expected_trades = [
    {"side": "long",  "qty": 10, "entry_price": "100.00", "exit_price": "105.00",
     "net_pnl": "50.00", "status": "closed", "closed_at": "T1"},
    {"side": "short", "qty": 5,  "entry_price": "105.00", "exit_price": None,
     "net_pnl": None, "status": "open", "opened_at": "T1"},
]

# Vector 4: open trade (no closing execution yet)
executions = [
    {"side": "buy", "qty": 5, "price": "20.00", "fee": "0.50", "ts": "T0"},
]
expected_trade = {"side": "long", "qty": 5, "net_pnl": None, "status": "open"}

# Vector 5: win-rate tie-break (§2)
trades_net_pnl = ["50.00", "-30.00", "0.00", "10.00", "-10.00", "0.00", "5.00"]
expected = {"wins": 3, "losses": 2, "breakeven": 2, "win_rate": "60.00", "sample_size": 5}

# Vector 6: R from stop_price, single-entry (§3). trade currency USD
trade = {"side": "long", "entry_lots": [{"price": "50.00", "qty": 100}], "multiplier": "1",
         "currency": "USD", "exit_price": "51.20", "fees": "2.00",
         "journal": {"stop_price": "49.50", "planned_risk_amount": None, "risk_currency": None}}
expected = {"risk_amount": "50.00", "net_pnl": "118.00", "r_multiple": "2.3600", "r_reason": None}

# Vector 7: no risk input -> excluded, not 0
trade = {**trade, "journal": {"stop_price": None, "planned_risk_amount": None, "risk_currency": None}}
expected = {"r_multiple": None, "r_reason": "no_risk_input"}

# Vector 8: R from planned_risk_amount only
trade = {**trade, "journal": {"stop_price": None, "planned_risk_amount": "59.00", "risk_currency": "USD"}}
expected = {"risk_amount": "59.00", "r_multiple": "2.0000", "r_reason": None}

# Vector 9: both set -> planned_risk_amount wins (stop would give 2.3600)
trade = {**trade, "journal": {"stop_price": "49.50", "planned_risk_amount": "59.00", "risk_currency": "USD"}}
expected = {"risk_amount": "59.00", "r_multiple": "2.0000", "r_reason": None}

# Vector 10: multi-leg (Vector 2 executions, net_pnl 69.00)
trade = {"side": "long", "entry_lots": [{"price": "10.00", "qty": 100}, {"price": "10.20", "qty": 50}],
         "multiplier": "1", "currency": "USD", "net_pnl": "69.00"}
# 10a: stop only -> null
journal = {"stop_price": "9.80", "planned_risk_amount": None, "risk_currency": None}
expected = {"r_multiple": None, "r_reason": "stop_price_multi_leg"}
# 10b: planned risk -> valid
journal = {"stop_price": None, "planned_risk_amount": "30.00", "risk_currency": "USD"}
expected = {"risk_amount": "30.00", "r_multiple": "2.3000", "r_reason": None}

# Vector 11: currency mismatch -> null, no fallback to stop_price (Vector 6 trade, USD)
journal = {"stop_price": "49.50", "planned_risk_amount": "59.00", "risk_currency": "EUR"}
expected = {"r_multiple": None, "r_reason": "risk_currency_mismatch"}

# Vector 12: short, stop-derived. 20 @ 50.00, stop 50.50, exit 48.00, fees 1.00
trade = {"side": "short", "entry_lots": [{"price": "50.00", "qty": 20}], "multiplier": "1",
         "currency": "USD", "exit_price": "48.00", "fees": "1.00",
         "journal": {"stop_price": "50.50", "planned_risk_amount": None, "risk_currency": None}}
expected = {"risk_amount": "10.00", "net_pnl": "39.00", "r_multiple": "3.9000"}

# Vector 13: futures multiplier (Topstep T1 shape: 2 CL, mult 1000), stop-derived
trade = {"side": "long", "entry_lots": [{"price": "80.00", "qty": 2}], "multiplier": "1000",
         "currency": "USD", "exit_price": "80.15", "fees": "8.04",
         "journal": {"stop_price": "79.90", "planned_risk_amount": None, "risk_currency": None}}
expected = {"risk_amount": "200.00", "net_pnl": "291.96", "r_multiple": "1.4598"}

# Vector 14: stop on the profit side / equal to entry -> null (form also rejects)
trade = {**trade, "journal": {"stop_price": "80.00", "planned_risk_amount": None, "risk_currency": None}}
expected = {"r_multiple": None, "r_reason": "stop_not_a_risk"}

# Vector 15: losing trade gives negative R. Vector 6 shape, exit 49.50, fees 2.00, stop 49.50
expected = {"net_pnl": "-52.00", "risk_amount": "50.00", "r_multiple": "-1.0400"}

# Vector 16: open trade -> null
expected = {"r_multiple": None, "r_reason": "trade_open"}

# Vector 17: average R over a filtered set: R values [2.3600, null, -1.0400, 2.0000]
expected = {"avg_r": "1.11", "n": 3}   # (2.36 - 1.04 + 2.00) / 3 = 1.1067 -> 1.11

# Vector 18: single-entry with partial exits: stop check uses the one entry lot (§3)
# Long 100 @ 50.00, stop 49.50. Sell 40 @ 51.00, sell 60 @ 50.60. Fees 2.00 total. USD.
trade = {"side": "long", "entry_lots": [{"price": "50.00", "qty": 100}], "multiplier": "1",
         "currency": "USD", "net_pnl": "74.00",   # 40*1.00 + 60*0.60 = 76.00, minus 2.00
         "journal": {"stop_price": "49.50", "planned_risk_amount": None, "risk_currency": None}}
expected = {"risk_amount": "50.00", "r_multiple": "1.4800", "r_reason": None}   # 74/50

# Vector 19: multi-leg, stop only, stop on the "profit side" of both entries -> still
# stop_price_multi_leg, never stop_not_a_risk (Vector 10 trade, net 69.00)
journal = {"stop_price": "11.00", "planned_risk_amount": None, "risk_currency": None}
expected = {"r_multiple": None, "r_reason": "stop_price_multi_leg"}

# Vector 20: multi-leg, both set, stop unusable -> planned wins, stop ignored, no check
journal = {"stop_price": "11.00", "planned_risk_amount": "30.00", "risk_currency": "USD"}
expected = {"risk_amount": "30.00", "r_multiple": "2.3000", "r_reason": None}

# Vector 21: risk_currency derivation on save (non-USD trade). Trade currency EUR.
save_input = {"planned_risk_amount": "40.00"}            # no currency field on the form
expected_stored = {"planned_risk_amount": "40.00", "risk_currency": "EUR"}
save_input = {"planned_risk_amount": None}               # cleared
expected_stored = {"planned_risk_amount": None, "risk_currency": None}

# Vector 22: stored currency no longer matches the trade (re-import changed it to EUR)
journal = {"stop_price": None, "planned_risk_amount": "59.00", "risk_currency": "USD"}
trade_currency = "EUR"
expected = {"r_multiple": None, "r_reason": "risk_currency_mismatch"}

# Vector 23: Avg R with 4 closed trades in one group, 2 with risk (hand-computed)
#   T1 net 20.00, planned 100.00 USD -> R = 0.2000
#   T2 net  5.00, planned 100.00 USD -> R = 0.0500
#   T3 closed, no risk input          -> null (no_risk_input)
#   T4 closed, no risk input          -> null (no_risk_input)
# mean = (0.2000 + 0.0500) / 2 = 0.1250 exact tie at 2 dp -> ROUND_HALF_EVEN -> 0.12
# (ROUND_HALF_UP would give 0.13: an implementation using it fails this vector.)
# T3/T4 are NOT counted as 0: counting them would give 0.0625 -> 0.06.
expected = {"avg_r": "0.12", "n": 2, "left_out": 2}
# Add an open trade T5 with planned risk 100.00 USD: expected unchanged (open is in neither n nor left_out).
```

### Manual-entry vectors 24-34 (hand-computed; rules in §7)

Notation: fills are `(id, side, qty, price, fee, ts, mult, key)`. `K_A`, `K_B` are placeholders for
the per-trade pairing key, in the format `manual:<32 hex>` (ADR-0008 §1); each is shared by the
entry and exit fill of one manual trade and by nothing else. The tests use any two distinct
values of that format (for example `manual:` + `uuid4().hex`); no vector depends on the actual
hex digits. All fills in a vector have the same `broker` (the manual source), so adding `broker`
to the bucket key changes no vector and no expected value. Symbols `AAPL`, `TSLA`, `ZZZH7` are invented; `ZZZ` is a root
that never enters the multiplier table. Currency `USD`, label `''`. Net = gross - fees, rounded
once to 2 dp `ROUND_HALF_EVEN`; R to 4 dp.

```python
# Vector 24: open manual long, then the same trade closed with "Close this trade".
fills = [(101, "buy", 10, "100.00", "0", "2026-03-02T14:30Z", 1, "K_A")]            # AAPL
expected = {"status": "open", "side": "long", "qty": 10, "entry_price": "100.00",
            "exit_price": None, "gross_pnl": None, "fees": None, "net_pnl": None,
            "entry_lot_count": 1, "opening_execution_id": 101,
            "r_reason": "trade_open"}                    # in no stat card
fills += [(102, "sell", 10, "105.00", "2.00", "2026-03-02T15:00Z", 1, "K_A")]       # close
expected = {"status": "closed", "opening_execution_id": 101,   # unchanged: journal URL survives
            "gross_pnl": "50.00", "fees": "2.00", "net_pnl": "48.00"}   # = vector 1 net
# With journal stop 99.00: risk = |100.00-99.00| * 10 * 1 = 10.00, R = 48.00/10.00 = 4.8000.

# Vector 25: closed manual long, all fees on the exit fill (= vector 1 net 48.00; AC 5).
fills = [(1, "buy", 10, "100.00", "0.00", "T0", 1, "K_A"), (2, "sell", 10, "105.00", "2.00", "T1", 1, "K_A")]
expected = {"gross_pnl": "50.00", "fees": "2.00", "net_pnl": "48.00", "status": "closed"}

# Vector 26: closed manual short with a stop. TSLA, side=Short: entry is the SELL fill.
fills = [(1, "sell", 5, "200.00", "0.00", "T0", 1, "K_A"), (2, "buy", 5, "195.50", "1.25", "T1", 1, "K_A")]
# gross = (195.50 - 200.00) * 5 * (-1) * 1 = +22.50 ; net = 22.50 - 1.25 = 21.25
journal = {"stop_price": "202.00"}   # side-check: (200.00 - 202.00) * (-1) = +2.00 > 0, valid
# risk = |200.00 - 202.00| * 5 * 1 = 10.00 ; R = 21.25 / 10.00 = 2.1250
expected = {"side": "short", "gross_pnl": "22.50", "fees": "1.25", "net_pnl": "21.25",
            "risk_amount": "10.00", "r_multiple": "2.1250"}

# Vector 27: unknown root, user-typed point value (AC 17). ZZZH7 -> root "ZZZ" (not in MULTIPLIERS),
# so the form requires the Point value; the user types 25 (an invented value for this vector).
fills = [(1, "buy", 2, "5000.00", "0.00", "T0", 25, "K_A"), (2, "sell", 2, "5002.50", "4.20", "T1", 25, "K_A")]
# gross = (5002.50 - 5000.00) * 2 * 1 * 25 = 125.00 ; net = 125.00 - 4.20 = 120.80
journal = {"stop_price": "4995.00"}  # risk = 5.00 * 2 * 25 = 250.00 ; R = 120.80 / 250.00 = 0.4832
expected = {"gross_pnl": "125.00", "net_pnl": "120.80", "risk_amount": "250.00", "r_multiple": "0.4832"}
# 27b: known root ignores a posted value. CLZ6 Long 2, 80.00 -> 80.15, fees 8.04, tampered POST
# point_value=1. Stored mult must be 1000 (lookup wins), so net = T1 = 291.96, not 0.30 - 8.04 = -7.74.

# Vector 28: fee rounding. Fees accept 4 dp; net rounds once, half-even (AC 28).
fills = [(1, "buy", 10, "100.00", "0", "T0", 1, "K_A"), (2, "sell", 10, "105.00", "1.015", "T1", 1, "K_A")]
# gross 50.00 ; net = 50.00 - 1.015 = 48.985 -> tie -> HALF_EVEN 48.98 (HALF_UP would give 48.99)
expected = {"gross_pnl": "50.00", "fees": "1.02", "net_pnl": "48.98"}   # fees 1.015 -> 1.02 (even)

# Vector 29: edit in place (typo fixes). Start = vector 25 shape + journal stop 99.00:
#   net 48.00, risk = 1.00 * 10 = 10.00, R = 4.8000. Fill ids 1 and 2, journal attached to 1.
# 29a: entry price typed wrong, fix entry 100.00 -> 100.50, exit fee 2.00 -> 2.50. Same ids.
#   gross = (105.00 - 100.50) * 10 = 45.00 ; net = 45.00 - 2.50 = 42.50
#   risk = |100.50 - 99.00| * 10 = 15.00 ; R = 42.50 / 15.00 = 2.8333 (2.83333.. -> 4 dp)
expected = {"opening_execution_id": 1, "fill_count": 2, "gross_pnl": "45.00", "net_pnl": "42.50",
            "r_multiple": "2.8333"}
# 29b: then entry 100.50 -> 98.00 with the stop still 99.00 (long: stop above entry).
#   gross = 7.00 * 10 = 70.00 ; net = 70.00 - 2.50 = 67.50 ; side-check (98.00-99.00)*1 = -1.00 <= 0
expected = {"net_pnl": "67.50", "r_multiple": None, "r_reason": "stop_not_a_risk"}
# The stored stop and the journal text are untouched either way (refuse-at-edit vs left-out is ADR-0008).

# Vector 30: two open manual longs, same symbol and label, own keys (AC 23). AAPL.
fills = [(1, "buy", 10, "100.00", "0", "2026-03-02T14:30Z", 1, "K_A"),
         (2, "buy",  5, "101.00", "0", "2026-03-02T15:00Z", 1, "K_B")]
expected = [{"opening": 1, "status": "open", "qty": 10, "entry_lot_count": 1},
            {"opening": 2, "status": "open", "qty": 5,  "entry_lot_count": 1}]     # two trades
# Close only B: sell 5 @ 103.00, fee 1.00, key K_B, at 16:00Z.
fills += [(3, "sell", 5, "103.00", "1.00", "2026-03-02T16:00Z", 1, "K_B")]
expected = [{"opening": 1, "status": "open", "qty": 10},                            # untouched
            {"opening": 2, "status": "closed", "gross_pnl": "10.00", "fees": "1.00", "net_pnl": "9.00"}]
# gross B = (103.00 - 101.00) * 5 = 10.00. Companion (what NULL keys would do, and why it is wrong):
# all keys None -> ONE open trade, qty 15, entry_lot_count 2, the sell consumed 5 of lot A
# (103.00 - 100.00 = 3.00 * 5), nothing realized, net_pnl None. Manual entry must not do this.

# Vector 31: manual trade and an imported trade on the same symbol and label never pair (AC 23).
# Imported: T1 (CLZ6, key "9000000001", net 291.96). Manual, label '' like T1: CLZ6, Long 1,
# entry 80.00 -> exit 80.10, fees 3.00, mult 1000 (known root), keys K_A.
# manual gross = 0.10 * 1 * 1000 = 100.00 ; net = 97.00
expected = [{"net_pnl": "291.96", "unchanged": True}, {"net_pnl": "97.00"}]
expected_stats = {"total_pnl": "388.96", "wins": 2, "losses": 0}   # 291.96 + 97.00

# Vector 32: time order inside one manual trade (AC 8; rule §7.1).
# 32a: equal times are allowed. Entry id 1, exit id 2, both 2026-03-02T14:30Z; Long 10, 100.00 -> 100.50.
expected = {"side": "long", "opening_execution_id": 1, "gross_pnl": "5.00", "status": "closed"}
# 32b: exit time before entry time is REJECTED by the form. If it were saved, the matcher would sort
# the exit (sell 105.00 at 14:29Z, id 2) before the entry (buy 100.00 at 14:30Z, id 1): the sell opens
# a SHORT, so side=short, opening_execution_id=2 (journal URL changes, entry orphaned), gross
# = (100.00 - 105.00) * 10 * (-1) = +50.00 (the amount happens to match), and a long stop 99.00
# would be checked as a short stop: (105.00 - 99.00) * (-1) = -6.00 -> stop_not_a_risk.

# Vector 33: guard against a one-leg edit. Invariant (§7.4): both fills always agree on qty, symbol,
# label, mult and key. Start: buy 10 @ 100.00 (id 1, K_A), sell 10 @ 105.00 (id 2, K_A).
# 33a: only the entry qty is changed to 20 -> sell 10 closes half: trade stays OPEN (qty 20), net None.
# 33b: only the exit qty is changed to 15 -> closes 10, leftover 5 opens a SHORT in the same bucket:
#      closed long net 50.00 PLUS an open short qty 5 @ 105.00 (vector 3's flip). A two-fill edit
#      must write both legs or neither, in one transaction.

# Vector 34: local time -> UTC for typed times (America/New_York; rules §7.2). "reject" = form error.
cases = {
  "2026-03-02 09:30": "2026-03-02T14:30Z",   # EST, UTC-5 (AC 20)
  "2026-03-09 09:30": "2026-03-09T13:30Z",   # EDT, UTC-4, day after the spring jump
  "2026-03-08 01:59": "2026-03-08T06:59Z",   # last valid minute before the gap
  "2026-03-08 02:00": "reject (nonexistent)", "2026-03-08 02:30": "reject (nonexistent)",
  "2026-03-08 02:59": "reject (nonexistent)",
  "2026-03-08 03:00": "2026-03-08T07:00Z",   # first valid minute after the gap
  "2026-11-01 00:59": "2026-11-01T04:59Z",   # EDT, before the overlap
  "2026-11-01 01:00": "reject (ambiguous)",  "2026-11-01 01:30": "reject (ambiguous: 05:30Z or 06:30Z)",
  "2026-11-01 01:59": "reject (ambiguous)",
  "2026-11-01 02:00": "2026-11-01T07:00Z",   # EST, UTC-5, first unambiguous minute after
}
# Exit vs entry is compared in UTC. Entry 2026-03-08 01:45 (06:45Z), exit 03:15 (07:15Z): 30 min, valid
# (wall-clock difference reads 1h30). Entry 2026-11-01 00:45 (04:45Z), exit 02:15 (07:15Z): 2h30, valid.
# Equal UTC instants save. Both facts verified against Django's from_current_timezone (zoneinfo).
```

## 6. Open items (need verification, flagged rather than guessed)

- Futures contract multiplier table (per symbol root) — does not exist yet; blocks trustworthy
  futures P&L if Topstep's export gives point prices rather than a precomputed dollar P&L column.
  See `docs/domain/topstep-import.md`.
- Same-timestamp fill ordering assumes the broker export is already correctly ordered; unverified
  against a real Topstep export.

---

## 7. Manual entry (rulings 2026-10-13; spec `docs/product/features/manual-entry.md`)

A manual trade is **one entry fill plus at most one full-size exit fill**, own pairing key (§1).
No scale-in, partial close, or flip exists for manual trades; the matcher is unchanged. Vectors
24-34 (§5) are the reference.

### 7.1 Exit before entry (AC 8): reject

- **Rule:** exit time earlier than entry time is a form error on the exit time; equal times are
  allowed (matches the importer ruling of 2026-09-27). Compared as UTC instants, after the local
  times are converted (§7.2). Applies to add, close, and edit (moving the entry later than an
  existing exit, or the exit earlier than the entry).
- **Why not "accept as a short":** direction comes from the Side the user chose, not from time
  order. The matcher orders fills by `executed_at`, so an early exit fill would sort first and
  become the opener: the trade silently flips side, its id (opening fill pk) changes so the
  journal URL moves and the journal entry is orphaned, and stop checks run against the wrong
  side (vector 32b). A wrong time zone or a typo is by far the likelier cause than an intended
  reversal, and a real short is entered by choosing Short.
- **Equal times:** order falls to the pk tie-break (§1), so the entry fill must have the lower pk.
  Add-closed must insert the entry fill before the exit fill; a later Close always has a higher
  pk. Edit never changes pks. (Requirement for ADR-0008, item 6.)

### 7.2 Local time entry, DST gap and overlap (AC 21): reject both, never pick one

Entry and exit are typed as local wall-clock time in the user's saved zone (`User.timezone`)
and stored as UTC. Resolution of a typed time:

| Typed local time in the zone | Result |
|---|---|
| Exactly one UTC instant | Convert and store. |
| **Nonexistent** (inside a spring-forward gap) | Reject: "that time does not exist on that date in your time zone" (copy: `ux-designer`). |
| **Ambiguous** (inside a fall-back overlap, occurs twice) | Reject: same family of message, asks the user to check the date or time. |

Boundaries (America/New_York 2026; the rule is "detect by round trip, not by the hour", so zones
with other transition times, or 30-minute shifts, behave the same):

- **Gap**, 2026-03-08, clocks jump 02:00 EST to 03:00 EDT: `02:00` to `02:59` do not exist
  (example `02:30` is rejected). `01:59` is valid (06:59Z), `03:00` is valid (07:00Z).
- **Overlap**, 2026-11-01, clocks fall 02:00 EDT to 01:00 EST: `01:00` to `01:59` happen twice
  (example `01:30` is either 05:30Z or 06:30Z) and are rejected. `00:59` is valid (04:59Z),
  `02:00` is valid (07:00Z, EST only).

Why reject rather than pick (`fold=0`/first occurrence): a silent default can be an hour wrong,
which shifts durations and time-of-day data that later behavior features read, and the user
cannot see it. Why this is cheap: both hours fall early on a Sunday morning, when CME futures,
US equities and spot FX are closed, so mainly 24/7 crypto traders can hit it. **Cost, stated:**
such a trader cannot record a trade inside that hour in local time (workaround: the nearest
valid minute). Follow-up if a 24/7 user hits it: an explicit "first/second occurrence" choice
shown only for an ambiguous time (UI work, deliberately not built now).

Implementation facts (verified 2026-10-13 against Django 5.2's `from_current_timezone`, zoneinfo):
with the user's zone **activated**, a plain `forms.DateTimeField` already raises on both a
nonexistent and an ambiguous time, and returns the right UTC instant for the boundary minutes in
the table above. It does nothing useful if the server zone, not the user's zone, is active, so
the form must activate `User.timezone`. A naive datetime outside Django's behavior (own
`zoneinfo` code) must round-trip: a local time is nonexistent if converting to UTC and back
changes it, and ambiguous if the offsets for `fold=0` and `fold=1` differ (test the first
condition first; a gap also differs by fold).

**Edit:** a stored UTC time is shown as local time in the user's zone (§5 vector 34 inverse). If
the user later changed `User.timezone`, a stored instant can render as a time that is now
ambiguous or nonexistent. An **unchanged** time field must keep the stored UTC value and not be
re-parsed; only a changed field is parsed and checked. (Compare posted text with the rendered text
of the stored value.) Needs a decision by `architect`/`backend-engineer`; flagged in the report.

Validation order for a time field (so each form shows one error per field): required, parse,
DST gap/overlap, not in the future (user ruling), then exit >= entry (§7.1).

### 7.3 Fees

- **All fees go on the exit fill; the entry fill is always 0**, as in imports
  (`topstep-import.md` §5). Splitting fees across the two legs would not change `net_pnl` with one
  entry lot and one full-size exit (same argument as the Topstep note).
- **Open trade:** no exit fill, so no fee exists and none is offered. The matcher returns
  `fees = None`, `net_pnl = None` while open (vector 24). A fee posted for an open trade is
  rejected, never dropped.
- **Close later:** the Fees value on the Close form is written to the new exit fill. Blank = 0.
- **Edit of a closed trade:** Fees change only the exit fill's `fees`; entry stays 0 (vector 29a).
- **Edit of an open trade:** no fees exist. If the edit form is allowed to add an exit to an open
  trade, that is a Close in effect (a new exit fill with the fee); the product spec should say
  whether edit may do that or the user must use Close (AC 38 says edit adds no row).
- Fees are 0 or more (user ruling), at most 4 dp. Net rounds once at the trade level, half-even
  (vector 28). The fee is a total in the trade currency (USD); the app does not convert fees
  charged in another asset.

### 7.4 Edit invariants (one transaction, both fills or neither)

Both fills of a closed manual trade always agree on symbol, account label, currency, point value
(`contract_multiplier`), quantity, and pairing key, and the exit side is the opposite of the entry
side. An edit of any of these writes both fills. Writing only one leg breaks the shape: a larger
entry quantity leaves the trade open, a larger exit quantity flips and opens a second trade
(vector 33). Changing Side swaps both fills' sides. Prices and times are per fill. Imported fills
are never edited. A closed trade is not reopened by blanking its exit (user ruling).

### 7.5 Stop and planned risk: no difference for manual trades

Same §3 rules, same precedence, same reason codes. A manual trade has one entry lot, so a stop is
always a valid R input and `stop_price_multi_leg` cannot occur. `risk_currency` is derived and is
always USD (currency is fixed). Stop side-check after an edit: the entry price can move to the
wrong side of a stored stop; then `r_multiple` returns `stop_not_a_risk` (vector 29b). Whether
the edit is refused instead is ADR-0008's call; no domain rule changes either way. The form has
no stop input (the Journal control owns it), so manual and imported trades take R inputs the same
way.

### 7.6 Point value, forex, crypto

- Known root: the importer's `MULTIPLIERS` value, read-only, stored on both fills. Unknown root,
  including no root (`AAPL`): the user types it; equities type 1. The value is the user's
  statement and is not validated against any contract spec (vector 27).
- **Forex:** enter the quantity in units of the base currency with point value 1. A lot-based
  entry gives the same P&L (`(exit - entry) * qty * point_value`): 1 standard lot is 100,000 units,
  so quantity 1 with point value 100000 equals quantity 100000 with point value 1. Pips and
  swap/rollover are not modelled.
- **Crypto:** quantity in base-asset units (fractional up to 10 dp), point value 1. Fees are
  entered as one total; a fee charged in the base asset or a third token is the user's to convert.
- **Open issue (needs a user or PM ruling, not decided here):** P&L from `(exit - entry) * qty *
  point_value` is in the instrument's quote currency. With currency fixed to USD, it is correct
  only for USD-quoted instruments (EURUSD, GBPUSD, BTCUSD). For USDJPY, EURGBP, a USDT-quoted
  pair (USDT is not USD), or a non-US equity, the amount would be stored and summed as USD
  though it is in another currency. This is the real content of the currency deferral
  (follow-ups row 45); until it is ruled, the UI should not imply that any symbol is USD-quoted.

### 7.7 Verified multiplier roots

Only `CL` and `MCL` are verified. Which roots to verify next, with no values added anywhere, is
listed in `topstep-import.md` §7 for the user to verify against a real export.
