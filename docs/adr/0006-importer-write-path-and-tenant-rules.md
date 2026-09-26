# ADR-0006: Importer write path, and tenant rules for forms and views

- **Status:** Proposed
- **Date:** 2026-09-26
- **Deciders:** architect (proposed), user (pending)
- **Depends on:** [ADR-0002](0002-stack-revised.md) (tenant isolation, RLS trigger), [ADR-0003](0003-data-model.md)
  (§4 dedupe index, pre-fetch diff), [ADR-0004](0004-topstep-dedupe-and-pairing.md) (dedupe key, collision
  table, `broker_trade_id`), [ADR-0005](0005-batch-delete.md) (`for_user()` service pattern)
- **Resolves:** [`docs/data/follow-ups.md`](../data/follow-ups.md) row 5 (rules part); sets the trigger for row 2
- **Blocks:** `backend-engineer` (Topstep importer, first form/view), `frontend-engineer` (first form)

## Context

**Decision 1.** `UserOwned.save()` runs `_check_cross_tenant_fks()`. It is check-then-write, and it is
skipped entirely by `bulk_create`, `bulk_update` and `QuerySet.update` (follow-ups row 2). The Topstep
importer writes one `ImportBatch`, one `RawImportRow` per CSV line, and two `Execution`s per imported line
(ADR-0004). The real sample is 177 lines, so about 530 rows per upload.

One fact from the code changes the cost picture. The guard issues a `SELECT` per FK **only when the
related instance is not cached** (`field.is_cached(self)`). If the importer passes model instances
(`import_batch=batch`, `raw_import_row=row`), not ids, the guard reads `user_id` from memory and costs
zero queries.

**Decision 2.** Every `UserOwned` model sets `default_manager_name = "unscoped"`, so Django internals
that use `_default_manager` see every tenant's rows: `ModelChoiceField` defaults on a `ModelForm`,
`get_object_or_404(Model, ...)`, and the generic model views. `journal/test_form_scoping.py` already
fails when a project `ModelForm` on user-owned data does not inherit a class named `UserScopedModelForm`.
That class does not exist yet. No forms or views exist yet (`journal/views.py` is empty).

## Decision 1: per-row `create()`, instances not ids, one transaction (option a)

### Module boundaries

| Step | Where | DB? | Does |
|---|---|---|---|
| parse | `journal/importers/topstep.py` `parse(file_bytes) -> list[ParsedRow]` | no | BOM strip, header check, per line: raw dict of **strings**, then either two leg dicts (`Decimal`, UTC `datetime`, `{Id}:entry` / `{Id}:exit`, `broker_trade_id=Id`) or an error (unknown root, PnL cross-check mismatch, bad value). Pure, so it can be re-run over stored raw data. |
| import | `journal/services.py` `import_file(user, filename, file_bytes, account_label) -> ImportBatch` | yes | one `transaction.atomic()`: pre-fetch, classify, write |
| derive | `journal/matching.py` `derive_trades()` | read path only | **Not called by the importer.** Trades are computed on read (ADR-0002/0003). |

### Write sequence inside `import_file` (one transaction per upload)

1. `parsed = parse(file_bytes)`. Nothing is written if the header is unrecognised.
2. Pre-fetch **one** query:
   `Execution.objects.for_user(user).filter(broker="topstep", broker_account_label=label, broker_execution_id__in=<all leg ids>)`,
   with the fields ADR-0004 §2 compares.
3. Classify each row in file order with the ADR-0004 §2 table. Rows imported earlier **in the same file**
   count as "existing" (add their legs to the in-memory map as you go). This is the same table, applied
   to one more source. Without it, a repeated `Id` inside one file would hit the unique index and abort
   the whole upload.
4. `batch = ImportBatch.objects.create(user=user, ..., row_count=..., imported_count=..., skipped_count=..., failed_count=...)`.
   The counts are known after step 3, so the batch is never updated later.
5. For each row: `row = RawImportRow.objects.create(user=user, import_batch=batch, raw=<strings>, status=..., error=...)`.
   Then, if the row is `imported`: two `Execution.objects.create(user=user, raw_import_row=row, source="import", ...)`.
   **Always pass the instance, never `*_id=`.** That keeps the guard at zero queries.
6. `IntegrityError` from `execution_broker_dedupe` (a concurrent upload of the same file won the race)
   rolls back everything. Catch it **outside** the `atomic()` block and tell the user to upload again.
   The retry is idempotent: the other upload's rows now show as `skipped_duplicate`.

### Cost

About 1 pre-fetch + 1 + N + 2×(imported rows) INSERTs, with 0 guard SELECTs. For 177 rows that is
about 530 statements, roughly 0.1–0.3 s on a local Postgres. If someone passes ids instead of instances,
that adds about 354 SELECTs, which is still under a second. That is why a test pins it (below). At ADR-0003
volumes (a few thousand rows per file, uploads that happen rarely, by hand) this is fine. bulk_create
would save a fraction of a second on an action a user runs weekly.

### Guarantees kept

- **Idempotent + dedupe:** the ADR-0004 four-field partial unique index is the guarantee. The pre-fetch only produces the counts and statuses.
- **Raw kept:** `ImportBatch.raw_file` holds the bytes. `RawImportRow.raw` holds values as strings (see the models.py note on JSONB numerics). This ADR builds no re-parse command. `parse()` being pure is what makes one possible later.
- **All-or-nothing:** one `atomic()` per upload.
- **Decimal only:** `parse()` builds `Decimal(str_value)` from `csv` strings. `float()` is never called.

### Tests to write first (backend-engineer)

1. **Query budget:** importing a fixture of N lines runs in `<= 3 + 3*N` queries (`django_assert_max_num_queries`). This fails if someone switches to ids (guard SELECTs) or to a query per row for dedupe.
2. Same file twice → second batch is all `skipped_duplicate`, and the execution count does not change.
3. Repeated `Id` within one file (same contents) → second row is `skipped_duplicate`. With different contents → `skipped_conflict`. No `IntegrityError`.
4. A forced failure after some rows are written (for example, patch the second `Execution.create` to raise) → zero batch, raw-row, and execution rows remain.
5. Isolation: user B imports a file with the same `Id`s as user A → both import. Neither user sees the other's rows.

### Revisit trigger

Revisit when **either** of these happens:

- A real upload's import takes more than 2 s in prod, or a supported broker file is more than about 2,000 lines.
- Before any non-author account exists (ADR-0002's RLS trigger).

Then, in this order: `database-engineer` adds **composite FKs** `(user_id, fk_id) -> (user_id, id)` (plus
`UNIQUE (user_id, id)` on the three parent tables). **Only after that** may the importer use `bulk_create`.
The Python guard can then be deleted.

**Correction to follow-ups row 2:** it lists "composite FK **or** RLS" as interchangeable. They are not.
Postgres FK checks bypass row-level security. RLS stops a forgotten `for_user()` from *reading* another
tenant's rows, but it does not stop an INSERT from *referencing* another tenant's row. Only the composite
FK replaces the `save()` guard. RLS remains ADR-0002's separate read-side safety net.

## Decision 2: three rules, one base form, one grep test

### Rules (backend and frontend engineers)

1. **Forms.** Every `ModelForm` on a `UserOwned` model, or with an FK to one, inherits
   `UserScopedModelForm` from `journal/forms.py` and is built with `Form(..., user=request.user)`.
   `user` is never in `Meta.fields`. Plain `forms.Form` (for example, the upload form) is exempt when it
   has no `ModelChoiceField`.
2. **Lookups.** Objects are fetched only through `Model.objects.for_user(request.user)`. For a single
   object: `get_object_or_404(Model.objects.for_user(request.user), pk=pk)`. The queryset form is stock
   Django, so no helper is needed. Any FK id that arrives in a request (form, htmx, `django-ninja`) is
   resolved through `for_user(...)` before it is assigned.
3. **Banned in app code** (everything under `journal/` and `accounts/` except `migrations/`, tests,
   `admin.py`, and `journal/models.py`):
   - `_default_manager` and `.unscoped`
   - `get_object_or_404(Model, ...)` / `get_list_or_404(Model, ...)` with a model class as the first argument
   - `ListView`, `DetailView`, `CreateView`, `UpdateView`, `DeleteView`. Use function views. They are small, and there is no `get_queryset()` override to forget.
   - `bulk_create` and `bulk_update` (Decision 1, until the composite FKs exist)
   - `QuerySet.update()` on `UserOwned` models. No current feature needs it: executions are immutable and batch counts are written once. This one cannot be grepped (it would match `dict.update`), so code review enforces it.

Admin is the one sanctioned unscoped surface: staff only, which today means the author.

### The base form (about 10 lines, built with the first form)

```python
# journal/forms.py
class UserScopedModelForm(forms.ModelForm):
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.user_id is None:
            self.instance.user = user
        elif self.instance.user_id != user.pk:
            raise PermissionDenied  # instance was not fetched via for_user()
        for field in self.fields.values():
            qs = getattr(field, "queryset", None)
            if qs is not None and issubclass(qs.model, UserOwned):
                field.queryset = qs.model.objects.for_user(user)
```

Because every FK choice is scoped, validation rejects another tenant's id as an "invalid choice".
`CrossTenantForeignKeyError` can then fire only because of a bug, so a 500 is the correct outcome.
There is no need to translate it into a form error. This closes the open note in the
`CrossTenantForeignKeyError` docstring.

### Enforcement (extend `journal/test_form_scoping.py`)

1. Existing tripwire: keep it. Once `journal/forms.py` exists, replace the class-name match with
   `issubclass(c, journal.forms.UserScopedModelForm)`, so a local look-alike class named
   `UserScopedModelForm` no longer passes. Also assert `"user" not in c.base_fields`.
2. New grep test: walk the app `.py` files (with the exclusions from rule 3) and fail on the banned
   patterns in rule 3, except `QuerySet.update()`. To add an exception, edit the test's exclusion list,
   which makes it visible in PR review.
3. Behaviour: ADR-0002's mandatory isolation test still applies to each view. User B requests user A's id
   and gets a 404, with zero rows changed (same pattern as ADR-0005 test 5).

## Alternatives rejected

- **(b) `bulk_create` + an explicit tenant assertion in the importer.** It saves well under a second on
  an action a user runs rarely. It also adds a second, importer-only guard to keep in sync with the
  generic one. And the assertion would be check-then-write too, which fixes nothing that per-row `create()`
  leaves open.
- **(c) Composite FKs or RLS now.** Composite FKs are the correct end state, but Django 5.2 has no
  composite `ForeignKey`. They need `RunSQL`, three extra unique constraints, and state the ORM does not
  know about. That is real cost with no user benefit while only the author has an account and the importer
  writes through `save()`. RLS does not close the FK gap at all (see the correction above), and it needs
  per-request `SET app.user_id` plumbing. Both wait for the trigger.
- **A `get_scoped_or_404(request, Model, pk)` view helper.** It wraps one stock call. `get_object_or_404`
  already accepts a queryset.
- **Allow generic views with a mandatory `get_queryset()` override.** That cannot be checked by grep, and
  forgetting it leaks every tenant's rows. Function views cost a few lines more per page.
- **Change `default_manager_name` back to the raising manager.** That breaks reverse relations
  (`batch.rows.all()`) and the deletion collector, which is why it was set to `unscoped` (models.py comment).

## Consequences

- **Positive:** the importer goes through the same guard as every other write. Nothing new is built for
  tenancy. One base form and one grep test cover follow-ups row 5. Cross-tenant form input becomes a
  normal validation error.
- **Accepted:** the guard stays check-then-write. Under concurrency, a parent row could change owner
  between the check and the write, but no code path changes `user_id` on an existing row. That gap closes
  with the composite FKs at the trigger.
- **Accepted:** grep tests are crude. They catch the obvious forms and miss aliasing (for example,
  `m = Model; get_object_or_404(m, ...)`). Code review and the isolation tests cover the rest.
- **Accepted:** a concurrent duplicate upload fails the whole upload with a "try again" message. It does
  not partially succeed.

## Not built

Composite FKs, RLS, `bulk_create` in the importer, a re-parse command, a view helper, a CBV mixin,
per-row savepoints, locking to serialise uploads.

## Open questions

None.
