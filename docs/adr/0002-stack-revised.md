# ADR-0002: Stack choice (revised)

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** architect (proposed), user (approved)
- **Supersedes:** [ADR-0001](0001-stack.md)
- **Amended 2026-09-22:** production deployment is Docker Compose (was gunicorn + Caddy under
  systemd) at the user's direction. Amended in place rather than in a new ADR because this ADR
  was accepted the same day and nothing has been built on it yet. See "Hosting".

## Context

ADR-0001 chose a stack under one dominant constraint: development happened on a Raspberry Pi
with ~780 MB of usable RAM. **That constraint is gone.** Development moves to a 16 GB desktop,
and production moves to a rented VPS. The Pi is out of the picture entirely.

Roughly half of ADR-0001's reasoning was RAM arithmetic, so this ADR supersedes it rather than
amending it. ADR-0001 is kept as history: it records *why* the Pi-era choices were made and which
of them were never about hardware.

**Unchanged from ADR-0001** — these were not RAM decisions and are not re-litigated here. See
ADR-0001 for the full argument:

- Python over Node/TypeScript/JVM. The user's stated preference, plus `decimal.Decimal` in the
  stdlib. (The float argument was over-weighted in ADR-0001; see "Money" below for its correct
  weight.)
- No pandas/numpy. Float64-first, so every money aggregation through a DataFrame is wrong by
  default. This is a domain rule, not a hardware call.
- Executions are the source of truth; trades and positions are derived.
- Domain logic (fill→trade matching, metrics, behavior detectors) is pure functions over plain
  Python values, not methods on Django models.

### Hard constraints (carried forward from CLAUDE.md)

1. Multi-user from day one; real auth, real per-user isolation.
2. No floats for money or quantity.
3. Timestamps UTC in DB, user timezone at display; currency stored with every amount.
4. Imports idempotent (dedupe by broker + execution id), raw rows retained.
5. Every query scoped to the authenticated user, through one enforceable chokepoint.

### Expected data volume

Unchanged and still the most important number in this document: **thousands of executions per
user, tens to low hundreds of users.** This is a small-data problem. Sizing for more is the main
over-engineering risk. A 2 GB VPS is not a constraint we are designing around — it is more than
this workload needs.

### Environment

- **Dev:** 16 GB desktop. Postgres in Docker. An RTX 4090 is present and is **irrelevant** to
  this application — nothing here is a GPU workload. (Revisit only if local-LLM journal
  summarisation becomes a feature. Not MVP.)
- **Prod:** VPS, 2 vCPU / 2 GB is sufficient.

## Decision

| Layer | Choice | Change from ADR-0001 |
|---|---|---|
| Language / runtime | Python 3.12 | — |
| Web framework | Django 5.2 LTS | — |
| JSON endpoints | **`django-ninja`**, added per-page on demand | **new** |
| Database | **Postgres 16+** | **was SQLite** |
| ORM / query layer | Django ORM, all access via one user-scoped manager | — |
| Tenant isolation | ORM chokepoint + mandatory isolation test; **Postgres RLS deferred** | RLS now available |
| Money | **`NUMERIC(19,4)` + currency column** | **was integer minor units** |
| Price / quantity | `NUMERIC(20,10)` via `DecimalField` | — |
| Frontend | Django templates + **htmx + Alpine + Tailwind** | Alpine/Tailwind named |
| Charts | one vendored JS chart library, static file | — |
| Auth | `django.contrib.auth` — sessions, CSRF, PBKDF2 | — |
| Background work | none; metrics computed on read | — |
| Hosting (dev) | desktop, `runserver` on the host, **Postgres in Docker** | **was the Pi** |
| Hosting (prod) | VPS: **Docker Compose** — web (gunicorn) + db (Postgres) + Caddy | **was the Pi** |
| Deploy | `git pull && docker compose up -d --build` on the VPS; image built on the box | **new** |
| Backups | `pg_dump` on cron to off-box storage, from day one | — |
| Testing | pytest + pytest-django | — |
| Packaging | uv + `pyproject.toml` + committed `uv.lock` | — |
| Migrations | Django migrations | — |

### Postgres from day one

SQLite was chosen in ADR-0001 because a Postgres server cost a quarter of the Pi's available RAM
to solve problems we do not have at our data volume. On a desktop and a VPS that cost is noise,
and Postgres is where this app ends up anyway — so pay now and skip the migration.

What it buys, in order of real value:

1. **Native `NUMERIC`.** Exact decimal arithmetic in the database, including `SUM()` and `AVG()`.
   This deletes ADR-0001's most dangerous rule ("never aggregate a decimal column in SQL") — a
   rule that depended on humans remembering it forever.
2. **No later migration.** Dev and prod run the same engine from the first commit. No
   SQLite-in-dev / Postgres-in-prod split, which is how you ship a bug that only exists in prod.
3. **Real concurrency.** Multiple writers, so simultaneous CSV imports do not serialise on a
   single write lock.
4. **RLS available when wanted.** See below.

Cost accepted: one more service to run in dev (`docker compose up`), and Postgres operational
basics in prod (a role, a database, backups). Both are hours of setup, once.

### Money as `NUMERIC(19,4)`, not integer minor units

This reverses ADR-0001, and the reversal is a *simplification*.

ADR-0001 used integer minor units because SQLite has no decimal type, so integers were the only
way to keep `SUM()` exact. Postgres has `NUMERIC`, which is exact, so the workaround is no longer
needed — and it was not free: every read and write needed scaling by a currency-dependent
exponent, and every display path had to know that exponent.

- **Money** (P&L, fees, commissions, cash) → `NUMERIC(19,4)` + an ISO 4217 `currency` column.
  Four decimal places covers fractional commissions and sub-cent amounts. Maps to
  `Decimal` in Python via `DecimalField`. Presentation rounding is driven by the currency.
- **Prices and quantities** → `NUMERIC(20,10)`, wide enough for fractional shares and crypto.
- **Rounding is explicit** at the point of computation (`Decimal.quantize`, stated rounding mode).
  Display-time formatting never mutates a stored value.
- **Still never a float**, anywhere, for either. Satisfies constraint 2 and CLAUDE.md's domain
  rule ("decimal or integer minor units" — `NUMERIC`/`Decimal` is the decimal branch).

On the weight of the float argument generally: it is a real, demonstrable bug class — summing
200 tick-sized wins as floats yields `2242.000000000005`, and `0.1 + 0.2 - 0.3 > 0` classifies a
breakeven trade as a *win*, silently corrupting win rate. But it is **three rules, not an
architecture**: money in a decimal column, never floats in arithmetic, round once explicitly at
the end. ADR-0001 leaned on it harder than it deserved. It is not the reason for any choice in
this table except the rejection of pandas.

### `django-ninja` for JSON endpoints, added on demand

The user asked whether FastAPI would be faster for building an API, while also wanting Django's
admin. `django-ninja` resolves this without a trade: it provides FastAPI's developer experience
(type hints, Pydantic schemas, automatic OpenAPI/Swagger docs, async views) on top of Django,
keeping the ORM, migrations, `contrib.auth`, and the admin.

Choosing FastAPI standalone would mean hand-rolling auth (the highest-risk code in the app),
Alembic instead of Django migrations, SQLAlchemy instead of the ORM, and building throwaway
internal CRUD screens to replace the admin. Three rebuilds to gain nicer decorators.

**It is added per page, on demand, not up front.** The MVP screens in
`docs/product/features/mvp.md` are server-rendered forms and tables; they need no JSON API. The
first endpoint gets written when something actually needs to fetch JSON. Do not build an API for
a client that does not exist.

### Frontend: templates + htmx + Alpine + Tailwind

The user asked whether the frontend can be as extensive as a React/Vite app. Two separate
answers:

- **Polish and density: yes, and React is not the source of it.** Visual quality comes from
  Tailwind and UX design. Server-rendered HTML with htmx and Alpine does inline editing, live
  filter-as-you-type, partial table refresh, modals, infinite scroll, keyboard shortcuts, and
  optimistic-looking saves. That spans every screen in the MVP spec.
- **Client-side state management: no, and this app barely has any.** What React genuinely wins is
  a cross-filtering analytics dashboard — click a chart, three others re-filter with no round
  trip. That is v2, and it is one page.

With 16 GB, RAM is no longer an argument against an SPA. The surviving argument is surface area:
two codebases, an API as a hard boundary, auth across that boundary, money formatting written
twice, and the loss of Django forms — roughly double the surface for a UI category we do not yet
need. Tailwind uses the **standalone CLI binary**, so there is still no `node_modules` and no
bundler.

**Escape hatch:** because `django-ninja` is available, adding React/Vite to one page later costs
one endpoint plus one component. It is a per-page decision, not an architecture rewrite — which
is why this choice stays cheap to be wrong about.

### Tenant isolation: ORM chokepoint now, RLS before external users

Unchanged mechanism from ADR-0001: every user-owned model carries a `user` FK, reads go through
one manager method (`Model.objects.for_user(request.user)`), and one mandatory test enumerates
every user-owned model and asserts a second user's session cannot reach the first user's rows.

Postgres adds **row-level security** as an option: a per-table policy
(`USING (user_id = current_setting('app.user_id')::bigint)`) makes the *database* refuse to return
other users' rows, so a forgotten `.for_user()` returns zero rows instead of a data breach.

**Deferred, with a trigger: enable RLS before any account exists that is not the author's.** It
is a seatbelt; the ORM chokepoint is the brakes. Policies are pure DDL with no schema change, so
switching it on later is one migration — provided every user-owned table carries a plain
`user_id` column for the policy to bite on, which the data model does anyway. That column
requirement is a standing constraint on `database-engineer`.

### Analytics: still compute on read

Unchanged, and still justified by data volume rather than by hardware. Staged upgrade path, each
step gated on **measurement, not guessing** — trigger is a dashboard exceeding ~300 ms on a
realistic dataset:

1. Postgres indexes.
2. A `trade` table materialized at import time from executions. Executions remain the source of
   truth; the table is rebuildable from them.
3. Only then a background worker — and if that day comes, a **Postgres-backed queue**
   (`django-tasks`, `django-q2`) before Redis + Celery. We already run Postgres; a second
   datastore for a job queue at this volume is not warranted.

To be confirmed in ADR-0003 (data model).

### Hosting and deployment: Docker Compose in production, Docker for Postgres in dev

**Dev — `compose.yaml`, one service.** Postgres only, with a named volume for data. The Django app
runs on the host under `manage.py runserver`, inside a uv venv. The app is *deliberately not*
containerised in dev: a container adds a rebuild or a bind-mount dance to every code change and
slows the edit-reload loop, which is the thing you do a thousand times a day. Postgres in Docker
is pure win — no system package, no version drift from prod, and `docker compose down -v` resets
the database.

**Prod — `compose.prod.yaml`, three services.** Chosen at the user's direction; it is also the
better fit now that Postgres is in the stack, because it makes the whole box reproducible from two
files instead of a runbook of `apt` steps.

```yaml
services:
  db:       # postgres:16-alpine, named volume, no published ports
  web:      # built from Dockerfile: gunicorn, 2-3 sync workers, depends_on db
  caddy:    # caddy:alpine, ports 80/443, volumes for certs + collected static
```

Specifics that matter, so the next agent does not have to guess:

- **Dockerfile:** `python:3.12-slim`, install via `uv sync --frozen`, run `collectstatic` at build
  time. Non-root user. No Node, so no multi-stage build needed — the Tailwind standalone binary
  runs at build time or its output is committed.
- **Caddy stays the reverse proxy** (automatic HTTPS, less setup than nginx + certbot) and serves
  collected static files directly from a shared volume, so gunicorn never serves static.
- **`db` publishes no ports.** Reachable only on the Compose network. Postgres is never exposed to
  the internet.
- **Secrets** (`SECRET_KEY`, `POSTGRES_PASSWORD`, `ALLOWED_HOSTS`) come from an `.env` file on the
  VPS, git-ignored, `chmod 600`. Not Docker secrets — that is Swarm machinery for a single box.
- **Migrations run in the web container's entrypoint** (`manage.py migrate` then `gunicorn`).
  <!-- ponytail: safe because there is exactly one web container; if it ever scales to two,
  move migrate into the deploy script as `docker compose run --rm web manage.py migrate` -->
- **Deploy is `git pull && docker compose -f compose.prod.yaml up -d --build`.** The image is built
  on the VPS. No registry, no CI pipeline, no image versioning — a Django image with no Node
  toolchain builds in well under a minute on 2 vCPU. Add a registry when builds get slow or a
  second machine appears.
- **Backups are not optional:** host cron running
  `docker compose exec -T db pg_dump ...` to storage that is not the VPS's own disk, from day one.
  Verify a restore once, manually, before there are real users. A Docker volume is not a backup.

2 vCPU / 2 GB is sufficient for three containers at this volume; resize before re-architecting.

## Alternatives considered

Alternatives already rejected in ADR-0001 on grounds that had nothing to do with RAM — Node/
TypeScript, JVM/.NET, Flask, pandas, DuckDB — remain rejected for the reasons recorded there.
Only the alternatives whose status actually changed are re-argued here.

### FastAPI + SQLAlchemy standalone — rejected, on stronger grounds than ADR-0001 used

ADR-0001 partly leaned on `pydantic-core` being a Rust dependency on ARM64, which no longer
matters. The real objection stands and is simpler: it costs hand-rolled auth (constraint 1, the
riskiest code in the app), hand-rolled user scoping (constraint 5), Alembic, and the admin the
user specifically values. `django-ninja` supplies the only thing FastAPI was wanted for.
Reconsider only if this becomes primarily a public API rather than a web app.

### React/Vite SPA — rejected *for now*, with a per-page escape hatch

ADR-0001's primary objection (a bundler and dev server in a ~780 MB budget) is void. It is now a
straight surface-area trade, argued above: ~2x the surface for client-side state complexity this
app does not yet have. **Revisit per page, not globally** — the likely first candidate is a v2
cross-filtering analytics dashboard. If journaling friction (the stated top product risk) turns
out to demand richer interaction, the mitigation is an Alpine or Preact island on that page.

### SQLite + Litestream on the VPS — rejected

Genuinely viable at this data volume and cheaper to operate. Rejected because it keeps the
decimal-aggregation footgun, forecloses RLS, and defers a Postgres migration we know is coming.
Postgres costs hours of setup, once; the workarounds cost vigilance forever.

### Integer minor units for money on Postgres — rejected

Exact, and the correct answer on SQLite. Rejected here because `NUMERIC` is equally exact and
needs no scaling at every boundary. Choosing it would be carrying a workaround past the
constraint that caused it.

### Playwright / browser E2E tests — still deferred, but no longer for hardware reasons

Headless Chromium is affordable on a 16 GB desktop, so ADR-0001's RAM objection is void. Still
deferred on value: there is no checkout-critical flow yet. Add when one exists. This is now a
cheap decision to reverse, and it should probably run in CI rather than locally.

### Deployment: gunicorn + Caddy under systemd — rejected (was the original choice)

Fewer moving parts and no Docker layer on the VPS. Rejected at the user's direction, and the
direction is sound now that Postgres is in the stack: bare systemd means the VPS accumulates an
undocumented `apt` history (Python version, Postgres version, Caddy binary, service files), and
dev/prod parity depends on remembering to match versions. Compose makes the box reproducible from
two files. Cost accepted: a Docker layer and a build step on deploy.

### Deployment: containerising the app in dev too — rejected

Maximum dev/prod parity, and tempting since Compose is already there. Rejected because it taxes the
single most frequent action in the project: a code change either triggers a rebuild or needs a
bind-mount plus a reload story inside the container. Postgres in Docker already gives us the parity
that actually matters (the database engine and its version). Revisit if a native dependency ever
makes host installs painful — there is none today.

### Deployment: image registry + CI pipeline — deferred

The standard answer, and correct for a team. Deferred for one machine and one developer: building
on the VPS is a sub-minute step with no Node toolchain, and a registry plus CI is infrastructure
with no second consumer. **Revisit when** builds become slow enough to notice, a second machine
appears, or a rollback needs a previously-built image (right now rollback is `git checkout` plus a
rebuild).

### Deployment: Kubernetes / Swarm / Docker secrets — rejected

Orchestration for one box with three containers. Compose is the orchestrator. Docker secrets
specifically requires Swarm mode, so an `.env` file with `chmod 600` is the simpler equivalent at
this scale.

### Keeping the Pi as a staging environment — rejected

Tempting since it exists. It would mean maintaining a second deployment target, on ARM64, on an
SD card, for an app with no users. Add a staging environment when there is a production worth
protecting.

## Consequences

### Positive

- One language, one database. Dev environment is a uv venv plus `docker compose up -d`.
- Dev and prod run the same database engine *and the same pinned image tag* from commit one. No
  prod-only class of bug, and no Postgres installed as a host package on either machine.
- Production is reproducible from two files in the repo (`Dockerfile`, `compose.prod.yaml`) plus
  one `.env` on the box. Rebuilding the VPS is a `git clone` and one command, not a runbook.
- The most dangerous rule in ADR-0001 (never aggregate decimals in SQL) is **deleted**, not
  mitigated — Postgres `NUMERIC` aggregation is exact.
- Money handling is simpler than ADR-0001: no minor-unit scaling at boundaries, and
  `DecimalField` ↔ `Decimal` end to end.
- A defense-in-depth option (RLS) now exists for tenant isolation and costs one migration
  whenever we want it.
- FastAPI's ergonomics are available via `django-ninja` without giving up admin, auth, or
  migrations.
- The two uncertain decisions (server-rendered UI, compute-on-read) remain independently
  reversible, per page and per metric respectively.
- No GPU, no Redis, no worker, no bundler, no `node_modules`.

### Negative / accepted risks

- **Postgres is a service to operate.** Backups, a role, a restore drill. Mitigation: it is
  hours, once, and `pg_dump` on cron from day one.
- **Docker is a layer to debug.** Container networking, volume permissions, and "works on my
  machine, not in the image" are real costs, and a Docker volume is not a backup. Accepted for
  reproducibility; mitigated by keeping it to three stock images and no orchestration.
- **Deploy has a build step and brief downtime.** `up -d --build` restarts the web container.
  Fine at this stage; zero-downtime deploys need a second container and a proxy dance, which is
  not worth it before there are users who would notice.
- **Rollback means rebuilding an older commit**, since no images are versioned in a registry.
  Acceptable at sub-minute build times; the registry trigger is written above.
- **Tenant isolation still rests on one application-layer pattern for the MVP.** The isolation
  test is mandatory; `security-reviewer` audits it before release; RLS is the written trigger
  before any non-author account exists.
- **UI ceiling remains.** Highly interactive journaling or cross-filtered analytics will strain
  htmx. Accepted; mitigation is per-page JS islands, not a rewrite.
- **`NUMERIC(19,4)` fixes a scale by fiat.** Fine for tradeable instruments and every currency in
  ISO 4217; would need widening for a pathological case. A column-type migration, not a redesign.
- **`django-ninja` is a smaller project than Django or FastAPI.** Bus-factor risk accepted
  because it is a thin layer — dropping it means rewriting a handful of view signatures, and only
  for pages that have endpoints at all.
- **Compute-on-read will not scale forever.** Deliberate; the staged path above is gated on
  measurement.
- **A 2 GB VPS is a real ceiling** for gunicorn + Postgres + Caddy together. Adequate at this
  volume; resize before re-architecting.

## Deliberately not building

Named so nobody adds them speculatively: no SPA, no JSON API beyond what a specific page needs,
no GraphQL, no Celery/Redis, no background worker, no Docker for the *app* in dev (Postgres only),
no image registry, no CI pipeline, no Kubernetes or Swarm, no Docker secrets, no zero-downtime
deploys, no microservices, no event sourcing beyond "executions are immutable facts", no multi-currency FX
conversion engine (store currency per amount; convert only when a feature asks), no OAuth/social
login, no 2FA yet (`django-otp` before external users), no password reset yet (before external
users — see `mvp.md` story 1), no real-time market data, no broker API integrations in v1 (CSV
import first), no pandas, no DuckDB, no E2E browser tests, no staging environment, no GPU
workloads, no RLS until the trigger above fires, no Litestream.

## Follow-ups

1. Fill in the "Stack & commands" section of `CLAUDE.md` and remove the Raspberry Pi constraint
   note. (Architect has drafted the replacement text; orchestrator/user to apply.)
2. Mark ADR-0001 as superseded by this ADR.
3. **ADR-0003: data model** — executions, derived trades, journal entries, the single free-text
   "my rules" field, extensible tags/custom fields. Must confirm the compute-on-read position and
   the `NUMERIC` column types above, and must give every user-owned table a `user_id` column so
   RLS stays a one-migration change.
4. `database-engineer`: Postgres is now the target. SQLite-portability is **no longer a
   constraint** — Postgres-specific DDL, types, and indexes are fair game.
5. `trading-domain-expert`: the four open questions in `docs/product/features/mvp.md` (R-multiple
   inputs, Topstep CSV format, win-rate tie-breaking, fill→trade matching) still block
   implementation and are unaffected by this ADR.
