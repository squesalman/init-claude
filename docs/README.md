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

## Product (`docs/product/`, owner: `product-manager`)
| Doc | Status | Answers |
|---|---|---|
| [features/mvp](product/features/mvp.md) | Accepted (open questions all answered) | MVP scope, user stories 1-6, non-goals. |
| [features/import-account-label](product/features/import-account-label.md) | Accepted | Optional Account field on upload, conflict banner, acceptance criteria. |

## Domain (`docs/domain/`, owner: `trading-domain-expert`)
| Doc | Status | Answers |
|---|---|---|
| [pnl-and-matching](domain/pnl-and-matching.md) | Accepted (§3 precedence rule awaiting product confirmation, see below) | Fill→trade matching, P&L, win rate, R-multiple, test vectors. |
| [topstep-import](domain/topstep-import.md) | Accepted | Real TopstepX CSV format, column mapping, multipliers, fees, dedupe, test vectors. |

## Design (`docs/design/`, owner: `ux-designer`)
| Doc | Status | Answers |
|---|---|---|
| [import-account-label](design/import-account-label.md) | Accepted | Upload page, conflict banner, `/imports/` list + detail, delete flow, final copy. |

Not designed yet: signup/login, trades list, stat cards.

## Data (`docs/data/`, owner: `database-engineer`)
| Doc | Status | Answers |
|---|---|---|
| [schema](data/schema.md) | Living | Data dictionary for the merged schema. Must match `journal/models.py`. |
| [follow-ups](data/follow-ups.md) | Living | Deferred work with triggers (rows 1-15). Owner of the file: orchestrator. |

## Known conflicts
None.

## Open decisions (not conflicts)
- **R-multiple precedence** (`pnl-and-matching.md` §3): when both `planned_risk_amount` and `stop_price` are set, `planned_risk_amount` wins, with no fallback to the stop if the planned risk is unusable. This is a domain-expert call; `product-manager` or the user should confirm.
