# Docs index

Read this first. One row per doc. If two docs disagree, stop and report it; don't pick a side.
Update this file whenever a doc is added, superseded, or changes status.

Statuses: **Accepted** (build from it) · **Draft** (don't build from it yet) · **Superseded** (ignore, kept for history) · **Living** (edited continuously).

## Decisions (`docs/adr/`, owner: `architect`)
| Doc | Status | Answers |
|---|---|---|
| [0001-stack](adr/0001-stack.md) | Superseded by 0002 | Original stack choice. Ignore. |
| [0002-stack-revised](adr/0002-stack-revised.md) | Accepted | Django, Postgres, htmx/Alpine/Tailwind, no worker, no SPA, tenant isolation approach. |
| [0003-data-model](adr/0003-data-model.md) | Accepted, amended by 0004 | Executions → derived trades → journal entries. Columns, constraints, indexes. |
| [0004-topstep-dedupe-and-pairing](adr/0004-topstep-dedupe-and-pairing.md) | Accepted | Dedupe key includes account label; per-row trade pairing via `broker_trade_id`; `skipped_conflict`. |
| [0005-batch-delete](adr/0005-batch-delete.md) | Accepted, amended by 0007 | "Delete this import" semantics, including journaled entries behind a counted tick box. Stale guard also signs `max(updated_at)`. |
| [0006-importer-write-path-and-tenant-rules](adr/0006-importer-write-path-and-tenant-rules.md) | Accepted, amended by 0007 | Importer writes per-row in one transaction; `UserScopedModelForm`; banned query patterns. Cross-tenant form error uses the form's own save-failure copy. |
| [0007-journaling](adr/0007-journaling.md) | Accepted (user rulings 2026-09-30) | Journal entry form/service/views, R + Avg R + "Left out" on read, `planned_risk_amount > 0` CHECK, ADR-0005 stale-guard amendment, template contract, PR split J1-J3. |

## Product (`docs/product/`, owner: `product-manager`)
| Doc | Status | Answers |
|---|---|---|
| [features/mvp](product/features/mvp.md) | Accepted (open questions all answered) | MVP scope, user stories 1-6, non-goals. |
| [features/import-account-label](product/features/import-account-label.md) | Accepted | Optional Account field on upload, conflict banner, acceptance criteria. |
| [features/import-and-list](product/features/import-and-list.md) | Accepted (rulings applied 2026-09-26) | Slice 1: signup/login, `/trades/`, 3 stat cards, isolation test. 25 acceptance criteria. |
| [features/journaling](product/features/journaling.md) | Ready to build (decisions 1-8 recorded 2026-09-30) | MVP stories 4-5: journal entry per trade, "My rules", Avg R lighting up. 34 acceptance criteria. |
| [features/manual-entry](product/features/manual-entry.md) | Draft: user and domain rulings applied 2026-10-13; pending ADR-0008 and the user accepting the design draft | MVP story 2: add open/closed manual trade, close, edit, delete; USD only (with a USD-quoted-only hint), point-value rule, More details, tenant isolation. 53 acceptance criteria (plus 12a, resolved); none PENDING (AC 8 and 21 resolved by `pnl-and-matching.md` §7). |

## Domain (`docs/domain/`, owner: `trading-domain-expert`)
| Doc | Status | Answers |
|---|---|---|
| [pnl-and-matching](domain/pnl-and-matching.md) | Accepted | Fill→trade matching, P&L, win rate, R-multiple, test vectors. |
| [topstep-import](domain/topstep-import.md) | Accepted | Real TopstepX CSV format, column mapping, multipliers, fees, dedupe, test vectors. |
| (updated 2026-09-30) [pnl-and-matching](domain/pnl-and-matching.md) §3 | Accepted | Journaling rulings: `risk_not_positive`, stop side-check (single-entry only), derived `risk_currency`, Avg R / "Left out" counting, vectors 18-23. |
| (updated 2026-10-13) [pnl-and-matching](domain/pnl-and-matching.md) §7 and vectors 24-34 | Accepted (ADR-0008 still pending for key format and edit locks) | Manual entry: exit before entry rejected, DST gap/overlap rejected, fees on the exit fill, edit invariants, point value, forex/crypto, USD-quoted-only caveat. Also `topstep-import.md` §7 (roots to verify). |

## Design (`docs/design/`, owner: `ux-designer`)
| Doc | Status | Answers |
|---|---|---|
| [import-account-label](design/import-account-label.md) | Accepted | Upload page, conflict banner, `/imports/` list + detail, delete flow, final copy. |
| [auth-and-trades-list](design/auth-and-trades-list.md) | Accepted (rulings applied 2026-09-26; single source for layout and copy) | Shell/nav, signup, login, `/trades/`, empty states, stat cards. |
| [journaling](design/journaling.md) | Accepted (owner rulings 2026-09-30: My rules collapsed, stay on page after save) | Journal page, Journal column on `/trades/`, Avg R card states, delete-dialog consistency, finished copy table. |
| [manual-entry](design/manual-entry.md) | Draft (not buildable until the user accepts it and ADR-0008 is Accepted) | Add / Close / Edit / Delete flows for manual trades, point-value states, entry points, copy table. |


## Plan
| Doc | Status | Answers |
|---|---|---|
| [plan](plan.md) | Living (owner: orchestrator) | What is done, rulings so far, remaining PRs (A to D), end-of-slice verification. |

## Data (`docs/data/`, owner: `database-engineer`)
| Doc | Status | Answers |
|---|---|---|
| [schema](data/schema.md) | Living | Data dictionary for the merged schema. Must match `journal/models.py`. |
| [follow-ups](data/follow-ups.md) | Living | Deferred work with triggers (rows 1-21). Owner of the file: orchestrator. |

## Agents (`docs/agents/`, owner: orchestrator)
| Doc | Status | Answers |
|---|---|---|
| [pipeline](agents/pipeline.mmd) | Living (mirrors CLAUDE.md) | Order agents run in for a feature. |
| [ownership](agents/ownership.mmd) | Living (mirrors CLAUDE.md) | Which agent writes where; branch vs main; PR and merge flow. |

## Known conflicts
Open until fixed. Do not pick a side; the owner edits.
- (a) [`design/auth-and-trades-list.md`](design/auth-and-trades-list.md) 5.4 A (line 323: "no 'add by hand' option or link"; also "Former Q4", line 483) vs [`design/manual-entry.md`](design/manual-entry.md) (two-button empty state, "Add trade" on `/trades/`). Owner: `ux-designer`; to be edited once the user accepts the design draft.
- (b) [`adr/0004-topstep-dedupe-and-pairing.md`](adr/0004-topstep-dedupe-and-pairing.md) lines 90 and 96 (manual entries leave `broker_trade_id` NULL and share one FIFO bucket) vs the 2026-10-13 ruling (each manual trade has its own pairing key; `domain/pnl-and-matching.md` §1 and §7 already say so). Owner: `architect`; fixed by ADR-0008.

## Gaps (not conflicts)
- [`product/features/import-and-list.md`](product/features/import-and-list.md) §2 words the `/trades/` empty state around imports only; it needs a manual-entry pass (see `design/manual-entry.md` 3.2, `TRADES_EMPTY_BODY`). Owner: `product-manager` once the design is accepted.

## Open decisions (not conflicts)
None.

## Recent rulings
- 2026-10-13 (third round, manual entry, user "hint and go"): (1) exit before entry is rejected on add, close and edit, equal times allowed, compared in UTC (AC 8; `pnl-and-matching.md` §7.1, vector 32). (2) DST: a nonexistent and an ambiguous local time are both rejected, never picked; the backend activates `User.timezone`; an unchanged time field on edit keeps its stored UTC value (AC 21; §7.2, vector 34). (3) Close shows the Account label read-only, only Fees in More details (AC 27); the add form always offers Fees, Fees with no exit is a field error and `0` counts; editing an open trade shows no exit and no Fees, Close is the only way to add an exit (AC 29, 37, 38). (4) The add form shows a hint near Symbol that only USD-quoted instruments are supported for now (USDJPY, EURGBP, USDT pairs and non-US equities would be stored as USD); this is the practical content of the non-USD deferral, `data/follow-ups.md` row 45 (AC 53).
- 2026-10-13 (second round, manual entry, user "defaults"): (1) currency is fixed to USD for the MVP (read-only, still stored on every fill); non-USD manual entry is deferred until a ruling on `data/follow-ups.md` row 45 plus a minor-unit rounding table. (2) Point value for a known root is read-only. (3) Side and entry time have no default; future times rejected; fees are 0 or more; delete confirmation mirrors ADR-0005; a closed trade cannot be reopened. (4) Last-used point value suggestion skipped (deferred one-liner only). AC 8 (exit before entry) and AC 21 (DST gap or overlap) stay PENDING for `trading-domain-expert` and `architect`.
- 2026-10-13: manual entry (MVP story 2) user rulings: (1) a manual trade may be open when saved (exit price/time optional); a "Close this trade" form adds one full-size exit fill, no partial closes. (2) Contract multiplier: prefill from the importer's root lookup (`MULTIPLIERS`) for known roots; for an unknown root a required "Point value" field with no default (equities type 1). (3) Typo fixes: edit in place for `source=manual` fills only (imported fills stay immutable), keeping the journal entry, plus a delete action with a counted confirmation; needs ADR-0008 to amend ADR-0003 "immutable by convention". (4) Optional collapsed "More details": one Fees number (on the exit fill, like imports) and an Account label with suggestions. Constraints for `architect`: every manual trade needs its own pairing key (NULL `broker_trade_id` would make all manual fills for a label+symbol share one FIFO bucket, `journal/matching.py:48`); which fields lock once a journal entry exists, stop side re-check on edit, delete removes the journal entry first in the same transaction (RESTRICT FK); double-submit guard.
- 2026-09-26: slice 1 = no filters, no pagination; stat card strings per PM spec; visible time zone field and confirm-password at signup; signup email leak accepted; login throttling deferred; `design/auth-and-trades-list.md` is the single source for table layout and copy, `product/features/import-and-list.md` for behavior; ADR-0006 approved.
- 2026-09-30: ADR-0007 accepted. Parser accepts a leading minus (risk <= 0 is `risk_not_positive`); cross-tenant form error uses `JOURNAL_SAVE_FAILED` (ADR-0006 amended); build journaling now, follow-ups row 16 stays user-owned and non-blocking; `planned_risk_amount > 0` CHECK approved (PR J1); delete token signs `max(updated_at)` (ADR-0005 amended, PR J2).
- 2026-09-26: R-multiple precedence kept as written in `pnl-and-matching.md` §3 (`planned_risk_amount` wins over `stop_price`; no fallback when planned risk is unusable). User ruling: "KEEP".
