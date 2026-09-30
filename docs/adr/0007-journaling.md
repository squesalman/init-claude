# ADR-0007: Journaling (per-trade entry, "My rules", Avg R)

- **Status:** Accepted (user rulings 2026-09-30 on every open question, see "Rulings"; no open [GUESS])
- **Date:** 2026-09-30
- **Deciders:** architect (proposed), user (approved 2026-09-30)
- **Amends:** [ADR-0005](0005-batch-delete.md) §2 (stale guard, §7 below), [ADR-0006](0006-importer-write-path-and-tenant-rules.md) Decision 2 (error copy)
- **Depends on:** [ADR-0002](0002-stack-revised.md) (compute-on-read, `for_user()`), [ADR-0003](0003-data-model.md) §1 and §6
  (`User.trading_rules`, `JournalEntry`), [ADR-0005](0005-batch-delete.md) (delete dialog, stale guard),
  [ADR-0006](0006-importer-write-path-and-tenant-rules.md) (`UserScopedModelForm`, banned patterns),
  [`product/features/journaling.md`](../product/features/journaling.md) (behavior, AC 1-34),
  [`design/journaling.md`](../design/journaling.md) (layout and copy, single source),
  [`domain/pnl-and-matching.md`](../domain/pnl-and-matching.md) §3 (R rules, vectors 6-23)
- **Resolves (when built):** [`docs/data/follow-ups.md`](../data/follow-ups.md) rows 14, 26, 32, and the root cause of 36c
- **Blocks:** `database-engineer` (PR J1), `backend-engineer` (PR J2), `frontend-engineer` (PR J3)

This ADR adds no domain rule. Formulas, reason codes and precedence come from domain §3. Copy comes
from design §12. Behavior comes from the spec. If this ADR and one of those docs disagree, the other doc wins
and this ADR is the one that's wrong.

## Context

The schema already has what journaling needs: `JournalEntry` (1:1 to the opening execution, RESTRICT),
`User.trading_rules`, and both risk-currency CHECKs. What is missing is application code: a form, a
write path, the R computation, and the list and dialog changes. Two gaps in the code change the design:

1. `matching.Trade` has no entry-lot count and no multiplier. Domain §3 needs both: single-entry vs
   multi-leg decides whether a stop can give R, and stop-derived risk is `|entry - stop| * qty * multiplier`.
2. `JournalEntry` has only ever been created or deleted. The journal form is its first UPDATE path, and
   ADR-0005's max-pk stale guard can't see an update.

## Decision

### 1. Data model: no new table, one CHECK

| Item | Decision |
|---|---|
| Tables | None added. `JournalEntry` and `User.trading_rules` as they are (ADR-0003 §1, §6). |
| Attachment | Unchanged: `opening_execution_id` is the trade id. |
| Nullability | Unchanged. `rules_followed NULL` = unanswered; `note NOT NULL DEFAULT ''`; risk columns nullable. |
| New CHECK (PR J1) | `journalentry_planned_risk_positive`: `planned_risk_amount IS NULL OR planned_risk_amount > 0` (follow-ups row 14, whose trigger "before journaling forms ship" has now fired). The form is still the thing that shows the message. The CHECK makes the invariant hard. `risk_not_positive` stays in the R function as a backstop for legacy rows (domain §3). |
| No CHECK on text length | The 10,000 cap is enforced in forms only. A DB length CHECK would reject rows the spec never asked the DB to police (admin edits). |
| `risk_currency` | Never posted. The service sets it to `trade.currency` when planned risk is set and to `NULL` when it is blank (domain §3, vector 21). The existing CHECKs hold by construction. |

Indexes needed by the new queries all exist already (no index work):

| Query | Index |
|---|---|
| Opening execution by pk, scoped | PK |
| Executions in the opener's `(label, symbol)`, ordered | `execution_user_symbol_ts_idx` `(user, symbol, executed_at)` |
| Entry for one trade | `UNIQUE (opening_execution_id)` |
| All of a user's entries, for list and stats | `journalentry_user_flag_idx` `(user, rules_followed)`, which has `user` as its leading column |
| Rules save | `accounts_user` PK |

### 2. Re-import, re-match, delete-import

| Event | What happens to entries |
|---|---|
| Same file re-imported | Every row is `skipped_duplicate` and no execution changes, so every entry stays attached. |
| Different contents under the same Id | `skipped_conflict`. Executions are immutable, so the trade's currency, price and lots can't change underneath an entry through import. |
| Re-match that shifts an opener (only possible for `broker_trade_id = NULL` fills: manual or fill-only, and neither exists today) | The entry is orphaned: it drops out of the list, the stats and the journal URL (404), but it still counts in the delete dialog because it is attached to that batch's execution. ADR-0003 accepted this, and the spec puts orphan detection out of scope. Topstep trades can't shift, because each row is its own bucket (ADR-0004 §3). |
| Delete import | ADR-0005, unchanged except for the stale-guard amendment in §7. Entries go only behind the counted checkbox. |
| `risk_currency_mismatch` | Import can't change a trade's currency today (see the rows above), so this reason can only come from legacy rows or a future correction flow. It is still implemented and tested (vectors 11, 22), because the domain requires the backstop. |

### 3. Matcher: two new `Trade` fields (PR J2)

`journal/matching.py` `Trade` gains:

- `entry_lot_count: int`: incremented every time `_fifo` pushes a lot into the current draft (the `if qty:`
  branch). A flip's leftover is one lot of the new trade. 1 = single-entry, 2 or more = multi-leg (domain §3 Terms).
- `multiplier: Decimal`: the opening execution's `contract_multiplier` (domain §3: "the opening lot's").

For a single-entry trade `avg_entry_price` is the one lot's price, so the stop side-check and stop-derived
risk use it. Nothing else in the matcher changes, and the existing matcher tests must stay green.

### 4. R, Avg R and "Left out": one module, computed on read

All of this lives in `journal/stats.py`, as pure functions with no DB access. Three callers use them: the stat
cards, the journal page's risk status line (design 3.6), and the form's stop check. There is no second
implementation (design §10).

```python
def stop_on_loss_side(trade, stop_price) -> bool:        # single-entry only: (entry - stop) * sign > 0
def r_multiple(trade, entry) -> tuple[Decimal | None, str | None]:   # (R at 4 dp HALF_EVEN, reason)
def compute_stats(trades, entries_by_opening_id) -> dict[str, CurrencyStats]
```

- `r_multiple` follows domain §3's evaluation order exactly: `trade_open`, then planned risk set
  (`risk_not_positive`, `risk_currency_mismatch`, else valid, **with no fallback to the stop**), then stop set
  (`stop_price_multi_leg` if `entry_lot_count > 1`, otherwise `stop_not_a_risk` or valid), then
  `no_risk_input`. The reason codes are exactly the domain strings. `entry is None` gives `no_risk_input`
  (or `trade_open`).
- `CurrencyStats` gains `avg_r_left_out: int`. Per currency group: `n` = closed trades with non-null R.
  `avg_r` = the mean of the stored 4 dp values, quantized once to 2 dp `ROUND_HALF_EVEN`. `left_out` = closed
  trades with null R. Open trades are in neither. `avg_r = None` when `n = 0`.
- **Form error codes vs read-time reasons.** Only two domain codes are ever form `ValidationError` codes:
  `risk_not_positive` on `planned_risk_amount` and `stop_not_a_risk` on `stop_price`. **`stop_price_multi_leg`
  and `risk_currency_mismatch` are never form errors.** A multi-leg stop saves unchecked (vector 19), and the
  currency is derived, so the form can't produce a mismatch. Those two exist only as `r_multiple` reasons, which
  drive the status line and "Left out".
- The stop check runs only when the trade is single-entry **and** `planned_risk_amount` is blank on this
  submission (spec, vectors 9 and 20). It uses `stop_on_loss_side`, the same predicate `r_multiple` uses.

<!-- ponytail: R is recomputed for every trade on every /trades/ load, O(trades). Fine at ADR-0003
     volume; it rides the same TRADES_CEILING / materialization trigger as derive_trades. -->

### 5. Forms (PR J2, `journal/forms.py`)

- `NormalizedTextField(forms.CharField)`: `to_python` replaces `\r\n` with `\n` **before** the base
  class strips and `MaxLengthValidator` counts. Python `len()` counts code points, which is the spec's
  definition. `max_length=10000`, `required=False`. Django's built-in NUL-character validator stays on.
  The note and the rules both use this field.
- `JournalEntryForm(UserScopedModelForm)`: `Meta.fields = ["rules_followed", "note", "stop_price",
  "planned_risk_amount"]`. There is no `user`, `opening_execution` or `risk_currency` field, so anything posted
  under those names is ignored (AC 13). It is built as `JournalEntryForm(data, instance=entry_or_new, user=request.user, trade=trade)`.
  - `rules_followed`: two radios, values `true` and `false`, not required. An absent value cleans to `None`.
  - `stop_price` / `planned_risk_amount`: a small `PlainDecimalField` that trims the value, strips
    thousands-separator commas, and rejects anything else with the "not a number" message (design 3.5). It
    keeps Django's `max_digits`/`decimal_places` checks, mapped to design 4.3's messages (`decimal_places` gets
    the decimals message; `max_digits`/`max_whole_digits` get "That number is too large."). Scales are 20/10 and 19/4.
    An optional leading minus parses (ruling 1): a planned risk `<= 0` then fails with `risk_not_positive`,
    and a negative stop is a valid price, checked by the normal side check.
  - `opening_execution` is not a form field, so `ModelForm.validate_unique` never checks it. Uniqueness
    in a race is handled by the service (§6).
- `RulesForm(forms.Form)`: one field, `trading_rules = NormalizedTextField(...)`. It is a plain `Form` on
  `User` (not `UserOwned`), so ADR-0006 rule 1 doesn't apply. It never takes a user id.

### 6. Services (PR J2, `journal/services.py`)

```python
def find_trade(user, execution_id) -> tuple[Execution, Trade] | None
def save_journal_entry(user, opening, trade, *, note, rules_followed, stop_price,
                       planned_risk_amount) -> JournalEntry | None
```

- **`find_trade`**: `Execution.objects.for_user(user).filter(pk=execution_id).first()`. When that finds an
  execution, derive over `Execution.objects.for_user(user).filter(broker_account_label=opening.broker_account_label, symbol=opening.symbol).order_by("executed_at", "id")`,
  and return the trade whose `opening_execution_id == execution_id`. Return `None` for a missing id, another
  user's id, or a closing fill. The narrowing is correct because every matcher bucket sits inside one
  `(label, symbol)`. **Keep the `order_by`** (follow-ups row 27).
- **`save_journal_entry`**: the rules below, in this order.
  1. If every input is blank (`note == ""`, `rules_followed is None`, both risk values `None`) and no entry
     exists yet, return `None`. The view then shows "Nothing to save yet" (AC 5), and no row is created.
  2. Inside `transaction.atomic()`:
     `entry, _ = JournalEntry.objects.for_user(user).get_or_create(user=user, opening_execution=opening)`.
     **Pass `user=` explicitly.** `for_user()`'s filter is not carried into `create()`, and the NOT NULL
     `user_id` would fail. Pass the `opening` instance, not the id, so the cross-tenant guard costs no query.
     `get_or_create` catches the UNIQUE race and re-reads the row, so a second tab updates the first tab's row
     and never returns a 500 (AC 8).
  3. Assign `note` and both risk values. Set `rules_followed` **only when the submitted value is not
     `None`**: a missing answer never sets an answered entry back to unanswered (spec: "not returned to
     unanswered"; AC 7). Set `risk_currency = trade.currency if planned_risk_amount is not None else None`.
     Then `save()`.
- **Rules save**: `user.trading_rules = text; user.save(update_fields=["trading_rules"])`, on
  `request.user` only. It touches no `JournalEntry` (AC 27).

### 7. ADR-0005 amendment: the stale guard also covers edits

The journal form makes it possible to add a note to an entry that the delete dialog already showed as
"(no written note)", in another tab, between the confirm and the delete. Max pk can't see that, so the
writing would be lost unseen, which is what ADR-0005 exists to prevent. The fix is small, so it goes into PR J2:

- The signed `shown` token carries `"<max_pk>:<max_updated_at ISO>"` (either part may be empty/0).
  A token in the old format, a forged token, or a missing one reads as `(0, None)`, which fails safe (`StaleConfirm`).
- In `delete_import_batch`, inside the existing atomic block and before anything is deleted: if any of the
  batch's entries has `updated_at > confirmed_updated_at`, raise `StaleConfirm`. Everything else is unchanged.
- One extra test: edit a shown entry's note after the confirm renders, then POST, and nothing is deleted.
- ADR-0005 §2 now carries this amendment (user approved 2026-09-30).

### 8. Views and URLs (PR J2, `journal/views.py`, `journal/urls.py`)

| URL | Name | Methods | View |
|---|---|---|---|
| `/trades/<int:pk>/journal/` | `trade_journal` | GET, POST | `trade_journal`, `@htmx_login_required` |
| `/rules/` | `rules` | POST only (`require_POST`) | `save_rules`, `@htmx_login_required` |

Both are function views (ADR-0006 rule 3). `no-store` comes from the existing middleware.

**`trade_journal`:**

1. `find_trade(user, pk)`. If it returns `None`, return `_trade_not_found(request)`. That is **one** function,
   and it renders `journal/trade_not_found.html` with status 404 and design 4.6's copy. Missing ids, another
   user's ids, closing fills, and a trade that vanished before a POST all go through it, so AC 11 and 12
   ("identical") hold by construction. There is no project `404.html` (follow-ups row 21), so don't `raise Http404`.
2. Load `entry = JournalEntry.objects.for_user(user).filter(opening_execution_id=pk).first()`.
   **Build the whole page context before attempting a save**, so a failed save can re-render without another query.
3. GET: render the unbound form, prefilled from `entry`.
4. POST, form invalid: 200, error summary, values kept, risk section open if a risk field has the error.
5. POST, valid: `save_journal_entry(...)` wrapped in `except (DatabaseError, CrossTenantForeignKeyError)`.
   The handler logs the exception class, user id and model name only, then re-renders 200 with the submitted
   values and `JOURNAL_SAVE_FAILED` (AC 14, 14a; ruling 2). A `None` result re-renders 200 with
   `JOURNAL_NOTHING_TO_SAVE`. Anything else is a 302 to the same URL with the flash `JOURNAL_SAVED` (PRG).

**`save_rules`:**

- Reads `next` and accepts it only if `url_has_allowed_host_and_scheme` passes **and**
  `resolve(urlsplit(next).path)` is `trade_journal`. It then keeps only that pk. The redirect target is
  always server-built (`reverse("trade_journal", args=[pk])`), never the raw `next` string, which avoids the
  same-host HTTPS-to-HTTP downgrade in follow-ups row 1. If the check fails, the pk is None and the target is `/trades/`.
- htmx request: responds **200 in all three outcomes** (saved, invalid, failed), because htmx 2 swaps only 2xx.
  The body is `journal/partials/rules_panel.html` rendered open, with the status line, the field error, or
  `RULES_SAVE_FAILED` plus the submitted text. `Vary: HX-Request`.
- No-JS: on success, 302 to that server-built target with the flash `RULES_SAVED`. On error or failure,
  re-run `find_trade(user, pk)` and re-render the journal page with the rules panel open and the bound
  `RulesForm` (the same page-building helper as `trade_journal`). A pk the user doesn't own gets the same
  `_trade_not_found` 404. If there is no pk at all, return 400 with plain text. Only a tampered form can reach that.
- A `DatabaseError` is caught, and the rules text is kept (AC 14a covers `/rules/` too).

**Changes to existing views:**

- `trades`: one extra query, `JournalEntry.objects.for_user(user)` loaded into a dict keyed by
  `opening_execution_id`. It is passed to `compute_stats(derived, entries)` and to each row. The query
  count must stay constant as trades and entries grow.
- `import_delete` / `_delete_context`: each `journal_list` item's `url` is `reverse("trade_journal", args=[e.opening_execution_id])`,
  and a new `has_risk` field is set (`stop_price` or `planned_risk_amount` not null). The copy
  replacements are listed in §9. `shown_token` changes as in §7.

### 9. Template contract (view context keys; frontend builds against these)

Journal page (`journal/journal_form.html`):

| Key | Content |
|---|---|
| `trade` | dict: `id`, `symbol`, `direction`, `result`, `net_pnl` (display string or None), `is_open`, `opened`, `zone`, `quantity`, `entry`, `exit`, `duration`, `account` (None when blank), `entries` (`TRADE_ENTRIES` string when `entry_lot_count > 1`, else None) |
| `form` | `JournalEntryForm`, bound or unbound |
| `rules_form`, `rules_text` | `RulesForm`; the saved rules (for the preview) |
| `rules_open` | True only after a rules save, error or failure |
| `rules_status` / `rules_notice` | `RULES_SAVED` / `RULES_SAVE_FAILED`, or None |
| `rules_next` | this journal URL (hidden `next`) |
| `risk_open` | a risk value saved or typed, a risk error, or `entry_lot_count > 1` |
| `risk_currency` | trade currency (the suffix and `RISK_HELP`) |
| `both_set` | both risk fields have a value in this render |
| `r_status` | design 3.6 string from `r_multiple`'s reason, or None (no line for `no_risk_input`, and none when there is no entry with a risk value, unless the trade is open) |
| `notice` | None, or `{"variant": "info" / "attention", "text": ...}` for nothing-to-save / save-failed |
| `back_url` | `/trades/#trade-<id>` |
| `copy` | `journal.copy` |

POST field names (fixed; the later inline-htmx PR must reuse them): `rules_followed` (`true`/`false`),
`note`, `stop_price`, `planned_risk_amount`; the rules form uses `trading_rules` and `next`.

Rules panel partial (`journal/partials/rules_panel.html`): the swap target `id="rules-panel"`. Its form has
`action="{% url 'rules' %}" method="post"` plus `hx-post` to the same URL, `hx-target="#rules-panel"`, and
`hx-swap="outerHTML"`, and a hidden `next`. It is never nested inside the journal form, and it sits first in DOM
order. The same partial renders inside the page and as the `/rules/` htmx response. A 5xx or network failure uses
the existing `data-request-error` pattern. A 401 comes back with `HX-Redirect` from `htmx_login_required`.

The journal form itself uses **no htmx** in this slice: a full POST, PRG, and the existing `data-busy` pattern.

`/trades/` rows and cards gain `id` (opening execution id), `journal_url`, `journal_label`,
`journal_state` (`add` / `followed` / `not_followed` / `note_only` / `risk_only` / `note_and_risk`, the icon key per
design 5.3), and `journal_sr` (`LIST_JOURNAL_SR_CONTEXT`). An existing entry with nothing in it reads as
`add` (design §11 item 2). `cards.avg_r` gains `left_out` (the `AVG_R_LEFT_OUT_ONE` / `_MANY` string, or None when N = 0).

Delete dialog items gain `url` and `has_risk`. Copy replacements in `journal/copy.py`: `DELETE_JOURNAL_BODY_ONE`,
`DELETE_JOURNAL_BODY_MANY` and `DELETE_LIST_HELPER`, plus the new `DELETE_HAS_RISK`, `DELETE_VIEW_TRADE` and
`DELETE_VIEW_SR`. In `accounts/copy.py`: `AVG_R_HELP` and `CALC_AVG_R`. Every string is verbatim from design §12.

### 10. Tenant isolation and required tests

Tenant rules (ADR-0006, unchanged):

- Every lookup starts from `for_user(request.user)`.
- The form is a `UserScopedModelForm`, and `test_form_scoping.py` picks it up automatically.
- The banned-pattern grep test still passes.
- `/rules/` writes only `request.user`.
- The isolation test's `UserOwned.__subclasses__()` enumeration already covers `JournalEntry`.

Tests to write first, all left in the repo:

1. **Vectors 6-23 as `r_multiple` / `compute_stats` unit tests.** Vector 23 must fail under `ROUND_HALF_UP`.
   Vector 19 must give `stop_price_multi_leg`, never `stop_not_a_risk`.
2. **Matcher**: `entry_lot_count` is 1 for vectors 1, 18 and the flip leftover (vector 3), and 2 for vector 2.
   `multiplier` comes from the opener.
3. **Isolation**: user B GETs and POSTs user A's `/trades/<id>/journal/` and gets a 404 with the same status, template
   and text as a missing id's (not byte-identical: masked CSRF tokens differ per render), with zero rows changed. A closing fill's id also gives the same 404 (AC 11, 12). User B's
   `/rules/` changes only B (AC 29).
4. **Form**: `\r\n` normalisation at the 10,000 boundary (AC 10). A whitespace-only note is stored as `""`
   (AC 9). The `stop_not_a_risk` side-check applies to single-entry only, and is skipped when planned risk is
   set (AC 16, 18). `risk_not_positive` (AC 15). A posted `opening_execution`, `user` or `risk_currency` is
   ignored (AC 13).
5. **Service**: all-blank with no entry creates no row (AC 5). An absent answer keeps the existing one (AC 7).
   The currency is derived and cleared (AC 17). An entry that already exists when `get_or_create` runs gets
   updated, and exactly one row remains (AC 8).
6. **Failure path**: force a `DatabaseError` in the save. The response is 200 and contains `JOURNAL_SAVE_FAILED`
   and the submitted note (AC 14a). Same test for `/rules/`. A `CrossTenantForeignKeyError` gets the same render
   plus a log line with no values.
7. **List**: the six labels (AC 31). `/trades/` query count doesn't change with the number of entries. The
   Avg R card strings for vectors 17 and 23 and for n = 0 (AC 20-22).
8. **Delete dialog**: the risk-only entry (AC 32), the `url` (AC 33), and the §7 edit-after-confirm stale test.
9. **Cache headers**: `/trades/<id>/journal/` gets its own `private, no-store` test, because it takes an id
   and so can't join the parametrized list. POST `/rules/` responses are also checked.

### 11. PR split

| PR | Owner | Contents | Depends on |
|---|---|---|---|
| **J1** | `database-engineer` | Migration: add `journalentry_planned_risk_positive`. Before running it, check no existing row has a value `<= 0` (the check fails `migrate` otherwise). Test first. Update `docs/data/schema.md`. Whether to fold in follow-ups row 11's redundant `~Q(risk_currency='')` term (its trigger, "next migration on these tables", also fires here) is database-engineer's call; it changes no behavior. | none (CHECK approved, ruling 4) |
| **J2** | `backend-engineer` | §3-§10: matcher fields, R/stats, forms, services, views, URLs, copy strings, the ADR-0005 guard amendment, and all tests. It ships **working, unstyled templates** (semantic markup, existing `field.html` / Notice / error-summary partials) so the AC tests can assert on real HTML. | none: parallel with J1 (J2 touches no model or migration; the schema it needs already exists) |
| **J3** | `frontend-engineer` | Design-complete templates on J2's context contract (§9): `trade_summary`, the radio tiles, the `rules_panel` styling and `details[open] + .rules-preview`, `suffix_input`, the Alpine CSP `charCounter` (it must count code points, e.g. `[...value.replace(/\r\n/g, "\n")].length`, not `.length`, which counts UTF-16 units, so it agrees with the server), the `plus`/`document`/`pencil` icons, the Journal column and card link, the Avg R "Left out" line, and the delete-dialog row line and link. No new dependency. | J2 merged |

J2 and J3 run in sequence, not in parallel, because both touch the same templates. Then `qa-engineer`
(AC 1-34, vectors 6-23), then `security-reviewer` (two new write endpoints, the trade-id lookup, and the `next` handling).

## Alternatives rejected

- **A `trade` table so journal entries hang off a stable trade id.** Rejected by ADR-0003 and still rejected:
  a second source of truth, and no measured need.
- **Deriving every trade for the journal page.** It would work, but narrowing to the opener's
  `(label, symbol)` costs the same code and loads far fewer rows. It is correct because buckets never cross that pair.
- **`form.save()` instead of a service.** It can't express three rules this feature needs: create no row
  when all fields are blank, keep an existing answer when none is posted, and update the other tab's row in a race.
- **A plain `forms.Form` for the entry.** Spec AC 14 and ADR-0006 rule 1 require `UserScopedModelForm`, and
  the ModelForm also prefills from the instance.
- **htmx for the journal form now.** Decision 1 of the spec defers it to its own PR.
- **A DB CHECK on note/rules length.** It would duplicate the form rule and turn admin edits into errors,
  with no user benefit.
- **Leaving the updated-entry gap in ADR-0005 as a documented ceiling.** Rejected: the fix is a few lines,
  and the gap loses writing unseen, which is exactly the failure ADR-0005 exists to prevent.

## Consequences

- **Positive:** no new tables, no new dependencies, one R implementation for three callers, the tenant
  rules unchanged, one small additive migration.
- **Accepted:** orphaned entries (from a shifted opener) are invisible, except in the delete dialog. Only
  non-Topstep fills can produce them, and none exist yet.
- **Accepted:** without JS, a rules save loses unsaved journal-form text (design 3.2).
- **Accepted:** if two tabs save the same entry, the last one wins, with no merge and no message (spec AC 8).
- **Accepted:** `/trades/` gains one query and an O(trades) R pass.

## Not built

The inline htmx journal panel, orphan detection and repair, rule versioning or snapshots, deleting or
clearing an entry, autosave or drafts, a "last used planned risk" suggestion, tags and emotions, filters and
sorting on journal state, a settings page, a `django-ninja` endpoint, and a project `404.html`.

## Rulings (user, 2026-09-30)

1. **Negative numbers:** the parser accepts an optional leading minus. A planned risk `<= 0` fails with
   `risk_not_positive` ("Planned risk needs to be more than 0."); a negative stop is a valid price. Design 3.5
   updated to match.
2. **Cross-tenant error copy:** follow the spec. The journal form shows `JOURNAL_SAVE_FAILED` (rules form:
   `RULES_SAVE_FAILED`). ADR-0006 Decision 2 amended.
3. **Follow-ups row 16:** build now. The TopstepX stop-order check stays with the user and doesn't block.
4. **CHECK approved:** `journalentry_planned_risk_positive` ships in PR J1 (`database-engineer`).
5. **ADR-0005 amendment approved:** `max(updated_at)` is signed into the delete token, and any change reads as stale (§7, PR J2).

Resolved (2026-09-30): ADR-0003 §6 now says RESTRICT, matching the code, `docs/data/schema.md` and ADR-0005. No open conflict.

## Amendment (J2 as built, 2026-09-30; user accepted after advisor review)
- **Save service (§6 step 2):** `JournalEntry.objects.for_user(user).update_or_create(user=user, opening_execution=opening, defaults=...)` replaces `get_or_create` plus `save()`. It is built on `get_or_create` (same UNIQUE-race re-read, AC 8) and adds a row lock. A missing yes/no is left out of `defaults`, so it never clears a saved answer. `updated_at` must stay in `update_fields`: the ADR-0005 stale guard depends on it (test_4h).
- **Stale check (§7):** folded into the delete, not a separate query before it. The delete filters `pk <= max_pk` and `updated_at <= signed`, then the existing "anything left?" `exists()` raises `StaleConfirm` inside the same `atomic()`, which rolls back everything. Nothing is deleted on a stale confirm (test_4h, two-entry case included).
- **Template contract (§9):** `multi_leg` and `stop_help` are not context keys. The per-trade stop and risk help is set as `help_text` on the fields inside `JournalEntryForm.__init__` (`STOP_HELP_LONG` / `STOP_HELP_SHORT` formatted with the entry / `STOP_HELP_MULTI_LEG`, and `RISK_HELP` with the currency), so the template renders `field.help_text` and the input's `aria-describedby` points at it on GET and on a POST re-render. `risk_open` also opens when a risk value was typed.
