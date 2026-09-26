# Trading Journal + Behavior Analysis App

A web app where retail traders log trades, journal their reasoning and emotions, and get evidence-backed insights into *behavioral* patterns (revenge trading, overtrading, tilt, rule-breaking) — not just P&L stats.

**Status:** building the MVP. Stack chosen (ADR-0002); schema, tenant-isolation tripwire and Topstep dedupe migration are merged. No importer, views, or UI yet. Next slice: signup/login → Topstep CSV upload → trade list + 3 stats.

**Read `docs/README.md` first.** It indexes every doc with owner and status. Found two docs that disagree? Stop, report it, and don't pick a side.

## Response style
The user has ADHD. Use the `i-have-adhd:i-have-adhd` skill (plugin: i-have-adhd) for every response, in every turn, on every topic. It stays on until the user says "stop adhd mode" or "normal mode".

Core habits: lead with the next action, number multi-step work, end with one concrete next action, restate progress each turn, give concrete time estimates, no preamble or closing pleasantries.

This also applies when briefing subagents: ask them to return short, action-first results.

## Stack & commands
Chosen in `docs/adr/0002-stack-revised.md` (supersedes `docs/adr/0001-stack.md`).

Python 3.12 · Django 5.2 LTS · Postgres 16+ · Django ORM · `django-ninja` (JSON endpoints, added per page on demand) · Django templates + htmx + Alpine + Tailwind (standalone CLI, no `node_modules`) · `django.contrib.auth` sessions · pytest + pytest-django · uv.

- Dev: `docker compose up -d` (Postgres only; the app runs on the host via `uv run --env-file .env manage.py runserver`). Copy `.env.example` to `.env` first: settings fail closed without `DJANGO_SECRET_KEY`, so every `uv run` (manage.py, pytest) needs `--env-file .env`.
- Prod: Docker Compose (web + db + caddy) on a rented VPS; deploy with `git pull && docker compose -f compose.prod.yaml up -d --build`; `pg_dump` on cron to off-box storage from day one.
- Money: `NUMERIC(19,4)` + ISO 4217 currency column. Prices/quantities: `NUMERIC(20,10)`. `Decimal` end to end, never floats. Round explicitly at computation, never at display.
- No background worker; metrics computed on read. No SPA, no Redis/Celery, no pandas.
- Tenant isolation: one user-scoped manager chokepoint (`Model.objects.for_user(request.user)`) plus a mandatory isolation test. Postgres RLS before any non-author account exists.

Constraints known so far: dev on a 16 GB desktop, prod on a rented VPS. Multi-user from day one.

## Product principles
- Journaling friction is the top risk. Every required field must earn its place.
- Insights are actionable and evidence-backed: show sample size, link to the underlying trades, hedge causal claims.
- Coach tone, never shaming. No financial advice, trade signals, or mental-health diagnosis.
- Traders are compared to *their own* baseline, not a universe.

## Domain rules (apply to all code and specs)
- Never floats for money or quantity — decimal or integer minor units.
- Timestamps stored in UTC; displayed in the user's timezone. Store currency with every amount.
- Executions (fills) are the source of truth; trades/positions are derived.
- Imports are idempotent (dedupe by broker + execution id) and keep raw data so parsing can be re-run.
- Every query is scoped to the authenticated user.
- Metric and detector definitions live in `docs/domain/` and `docs/behavior/`; code implements the specs and reuses their test vectors.

## Agents
Project subagents live in `.claude/agents/`. **The main session is the orchestrator** (run it on Opus); subagents cannot spawn other subagents, so the main session chains them.

| Agent | Model | Use for |
|---|---|---|
| `product-manager` | sonnet | Feature specs, acceptance criteria, MVP scope |
| `architect` | opus | Stack, data model, API design, ADRs |
| `trading-domain-expert` | sonnet | Metric formulas, fill→trade matching, broker formats, test vectors |
| `behavior-analyst` | sonnet | Behavior detectors and insight wording |
| `ux-designer` | sonnet | Flows, wireframes, design system |
| `database-engineer` | sonnet | Schema/DDL, migrations, indexes, query performance, analytics SQL, seed data |
| `backend-engineer` | opus 5.5 | APIs, importers, analytics services, auth (application code) |
| `frontend-engineer` | opus 5.5 | UI, tables, charts, journaling forms |
| `qa-engineer` | sonnet | Test plans, running tests, verifying calculations |
| `security-reviewer` | opus | Auth, tenant isolation, uploads, credentials, privacy (read-only) |

Model policy: only `backend-engineer` and `frontend-engineer` run on Opus 5.5 (`model: claude-opus-5-5` in their agent files); `architect` and `security-reviewer` stay on `opus`; the rest stay on `sonnet`. `code-review` is a skill, not an agent: it runs on the session's model, so run it from an Opus session.

### Delegation workflow
Default pipeline for a new feature — skip steps that don't apply:

1. `product-manager` → spec in `docs/product/features/<name>.md`
2. In parallel where independent: `ux-designer` (`docs/design/`), `trading-domain-expert` (`docs/domain/`), `behavior-analyst` (`docs/behavior/`)
3. `architect` → design / ADR in `docs/adr/`
4. `database-engineer` → schema, migrations, and indexes from the architect's model (`docs/data/`)
5. `backend-engineer` and `frontend-engineer` → implementation (parallel only if they don't touch the same files; backend starts after the schema exists)
6. `qa-engineer` → verify against acceptance criteria and reference vectors
7. `security-reviewer` → before release, and after touching auth, imports, or data access

### Branching for code-writing agents
Before delegating to a code-writing agent (`backend-engineer`, `frontend-engineer`, `database-engineer`, `qa-engineer`), the orchestrator creates a branch and worktree for that task first, and the agent works only inside that worktree — never directly on `main`. Doc-only agents (`architect`, `product-manager`, `trading-domain-expert`, `behavior-analyst`, `ux-designer`, `security-reviewer`) keep writing straight to `docs/` on `main`, no branch needed.

Workers never push to `main` or merge their own branch. Once a worker's task is done, the orchestrator pushes its branch and opens a PR (`gh pr create`). The PR is reviewed — via the `code-review` skill for correctness, `ponytail-review` for over-engineering, or the orchestrator directly — and only the orchestrator merges the PR into `main` afterward.

### Delegation rules
- Agents start with no memory of this conversation. Every prompt must state the goal, point to the relevant docs/files, and say what to return.
- Handoffs happen through files in `docs/`, not chat. Tell each agent which doc to read and which to write.
- Engineers implement from specs; if a spec is ambiguous or missing, route back to the spec owner instead of letting the engineer invent domain rules.
- The orchestrator does not silently overrule an agent. Surface disagreements to the user.
- Ownership boundary: `database-engineer` owns schema, migrations, indexes, and raw query design; `backend-engineer` owns application code that calls the DB. Neither edits the other's files — requests go through the orchestrator or `docs/`.
- Destructive or data-rewriting migrations need the user's confirmation before they run against a database holding real data.
- Commit doc-agent output to `main` before creating a code worktree, so the worktree branches from current docs.
- An ADR cannot be marked Accepted while any `[GUESS]` in it is open. Resolve it against `docs/domain/` first.
- Independent tasks run in parallel; dependent ones run in sequence.
- Don't spin up agents for trivial edits — do those directly.

## Working conventions
- Docs layout: `docs/product/`, `docs/adr/` (`NNNN-title.md`), `docs/domain/`, `docs/behavior/`, `docs/design/`, `docs/data/` (data dictionary).
- Use the superpowers skills where they fit: `brainstorming` before designing a new feature, `writing-plans` for multi-step work, `test-driven-development` for all code-writing agents (not just calculations/parsers) — write the test first, and leave it in the repo; a fix verified only by a throwaway script that gets deleted afterward doesn't count as tested, `systematic-debugging` for bugs, `verification-before-completion` before claiming anything is done.
- Report results faithfully: say what was run, what passed or failed, and what was not verified.
- Keep changes small and focused; no speculative abstractions.
- Update this file when the stack, commands, or agent roster changes.
