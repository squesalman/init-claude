# Follow-ups from PR #1 code review (rounds 9–11)

Deferred on purpose. Each has a trigger: do it when that happens, not before.

| # | Item | Trigger | Owner | Where |
|---|---|---|---|---|
| 1 | **Proxy/prod settings**: `SECURE_PROXY_SSL_HEADER`, `CSRF_TRUSTED_ORIGINS`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`. Behind Caddy, every POST fails CSRF without them; `check --deploy` warns W008/W012/W016. | Building `compose.prod.yaml` | backend-engineer | `config/settings.py` |
| 2 | **DB-level tenant integrity**: composite FK `(user_id, fk_id) -> (user_id, id)` or Postgres RLS (ADR-0002 plans RLS). Replaces the Python `save()` guard, which is check-then-write, costs a SELECT per FK, and is bypassed by `bulk_create`, `bulk_update`, `QuerySet.update`. | Before any non-author account exists, or the importer uses bulk writes | database-engineer | `journal/models.py` `_check_cross_tenant_fks` |
| 3 | **`ImportBatch.raw_file` blob (<=10 MiB)** loads on every `ImportBatch` query. Move to a side table, or `.defer('raw_file')` in list queries. | Building the import-history page | database-engineer | `journal/models.py` |
| 4 | **DONE (de74698)** — **Dev env loading**: settings fail closed without `DJANGO_SECRET_KEY`. README has the `uv run --env-file .env` line; CLAUDE.md "Stack & commands" does not. | Next CLAUDE.md edit | orchestrator | `CLAUDE.md` |
| 5 | **TRIPWIRE DONE (PR #2, `journal/test_form_scoping.py`); base form + view rules still due with the first form or view** — **`default_manager_name="unscoped"` leaks tenants through `_default_manager`**: a ModelForm `ModelChoiceField`, `get_object_or_404(Model, ...)` or generic views list every user's rows. Rule: forms must set querysets from `for_user()` in `__init__`; never use `_default_manager` in views. | Building the first form or view | frontend-engineer + security-reviewer | `journal/models.py` Meta |
| 6 | **DECIDED — [ADR-0004](../adr/0004-topstep-dedupe-and-pairing.md)**. Migration spec below ("Row 6 migration spec"). | Before the importer | database-engineer | `journal/models.py` Execution, RawImportRow |
| 7 | **Blanket `*.csv` in `.gitignore`** hides CSV test fixtures. Add `!tests/fixtures/*.csv` (or similar). | First fixture CSV | backend-engineer | `.gitignore` |
| 8 | **`save(*args, **kwargs)` positional signature** dies in Django 6.0 (deprecated now). Also `save(False, False, None, [...])` positional `update_fields` bypasses the guard's `update_fields` logic. Switch to `save(self, **kwargs)` and drop the positional test. | Django 6 upgrade | backend-engineer | `journal/models.py` `save`, `test_save_accepts_django_real_positional_signature` |
| 9 | **DONE** — replaced the `UserManager.get_or_create`/`update_or_create` stubs with a guard in `User.save()` that rejects a non-hash `password` (covers `create()`, `setattr`, stock `get_or_create`). Still bypassed by `bulk_create`/`QuerySet.update`/`loaddata`. | — | — | `accounts/models.py` |
| 10 | **`SILENCED_SYSTEM_CHECKS = ['auth.E003']`** is project-wide. Replace with a custom check that asserts the `Lower(email)` unique constraint exists. | Any change to the email constraint | backend-engineer | `config/settings.py` |
| 11 | **Redundant CHECKs**: `execution_currency_not_blank` and the `~Q(risk_currency='')` term are covered by the currency regex CHECK. | Next migration on these tables | database-engineer | `journal/models.py` |
| 12 | **`available_timezones()` walks the tz db at import** (every `manage.py`, pytest, worker start). Validate with `ZoneInfo(value)` in try/except, or cache lazily. | Startup time annoys | backend-engineer | `accounts/models.py` |
| 13 | **DECIDED — [ADR-0005](../adr/0005-batch-delete.md)** (Accepted, option (a): counted-checkbox delete of journaled entries). No schema change. Build: backend-engineer from ADR-0005 (7 tests first), ux-designer fills `[TBD-0005]`. | Before/with the import UI | backend-engineer, ux-designer | `journal/models.py` on_delete rules unchanged |

## Row 6 migration spec (ADR-0004)

One migration, all additive/loosening, no data rewrite, safe on a populated table.

1. **`journal_execution.broker_trade_id`**: add `VARCHAR(128) NULL`, no default (metadata-only in PG).
   Model: `CharField(max_length=128, null=True, blank=True)`.
2. **CHECK `execution_broker_trade_id_not_blank`**: `broker_trade_id IS NULL OR broker_trade_id <> ''`
   (a stray `''` would merge unrelated trades into one matcher bucket).
3. **Replace `execution_broker_dedupe`** (RemoveConstraint + AddConstraint, same name):
   `UNIQUE (user_id, broker, broker_account_label, broker_execution_id) WHERE broker_execution_id IS NOT NULL AND broker_execution_id <> ''`.
   Condition unchanged; key only gets looser, so it cannot fail on existing rows.
4. **`RawImportRow` status**: append `("skipped_conflict", "Skipped (conflict)")` to `_STATUS_CHOICES`;
   `rawimportrow_status_valid` regenerates (drop + add). Fits existing `VARCHAR(20)`; no length change.
5. **No change** to `execution_import_requires_broker_execution_id` or `broker_account_label` (stays `NOT NULL DEFAULT ''`).
6. Tests first: same id + different label → both insert; same id + same label → `IntegrityError`;
   `broker_trade_id=''` rejected; `skipped_conflict` accepted, unknown status rejected.
   Update `docs/data/schema.md` to match.
