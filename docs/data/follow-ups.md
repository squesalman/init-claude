# Follow-ups (deferred work, each with a trigger)

Started from the PR #1 code review (rounds 9–11); now the project's single tracker for deferred work. Add rows here, not in chat.

Deferred on purpose. Each has a trigger: do it when that happens, not before.

| # | Item | Trigger | Owner | Where |
|---|---|---|---|---|
| 1 | **Proxy/prod settings**: `SECURE_PROXY_SSL_HEADER`, `CSRF_TRUSTED_ORIGINS`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`. Behind Caddy, every POST fails CSRF without them; `check --deploy` warns W008/W012/W016. | Building `compose.prod.yaml` | backend-engineer | `config/settings.py` **Addendum (PR A security review):** without `SECURE_PROXY_SSL_HEADER`, `request.is_secure()` is False behind Caddy, so a `?next=http://<own-host>/...` passes the login redirect check (same-host HTTPS to HTTP downgrade). |
| 2 | **DECIDED [ADR-0006](../adr/0006-importer-write-path-and-tenant-rules.md): importer uses per-row `create()` with model instances; revisit if an upload takes >2 s, a file exceeds ~2,000 lines, or a second person's account is about to exist.** **DB-level tenant integrity**: composite FK `(user_id, fk_id) -> (user_id, id)`. **Correction (ADR-0006): RLS is NOT an alternative for this.** Postgres FK checks bypass RLS, so RLS cannot stop an insert pointing at another user's row. RLS is still planned (ADR-0002) for read isolation. Replaces the Python `save()` guard, which is check-then-write, costs a SELECT per FK, and is bypassed by `bulk_create`, `bulk_update`, `QuerySet.update`. | Before any non-author account exists, or the importer uses bulk writes | database-engineer | `journal/models.py` `_check_cross_tenant_fks` |
| 3 | **`ImportBatch.raw_file` blob (<=10 MiB)** loads on every `ImportBatch` query. Move to a side table, or `.defer('raw_file')` in list queries. | Building the import-history page | database-engineer | `journal/models.py` |
| 4 | **DONE (de74698)** — **Dev env loading**: settings fail closed without `DJANGO_SECRET_KEY`. README has the `uv run --env-file .env` line; CLAUDE.md "Stack & commands" does not. | Next CLAUDE.md edit | orchestrator | `CLAUDE.md` |
| 5 | **RULES DECIDED in [ADR-0006](../adr/0006-importer-write-path-and-tenant-rules.md) (`UserScopedModelForm`, scoped `get_object_or_404`, banned-pattern grep test).** **TRIPWIRE DONE (PR #2, `journal/test_form_scoping.py`); base form + view rules still due with the first form or view** — **`default_manager_name="unscoped"` leaks tenants through `_default_manager`**: a ModelForm `ModelChoiceField`, `get_object_or_404(Model, ...)` or generic views list every user's rows. Rule: forms must set querysets from `for_user()` in `__init__`; never use `_default_manager` in views. | Building the first form or view | frontend-engineer + security-reviewer | `journal/models.py` Meta |
| 6 | **DECIDED — [ADR-0004](../adr/0004-topstep-dedupe-and-pairing.md)**. Migration spec below ("Row 6 migration spec"). | Before the importer | database-engineer | `journal/models.py` Execution, RawImportRow |
| 7 | **Blanket `*.csv` in `.gitignore`** hides CSV test fixtures. Add `!tests/fixtures/*.csv` (or similar). | First fixture CSV | backend-engineer | `.gitignore` |
| 8 | **`save(*args, **kwargs)` positional signature** dies in Django 6.0 (deprecated now). Also `save(False, False, None, [...])` positional `update_fields` bypasses the guard's `update_fields` logic. Switch to `save(self, **kwargs)` and drop the positional test. | Django 6 upgrade | backend-engineer | `journal/models.py` `save`, `test_save_accepts_django_real_positional_signature` |
| 9 | **DONE** — replaced the `UserManager.get_or_create`/`update_or_create` stubs with a guard in `User.save()` that rejects a non-hash `password` (covers `create()`, `setattr`, stock `get_or_create`). Still bypassed by `bulk_create`/`QuerySet.update`/`loaddata`. | — | — | `accounts/models.py` |
| 10 | **`SILENCED_SYSTEM_CHECKS = ['auth.E003']`** is project-wide. Replace with a custom check that asserts the `Lower(email)` unique constraint exists. | Any change to the email constraint | backend-engineer | `config/settings.py` |
| 11 | **Redundant CHECKs**: `execution_currency_not_blank` and the `~Q(risk_currency='')` term are covered by the currency regex CHECK. | Next migration on these tables | database-engineer | `journal/models.py` |
| 12 | **`available_timezones()` walks the tz db at import** (every `manage.py`, pytest, worker start). Validate with `ZoneInfo(value)` in try/except, or cache lazily. | Startup time annoys | backend-engineer | `accounts/models.py` |
| 13 | **DECIDED — [ADR-0005](../adr/0005-batch-delete.md)** (Accepted, option (a): counted-checkbox delete of journaled entries). No schema change. Build: backend-engineer from ADR-0005 (7 tests first), ux-designer fills `[TBD-0005]`. | Before/with the import UI | backend-engineer, ux-designer | `journal/models.py` on_delete rules unchanged |
| 14 | **No `CHECK planned_risk_amount > 0`** on `JournalEntry`. `pnl-and-matching.md` §3 requires it at form validation and as a computation guard (R is null with reason `planned_risk_not_positive` otherwise). A DB CHECK would make it a hard guarantee. | Next migration on `JournalEntry`, or before journaling forms ship | database-engineer | `journal/models.py` JournalEntry |
| 15 | **Contract multipliers verified only for `CL` (1000) and `MCL` (100).** `ES`, `MES`, `NQ`, `MNQ`, `GC` are unverified; the importer rejects unknown roots rather than defaulting to 1. | A user imports a non-CL/MCL export | trading-domain-expert | `docs/domain/topstep-import.md` §2, §7 |
| 16 | **Check whether TopstepX can export stop/bracket orders** (or stop price per trade). The closed-trade CSV has no stop or risk data, so `stop_price` and `planned_risk_amount` are typed by hand. A second export with stop orders would let a future importer fill them from real data. Findings go in `docs/domain/topstep-import.md`. Do not derive risk from average loss or price movement (circular, invents a stop). | User checks their TopstepX account; do before the journaling form is built | **user** (then trading-domain-expert) | `docs/domain/topstep-import.md`, `JournalEntry` R fields |
| 17 | **Signup reveals whether an email is registered** (login does not). Fixing it needs email verification. User accepted the leak for now (2026-09-26). | Before signup is opened to strangers | security-reviewer + backend-engineer | `accounts/` signup view |
| 18 | **No login throttling** (repeated wrong passwords). User deferred (2026-09-26). | Before any non-author account exists | security-reviewer + backend-engineer | `accounts/` login view **`/admin/` is live and has no throttling either; fix both together.** |
| 19 | **Only `/trades/` sends `Cache-Control: no-store`.** The logged-in nav shows the user's email on every page, and `/imports/` pages are coming. Each new view must remember `never_cache`. Fix: middleware setting `private, no-store` for authenticated responses, or a test that every `login_required` view is `never_cache`. | Before PR B/C adds authenticated pages | backend-engineer | `journal/views.py`, `config/settings.py` MIDDLEWARE |
| 20 | **No Content-Security-Policy.** Inline scripts in `base.html` and `signup.html` need nonces or must move to external files before a strict CSP. | Before prod deploy / `compose.prod.yaml` | frontend-engineer + security-reviewer | `templates/base.html`, `templates/accounts/signup.html` |
| 21 | **Move template-only strings into `accounts/copy.py`** (time zone help, busy labels, empty state, 403/500 copy, skip-link and close labels), and wire the unused `SIGNUP_SERVER_FAILURE`/`LOGIN_SERVER_FAILURE`. Also no `404.html` (design defines no copy). | Next touch of the auth templates | frontend-engineer + backend-engineer | `accounts/copy.py`, `templates/` |

## Row 6 migration spec (ADR-0004)

One migration, no data rewrite. Additive/loosening except the two new CHECKs (items 2, 5b), which fail `migrate` if a populated table already holds a padded label or whitespace-only `broker_trade_id` (none exist: `0003` is unmerged and `broker_trade_id` is new).

1. **`journal_execution.broker_trade_id`**: add `VARCHAR(128) NULL`, no default (metadata-only in PG).
   Model: `CharField(max_length=128, null=True, blank=True)`.
2. **CHECK `execution_broker_trade_id_not_blank`**: `broker_trade_id IS NULL OR btrim(broker_trade_id) <> ''`
   (a stray `''` or whitespace-only value would merge unrelated trades into one matcher bucket).
3. **Replace `execution_broker_dedupe`** (RemoveConstraint + AddConstraint, same name):
   `UNIQUE (user_id, broker, broker_account_label, broker_execution_id) WHERE broker_execution_id IS NOT NULL AND broker_execution_id <> ''`.
   Condition unchanged; key only gets looser, so it cannot fail on existing rows.
4. **`RawImportRow` status**: append `("skipped_conflict", "Skipped (conflict)")` to `_STATUS_CHOICES`;
   `rawimportrow_status_valid` regenerates (drop + add). Fits existing `VARCHAR(20)`; no length change.
5. **No change** to `execution_import_requires_broker_execution_id` or `broker_account_label` (stays `NOT NULL DEFAULT ''`).
   5b. **CHECK `execution_broker_account_label_trimmed`**: `broker_account_label = btrim(broker_account_label)` (label is part of the dedupe key; `'A'` vs `'A '` would double-insert one fill).
6. Tests first: same id + different label → both insert; same id + same label → `IntegrityError`;
   `broker_trade_id=''` rejected; `skipped_conflict` accepted, unknown status rejected.
   Update `docs/data/schema.md` to match.
