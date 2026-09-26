# Topstep CSV import

- **Status:** resolved — verified against a real export (`all_trades_export.csv`, 177 data rows,
  TopstepX "Trades" tab CSV download, 2026-09 dates). Column mapping and formulas below are
  confirmed, not guessed. Contents of that file are **not** reproduced here (real user data); all
  worked examples use invented numbers that follow the same confirmed formulas.
- **Answers:** `docs/product/features/mvp.md` open question 2. Closes the open item from the
  previous version of this doc.
- **Owner:** `trading-domain-expert`
- **Consumers:** `backend-engineer` (importer), `database-engineer` (dedupe index,
  `broker_trade_id`, `skipped_conflict` — see ADR-0004), `architect` (decided in
  `docs/adr/0004-topstep-dedupe-and-pairing.md`)

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

**Pairing (corrected, see ADR-0004 §3):** rows *can* overlap in time (§8 found 12 nested/overlapping
same-direction pairs), so plain FIFO across rows would merge them into fewer trades. Both
synthesized executions of a row get `broker_trade_id = Id`, and the matcher runs FIFO per
`(account label, symbol, broker_trade_id)` — so each row yields exactly one trade. Test vector T4
(ADR-0004) covers the nested case. Rows whose computed gross P&L disagrees with `PnL` are `failed`.

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

## 8. Amendment: multi-account dedupe key (proposed, for architect/database-engineer)

**Question:** is `(user_id, broker, broker_execution_id)` safe when one user imports two Topstep
accounts?

**Evidence (sample, 177 rows, checked by script; no file contents reproduced):**

- `Id` feeds `broker_execution_id` (as `{Id}:entry` / `{Id}:exit`, §5). All 177 are 10-digit integers,
  all distinct.
- The export has **no account column** (13 columns, listed in §1). So `broker_account_label` is `''`
  for every Topstep row today, and the sample cannot tell us how many accounts it holds. Id
  uniqueness *across accounts* is therefore **not verifiable from this file**.
- Ids look drawn from a large shared sequence: range 3,071,723,551 to 3,115,612,891 (span ~43.9M)
  over 177 rows in ~11 days. Median gap between consecutive own-trade ids is ~64k. A per-account
  counter would not skip that far; a platform-wide counter shared with other users would. This
  points to globally unique ids but does not prove it.
- Ids are strictly increasing by `ExitedAt` (0 inversions in 177 rows), 1 inversion by `EnteredAt`.
  So the id is assigned at trade close. (Refines §1's "correlates with chronological order".)

**Known vs unknown:**

| Claim | Status |
|---|---|
| Ids unique within the sample | Verified |
| Ids look platform-wide, not per-account | Inferred from gap size, not proven |
| TopstepX/ProjectX and Tradovate use platform-wide integer entity ids, with account as a separate field | General knowledge, unverified for this export |
| A second account's export shares no ids with the first | **Unknown** (needs a two-account export) |
| A re-export of the same range keeps the same ids | Unknown (§4 caveat stands) |

**Decision (confirmed in ADR-0004 §1–2; conflict rows get status `skipped_conflict`):** include `broker_account_label` in the dedupe key. Cost is near zero, and the two failure
modes are not symmetric. Excluding it risks silent loss of real fills (invisible, unrecoverable
P&L error). Including it risks visible duplicate rows, and only when the user types a different
label for the same file (fixable by deleting the batch). Because the column is `NOT NULL DEFAULT ''`,
every existing and blank-label row keeps today's exact dedupe behaviour.

**Amended index (replaces `execution_broker_dedupe`; drop + create, no data rewrite, cannot
violate on existing rows because all labels are `''`):**

```sql
CREATE UNIQUE INDEX execution_broker_dedupe
  ON journal_execution (user_id, broker, broker_account_label, broker_execution_id)
  WHERE broker_execution_id IS NOT NULL;
```

**Importer rules:**

1. Dedupe key = `(user_id, broker, broker_account_label, broker_execution_id)`. The pre-fetch diff
   uses the same four fields.
2. The label cannot come from the Topstep CSV. Add one **optional** "Account" text input on upload,
   default blank, stored verbatim (trimmed) on every execution of that batch. Blank stays `''`.
   Product-manager to confirm; it must not be required (journaling-friction principle).
3. **Collision guard (needed with or without the label):** when a row is skipped as a duplicate,
   compare `symbol`, `side`, `quantity`, `price`, `executed_at` against the stored execution. If any
   differ, do not skip silently: report it as a warning row ("id already exists with different
   contents; possible second account, set the Account field"). This is the only way the per-account
   collision case becomes visible when the user left the field blank.
4. UI hint: label is free text, so `Combine 50K` vs `combine-50k` is a user typo, not a key
   problem. Suggest the last-used labels as autocomplete; do not normalise case silently.
5. Matcher: per ADR-0003 L355, group by `(symbol, broker_account_label)`, otherwise fills from two
   accounts pair against each other. With blank labels this is a no-op.

**Separate issue found in the same check — DECIDED in ADR-0004 §3 (option (a) below, per-row pairing):** within one
contract, 12 row pairs overlap in time, all same direction (11 fully nested, 1 partial), i.e.
concurrent same-direction positions. §5 says "no cross-row lot bleed expected", but a FIFO matcher
over the synthesized executions will pair an outer row's entry with an inner row's exit. Per-trade
`gross_pnl`, duration and R differ from the source `PnL` column, though the sum over the batch is
unchanged when sizes match. This could be scale-ins in one account or copy-traded accounts (unknown
which; another reason to capture the label). Options for architect: (a) trust the export and keep
one trade per row by matching within the row's own two executions (bypass FIFO for closed-trade
imports), or (b) accept FIFO re-pairing. Recommend (a) for imports, verified with the `PnL`
cross-check in §2. Add a test vector with two nested same-direction rows before implementing.

**Still unknown:** cross-account id overlap (needs an export from a user with two accounts), re-export
id stability, and whether a TopstepX export from a specific account ever carries an account column.
