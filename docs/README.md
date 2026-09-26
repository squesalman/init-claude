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
| [0005-batch-delete](adr/0005-batch-delete.md) | Accepted | "Delete this import" semantics, including journaled entries behind a counted tick box. |
| [0006-importer-write-path-and-tenant-rules](adr/0006-importer-write-path-and-tenant-rules.md) | Accepted | Importer writes per-row in one transaction; `UserScopedModelForm`; banned query patterns. |

## Product (`docs/product/`, owner: `product-manager`)
| Doc | Status | Answers |
|---|---|---|
| [features/mvp](product/features/mvp.md) | Accepted (open questions all answered) | MVP scope, user stories 1-6, non-goals. |
| [features/import-account-label](product/features/import-account-label.md) | Accepted | Optional Account field on upload, conflict banner, acceptance criteria. |
| [features/import-and-list](product/features/import-and-list.md) | Accepted (rulings applied 2026-09-26) | Slice 1: signup/login, `/trades/`, 3 stat cards, isolation test. 25 acceptance criteria. |

## Domain (`docs/domain/`, owner: `trading-domain-expert`)
| Doc | Status | Answers |
|---|---|---|
| [pnl-and-matching](domain/pnl-and-matching.md) | Accepted | Fill→trade matching, P&L, win rate, R-multiple, test vectors. |
| [topstep-import](domain/topstep-import.md) | Accepted | Real TopstepX CSV format, column mapping, multipliers, fees, dedupe, test vectors. |

## Design (`docs/design/`, owner: `ux-designer`)
| Doc | Status | Answers |
|---|---|---|
| [import-account-label](design/import-account-label.md) | Accepted | Upload page, conflict banner, `/imports/` list + detail, delete flow, final copy. |
| [auth-and-trades-list](design/auth-and-trades-list.md) | Accepted (rulings applied 2026-09-26; single source for layout and copy) | Shell/nav, signup, login, `/trades/`, empty states, stat cards. |


## Plan
| Doc | Status | Answers |
|---|---|---|
| [plan](plan.md) | Living (owner: orchestrator) | What is done, rulings so far, remaining PRs (A to D), end-of-slice verification. |

## Data (`docs/data/`, owner: `database-engineer`)
| Doc | Status | Answers |
|---|---|---|
| [schema](data/schema.md) | Living | Data dictionary for the merged schema. Must match `journal/models.py`. |
| [follow-ups](data/follow-ups.md) | Living | Deferred work with triggers (rows 1-21). Owner of the file: orchestrator. |

## Known conflicts
None.

## Open decisions (not conflicts)
None.

## Recent rulings
- 2026-09-26: slice 1 = no filters, no pagination; stat card strings per PM spec; visible time zone field and confirm-password at signup; signup email leak accepted; login throttling deferred; `design/auth-and-trades-list.md` is the single source for table layout and copy, `product/features/import-and-list.md` for behavior; ADR-0006 approved.
- 2026-09-26: R-multiple precedence kept as written in `pnl-and-matching.md` §3 (`planned_risk_amount` wins over `stop_price`; no fallback when planned risk is unusable). User ruling: "KEEP".
