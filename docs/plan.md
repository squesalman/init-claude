# Build plan

Owner: orchestrator. Living doc. Last updated 2026-09-30. Source of truth for *what is next*; decisions live in the ADRs, follow-ups in `docs/data/follow-ups.md`.

## Where we are
- Merged: Django skeleton + schema (PR #1), tenant-scoped form tripwire (PR #2), Topstep dedupe migration (PR #3), auth + `UserScopedModelForm` + styled shell (PR #4), ruff lint (PR #5), Topstep importer + FIFO matcher + stats + `no-store` middleware (PR #6, merged 2026-09-27), import UI read side — upload, `/imports/` list, import detail (PR #7 / PR C1, merged 2026-09-28), delete-import flow (PR C2) + `/trades/` list and stat cards (PR D) (PR #8, merged 2026-09-29), import-delete hardening — signed max-pk stale guard, query-field guards, follow-ups 29-30 (PR #9, merged 2026-09-29). 740 tests pass. J1 (`planned_risk_amount > 0` CHECK, migration 0004) merged 2026-09-30 (PR #10, 747 tests). J2 rule: the journal form must reject more than 4 decimal places, never quantize before validating.
- Accepted: ADR-0002 to ADR-0006 (Decision 2 amended 2026-09-27), domain specs, upload/`/imports` design (rulings added 2026-09-27), slice spec, auth/list design.
- Final for build: `product/features/import-and-list.md` (behavior, stat card strings) and `design/auth-and-trades-list.md` (layout and copy, single source). Conflicts between them were ruled on 2026-09-26.

## Done
- **Phase 0, agent alignment:** `docs/README.md` index, `CLAUDE.md` status and rules, shared "Before you start" section in all 10 agent files, ADR-0003 `[GUESS]` items closed, R-multiple rewritten to match the schema.
- **Phase 1 design step:** slice spec, auth/list design, ADR-0006 (Accepted).
- **PR A (auth), PR #4:** signup (time zone, confirm password), login, logout, `/trades/` placeholder, `UserScopedModelForm` + banned-pattern tests, Tailwind standalone shell, 403/500. Security review (no High), QA (131 cases), code-review and ponytail-review findings applied; checked by hand in Firefox.
- **PR A2 (lint), PR #5:** ruff, lint only.
- **PR C1 (import UI, read side), PR #7:** upload form (Account datalist, htmx fragment swap on error), `/imports/` list (25/page, defers `raw_file`), import detail (filters, paging, conflict/mismatch banners, timezone display), htmx 2.0.11 + Alpine CSP 3.17.4 vendored, Tailwind rebuilt via WSL. Code-review (4 findings) and ponytail-review (6 findings, net -32 lines) applied and verified. Delete flow deferred to PR C2.

## Rulings (2026-09-26)
- R-multiple: `planned_risk_amount` wins over `stop_price`; no fallback if planned risk is unusable. Keep all 3 columns.
- Slice 1 = import + list. No filters, no pagination (one page), sort only.
- Stat card strings: `58.33% (n=120)`, `+$1,234.56 (n=177)`, `— (n=0)` (avg R is n=0 until journaling exists).
- Signup: visible prefilled time zone field; password + confirm-password field; signup email leak accepted for now (follow-ups 17); login throttling deferred (follow-ups 18).
- Layout and copy live in `design/auth-and-trades-list.md`; behavior in `import-and-list.md`.
- ADR-0006: importer uses per-row `create()` in one transaction; forms use `UserScopedModelForm`.

## Start here (next session)
Slice 1 UI is merged (PR #8 closed C2 and D; PR #9 hardening). Next: journaling J1 -> J2 -> J3 (item 4 below; spec `docs/product/features/journaling.md`, design `docs/design/journaling.md`, ADR-0007). Other open follow-ups: `docs/data/follow-ups.md` rows 31-37. Old pointer: PR C2 / PR D, both done. Read in order: `CLAUDE.md`, `docs/README.md`, this file, ADR-0005 (for C2) or `docs/domain/topstep-import.md` + follow-ups row 27 (for D, execution ordering), `docs/data/follow-ups.md` rows 20/24/28 (open items from PR C1: signup.html inline script, delete + rate limit + Caddy cap, provisional `UPLOAD_ERROR` copy). Dev setup: an `.env` copied before 2026-09-27 needs `POSTGRES_HOST=127.0.0.1` (not `localhost`, follow-ups row 23); `.env.example` is fixed. Test vectors are synthetic; the real CSV (`docs/all_trades_export.csv`, git-ignored) stays out of git.

## Remaining work (one branch + worktree + PR per code task, TDD, tests stay in repo)
Merge order:
1. **PR B, importer + matcher + stats (backend only): DONE, PR #6 merged 2026-09-27.** Parser, `import_file()`, `derive_trades()`, win rate / total P&L, avg R stubbed at n=0 until journaling. QA (vectors hand-computed) and security review done; findings fixed. Real 177-row export checked locally (counts only): 177 imported, re-import all `skipped_duplicate`.
2. **PR C, import UI.** ~~C1, upload form with Account field, `/imports/` list + detail: DONE, PR #7 merged 2026-09-28.~~ ~~C2, delete with counted tick box (ADR-0005): DONE, PR #8 merged 2026-09-29.~~
3. ~~**PR D, `/trades/` list + 3 stat cards.**~~ DONE, PR #8 merged 2026-09-29. Order executions by `(executed_at, id)` in the read path (follow-ups row 27).

4. **Journaling (stories 4-5), ADR-0007 Accepted 2026-09-30.** Order (user ruling): ~~**J1** `database-engineer`, CHECK `planned_risk_amount IS NULL OR > 0` (+ follow-ups row 11 redundant CHECKs): DONE, PR #10~~, then **J2** `backend-engineer`, R module `journal/stats.py`, save service, views, unstyled templates, ADR-0005 stale-guard amendment (`max(updated_at)`), then **J3** `frontend-engineer`, design-complete templates after J2 merges. Then `qa-engineer`, and `security-reviewer` after J2 (delete guard is data access).

After each PR: `qa-engineer` against the acceptance criteria and vectors; `security-reviewer` after C (uploads, delete) and after any auth change; `code-review` before merge; only the orchestrator merges.

## Later (not in this plan)
Manual entry, journaling (incl. suggesting last-used planned risk), rules field, filters and pagination, login throttling and signup-leak fix (before non-author accounts), prod compose (follow-ups 1, 3), TopstepX stop-order export check (follow-ups 16, owner: user).

## Verification (end of slice 1)
- Each PR: `docker compose up -d`, then `uv run --env-file .env pytest` passes; `uv run --env-file .env manage.py makemigrations --check` is clean.
- End to end: `uv run --env-file .env manage.py runserver`; sign up two users; user 1 uploads the real 177-row CSV. Expected trade count is 177 minus skipped duplicates or conflicts (each Topstep row is one trade, ADR-0004). Re-upload imports nothing new. Stats match the domain vectors. User 2 sees no trades. Delete-import works.
