# ADR-0008: Manual trade entry (pairing key, edit in place, close, delete, double submit)

- **Status:** Accepted (user rulings 2026-10-13: edit with a wrong-side stop is allowed and shows `stop_not_a_risk`; Close double submit follows AC 36, no token on Close; `broker` joins the bucket key). No open [GUESS].
- **Date:** 2026-10-13
- **Deciders:** architect, user (accepted 2026-10-13)
- **Amends (after acceptance, text in "Amendments to other ADRs"):** [ADR-0003](0003-data-model.md) §4 (immutability), [ADR-0004](0004-topstep-dedupe-and-pairing.md) §3 lines 90 and 96 (bucket key, manual key), [ADR-0005](0005-batch-delete.md) §2 (re-pointing ceiling), [ADR-0007](0007-journaling.md) §2 and Consequences (opener shift)
- **Depends on:** [`product/features/manual-entry.md`](../product/features/manual-entry.md) (AC 1-53, behavior), [`design/manual-entry.md`](../design/manual-entry.md) (draft; layout and copy), [`domain/pnl-and-matching.md`](../domain/pnl-and-matching.md) §1, §3, §7, vectors 24-34, ADR-0003 to ADR-0007
- **Blocks:** `backend-engineer` (PR M2), `frontend-engineer` (PR M3). `database-engineer` gets docs and docstrings only (M1, no migration).

This ADR adds no domain rule except one bucket-key change (§1), which `trading-domain-expert` must confirm in
`pnl-and-matching.md` §1. Formulas, time rules and vectors come from the domain doc. Behavior comes from the spec,
and copy comes from the design doc. If this ADR disagrees with any of them, this ADR is the one that is wrong.

## Context

The schema already holds a manual trade: `Execution.source='manual'`, `broker='manual'`, `broker_execution_id`
NULL. The spec adds six things the code does not do yet:

1. A pairing key per manual trade. With a NULL `broker_trade_id`, every manual fill for one label and symbol lands
   in one FIFO bucket (`journal/matching.py:48`, vector 30 companion).
2. The first UPDATE path on `Execution`. ADR-0003 §4 says "never UPDATEd".
3. A second delete path that has to get past the RESTRICT FK on `JournalEntry.opening_execution`.
4. A write path with no dedupe key, which needs a double-submit guard (AC 14).
5. Typed local times, with DST gaps and overlaps rejected (AC 21).
6. Point value, looked up or typed (AC 16-19).

Facts from the code that shape the decisions:

- `config/middleware.py` `UserTimezoneMiddleware` already activates `User.timezone` on every authenticated request.
  With that zone active, a plain `forms.DateTimeField` already rejects nonexistent and ambiguous times (domain
  §7.2, verified). If the stored zone name is bad, the middleware falls back to UTC.
- The bucket key is `(label, symbol, broker_trade_id)`. It has no `broker`. The Topstep importer accepts any
  `\S{1,100}` as an `Id` (`journal/importers/topstep.py` `_ID`).
- Many test factories create `source='manual'` fills with a NULL key, as stand-ins for fill-only data (multi-leg
  vectors). Examples: `journal/tests.py`, `test_journal_views.py`, `test_qa_journaling.py`.
- `_account_labels()` in `journal/views.py` is filtered to `broker='topstep'`.

## Decision

### 1. Pairing key: a generated `broker_trade_id` per manual trade, and `broker` in the bucket key

| Option | Verdict |
|---|---|
| **A. `broker_trade_id = "manual:" + 32 hex chars` (a uuid4 nonce), set on the entry fill at add and copied to the exit fill by Close. The matcher buckets by `(broker, label, symbol, broker_trade_id)`.** | **Recommended.** |
| B. The same key, with the matcher unchanged | Rejected. Topstep `Id`s are any non-blank string, so a key that "can never collide" holds only by luck. A hand-crafted CSV whose `Id` is `manual:<hex>` would pair against the user's own manual trade. |
| C. A new column (`manual_trade_id`, or a self-FK `pairs_with`) | Rejected. It needs a migration and a second pairing concept, while `broker_trade_id` already means "these fills are one round trip" (ADR-0004 §3). |
| D. Key = the entry fill's own pk | Rejected. It is only known after the insert, so it needs an INSERT followed by an UPDATE, and it is not available as a form token (§4). |

- **Matcher change (PR M2, one token):** `journal/matching.py:48` becomes
  `key = (execution.broker, execution.broker_account_label, execution.symbol, execution.broker_trade_id)`.
  - Buckets never cross brokers. Manual and imported fills therefore can never pair, whatever the key strings are
    (AC 23, vector 31).
  - The change does nothing to today's data: every imported fill is `topstep`.
  - A future fill-only importer's NULL-key fills share one bucket per broker, which is correct: different brokers
    are different accounts.
  - The two fake-execution builders that feed `derive_trades` gain `broker="topstep"`:
    `test_matching.py` `ex()` and `test_topstep_parser.py` `as_executions()`. Every other caller passes real
    `Execution` rows.
  - `find_trade`'s `(label, symbol)` narrowing stays correct, because every bucket still sits inside that pair.
- **Key format:** `manual:` + `uuid4().hex`, 39 characters.
  - It fits `VARCHAR(128)` and passes `execution_broker_trade_id_not_blank`.
  - It is exempt from `execution_broker_dedupe`, because `broker_execution_id` stays NULL (AC 2).
  - With 122 random bits, two manual trades never share a key.
- **The key is the add form's token** (§4): one value serves as both the pairing key and the double-submit guard.
- **The entry fill stays the opener for the trade's whole life**, so the trade id (`opening_execution_id`) and the
  journal URL survive Close and Edit. Three rules guarantee it together:
  1. Own bucket (above).
  2. Exit at or after entry on add, close and edit, compared in UTC (domain §7.1, vector 32b). §2 re-checks this
     under the row lock.
  3. On equal times, the entry has the lower pk. Add inserts the entry fill before the exit fill in one
     transaction. Close always inserts later. Edit never changes a pk (vector 32a).
- **Migration: none.** A CHECK `source='manual' => broker_trade_id IS NOT NULL` would be a cheap DB backstop. It
  is deferred because it breaks the NULL-key manual test fixtures above, which stand for fill-only data. The single
  write path (§5) and vectors 30/31 are the guarantee for now. Follow-up below.

### 2. Edit in place (`source='manual'` only)

- **Lock set: empty.** No field locks when a journal entry exists.
  - Every way an edit could orphan the entry is closed by §1's three rules. Symbol, label, side and quantity are
    written to both legs, so the bucket and the opener stay the same.
  - Locking side or symbol would force delete-and-re-add for exactly the typo the feature exists for. That loses
    the journal entry, which is the outcome AC 39 forbids.
  - Consequence for M3: no `locked_fields` key, no locked-row rendering, no `LOCKED_REASON`. Design 7.2 is dropped.
- **Editable fields:**
  - All trades: symbol, side, quantity, entry price, entry time, point value (only when the root is unknown, §7)
    and Account label.
  - Closed trades only: exit price, exit time and Fees. The exit pair is required, so a closed trade can't be
    reopened (AC 41).
  - Open trades: no exit and no Fees inputs (AC 29, 38). A tampered POST naming them writes nothing, because the
    form for that shape has no such fields.
  - Currency is never posted. The service writes `USD`.
- **What is shared and what is per leg** (domain §7.4):
  - Shared, always written to every leg the trade has: symbol, label, quantity, `contract_multiplier`, currency,
    the key, and side (the entry takes the chosen side, the exit takes the opposite).
  - Per leg: price and time. Fees go on the exit leg only (the entry is always 0).
- **One transaction, rows locked, decided from the database and not from the form:**
  1. `_manual_fills(user, pk)` (§5) locks the trade's fills with `select_for_update()`.
  2. The shared fields go to every locked leg.
  3. Per-leg values go only to the legs the form carried.
  4. Exit at or after entry is re-checked against the locked rows.

  This covers an open-trade edit form posted after another tab closed the trade. The exit that Close added gets
  the new shared fields, so the legs can't disagree (vector 33). If the new entry time is after that exit,
  `ExitBeforeEntry` is raised, nothing is written, and the view re-renders with `EXIT_BEFORE_ENTRY` on
  `entry_time`. That race is rare, and the wording is accepted. Same-shape edits from two tabs: the last write
  wins, as for the journal (design §11 item 9).
- **How the UPDATE is written:** `fill.save(update_fields=[...])` per leg. It never touches `user`, `source`,
  `broker`, `broker_execution_id`, `raw_import_row` or `created_at` (AC 38). `QuerySet.update()` stays banned
  (ADR-0006 rule 3). Because no guarded FK is in `update_fields`, the cross-tenant guard costs no query.
- **Imported fills can't be edited.** The guard is in the service, not the database:
  - `_manual_fills()` filters `source='manual'` and a non-NULL key. It is the only way close, edit and delete
    reach a fill, so an imported id (or a NULL-key manual fixture) gives `None`, and the view returns the one 404
    (AC 42).
  - Tests:
    - (a) For an imported trade, GET and POST on `/edit/`, `/close/` and `/delete/` all give the 404, and no row
      changes.
    - (b) `edit_manual_trade`, `close_manual_trade` and `delete_manual_trade` called directly with an imported pk
      return `None`, and the row is unchanged after `refresh_from_db()`.
  - A Postgres trigger that rejects UPDATEs on `source='import'` rows is rejected for now. It is `RunSQL` the ORM
    can't see. It must not block the UPDATE the deletion collector issues through `raw_import_row` SET_NULL. And it
    protects a path nobody writes, since the importer only inserts. Revisit trigger: a second code path that writes
    `Execution`.
- **Unchanged time field** (AC 21, domain §7.2):
  - Each time field is compared with its rendered initial value, `localtime(stored).strftime("%Y-%m-%dT%H:%M")`.
    The posted text is stripped first, and `" "` is read as `"T"`.
  - If they are equal, the stored UTC instant is kept, and parsing, the DST check and the future check are all
    skipped. The value still takes part in exit at or after entry.
  - Manual times are minute precision (the input formats have no seconds), so the round trip is exact.
- **Stop and `risk_currency` after an edit:** allow the edit, and the existing backstop handles it (option (b)
  below). Currency can't change, so `risk_currency` can't go stale, and there is no code for it.

| Stop that ends up on the wrong side of the new entry or side | Verdict |
|---|---|
| (a) Refuse the edit (`STOP_RECHECK_REFUSED` under Entry price) | Rejected (recommended against). It is circular: the stop lives on another page, and the copy tells the user to fix the stop before fixing the price. That blocks the typo fix whenever the stop, not the price, was right. |
| **(b) Allow. `r_multiple` returns `stop_not_a_risk` (vector 29b). The trade drops out of Avg R into "Left out" (AC 40). The journal page shows `R_STATUS_STOP_NOT_A_RISK` after the redirect.** | **Recommended.** No new code, no new copy, no journal text lost. |

  **Stated consequence of (b):** the journal form runs the stop side-check on every submission where planned risk
  is blank (ADR-0007 §4). The next journal save on that trade therefore fails with `stop_not_a_risk` on the stop
  field, which is still prefilled, until the user changes or clears it. Values are kept on that re-render, so no
  text is lost. This is the nudge, in the place the stop lives. **User ruling needed** (see Rulings needed).

### 3. Delete a manual trade

- **Order, in one transaction:**
  1. Lock the fills (`_manual_fills`).
  2. Delete the journal entry, if it was confirmed.
  3. Refuse if any entry is left.
  4. Delete every fill of the trade: one for an open trade, two for a closed one.

  RESTRICT stays. A bare `execution.delete()` on a journaled fill still raises `RestrictedError` (AC 47,
  regression test kept).
- **Counted confirm and stale guard (ADR-0005 §2 and the ADR-0007 §7 pattern, one entry instead of N):**
  - The confirm signs `"<entry pk or 0>:<entry updated_at ISO or ''>"` with `django.core.signing.Signer`, salt
    `trade-delete:<user_id>:<trade pk>`, into the hidden `shown` field. Reuse the view helpers by giving
    `_shown_signer` a purpose prefix instead of the hard-coded `import-delete-max-pk`. Keep the import salt string
    byte-identical, so import confirms that are already open don't all read as stale.
  - A forged, old or missing token reads as `(0, None)`, which means "nothing shown".
  - If something was shown and the box is not ticked, the confirm re-renders with `DELETE_NOT_TICKED`, and nothing
    is deleted.
  - Inside the transaction, the entry is deleted only if `pk <= shown_pk` and `updated_at <= shown_at`. If an
    entry still exists afterwards, `StaleConfirm` is raised and everything rolls back. The confirm then re-renders
    with the fresh entry and `MANUAL_DELETE_STALE` (AC 46).
  - **What StaleConfirm means here:** an entry was added to the trade, or edited, after the confirm rendered. Same
    class as the import delete, reused.
- **A journal save that races the delete:** the delete holds `FOR UPDATE` on the fills. A concurrent journal insert
    has to take `FOR KEY SHARE` on the same opening fill for its FK check, so it waits. When the delete commits, the
    insert fails its FK check. The journal view catches that as a `DatabaseError` and re-renders with
    `JOURNAL_SAVE_FAILED`, keeping the text. Writing is never lost silently. As in `delete_import_batch`, an
    `IntegrityError` from the delete itself is mapped to `StaleConfirm`.
- **Double-clicked delete:** the second POST finds no trade and gets the one 404 page (design §8.3: "Trade already
  gone"). No session memory like the import `_gone()`. YAGNI.
- **Nothing else is touched:** other manual trades (different keys), imported trades and batches (manual fills have
  no `raw_import_row`) and other users' rows (AC 48). ADR-0005's batch delete never selects manual fills.

### 4. Double-submit guard: the signed add-form token, serialized per user

| Option | Verdict |
|---|---|
| **A. The add GET renders `form_token = Signer(salt="trade-add:<user_id>").sign(uuid4().hex)`. The nonce becomes the trade's key (`manual:<nonce>`). The POST replays by looking the key up under a per-user row lock.** | **Recommended.** Nothing new is stored, and there is no migration. |
| B. A server-side token table (`FormToken`, user, token, result) | Rejected. A new table and migration, and rows pile up with no worker to clean them. |
| C. Content-based dedupe | Rejected by the spec: two identical scalps are legitimate (AC 14). |
| D. JS button disable only | Already there (`data-busy`). Not enough on its own: no JS, and Back followed by a resubmit. |

- **Signed**, so a tampered POST can't choose its own bucket. For example it can't set an imported trade's
  `broker_trade_id`, or another manual trade's (AC 23, 50). A bad or missing signature writes nothing: the form
  re-renders 200 with a fresh token, the values kept, and `MANUAL_SAVE_FAILED`. A validation re-render keeps the
  posted token if it is valid.
- **Serialized:** inside `transaction.atomic()`, `select_for_update()` on the user's own `accounts_user` row runs
  first. Then `for_user(user).filter(source='manual', broker_trade_id=key)`: if a fill is found, return its trade's
  opening pk (the replay). Otherwise insert the entry, then the exit.
  - Without the lock, two concurrent POSTs could both miss the lookup and insert two entry fills with one key. The
    matcher would merge them into one trade of double size, which is worse than a duplicate.
  - `# ponytail: per-user lock on manual adds; a user adds trades by hand, seconds apart, so contention is nil.`
- **Replay result:** the same 302 to `/trades/<opening id>/journal/` with `TRADE_ADDED`, as the first request.
- **Accepted, stated:**
  - (1) Values changed after Back and resubmitted with the same token are ignored. The journal page shows what was
    saved, and Edit fixes it. This is what AC 14 asks.
  - (2) Back and resubmit after deleting that trade adds it again. The user did resubmit the form. No `max_age`:
    an expiring token would fail a form left open overnight, and journaling friction is the top risk.
- **Close needs no token.** Under the lock, a trade that already has an exit means no second fill. The response is
  a 302 to the journal page with `ALREADY_CLOSED`, which covers a double submit and a second tab (AC 36). Edit is
  idempotent, so it needs no token either. Delete is covered by the 404 above. Design 6.2 says a same-token close
  replay gets "the same redirect as the first". The target is the same URL; only the flash differs. Reported as a
  conflict, see Rulings needed.

### 5. Routes, views, services, ownership

| URL | Name | Methods | View | Notes |
|---|---|---|---|---|
| `/trades/new/` | `trade_add` | GET, POST | `trade_add` | form token, PRG to the journal page |
| `/trades/point-value/` | `trade_point_value` | GET | `trade_point_value` | htmx partial; no DB access |
| `/trades/<int:pk>/close/` | `trade_close` | GET, POST | `trade_close` | GET on a closed trade: 302 plus `ALREADY_CLOSED` |
| `/trades/<int:pk>/edit/` | `trade_edit` | GET, POST | `trade_edit` | |
| `/trades/<int:pk>/delete/` | `trade_delete` | GET, POST | `trade_delete` | htmx: dialog body; no JS: full page; GET never deletes |

All of these are function views in `journal/views.py` with routes in `journal/urls.py` (ADR-0006 rule 3).

**Rules that apply to every view:**

- `@htmx_login_required`, except `trade_point_value`. That one returns a bare `HttpResponse(status=401)` with **no
  `HX-Redirect`** when the user is logged out (design 5.4), so a typed form is never thrown away. It can't use
  `htmx_login_required`, which adds the header.
- `require_http_methods`. CSRF comes from the middleware on every POST, and `private, no-store` from
  `NoStoreWhenAuthenticatedMiddleware`.
- `pk` is always the opening fill. A closing fill's id, an imported id, a missing id and another user's id all get
  `_trade_not_found` (one template, status 404; AC 42, 49).
- `trade.is_manual = opening.source == "manual" and opening.broker_trade_id is not None`. This is the same rule as
  the gate, so a NULL-key manual row (test fixtures only) never shows links that lead to a 404. It is read from
  `find_trade`'s opening fill, with no extra query. `trade_journal` passes it, and the template uses it to show
  Close, Edit and Delete.
- **GET does not lock.** `ATOMIC_REQUESTS` is off, and `select_for_update()` outside `atomic()` raises
  `TransactionManagementError`. A GET of close, edit or the delete confirm uses `find_trade(user, pk)` plus the
  `is_manual` rule, and anything else gets the 404. Only the POST services call `_manual_fills`, inside their own
  `atomic()`.
- **Failure path (AC 15).** `trade_add`, `trade_close` and `trade_edit` catch
  `(DatabaseError, CrossTenantForeignKeyError)` **outside** the service's `atomic()`, so the rollback has already
  happened.
  - They log the class and user id only, then re-render 200 with the submitted values and `MANUAL_SAVE_FAILED`.
    This is ADR-0006 as amended by ADR-0007, the same pattern as `trade_journal`.
  - `ExitBeforeEntry` is caught there too and becomes a field error. `AlreadyClosed` becomes a 302 with
    `ALREADY_CLOSED`.
  - `trade_delete` catches `StaleConfirm` (re-render with the fresh entry and `MANUAL_DELETE_STALE`) and
    `DatabaseError` (`DELETE_FAILED`), both outside the atomic, as `import_delete` does.

**Services** (`journal/services.py`, new "Manual entry" section; signatures for M2):

```python
MANUAL = "manual"
MANUAL_CURRENCY = "USD"           # spec ruling: fixed, never posted
class AlreadyClosed(Exception): ...
class ExitBeforeEntry(Exception): ...   # raised under the lock; the view adds EXIT_BEFORE_ENTRY

def _manual_fills(user, pk) -> list[Execution] | None:
    """The one gate for writes; called only inside a service's atomic().
    for_user + source='manual' + key not NULL; locks the key's fills
    (select_for_update, order_by("executed_at", "id")); None unless fills[0].pk == pk
    and there are 1 or 2 fills (more: log and return None)."""
def add_manual_trade(user, nonce, **cleaned) -> int            # opening pk; replay returns the first
def close_manual_trade(user, pk, *, exit_price, exit_at, fees) -> bool | None   # None = not found
def edit_manual_trade(user, pk, **cleaned) -> bool | None
def delete_manual_trade(user, pk, shown_pk, shown_at) -> bool | None  # True = an entry went too; StaleConfirm
```

- Every create passes `user=` and model instances (ADR-0006/0007). Close copies symbol, label, quantity, multiplier,
  currency and key from the **locked** entry fill, never from the rendered form.
- **Forms** (`journal/forms.py`): `ManualTradeForm(UserScopedModelForm)` (modes add, edit-open and edit-closed) and
  `CloseTradeForm(UserScopedModelForm)`.
  - Both have `Meta.model = Execution` and `Meta.fields = []`, with every input declared. They never call
    `form.save()`: the service writes. That puts them under the form-scoping tripwire (AC 52), and on edit the base
    form's owner check on `instance=<entry fill>` adds a second guard.
  - Decimals use `PlainDecimalField`: price and quantity (20,10), fees (19,4) at 0 or more, point value (20,10)
    above 0.
  - Account label: the import form's trim, 64 maximum and control-character rules.
  - Times: `forms.DateTimeField(input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"])`. Map Django's DST error to
    `TIME_NOT_READABLE`.
- `_account_labels(user, broker=BROKER)` gains `broker=None` for "all brokers", which the manual form uses (AC 31).
- **Ownership:**
  - `backend-engineer`: forms, services, views, URLs, the matcher key, the `contract_root` helper, new copy keys
    (strings verbatim from the design), working unstyled templates, and every test. This follows the J2 pattern.
  - `frontend-engineer`: the design-complete templates, the `nowButton` Alpine component, the htmx point-value
    wiring, the `account_input.html` extraction and the trade delete dialog.
  - `database-engineer`: no migration. Docstring and `schema.md` text only (M1).

### 6. Time zone

- **Activation:** already done by `UserTimezoneMiddleware`. Do not add a second activation. The form's zone
  label is `timezone.get_current_timezone_name()`, never the raw `user.timezone`, so the zone shown and the zone
  parsed are always the same, including the UTC fallback when a stored name is bad.
  - Test: a New York user posts `2026-03-08 02:30` and `2026-11-01 01:30`, and both get the field error. The
    neighbours in vector 34 save to the stated UTC instants. This test fails if the middleware order breaks.
- **Validation order per field** (domain §7.2): required, parse, DST, not in the future, then exit at or after
  entry. The DST error comes from `from_current_timezone`.
- **Future check:** `instant > timezone.now() + FUTURE_GRACE`, with `FUTURE_GRACE = timedelta(minutes=5)`. The
  grace covers a browser clock that runs ahead. "Use current time" is floored to the minute, so it never trips the
  check.
- **Insert order for equal times:** §1 rule 3 (entry created first, pk tie-break). Test: vector 32a, plus
  `entry.pk < exit.pk` after an add-closed.

### 7. Point value

- **One lookup, one root rule:** add a public `contract_root(symbol) -> str | None` to
  `journal/importers/topstep.py` (`_CONTRACT.fullmatch`). The importer's `_legs()` switches to it, and manual
  entry uses `MULTIPLIERS.get(contract_root(symbol))`. AC 18 is then one test on one rule.
  `# ponytail: the table lives in the Topstep importer; move it to journal/instruments.py when a second importer needs it.`
- **Known root:** the lookup value is written to both legs, and any posted `point_value` is ignored (AC 16,
  vector 27b). Edit re-evaluates the root, so a trade saved with a typed value whose root later enters `MULTIPLIERS`
  takes the lookup value on its next save (AC 19). The read-only row shows it before the save.
- **Unknown root:** required, above 0, (20,10), with no default.
- **`pv_for` guard:** if the normalized symbol at submit differs from `pv_for`, or `pv_for` is missing, the posted
  value is dropped and the form re-renders with `POINT_VALUE_NEEDED` (design 5.3). `pv_for` is not signed: tampering
  with it can only misstate the user's own value, which they could type anyway.
- **Close** copies the entry's stored multiplier and never looks it up, so the legs always agree.

### 8. Query count and performance

- Compute-on-read is unchanged (ADR-0002/0003). Each manual trade is one more bucket of 1 or 2 fills, so the
  matcher stays O(fills).
- `find_trade` for a manual trade loads every fill on the opener's `(label, symbol)`, imported and manual, which is
  the same as today. That is bounded by one user's trades on one symbol. No new index.
- Writes are about 6 queries: savepoint, lock, 1 or 2 reads, 1 or 2 writes. The replay lookup filters
  `broker_trade_id`, which has no index, inside the user's rows (the `(user, -executed_at)` index leads with
  `user`). That is milliseconds at ADR-0003 volume. `# ponytail:` comment there; add an index only on measurement.
- `trade_point_value` makes 0 queries.
- Tests pin the counts:
  - Add POST runs in a constant number of queries.
  - `/trades/` keeps its constant count when manual trades are added.

### 9. Security review triggers (for `security-reviewer`, before release)

1. **Tenant isolation on all five new endpoints.** User B's GET and POST on A's `/close/`, `/edit/` and `/delete/`
   give the same 404 as a missing id (status, template, text), with zero rows changed (AC 49).
   `trade_point_value` returns no tenant data.
2. **IDOR:** the closing fill's id, an imported id and a NULL-key manual id all give the 404. The posted `user`,
   `source`, `broker`, key and execution ids are ignored (AC 50).
3. **Edit guard on imported fills:** `_manual_fills` is the only route to an UPDATE or DELETE of a fill. Review that
   no view calls `save()` on an `Execution` any other way.
4. **Token replay and forgery:** an unsigned, other-user or tampered `form_token` writes nothing. A replay returns
   only the caller's own trade. Check the per-user lock with two concurrent POSTs (a threaded test or a reasoned
   review).
5. **Delete:** the signed `shown` token is bound to user and trade. Check the stale paths (AC 46), the tick
   enforced by the server, and RESTRICT kept.
6. **Session expiry:** the 401 on `trade_point_value` has no `HX-Redirect`. The form views still redirect to login.
7. **Account suggestions** come only from the user's own labels (AC 51).
8. Banned-pattern grep, form-scoping tripwire and isolation enumeration all green (AC 52).

### 10. PR split

| PR | Owner | Contents | Depends on |
|---|---|---|---|
| **M1** | `database-engineer` | **No migration.** Replace the `Execution` docstring and the `JournalEntry.opening_execution` comment in `journal/models.py`, and the matching lines in `docs/data/schema.md`, with the text under "Amendments to other ADRs". Record the manual key format and the bucket key in `schema.md`. Small enough for the orchestrator to apply directly. | ADR accepted |
| **M2** | `backend-engineer` | §1-§9: the matcher key plus two test helpers; `contract_root`; forms; services; views; URLs; copy keys; `_account_labels(broker=None)`; `trade.is_manual`; `_shown_signer` purpose prefix; the `services.py` ponytail comment on the re-pointing ceiling (now closed); working unstyled templates. Tests first: vectors 24-34, AC 1-53, isolation, query counts, the imported-fill edit guard. | ADR accepted; domain §1 amended; design draft accepted |
| **M3** | `frontend-engineer` | Design §15 deliverables, minus the locked-field rows (§2) and minus `form_token` on close (§4). | M2 merged (same templates) |

After those: `qa-engineer` (AC 1-53; vectors 1, 24-34, T1, T2; vectors 6-23 regression), then `security-reviewer` (§9).
M1 can run in parallel with M2, because M2 doesn't touch `models.py`.

## Amendments to other ADRs (orchestrator applies these after the user accepts)

**ADR-0003 §4, lines 113-114.** Replace the paragraph

> Immutable by convention: append-only, never `UPDATE`d. Correcting a manual entry deletes and recreates its executions; imported executions are never edited (re-import instead).

with

> Imported executions (`source='import'`) are immutable: never `UPDATE`d. The fix is a re-import or "Delete this import" (ADR-0005). Manual executions (`source='manual'`) are edited in place, only through `journal/services.py` `edit_manual_trade` (ADR-0008 §2). It writes both legs of the trade in one transaction with `save(update_fields=...)`, so ids and `created_at` never change and the journal entry stays attached. A manual trade is removed only through `delete_manual_trade` (ADR-0008 §3). `QuerySet.update()` stays banned (ADR-0006).

Also in ADR-0003's status line, append: "; §4 immutability amended by ADR-0008".

**ADR-0004 §3, line 90.** Replace

> `(user, broker_account_label, symbol, broker_trade_id)   -- NULL broker_trade_id = one shared bucket`

with

> `(user, broker, broker_account_label, symbol, broker_trade_id)   -- NULL broker_trade_id = one shared bucket per broker (ADR-0008 §1)`

**ADR-0004 §3, line 96.** Replace

> - Manual entries and any future **fill-only** importer leave `broker_trade_id` NULL. They get plain FIFO per `(account, symbol)`, exactly as `pnl-and-matching.md` §1 specifies. No second code path.

with

> - A future **fill-only** importer leaves `broker_trade_id` NULL. Its fills get plain FIFO per `(broker, account, symbol)`, exactly as `pnl-and-matching.md` §1 specifies. Manual entries carry a generated key per trade (`manual:<32 hex>`, ADR-0008 §1), so each manual trade is its own bucket and never pairs with another trade. No second code path.

In ADR-0004's status line, append: "; §3 bucket key amended by ADR-0008".

**ADR-0005 §2, lines 77-78.** Replace

> Known ceiling: an UPDATE that re-points an older entry into the batch (ADR-0003 manual correction, not built) is not caught by max pk. When that ships, sign a hash of the sorted shown pk set instead.

with

> The re-pointing ceiling is closed by ADR-0008: manual edits update fills in place and never re-point a journal entry, and manual fills belong to no batch. Nothing re-points an entry today. (Still open: a lower pk committing after a higher one, `services.py` ponytail (b).)

**ADR-0007 §2, table row "Re-match that shifts an opener".** Replace the first cell

> Re-match that shifts an opener (only possible for `broker_trade_id = NULL` fills: manual or fill-only, and neither exists today)

with

> Re-match that shifts an opener (only possible for `broker_trade_id = NULL` fills from a future fill-only importer, which doesn't exist yet. Manual trades can't shift: each has its own key, the exit is never before the entry, and on equal times the entry fill has the lower pk, ADR-0008 §1)

**ADR-0007 Consequences**, the bullet "Accepted: orphaned entries ... Only non-Topstep fills can produce them, and none exist yet." Replace the last sentence with:

> Only a future fill-only importer's NULL-key fills can produce them; manual trades can't (ADR-0008 §1).

**`journal/models.py` (M1, `database-engineer`).**

- `Execution` docstring, replace lines 354-356 with:

  > The source of truth. Imported fills are immutable (never UPDATEd; re-import or delete the import). Manual fills are edited in place only by services.edit_manual_trade (ADR-0008), both legs in one transaction. Manual trades carry broker_trade_id = "manual:<32 hex>", one per trade.

- `JournalEntry.opening_execution` comment, replace its first two sentences with:

  > RESTRICT, not CASCADE and not PROTECT. Every path that deletes executions (import delete, ADR-0005; manual trade delete, ADR-0008) deletes the journal entry first, explicitly, after a counted confirm; CASCADE would destroy the note silently.

**`docs/domain/pnl-and-matching.md` §1 (owner `trading-domain-expert`, not an ADR).** The bucket gains `broker`:
`(user, broker, account, instrument, broker_trade_id)`. NULL keys share one bucket per `(user, broker, account,
instrument)`. `K_A` and `K_B` in vectors 24-34 are `manual:<32 hex>` values. No formula changes.

## Alternatives rejected (not listed above)

- **A `trade` table for manual trades.** Rejected again (ADR-0003 §5): it would be a second source of truth.
- **Delete-and-recreate as the "edit".** It changes the opener pk and loses the journal entry, which AC 39 forbids.
  It is what ADR-0003 used to say.
- **A separate `journal/manual.py` module.** One feature, about 150 lines. It belongs in `services.py` next to
  `find_trade` (fewest files). Split it when the file hurts.
- **Signing `pv_for`, or a `max_age` on the add token.** No security gain, and new friction (§4, §7).

## Consequences

- **Positive:**
  - No migration and no new table.
  - One gate (`_manual_fills`) for every manual UPDATE and DELETE.
  - Manual and imported trades share the matcher, R and stats.
  - The journal entry survives every edit by construction.
  - The double-submit guard costs one lookup and one row lock.
- **Accepted:**
  - The bucket key gains `broker`, a domain wording change, with no effect on today's data.
  - Imported-fill immutability is enforced in the service, not the database.
  - A same-token resubmit with changed values is ignored.
  - Resubmitting an add after deleting that trade adds it again.
  - A stop left on the wrong side blocks the next journal save until it is fixed (§2 option (b)).
  - The rare edit-versus-close race shows `EXIT_BEFORE_ENTRY` on the entry time.

## Not built

- Field locks.
- A DB trigger for imported-fill immutability.
- A `manual => key NOT NULL` CHECK (follow-up).
- A token table.
- A `max_age` on the token.
- A session "already deleted" flash for trades.
- An index on `broker_trade_id`.
- Moving `MULTIPLIERS`.
- Partial closes, reopen, bulk entry, non-USD (spec Out of scope).

## Follow-ups to record (`docs/data/follow-ups.md`, orchestrator)

1. CHECK `execution_manual_requires_broker_trade_id` (`source <> 'manual' OR broker_trade_id IS NOT NULL`). It needs
   the NULL-key manual test fixtures moved to a fill-only stand-in first. Trigger: before non-author accounts, or a
   second manual write path. Owner: `database-engineer`.
2. A Postgres trigger rejecting UPDATEs of `source='import'` fills. Trigger: a second code path that writes
   `Execution`. Owner: `database-engineer`.
3. Move `MULTIPLIERS` and `contract_root` to `journal/instruments.py`. Trigger: a second importer. Owner:
   `backend-engineer`.

## Rulings needed (user)

1. **Stop on the wrong side after an edit (AC 40):** (b) allow and show `stop_not_a_risk`, accepting that the next
   journal save fails on that stop until it is fixed (recommended). Or (a) refuse the edit.
2. **Close double submit, doc conflict:** spec AC 36 says a double submit gets the "already closed" message. Design
   6.2 says a same-token replay gets "the same redirect as the first". Both go to the journal page; only the flash
   differs. This ADR builds AC 36's version, with no token on close. The design's version would need the token
   stored on the exit fill, and `broker_execution_id` must stay NULL (AC 2). Confirm, then `ux-designer` drops
   `form_token` from the close form.
3. **Bucket key gains `broker` (§1):** `trading-domain-expert` must amend `pnl-and-matching.md` §1 before M2. This
   is not a user ruling, unless the user prefers option B (no matcher change, collision avoided only by key format).
