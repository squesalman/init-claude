# P&L, fill→trade matching, win rate, and R-multiple

- **Status:** accepted for MVP implementation
- **Answers:** `docs/product/features/mvp.md` open questions 1, 3, 4
- **Owner:** `trading-domain-expert`
- **Consumers:** `architect` (ADR-0003 data model), `database-engineer`, `backend-engineer`, `qa-engineer`

All amounts below use `Decimal`, never float, per `CLAUDE.md` and ADR-0002. Money is
`NUMERIC(19,4)` + currency; price/qty are `NUMERIC(20,10)`.

---

## 1. Fill → trade matching algorithm

**Method: FIFO, per user + account + instrument (+ `broker_trade_id` bucket, see below).** Not configurable in MVP (LIFO/average-cost
are a v2 toggle at most, and only if a user asks — do not build the switch speculatively).

**Bucket by `broker_trade_id` (ADR-0004 §3).** When a broker reports closed round-trips (Topstep),
the importer sets `broker_trade_id` on both legs of a row, and FIFO runs unchanged inside each
`(user, account, instrument, broker_trade_id)` bucket, so each row is exactly one trade. Executions
with `broker_trade_id = NULL` (manual entry, fill-only brokers) share one bucket per `(user,
account, instrument)` and get the plain FIFO below, including merging nested same-direction
lifecycles into one flat-to-flat trade. Everything in this doc after this paragraph describes FIFO
within one bucket.

### Model

- An **execution** (fill) is immutable: `(id, user_id, account_id, instrument, side [buy/sell],
  qty, price, fee, currency, executed_at_utc, broker, broker_execution_id)`.
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
  the order the broker export lists them, then by `broker_execution_id` ascending as tiebreak.
  Needs revisiting if a broker's export is provably unordered.
- **Multi-account:** matching is scoped per `(user, account, instrument)`. MVP doesn't have an
  explicit multi-account UI, but the matching key includes `account` from day one so it's not a
  retrofit — if Topstep exports one account per file this is a no-op.
- **Corrections/cancels after import:** out of MVP scope (story 2 excludes post-hoc execution
  editing beyond simple manual-entry correction); re-running the parser against retained raw
  rows (per `CLAUDE.md`) is the only supported fix path if a fill was wrong.

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
- **Which imported trades get R:** every Topstep row is one trade with one entry lot (ADR-0004 §3,
  bucketed by `broker_trade_id`), so both inputs work on Topstep imports. Multi-leg trades come
  only from merged FIFO, which means executions with `broker_trade_id = NULL` (manual entries that
  scale in, or a future fill-only importer). For those, only `planned_risk_amount` works.

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
contract multiplier (e.g., ES = $50/point, MES = $5/point) that is not derivable from a fill row
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
# Vector 1: simple single-entry/single-exit (manual entry style)
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
```

## 6. Open items (need verification, flagged rather than guessed)

- Futures contract multiplier table (per symbol root) — does not exist yet; blocks trustworthy
  futures P&L if Topstep's export gives point prices rather than a precomputed dollar P&L column.
  See `docs/domain/topstep-import.md`.
- Same-timestamp fill ordering assumes the broker export is already correctly ordered; unverified
  against a real Topstep export.
