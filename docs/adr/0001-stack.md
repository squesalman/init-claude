# ADR-0001: Stack choice

- **Status:** **Superseded by [ADR-0002](0002-stack-revised.md)** (2026-09-22)
- **Date:** 2026-09-22
- **Deciders:** architect (proposed), user (approves)
- **Supersedes:** none

> **Superseded.** This ADR's dominant constraint — development on a Raspberry Pi with ~780 MB
> usable RAM — no longer applies: dev moved to a 16 GB desktop and prod to a VPS. Read
> [ADR-0002](0002-stack-revised.md) for current decisions. Kept because it records which choices
> were hardware-driven (SQLite, integer minor units, no SPA, no E2E tests) and which were not
> (Python, Django, no pandas, compute-on-read, executions-as-source-of-truth) — ADR-0002 does not
> re-argue the latter.

## Context

We are building a multi-user web app where retail traders import/log executions, journal
reasoning and emotions, and receive evidence-backed behavioral insights. No code exists yet.
This ADR picks the stack; everything else in `docs/` is downstream of it.

### Hard constraints

1. **Dev machine is a Raspberry Pi.** Measured on the actual box, 2026-09-22:
   - `aarch64`, 4 cores, **3.7 GB total RAM, ~780 MB available** (the rest is in use by the
     dev tooling itself), 512 MB swap, 15 GB free disk.
   - Already installed: Python 3.11.2, Node 18.20.4, Docker. No Postgres, no SQLite CLI.
   - The ~780 MB working figure is the binding number. A stack that needs a bundler/dev server
     (~400–700 MB), a database server (~150–250 MB), and an app process concurrently does not
     fit without swapping, and swapping on an SD card is both slow and bad for the card.
   - ARM64 secondary effect: any dependency without prebuilt `aarch64` wheels gets compiled
     from source on 4 slow cores. Every compiled dependency is a tax paid repeatedly.
2. **Multi-user from day one.** Real auth, real per-user isolation, no single-user shortcuts.
3. **No floats for money or quantity.** This constrains both DB column types and the
   language-level numeric story.
4. **Timestamps UTC in DB, user timezone at display.** Needs a first-class tz story.
5. **Executions are the source of truth; trades/positions are derived.** Real server-side
   computation (fill→trade matching, aggregation, behavior detectors), not CRUD.
6. **Every query scoped to the authenticated user.** Needs a query layer with one enforceable
   chokepoint, not per-view discipline.

### Expected data volume

Thousands of executions per user, tens to low hundreds of users for the foreseeable future.
This is a *small data* problem. Sizing for anything larger is the main over-engineering risk
in this ADR, and most rejections below are rejections of scale we do not have.

## Decision

| Layer | Choice |
|---|---|
| Language / runtime | **Python 3.11** (already installed, no compile step on ARM64) |
| Web framework | **Django 5.x LTS** (pure Python, batteries-included auth) |
| Database | **SQLite** in WAL mode, single file (Postgres deferred, not rejected) |
| ORM / query layer | **Django ORM**, all access through one user-scoped manager |
| Frontend | **Django templates + htmx**, no bundler, no `node_modules` |
| Charts | one vendored JS charting lib (~40 KB), loaded from a static file |
| Auth | **`django.contrib.auth`** — session cookies, PBKDF2, built-in CSRF |
| Background work | **none** — metrics computed on read |
| Hosting (dev) | the Pi: `manage.py runserver`, one process |
| Hosting (prod) | gunicorn + **Caddy** on the Pi for private use; small VPS before external users |
| Testing | **pytest + pytest-django**, pure-function tests for domain logic |
| Packaging | **uv** + `pyproject.toml` |
| Migrations | Django migrations |

### Justification per choice, against the Pi constraint

**Python 3.11 over Node/TypeScript.** Two reasons, both concrete.
First, money: Python's stdlib has `decimal.Decimal`. Node has no native decimal — you add
`decimal.js` and then fight every boundary (JSON, DB driver, arithmetic operators) where a
value can silently become an IEEE-754 double. Constraint 3 is a domain rule we must not
violate quietly, and Python makes the correct thing the default. Second, the Pi: Python 3.11
is preinstalled and Django adds no compiled dependencies, so `uv sync` on ARM64 downloads pure
Python wheels and finishes. A TypeScript backend brings a `node_modules` tree and a build step
into the RAM budget for no offsetting benefit.

**Django over FastAPI/Flask.** The deciding factor is auth. Constraint 2 is a security
boundary, and "never be lazy about security" outranks "fewest dependencies." Django ships
session auth, password hashing with sensible defaults and upgrade-on-login, CSRF protection,
permission plumbing, and a migration system — all maintained by people who think about this
more than we will. With FastAPI we would hand-roll JWT issuance, refresh, revocation, and
password hashing; that is more code in the highest-risk area of the app. Secondary wins:
Django's ORM gives us the single user-scoping chokepoint constraint 6 needs, and the admin
gives us free CRUD for eyeballing imported executions during development, which saves building
throwaway internal screens.

On the Pi specifically: Django is pure Python, so nothing compiles. FastAPI depends on
`pydantic-core`, which is Rust; `aarch64` wheels do exist, so this is survivable rather than
fatal — but it is one more thing that can go wrong on a platform where a source build costs
real minutes. Django's larger *feature* surface is not a larger *runtime* surface: a Django
worker idles around 60–90 MB, comparable to FastAPI once you have added the libraries that
replace what Django includes.

**SQLite over Postgres, for now.** A Postgres server costs ~150–250 MB resident plus a Docker
layer, out of a ~780 MB budget, and buys us nothing at our data volume. SQLite in WAL mode
runs in-process at effectively zero overhead, handles concurrent readers with a single writer,
and is a single file — so backup is a file copy and moving to a VPS later is `scp`.

Two things Postgres would give us that we are explicitly choosing to live without:

- *Native `NUMERIC`.* SQLite has no decimal type, and SQL-side `SUM()`/`AVG()` over a decimal
  column goes through float. We neutralize this with the storage rules below rather than by
  buying a database server.
- *Row-level security.* Real defense-in-depth for constraint 6 — but it is a second enforcement
  layer for a single-app, single-schema deployment, and we would still need the application
  layer to be correct. We enforce in the ORM and test it instead.

This decision is deliberately the *reversible* one. Because access goes through the Django ORM,
swapping to Postgres is a settings change plus a data migration, not a rewrite. We revisit when
any of these is true: concurrent writers cause lock contention, the DB exceeds a few GB, or we
need more than one app server.

**Money and quantity storage rules.** These are part of this decision because they are what
makes SQLite safe for financial data:

- **Money amounts** (P&L, fees, commissions, cash) → `BIGINT` integer **minor units** plus a
  `currency` column (ISO 4217) plus the minor-unit exponent implied by that currency. SQLite
  integers are exact and SQL `SUM()` over integers is exact, so aggregation in the database
  stays correct.
- **Prices and quantities** → `DECIMAL` columns via Django's `DecimalField` (wide enough for
  fractional shares and crypto), mapped to `Decimal` in Python.
- **Hard rule for all code:** never `SUM()`/`AVG()`/`AVG` a decimal column in SQL. Aggregate
  decimals in Python with `Decimal`. We are already computing trades from fills in Python, so
  this costs nothing. `database-engineer` and `backend-engineer` both own upholding this.
- Rounding is explicit at the point of computation (`Decimal.quantize`, stated rounding mode);
  no implicit rounding at display time changing a stored value.

**Timezones.** Django with `USE_TZ = True` stores aware UTC datetimes and Python 3.11 has
`zoneinfo` in the stdlib. Users get a `timezone` field; formatting happens in templates. No
extra dependency, satisfying constraint 4.

**Django ORM with a user-scoped chokepoint.** Every user-owned model carries a `user` FK, and
reads go through one manager method (e.g. `Model.objects.for_user(request.user)`) rather than
bare `.objects.filter(...)` scattered through views. One test enumerates user-owned models and
asserts a second user cannot reach the first user's rows. That is the whole mechanism — no
middleware that magically rewrites querysets, no tenant-aware base class hierarchy, because
implicit scoping fails silently and a silent failure here is a data breach.

**Django templates + htmx, not React/Next.js.** This is the largest Pi-driven divergence from
the popular default and the one most worth arguing about, so it is treated at length under
Alternatives. Short version: an SPA means a second dev server and a bundler in a ~780 MB budget,
a `node_modules` tree on an SD card, a hand-written API layer to feed it, and duplicated money
formatting in a language without native decimals. Server-rendered HTML with htmx for partial
updates gives us one process, one language, no build step, and money formatting that happens
exactly once, server-side, in `Decimal`. The app is forms, tables, and charts — htmx's sweet
spot. Charts use one small vendored JS file rather than a charting framework, so there is still
nothing to build.

**No background worker (no Celery, no Redis).** At thousands of executions per user, metrics and
behavior detectors are fast enough to compute on read, which keeps them always-consistent with
the underlying executions and removes a broker, a worker process, and a whole class of staleness
bug from a machine that cannot spare the RAM. CSV import runs synchronously inside the request.

*Provisional* analytics position, to be confirmed in the data-model ADR: **compute on read, then
cache derived rows if measurement demands it.** The upgrade path, in order, is (1) DB indexes,
(2) a `trade` table materialized at import time from executions — executions stay the source of
truth and the table is rebuildable, (3) only then a background worker. We take step 2 when a
dashboard exceeds ~300 ms on a realistic dataset, measured, not guessed.

**Hosting.** Dev on the Pi with `runserver`. For production there is a fork in the road and it
depends on who the users are:

- *The user plus a few trusted people:* gunicorn (2 sync workers) behind Caddy on the same Pi.
  Caddy is a single static ARM64 binary and does automatic HTTPS, which is less setup than nginx
  plus certbot.
- *Real external users:* a small VPS. Not because the Pi lacks CPU — it does not — but because
  other people's financial history should not depend on a home internet connection, a consumer
  SD card, and a residential IP. SQLite makes that migration a file copy.

Backups are not optional and are not lazy: a cron job running SQLite's `.backup` to a
non-SD-card destination from day one. Litestream (continuous replication to object storage) when
there are users other than the author, not before.

**pytest + pytest-django.** `pytest` is the ecosystem default and parametrization maps directly
onto the reference test vectors that `docs/domain/` and `docs/behavior/` will carry. The
structural decision that matters more than the runner: **fill→trade matching, metric formulas,
and behavior detectors are pure functions over plain Python values**, not methods on Django
models. They get tested with no database and no fixtures, which is both correct design and
dramatically faster on a Pi. Django's test client covers view smoke tests and the tenant
isolation test.

**No browser E2E tests for now.** Headless Chromium on ARM64 is a large install and a heavy
process; against ~780 MB of RAM the cost is real and the value at this stage is low. Add
Playwright when there is a checkout-critical flow worth the RAM, and consider running it in CI
rather than on the Pi.

**uv over pip.** A single prebuilt ARM64 binary that resolves and installs dramatically faster
than pip. Dependency resolution on a Pi is genuinely slow, and this is the rare tool where the
Pi constraint argues *for* the newer option. `pyproject.toml` + `uv.lock` committed.

## Alternatives considered

### Frontend: React/Next.js SPA — rejected

**Why it is tempting:** the default for new web apps, best-in-class charting ecosystem, the
largest hiring pool, and genuinely better for highly interactive UI.

**Why rejected:** primarily the Pi. A Next.js dev server plus a TypeScript language server plus
an app process plus a browser does not coexist peacefully in ~780 MB; development would run in
swap on an SD card. `node_modules` is tens of thousands of files on slow storage. Secondarily,
even with infinite RAM it costs us a hand-written JSON API purely to serve our own UI, and it
pushes money formatting into a language without native decimals, where constraint 3 becomes
something we must actively defend at every boundary rather than something we get for free.

**What we give up:** a truly app-like feel, optimistic UI, offline capability, and easy reuse of
React chart libraries. If journaling friction — the stated top product risk — turns out to
demand richer client-side interaction, the mitigation is to add islands of interactivity to
specific pages (a small library like Alpine or Preact on one page), not to convert the app. That
is a per-page decision, not an architecture rewrite, which is precisely why this choice is
cheap to be wrong about.

### Backend: FastAPI + SQLModel/SQLAlchemy — rejected

Lighter-feeling and excellent for APIs. Rejected because it maximizes work in the two riskiest
areas: we would hand-roll auth (constraint 2) and hand-roll the user-scoping pattern
(constraint 6), and SQLAlchemy 2.x is a larger conceptual surface than the Django ORM for a
schema this simple. Reconsider only if this app becomes primarily a public API rather than a
web app.

### Backend: Flask + SQLAlchemy — rejected

Smallest core, but "small core" means we assemble auth, sessions, CSRF, migrations, and admin
from extensions and end up at Django's footprint with less cohesion and a bus factor per
extension.

### Backend: Node/TypeScript (Express, Nest, Remix) — rejected

The float problem (constraint 3) is the disqualifier; Nest additionally brings a decorator/DI
layer that is over-engineering at this size. Remix/SvelteKit are a real, respectable answer to
the "server-rendered, one process" argument — they lose on money handling, not on architecture.

### Backend: anything JVM or .NET — rejected

Correct decimal types and genuinely good tooling, but a JVM baseline heap plus build tooling on
a 4-core Pi with ~780 MB free is the wrong shape. Also the largest amount of ceremony per
feature of any option here.

### Database: Postgres from day one — rejected *for now*, with a trigger

The strongest rejected option, and the one most likely to be revisited. It offers native
`NUMERIC`, row-level security, real concurrency, and no migration later. Rejected because it
costs a quarter of our available RAM to solve problems we do not have at thousands of rows per
user, and because the ORM makes the switch cheap later. **Revisit when:** write lock contention
appears, DB > a few GB, more than one app server, or we need a Postgres-only feature. Keep the
schema portable (no SQLite-specific DDL) so this stays a settings change — a constraint on
`database-engineer`.

### Database: DuckDB for analytics alongside SQLite — rejected

Genuinely good at analytical queries, but it is a second database for a dataset that fits in
RAM, and its natural idiom (columnar, float-friendly aggregation) actively fights constraint 3.
Revisit only if analytical queries become the bottleneck, measured.

### Database: Postgres RLS for tenant isolation — deferred

Real defense-in-depth. Deferred as the second enforcement layer for a single-tenant-schema
deployment; we buy most of the safety with one ORM chokepoint and one isolation test. Revisit
alongside the Postgres decision.

### Analytics: pandas / numpy — rejected, and this one is a domain rule, not a preference

Worth naming explicitly because "behavior analysis" makes reaching for pandas nearly reflexive.
Two independent disqualifiers: (1) both are float64-first, so every money aggregation through a
DataFrame violates constraint 3 by default and `object`-dtype Decimal columns forfeit the
performance that was the reason to use pandas; (2) on ARM64 they are a large install. Our
aggregations are loops over thousands of Decimals — plain Python, `itertools`, and
`collections` are sufficient and exact. If a real statistical test is needed later, add
`statistics` (stdlib) or a narrowly scoped dependency for that one calculation.

### Hosting: Fly.io / Render / Railway from day one — deferred

Reasonable and cheap, and likely where this goes if it gets external users. Deferred because for
a solo dev plus a few users, the Pi is free, already running, and has no cold starts; adding a
deploy pipeline before there is a user is work without a customer.

## Consequences

### Positive

- One language, one process, one file to back up. The entire dev environment is a Python venv
  and `manage.py runserver`, which fits in RAM with room to spare.
- Nothing compiles on ARM64; no wheel-build or bundler-on-SD-card pain.
- Constraint 3 (no floats) is satisfied by defaults rather than by vigilance: `Decimal` in the
  stdlib, integer minor units for money, and a stated no-decimal-aggregation-in-SQL rule.
- Constraint 2 (auth) is delegated to Django's audited implementation rather than hand-rolled.
- Domain logic is pure functions, so the specs in `docs/domain/` and `docs/behavior/` can be
  tested directly against their own vectors with no DB and no fixtures.
- The two most uncertain decisions (SQLite, server-rendered UI) are both cheap to reverse.

### Negative / accepted risks

- **UI ceiling.** Highly interactive journaling UX will eventually strain htmx. Accepted;
  mitigation is per-page islands of JS, not a rewrite.
- **SQLite decimal aggregation is a footgun.** Mitigated by the storage rules above, but it is a
  rule humans must follow, which means it will be broken at some point. Mitigation: a QA check
  that greps for SQL aggregation over decimal columns.
- **Tenant isolation rests on one application-layer pattern.** No database-level safety net.
  Mitigation: the isolation test is mandatory and `security-reviewer` audits this specifically
  before any release.
- **Single writer.** Fine for our concurrency; would bite under many simultaneous imports.
  Trigger for the Postgres revisit is written above.
- **Compute-on-read will not scale forever.** Deliberate; the staged upgrade path is written
  above and gated on measurement.
- **Deploying on a Pi is not appropriate for other people's financial data.** Explicitly called
  out, with the VPS trigger and mandatory off-SD-card backups from day one.
- **Hiring/community optics.** "Django + htmx + SQLite" attracts less enthusiasm than a React
  stack. Irrelevant for a solo project on a Pi; noted so the decision is not mistaken for
  ignorance of the alternative.

## Deliberately not building

Named so nobody adds them speculatively: no SPA, no separate public JSON API, no Celery/Redis,
no Docker for local dev, no Kubernetes, no microservices, no GraphQL, no event sourcing beyond
"executions are immutable facts", no multi-currency FX conversion engine (store currency per
amount; convert only when a feature asks), no OAuth/social login, no 2FA *yet* (add via
`django-otp` before external users), no real-time market data feed, no broker API integrations
in v1 (CSV import first), no pandas, no Postgres, no E2E browser tests.

## Follow-ups

1. Update the "Stack & commands" section of `CLAUDE.md` with the chosen stack and commands.
2. ADR-0002: data model — executions, derived trades, journal entries, extensible tags/custom
   fields. Must confirm the compute-on-read position and the money/quantity column types above.
3. `database-engineer`: schema must stay Postgres-portable; no SQLite-specific DDL.
