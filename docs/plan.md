# Build plan

Owner: orchestrator. Living doc. Last updated 2026-09-26. Source of truth for *what is next*; decisions live in the ADRs, follow-ups in `docs/data/follow-ups.md`.

## Where we are
- Merged: Django skeleton + schema (PR #1), tenant-scoped form tripwire (PR #2), Topstep dedupe migration (PR #3). No views, importer, matcher, stats, or templates yet.
- Accepted: ADR-0002 to ADR-0006, domain specs, upload/`/imports` design, slice spec, auth/list design.
- Final for build: `product/features/import-and-list.md` (behavior, stat card strings) and `design/auth-and-trades-list.md` (layout and copy, single source). Conflicts between them were ruled on 2026-09-26.

## Done
- **Phase 0, agent alignment:** `docs/README.md` index, `CLAUDE.md` status and rules, shared "Before you start" section in all 10 agent files, ADR-0003 `[GUESS]` items closed, R-multiple rewritten to match the schema.
- **Phase 1 design step:** slice spec, auth/list design, ADR-0006 (Accepted). Next: PR A.

## Rulings (2026-09-26)
- R-multiple: `planned_risk_amount` wins over `stop_price`; no fallback if planned risk is unusable. Keep all 3 columns.
- Slice 1 = import + list. No filters, no pagination (one page), sort only.
- Stat card strings: `58.33% (n=120)`, `+$1,234.56 (n=177)`, `— (n=0)` (avg R is n=0 until journaling exists).
- Signup: visible prefilled time zone field; password + confirm-password field; signup email leak accepted for now (follow-ups 17); login throttling deferred (follow-ups 18).
- Layout and copy live in `design/auth-and-trades-list.md`; behavior in `import-and-list.md`.
- ADR-0006: importer uses per-row `create()` in one transaction; forms use `UserScopedModelForm`.

## Remaining work (one branch + worktree + PR per code task, TDD, tests stay in repo)
Merge order:
1. **PR A, auth:** signup (with time zone + confirm password), login, logout, base template/nav, `UserScopedModelForm` and the banned-pattern test (ADR-0006). Add `!tests/fixtures/*.csv` to `.gitignore` (follow-ups 7). Owners: `backend-engineer`, then `frontend-engineer`.
2. **PR B, importer + matcher + stats (backend only):** Topstep parser (idempotent, keeps raw rows, ADR-0004 pairing), `derive_trades()`, win rate / total P&L / avg R. Query-budget test from ADR-0006. Test vectors from `docs/domain/`.
3. **PR C, import UI:** upload form with Account field, `/imports/` list + detail, delete with counted tick box (ADR-0005; its 7 tests first).
4. **PR D, `/trades/` list + 3 stat cards.**

After each PR: `qa-engineer` against the acceptance criteria and vectors; `security-reviewer` after A and C; `code-review` before merge; only the orchestrator merges.

## Later (not in this plan)
Manual entry, journaling (incl. suggesting last-used planned risk), rules field, filters and pagination, login throttling and signup-leak fix (before non-author accounts), prod compose (follow-ups 1, 3), TopstepX stop-order export check (follow-ups 16, owner: user).

## Verification (end of slice 1)
- Each PR: `docker compose up -d`, then `uv run --env-file .env pytest` passes; `uv run --env-file .env manage.py makemigrations --check` is clean.
- End to end: `uv run --env-file .env manage.py runserver`; sign up two users; user 1 uploads the real 177-row CSV. Expected trade count is 177 minus skipped duplicates or conflicts (each Topstep row is one trade, ADR-0004). Re-upload imports nothing new. Stats match the domain vectors. User 2 sees no trades. Delete-import works.
