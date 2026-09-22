# Topstep CSV import

- **Status:** resolved — verified against a real export (`all_trades_export.csv`, 177 data rows,
  TopstepX "Trades" tab CSV download, 2026-09 dates). Column mapping and formulas below are
  confirmed, not guessed. Contents of that file are **not** reproduced here (real user data); all
  worked examples use invented numbers that follow the same confirmed formulas.
- **Answers:** `docs/product/features/mvp.md` open question 2. Closes the open item from the
  previous version of this doc.
- **Owner:** `trading-domain-expert`
- **Consumers:** `backend-engineer` (importer), `database-engineer` (raw-row storage + dedupe
  index — no schema change needed, see §5), `architect` (ADR-0003 correction, see §5)

## 1. Format identified

This is **TopstepX's own closed-trade export**, not a Tradovate/Rithmic raw fill/order log. Header
(verbatim column names, UTF-8 with a leading BOM — strip it before parsing the first header cell):

```
Id,ContractName,EnteredAt,ExitedAt,EntryPrice,ExitPrice,Fees,PnL,Size,Type,TradeDay,TradeDuration,Commissions
```

**One row = one already-closed round-trip trade**, not a fill. There is no separate entry-row/
exit-row pair, no order id, no partial-fill breakdown. This confirms the "closed-trade shape"
this doc previously called "plausible" and rules out the raw-fill shape for this export. If a user
later supplies a different Topstep-adjacent export (e.g. a raw Rithmic order log from a non-topstep
front end), treat it as a **different format requiring its own header-detection branch** — do not
assume this mapping applies.

### Column meanings (confirmed by arithmetic cross-check against the sample, §3)

| Column | Meaning | Notes |
|---|---|---|
| `Id` | TopstepX's internal trade id | Large monotonically-increasing integer, correlates with chronological order across the file → looks like a database auto-increment PK, not a row number. Used as dedupe key (§4). |
| `ContractName` | Futures symbol, root + month code + 1-digit year | e.g. `CLV6` = CL, Oct (V), 2026 (6); `MCLV6` = MCL, Oct, 2026. Root = strip the trailing `[A-Z][0-9]`. **Caveat:** this file only has single-digit years (2026); a contract spanning 2020–2029 vs 2030s could be ambiguous with a 1-digit year scheme if TopstepX ever exports across a decade boundary — unverified, flag if it comes up. |
| `EnteredAt` / `ExitedAt` | Entry/exit fill timestamps, **with explicit UTC offset per row** (e.g. `+08:00`) | Trust the row's own offset for UTC conversion; do not assume it matches `user.timezone` — this looks like the trader's TopstepX display-timezone setting, which can differ from the app's per-user display timezone. Convert to UTC directly from the offset given. |
| `EntryPrice` / `ExitPrice` | Points, not dollars | Needs `contract_multiplier` to become money — see §3. |
| `Fees` | One component of round-trip cost | **Not** the same as `Commissions` — two separate columns, both need subtracting (§3). Scales linearly with `Size` (confirmed: 2-lot and 3-lot rows show exactly 2× / 3× the 1-lot per-contract rate for the same contract), so it's a total-for-the-row figure, not a flat per-trade minimum. |
| `PnL` | **Gross** P&L in dollars, already multiplier-adjusted | Confirmed by re-deriving it from `(ExitPrice − EntryPrice) × Size × multiplier × side_sign` across multiple rows (§3) — matches exactly, to the cent, before subtracting `Fees`/`Commissions`. This is gross, not net. |
| `Size` | Contracts, integer | Values 1–3 seen in the sample; treat as unbounded positive integer. |
| `Type` | `Long` / `Short` | This is trade direction, already resolved by the platform — not a fill-level buy/sell. Maps to two synthetic execution sides (§5). |
| `TradeDay` | Exchange trading-day bucket (5pm CT futures session rollover), with its own separate offset (e.g. `-05:00`, CME's home timezone, DST-adjusted) | Distinct from `EnteredAt`'s calendar day — a trade entered late evening can belong to the next session's `TradeDay`. Not needed for MVP (no day-based aggregation feature yet); retain in the raw row only. |
| `TradeDuration` | `HH:MM:SS.fffffff` | Redundant with `ExitedAt − EnteredAt`. Ignore for parsing; raw row retains it. |
| `Commissions` | The other component of round-trip cost | See `Fees` above. |

No account/broker-account column exists in this export at all. ADR-0003's assumption 6
(`broker_account_label` defaults to empty string when absent) already covers this — confirmed
correct, nothing to fix.

No currency column exists. CME/NYMEX futures (this sample is all crude oil: `CL`, `MCL`) are
USD-denominated — hardcode `currency = 'USD'` for Topstep imports. Flag if a Topstep export with
a non-USD-denominated contract ever surfaces; unverified for that case.

## 2. Contract multiplier — verified values

Two roots appear in the sample, and the multiplier table entries below are now **arithmetically
verified** against real `PnL` values (§3 shows the method), not looked up from a spec sheet:

| Root | Instrument | Multiplier ($ per point) | Confidence |
|---|---|---|---|
| `CL` | WTI Crude Oil (full-size) | **1000** | Verified against this export |
| `MCL` | Micro WTI Crude Oil | **100** | Verified against this export |

The other entries in `pnl-and-matching.md` §4's illustrative list (`ES`→50, `MES`→5, `NQ`→20,
`MNQ`→2, `GC`→100) are **still unverified** — they were never checked against a real export and
should not be trusted until a sample containing those roots is seen. Extraction rule: root =
`ContractName` with the trailing month-code letter + 1 year digit stripped (`CLV6` → `CL`).

Recommended importer safeguard, now that we know the `PnL` column is independently reconstructable:
**when both a multiplier-table entry and a `PnL` column exist, compute
`(ExitPrice − EntryPrice) × Size × multiplier × side_sign` and compare it to `PnL`.** A mismatch
means either the multiplier table is missing/wrong for that root or something unexpected is in the
row — surface it as an import warning rather than silently trusting either number. This is cheap
because it's exactly the computation `derive_trades()` already does (§5).

## 3. Fees, Commissions, and PnL — how they combine (verified arithmetically)

`Fees` and `Commissions` are **two distinct columns in this export**, not a pre-summed total, and
`PnL` is **gross** (confirmed: reconstructing it from prices × multiplier × size matches exactly,
to the cent, on every row checked — a plain `Short` example: price moves against the short by X
points → `PnL = -X × multiplier × Size`, no fee deduction present in that number).

```
net_pnl = PnL - Fees - Commissions
```

### Worked example (invented numbers, same shape as the real export, root = CL)

| Field | Value |
|---|---|
| ContractName | `CLZ6` (invented month) |
| EntryPrice | 80.00 |
| ExitPrice | 80.15 |
| Type | Long |
| Size | 2 |
| Fees | 6.04 |
| Commissions | 2.00 |

```
gross_pnl = (80.15 - 80.00) * 1000 * 2 = 300.00   -- matches what "PnL" would carry
net_pnl   = 300.00 - 6.04 - 2.00 = 291.96
```

## 4. Dedupe key

**`Id` is the dedupe key: `(broker='topstep', broker_execution_id=<Id>)`.** This supersedes the
"no stable id" concern in this doc's previous version — TopstepX's `Id` looks like a real database
primary key (large, monotonically increasing, correlates with chronological trade order across the
whole file, no duplicates in the 177-row sample). Since a Topstep import needs two synthesized
executions per row (§5), the actual dedupe keys stored are `Id:entry` and `Id:exit`.

**Caveat, stated rather than hidden:** this is inferred from one export's structure, not proven
against a second re-export of the same date range. If a re-export of the same trades ever produces
different `Id` values, this key is wrong and the fallback composite key (below) is needed instead.
Recommend `backend-engineer`/QA confirm this by re-downloading the same date range twice before
relying on it in production; flag, don't block MVP on it.

**Fallback composite key** (only if `Id` stability is ever disproven):
`(broker='topstep', ContractName, EnteredAt, ExitedAt, EntryPrice, ExitPrice, Size, Type)`.

## 5. Import shape: synthesize two executions per row

Each closed-trade row becomes **two executions**, fed into the same `derive_trades()` FIFO matcher
that handles manual entry (per ADR-0003's one-code-path decision — confirmed correct, see §6):

| Execution | `broker_execution_id` | `side` | `quantity` | `price` | `executed_at` | `fee` |
|---|---|---|---|---|---|---|
| entry | `{Id}:entry` | `buy` if `Type=Long`, `sell` if `Type=Short` | `Size` | `EntryPrice` | `EnteredAt` → UTC | `0` |
| exit | `{Id}:exit` | `sell` if `Type=Long`, `buy` if `Type=Short` | `Size` | `ExitPrice` | `ExitedAt` → UTC | `Fees + Commissions` |

Both executions: `symbol = ContractName` (verbatim, uppercased — already is), `contract_multiplier`
= looked up by root (§2, default missing roots to **reject the row with an error**, not silently
`1` — a silent `1` on an unrecognized futures root produces a plausible-looking, badly wrong P&L,
which is worse than a failed import row), `currency = 'USD'`.

Putting the full `Fees + Commissions` on the exit leg (rather than splitting across entry/exit) is
a deliberate simplification: `pnl-and-matching.md` §1's fee allocation only matters when a lot is
later split across multiple partial closes, and every row here is already a fully closed,
single-entry/single-exit round trip (no scale-ins/partial closes are representable in this export
shape) — trade-level `net_pnl` sums `entry_fee + exit_fee` regardless of the split, so placement
doesn't change the number.
<!-- ponytail: fee split on one leg, not pro-rated — correct because these rows never partial-close
     against each other; revisit only if a future Topstep export shape shows partial fills. -->

Because each row is independently flat→flat (`Size` in, same `Size` out, nothing carries over to
the next row), running the generic FIFO matcher across a contract's synthesized executions in
timestamp order reproduces exactly one derived trade per source row — no cross-row lot bleed
expected, but this is a consequence of the export shape, not a new matcher rule.

## 6. Test vectors

Synthetic, not from the real file. Format matches `pnl-and-matching.md` §5.

```python
# Vector T1: Topstep closed-trade row, Long, full-size CL
row = {
    "Id": "9000000001", "ContractName": "CLZ6",
    "EnteredAt": "12/19/2026 09:00:00 +00:00", "ExitedAt": "12/19/2026 09:05:00 +00:00",
    "EntryPrice": "80.00", "ExitPrice": "80.15", "Fees": "6.04", "PnL": "300.00",
    "Size": "2", "Type": "Long", "Commissions": "2.00",
}
synthesized_executions = [
    {"broker_execution_id": "9000000001:entry", "side": "buy",  "qty": 2,
     "price": "80.00", "fee": "0.00", "executed_at": "2026-12-19T09:00:00Z"},
    {"broker_execution_id": "9000000001:exit",  "side": "sell", "qty": 2,
     "price": "80.15", "fee": "8.04", "executed_at": "2026-12-19T09:05:00Z"},
]
expected_trade = {
    "side": "long", "qty": 2, "entry_price": "80.00", "exit_price": "80.15",
    "gross_pnl": "300.00", "fees": "8.04", "net_pnl": "291.96", "status": "closed",
}

# Vector T2: Topstep closed-trade row, Short, micro CL (MCL, multiplier 100)
row = {
    "Id": "9000000002", "ContractName": "MCLZ6",
    "EnteredAt": "12/19/2026 10:00:00 +00:00", "ExitedAt": "12/19/2026 10:02:00 +00:00",
    "EntryPrice": "80.00", "ExitPrice": "79.80", "Fees": "1.02", "PnL": "20.00",
    "Size": "1", "Type": "Short", "Commissions": "0.50",
}
synthesized_executions = [
    {"broker_execution_id": "9000000002:entry", "side": "sell", "qty": 1,
     "price": "80.00", "fee": "0.00", "executed_at": "2026-12-19T10:00:00Z"},
    {"broker_execution_id": "9000000002:exit",  "side": "buy",  "qty": 1,
     "price": "79.80", "fee": "1.52", "executed_at": "2026-12-19T10:02:00Z"},
]
expected_trade = {
    "side": "short", "qty": 1, "entry_price": "80.00", "exit_price": "79.80",
    "gross_pnl": "20.00", "fees": "1.52", "net_pnl": "18.48", "status": "closed",
}

# Vector T3: multiplier-table miss — must reject, not default to 1
row = {"Id": "9000000003", "ContractName": "ZZZ99Z6", ...}  # root not in the multiplier table
expected = "import row failed: unknown contract root, status='failed' in journal_rawimportrow, no execution created"
```

## 7. Open items (still unverified — flagged, not guessed)

- `Id` stability across re-exports (§4) — inferred, not proven with a second download.
- Multiplier table entries for roots other than `CL`/`MCL` (`ES`, `MES`, `NQ`, `MNQ`, `GC`, …) — no
  sample seen yet, do not trust until verified.
- Non-USD Topstep contracts — none in the sample; `currency='USD'` is an assumption for this
  export, not proven for all Topstep accounts.
- Whether TopstepX ever exports a *raw fill* format for some account types (this doc only covers
  the closed-trade "Trades" tab export) — if a user later uploads a fill-level file, it needs its
  own header-detection branch, not this mapping.
