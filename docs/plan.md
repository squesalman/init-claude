# Build plan

Owner: orchestrator. Living doc. Last updated 2026-09-26. Source of truth for *what is next*; decisions live in the ADRs, follow-ups in `docs/data/follow-ups.md`.

## Where we are
- Merged: Django skeleton + schema (PR #1), tenant-scoped form tripwire (PR #2), Topstep dedupe migration (PR #3), auth + `UserScopedModelForm` + styled shell (PR #4), ruff lint (PR #5). 265 tests pass. No importer, matcher, stats, `/imports/`, or trades list yet.
- Accepted: ADR-0002 to ADR-0006, domain specs, upload/`/imports` design, slice spec, auth/list design.
- Final for build: `product/features/import-and-list.md` (behavior, stat card strings) and `design/auth-and-trades-list.md` (layout and copy, single source). Conflicts between them were ruled on 2026-09-26.

## Done
- **Phase 0, agent alignment:** `docs/README.md` index, `CLAUDE.md` status and rules, shared "Before you start" section in all 10 agent files, ADR-0003 `[GUESS]` items closed, R-multiple rewritten to match the schema.
- **Phase 1 design step:** slice spec, auth/list design, ADR-0006 (Accepted).
- **PR A (auth), PR #4:** signup (time zone, confirm password), login, logout, `/trades/` placeholder, `UserScopedModelForm` + banned-pattern tests, Tailwind standalone shell, 403/500. Security review (no High), QA (131 cases), code-review and ponytail-review findings applied; checked by hand in Firefox.
- **PR A2 (lint), PR #5:** ruff, lint only.

## Rulings (2026-09-26)
- R-multiple: `planned_risk_amount` wins over `stop_price`; no fallback if planned risk is unusable. Keep all 3 columns.
- Slice 1 = import + list. No filters, no pagination (one page), sort only.
- Stat card strings: `58.33% (n=120)`, `+$1,234.56 (n=177)`, `— (n=0)` (avg R is n=0 until journaling exists).
- Signup: visible prefilled time zone field; password + confirm-password field; signup email leak accepted for now (follow-ups 17); login throttling deferred (follow-ups 18).
- Layout and copy live in `design/auth-and-trades-list.md`; behavior in `import-and-list.md`.
- ADR-0006: importer uses per-row `create()` in one transaction; forms use `UserScopedModelForm`.

## Start here (next session)
Next task is **PR B**. Before writing importer views, clear follow-ups row 19 (add `Cache-Control: no-store` middleware for authenticated pages, or a test that every `login_required` view is `never_cache`); it was triaged as "before PR B/C". Read in order: `CLAUDE.md`, `docs/README.md`, this file, ADR-0004, ADR-0006, `docs/domain/topstep-import.md`, `docs/domain/pnl-and-matching.md`. Test vectors are synthetic; the real CSV stays out of git.

## Remaining work (one branch + worktree + PR per code task, TDD, tests stay in repo)
Merge order:
1. **PR B, importer + matcher + stats (backend only):** Topstep parser (idempotent, keeps raw rows, ADR-0004 pairing), `derive_trades()`, win rate / total P&L / avg R. Query-budget test from ADR-0006. Test vectors from `docs/domain/`.
2. **PR C, import UI:** upload form with Account field, `/imports/` list + detail, delete with counted tick box (ADR-0005; its 7 tests first).
3. **PR D, `/trades/` list + 3 stat cards.**

After each PR: `qa-engineer` against the acceptance criteria and vectors; `security-reviewer` after C (uploads, delete) and after any auth change; `code-review` before merge; only the orchestrator merges.

## Later (not in this plan)
Manual entry, journaling (incl. suggesting last-used planned risk), rules field, filters and pagination, login throttling and signup-leak fix (before non-author accounts), prod compose (follow-ups 1, 3), TopstepX stop-order export check (follow-ups 16, owner: user).

## Verification (end of slice 1)
- Each PR: `docker compose up -d`, then `uv run --env-file .env pytest` passes; `uv run --env-file .env manage.py makemigrations --check` is clean.
- End to end: `uv run --env-file .env manage.py runserver`; sign up two users; user 1 uploads the real 177-row CSV. Expected trade count is 177 minus skipped duplicates or conflicts (each Topstep row is one trade, ADR-0004). Re-upload imports nothing new. Stats match the domain vectors. User 2 sees no trades. Delete-import works.
