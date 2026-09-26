# Design: signup / login / logout, trades list, three stat cards

Specs: stories 1 and 6 of [`mvp.md`](../product/features/mvp.md). Formulas: [`pnl-and-matching.md`](../domain/pnl-and-matching.md) (§1 trades, §2 win rate, §3 R). Read path: [ADR-0003 §5](../adr/0003-data-model.md) (trades derived on read, no trade table). Layout, tokens, `Notice`, flash and tone conventions: [`import-account-label.md`](import-account-label.md) ("Conventions used" and section 6). This doc reuses them and adds only what is new.
Status: draft for `frontend-engineer`. `docs/product/features/import-and-list.md` did not exist when this was written; re-check it against section 9 before building.

Slice scope: journaling does not exist yet. So the list has no rule-followed or note columns, no filters, and Average R has n=0. All three come back in a later slice (section 9, item 1).

---

## 1. Decisions

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | Landing | `/` redirects: logged out to `/login/`, logged in to `/trades/`. No marketing page. | Nothing to sell yet; fewest screens. |
| 2 | After signup | Auto log in, land on `/trades/` (empty state points to `/imports/`). | One click from "no data" to upload. Seeing the empty list first tells the user where trades will appear. |
| 3 | After login | `?next=` if it is a safe same-site path, else `/trades/`. | Standard. Validate with `url_has_allowed_host_and_scheme`. |
| 4 | Logged-in user opens `/login/` or `/signup/` | Redirect to `/trades/`. | |
| 5 | Logout | POST button in the nav. Redirect to `/login/` with flash "You're logged out." No GET route. | Django 5 logout is POST-only; avoids logout-by-link. |
| 6 | Time zone at signup | Visible text field, prefilled from the browser, default `UTC` without JS. | `accounts_user.timezone` drives every displayed time and there is **no settings screen** in this slice to fix a wrong guess later. |
| 7 | Password confirm field | None. "Show password" toggle instead. | Less friction. There is no password reset (mvp story 1), so a typo lock-out is a real cost; the toggle lets the user see what they typed. |
| 8 | Trades list date | "Opened" time, sorted newest opened first. | Traders think in entry time. Open trades sort naturally with the rest. |
| 9 | htmx | **None on these screens.** Sorting and paging are plain links, forms are plain POSTs. | Compute-on-read loads the whole execution history for any request (ADR-0003 §5), so a partial swap saves nothing and costs back-button and no-JS behavior. |
| 10 | Stats scope | Cards cover **all** the user's closed trades, not the current page. | Pagination must not change the numbers. When filters exist, cards follow the filtered set (mvp story 6). |
| 11 | Account column | Shown only if the user's trades span 2 or more distinct account labels (a blank label counts as one value, "no name"). Hidden otherwise. | A single-account user never needs it; a multi-account user needs it at once. |

---

## 2. Flow

```
Visitor -> /  -> /login/ ----------------------+
             \-> /signup/ (link both ways)     |
   /signup/ POST ok -> logged in ----+         | POST ok
                                     v         v
Logged in  -> /trades/  (empty state -> /imports/ -> upload -> back to /trades/)
                |  nav: Trades | Imports | {email} | Log out
                +- Log out (POST) -> /login/ + flash "You're logged out."

Logged-out visit to any protected URL -> /login/?next=<url> + notice "Log in to continue."
```

---

## 3. Shell and nav (all pages)

The import doc fixes the content width, spacing, type and tokens but no nav, so this defines it. The upload page and `/imports/` should adopt it.

Logged in, desktop:

```
+--------------------------------------------------------------------------+
| [skip to content]                                                        |
| Trading Journal      Trades   Imports                 me@x.com  Log out  |
+--------------------------------------------------------------------------+
| (flash region, then <main id="main"> content)                            |
```

Logged out:

```
| Trading Journal                                        Log in   Sign up  |
```

- Working app name "Trading Journal" (open question 5). Brand links to `/`.
- Current page link gets `aria-current="page"` plus a visible underline (not color alone).
- `me@x.com` is plain text (not a link; there is no account page). Truncate in the middle over 24 characters, full value in `title`.
- **Log out** is a `<form method="post">` with a CSRF token, styled as a text button. No confirm.
- Mobile (under 640px): brand on the left and a native `<details>` "Menu" button on the right. Open panel lists Trades, Imports, the email, Log out, each 44px tall. No JS needed. Logged out: "Log in" and "Sign up" stay visible in the bar, no menu.
- Flash region sits under the nav, above `<main>`. It is the existing `Notice` (`role="status"`, close button, no auto-dismiss).
- `<html lang="en">`, one `<h1>` per page, skip link is the first focusable element.

---

## 4. Signup (`/signup/`) and login (`/login/`)

Centered card, max width 400px, 24px padding. Same card on mobile, full width minus 16px margins.

### 4.1 Signup

```
+------------------------------------------+
| Create your account                      |
| A private journal for your trades.       |
|                                          |
| Email                                    |
| [ name@example.com                     ] |
|                                          |
| Password                                 |
| [ ...................        ] [ Show ]  |
| At least 8 characters.                   |
|                                          |
| Time zone                                |
| [ America/New_York                     ] |  <- text + datalist
| Used to show your trade times. We        |
| detected this from your browser.         |
|                                          |
| [ Create account ]                       |
|                                          |
| Already have an account? Log in          |
+------------------------------------------+
```

| Field | Rules |
|---|---|
| Email | Required. `type="email"`, `autocomplete="email"`, `autocapitalize="none"`, `spellcheck="false"`, max 254. Trim spaces. Case-insensitive uniqueness is enforced by the DB (schema.md). |
| Password | Required. `autocomplete="new-password"`. Django validators (min 8, not too common, not all digits, not too like the email). "Show" is an Alpine `type=button` with `aria-pressed`; hidden without JS. |
| Time zone | Required, prefilled by Alpine from `Intl.DateTimeFormat().resolvedOptions().timeZone`; `UTC` when JS is off. `<input list="timezones">` with a `<datalist>` of `zoneinfo.available_timezones()` sorted. Help text without JS: "Used to show your trade times. Type your time zone, for example America/New_York." Validated server-side against the same set. |

**Wording** (exact):

| Where | String |
|---|---|
| Page title / h1 | Create your account |
| Subtitle | A private journal for your trades. |
| Email label | Email |
| Password label / help | Password / At least 8 characters. |
| Show / Hide toggle | Show / Hide (with `aria-label` "Show password" / "Hide password") |
| Time zone label | Time zone |
| Time zone help (JS) | Used to show your trade times. We detected this from your browser. |
| Submit / busy | Create account / Creating... (button disabled after first submit, label kept if JS off) |
| Footer link | Already have an account? Log in |
| Email missing | Enter your email address. |
| Email malformed | Enter an email address like name@example.com. |
| Password missing | Choose a password. |
| Password too short | Use at least 8 characters. |
| Password too common | That password is easy to guess. Try a longer one, or a few unrelated words together. |
| Password all digits | Add some letters as well as numbers. |
| Password like email | That password is too close to your email. Try something different. |
| Time zone missing / unknown | Choose a time zone from the list, for example America/New_York. |
| Email cannot be used (see below) | We couldn't create an account with those details. If you already have one, try logging in. |
| Error summary title (2+ errors or any error) | Please fix the items below. |
| Welcome flash | Welcome. Your account is ready. |

**Do not reveal whether an email exists.** On a duplicate email (any case), show the single message "We couldn't create an account with those details. If you already have one, try logging in." as a **form-level** message in the error summary, not under the Email field, and keep the Email value. Do not say "already registered", "taken", or "in use". Password is cleared on any error. Honest limit: without email verification a successful signup versus this message still differs, so an attacker can tell. Rate-limit signup and login per IP at the backend (open question 2); do not promise more in copy.

### 4.2 Login

```
+------------------------------------------+
| [i] Log in to continue.       (only if ?next)
|                                          |
| Log in                                   |
|                                          |
| Email                                    |
| [                                      ] |
|                                          |
| Password                                 |
| [ ...................        ] [ Show ]  |
|                                          |
| [ Log in ]                               |
|                                          |
| New here? Create an account              |
+------------------------------------------+
```

- Email `autocomplete="username"`, password `autocomplete="current-password"`. Autofocus Email only when no error.
- No "Forgot password" link (no reset exists; do not ship a dead link). No "Remember me".
- Wrong email and wrong password give the **same** message, same field position, and the backend must take about the same time for an unknown email as for a wrong password. Inactive account also gets this message.
- Email is kept on error, password cleared.

| Where | String |
|---|---|
| h1 | Log in |
| Login-required notice (`?next`) | Log in to continue. |
| Submit / busy | Log in / Logging in... |
| Footer link | New here? Create an account |
| Email missing | Enter your email address. |
| Password missing | Enter your password. |
| Bad credentials (form-level, in the error summary) | That email and password don't match. Check them and try again. |
| Throttled (only if the backend adds throttling) | Too many attempts. Wait a few minutes and try again. |
| Logout flash | You're logged out. |
| Session expired | Same as login-required notice: Log in to continue. |

### 4.3 Form error presentation (both forms)

- On error the page re-renders (full page, status 200 for validation, 429 if throttled). An error summary `Notice` (`attention` variant, not red) sits above the form with the title and one line per problem, each a link to its field. It has `tabindex="-1"` and receives focus on load, so screen readers and keyboard users land on it.
- Each field also shows its message directly beneath it, tied with `aria-describedby`, with `aria-invalid="true"` on the input. Message text is preceded by an icon and the word "Error:" is **not** used; the icon is decorative (`aria-hidden`), the text carries the meaning.
- Form-level messages (bad credentials, cannot create account) appear only in the summary.
- No `role="alert"` (consistent with the import screens). Focus move does the announcing.
- CSRF failure (custom 403): "That page expired. Go back, refresh it, and try again." with a link to `/login/`.

### 4.4 States

| State | Signup | Login |
|---|---|---|
| Default | Form as 4.1, time zone prefilled | Form as 4.2 |
| Submitting | Button "Creating...", disabled | Button "Logging in...", disabled |
| Validation error | 4.3 | 4.3 |
| Server failure | Above the form: "We couldn't finish creating your account. Try again in a moment." | Above the form: "We couldn't log you in just now. Try again in a moment." |
| Loading, empty | Not applicable (server-rendered forms) | Not applicable |

---

## 5. Trades list (`/trades/`)

### 5.1 Populated, desktop

Full width of the shell (max about 1200px), 24px gutters.

```
+--------------------------------------------------------------------------------------+
| Trades                                                        [ Import trades ]      |
|                                                                                      |
| +------------------+ +------------------------+ +----------------------------------+ |
| | Win rate         | | Total P&L              | | Average R                        | |
| | 60.0%            | | +69.00 USD             | | n/a                              | |
| | n=5 (3 wins,     | | n=7 closed trades,     | | n=0                              | |
| | 2 losses)        | | after fees             | | Average R needs a risk amount on | |
| | 2 breakeven are  | | 1 open trade is not    | | each trade. None of your trades  | |
| | not counted.     | | included.              | | have one yet, so there is        | |
| |                  | |                        | | nothing to average.              | |
| +------------------+ +------------------------+ +----------------------------------+ |
| > How these are calculated                                                           |
|                                                                                      |
| Opened (America/New_York) v  Symbol  Side   Qty  Entry   Exit  Duration Result   Net P&L (USD) Account |
| --------------------------------------------------------------------------------------|
| Sep 26, 2:31 PM   MNQZ6    Long    2   19,850.25 19,862.00 12m  (+) Win   +23.50     Combine 50K |
| Sep 26, 9:10 AM   CLZ6     Short   1   80.15     80.30  1h 05m  (-) Loss  -150.00    Combine 50K |
| Sep 25, 3:58 PM   ESZ6     Long    1   5,801.00  5,801.00 45s   (=) Breakeven 0.00   Live        |
| Sep 25, 3:40 PM   MNQZ6    Long    1   19,840.00  -      Open   (o) Open    -        Live        |
|                                                                                      |
| Showing 1-50 of 212                                         Previous   Next          |
+--------------------------------------------------------------------------------------+
```

Column spec (build this; the sketch shows shape only):

| # | Column | Content | Align | Sortable |
|---|---|---|---|---|
| 1 | Opened (zone) | Opened time in `user.timezone`, "Sep 26, 2:31 PM", adds the year if not the current year. Zone named once in the header: "Opened (America/New_York)". | left | yes, **default, newest first** |
| 2 | Symbol | As imported, e.g. `MNQZ6`. | left | yes |
| 3 | Side | "Long" or "Short" with an arrow icon (up or down). | left | no |
| 4 | Qty | Total entry quantity, trailing zeros trimmed. | right | no |
| 5 | Entry | Average entry price. | right | no |
| 6 | Exit | Average exit price; open trades show "-" with hidden text "not closed yet". | right | no |
| 7 | Duration | Closed minus opened: `45s`, `12m`, `1h 05m`, `2d 3h`. Open trades: "Open" text (no running clock). | right | no |
| 8 | Result | Icon + word: **Win**, **Loss**, **Breakeven**, **Open**. Definitions from domain §2: breakeven is `net_pnl == 0` exactly. | left | no |
| 9 | Net P&L (CCY) | Signed amount after fees: `+23.50`, `-150.00`, `0.00`. Real minus sign U+2212 for negatives. Open trades: "-" with hidden text "no result yet". Header carries the currency code; if the user's trades span more than one currency, each cell appends the code ("+23.50 USD") and the header drops it. | right | yes |
| 10 | Account | Label, or "no name" for a blank label. Column present only under decision 11. | left | no |

Formatting rules:
- Numbers use `font-variant-numeric: tabular-nums`. Prices and quantities are shown at up to 4 decimals with trailing zeros trimmed to a minimum of 2 for prices. This is a display filter only; no value is computed from a displayed string.
- **Win, loss, breakeven are never color alone.** The Result column has icon plus word (check-circle, minus-circle, equals, dot-circle), and the P&L cell carries the sign. Color is reinforcement only. Use `gain` and `loss` tokens (muted green and muted rose, AA against the row surface in light and dark). Do **not** use the `danger` token (reserved for delete) and no red backgrounds on loss rows. Breakeven and Open use the neutral text color.
- Header sort links: each sortable `<th>` has `aria-sort="ascending|descending|none"` and contains a link whose text is the column name plus an arrow glyph on the active column, with visually hidden text "sorted newest first" (or "oldest first", "highest first", "lowest first", "A to Z", "Z to A"). Clicking the active column flips direction.
- Sort URL: `?sort=opened|symbol|pnl&dir=asc|desc&page=N`. Unknown values fall back to the default without an error. Changing sort resets `page` to 1. Ties: opened time descending, then a stable id. **Open trades sort last for `pnl` in both directions.**
- Pagination: 50 per page, "Previous" and "Next" links that keep `sort` and `dir`, footer "Showing 51-100 of 212". Out-of-range `page` shows the last page.
- Trade rows are **not links** in this slice: a trade's identity is derived and can shift (ADR-0003), and there is no trade page yet. Do not fake a link.
- `<table>` has `<caption class="sr-only">Your trades</caption>`, real `<th scope="col">`, sticky header on desktop.

### 5.2 Populated, mobile (under 640px)

Cards replace the table (from the same data; table is `hidden sm:block`, the list is `sm:hidden`, so screen readers get one copy).

```
+--------------------------------------+
| Trades                               |
| [ Import trades ]  (full width)      |
|                                      |
| Win rate            60.0%            |
| n=5 (3 wins, 2 losses)               |
| 2 breakeven are not counted.         |
| ------------------------------------ |
| Total P&L           +69.00 USD       |
| n=7 closed trades, after fees        |
| 1 open trade is not included.        |
| ------------------------------------ |
| Average R           n/a              |
| n=0                                  |
| Average R needs a risk amount on     |
| each trade. None of your trades      |
| have one yet, so there is nothing    |
| to average.                          |
| > How these are calculated           |
|                                      |
| Sort by [ Newest opened v ] [Apply]  |   <- <select> in a GET form
|                                      |
| CLZ6  Short                 -150.00  |
| (-) Loss                     USD     |
| Sep 26, 9:10 AM (America/New_York)   |
| 1h 05m  -  Qty 1  -  80.15 to 80.30  |
| Account: Combine 50K                 |
| ------------------------------------ |
| MNQZ6  Long                  +23.50  |
| ...                                  |
| Previous            Showing 1-50  Next |
+--------------------------------------+
```

- Line 1: symbol, side in words, signed P&L right-aligned. Line 2: Result icon + word. Line 3: opened time; zone name shown once above the list ("Times in America/New_York"), not per card. Line 4: duration, quantity, entry to exit. Account line only under decision 11.
- Sort control on mobile is a labelled `<select>` inside a GET form with an "Apply" button (native, no JS; Alpine may auto-submit on change). Options: Newest opened, Oldest opened, Symbol A to Z, Symbol Z to A, Highest P&L, Lowest P&L.
- Stat cards stack in one column, compact (label and value on one line). 44px targets for Previous, Next, Apply and the import button.
- Text scales to 200% without horizontal scroll.

### 5.3 Multi-account and multi-currency

- Account labels come from the opening execution's `broker_account_label` (see open question 3). Blank displays as "no name". The value is user text: escape it, show full text in `title` if truncated at 24 characters.
- No account filter in this slice. The sort options do not include Account.
- Trades from different accounts sit in one list and one set of stats. That is intended (one trader, one journal). If the user has two or more distinct labels, add one quiet line under the stat cards: "Stats include all your accounts." Hidden for a single account.
- More than one currency: the stat cards repeat as one block per currency, each headed by an h2 with the code ("USD", "EUR"), no conversion (ADR-0002, ADR-0003 §5). The table stays one list with the code in each P&L cell.

### 5.4 Empty and other states

**A. No imports and no trades** (new user). Cards and table are not rendered.

```
+--------------------------------------------------------------------------+
| Trades                                                                   |
|                                                                          |
|   No trades yet                                                          |
|   Import your TopstepX export and your trades will show up here,         |
|   with your win rate and P&L.                                            |
|                                                                          |
|   [ Import trades ]                                                      |
+--------------------------------------------------------------------------+
```

- Button links to `/imports/`. It is the one primary action on the page.
- "You can also add a trade by hand." with a text link is added **only once** the manual-entry page exists (open question 4). Do not render a dead link.

**B. Has imports, but none produced a trade** (everything was skipped or failed, or the trades were deleted).

```
|   No trades to show                                                      |
|   Your imports didn't add any trades. The Imports page shows what        |
|   happened to each row.                                                  |
|   [ Go to Imports ]                                                      |
```

**C. Only open trades** (no closed trades yet). Table shows the open rows. Cards render with their n=0 states:

| Card | Value | Lines |
|---|---|---|
| Win rate | n/a | `n=0` / "No closed trades yet." |
| Total P&L | n/a | `n=0 closed trades` / "No closed trades yet." |
| Average R | n/a | as 6.3 |

**D. Errors and loading.**

| State | What the user sees |
|---|---|
| Loading | Server-rendered; no skeleton. Sorting and paging are full page loads. |
| Server error (500) | Standard app error page: "This page didn't load. Nothing was changed. Try again in a moment." with a "Try again" link to the same URL. |
| Bad sort or page param | Silent fallback (5.1). No error. |
| Logged out | Redirect to `/login/?next=...` (section 4.2). |
| Not applicable | Stale-data or partial states: none, since the page is a pure read. |

---

## 6. Stat cards

Markup: a `<div role="group" aria-label="Summary of your closed trades">` holding three `<section aria-labelledby>` cards, each with an `<h2>` label, one large value, and small text lines. Desktop: three equal columns with a 16px gap. Under 640px: one column. The value is never the only carrier of meaning: every card prints its `n=`.

Tone rules: neutral descriptions only. No adjectives that grade the number ("low", "great", "poor"), no color grading by threshold, no trend arrows, no comparison with other traders.

Definitions come from the domain doc and are shown in the "How these are calculated" disclosure (6.4).

### 6.1 Win rate

| State | Value | Line 1 (sample) | Line 2 |
|---|---|---|---|
| Populated | `60.0%` (one decimal) | `n=5 (3 wins, 2 losses)` | `2 breakeven trades are not counted.` (omit if 0; singular "1 breakeven trade is not counted.") |
| Zero wins and losses (all breakeven, or no closed trades) | `n/a` | `n=0` | `Breakeven trades are not counted.` if breakeven > 0, else `No closed trades yet.` |

`n` is wins plus losses, per domain §2. Singular: "1 win", "1 loss".

### 6.2 Total P&L

| State | Value | Line 1 (sample) | Line 2 |
|---|---|---|---|
| Populated | `+69.00 USD` / `-30.00 USD` / `0.00 USD` (sign always shown; zero has no sign) | `n=7 closed trades, after fees` | `1 open trade is not included.` (omit if 0; plural "3 open trades are not included.") |
| No closed trades | `n/a` | `n=0 closed trades` | `No closed trades yet.` |

Breakeven trades count in `n` here (they contribute 0), which is why the win-rate `n` and this `n` can differ. The Win rate card's second line explains it; the disclosure states it again. Negative value uses the loss token plus the minus sign; positive uses the gain token plus the plus sign; never color alone.

### 6.3 Average R (this slice: always n=0)

Exact strings:

| Element | String |
|---|---|
| Heading | Average R |
| Value | n/a |
| Sample line | n=0 |
| Body | Average R needs a risk amount on each trade. None of your trades have one yet, so there is nothing to average. |
| Screen-reader value | Average R: not available, based on 0 trades |

No date, no "coming soon", no "start journaling" prompt or link, no styling that reads as broken or missing (same card frame, value in the normal text color, body in the muted text token that still meets AA). The Win rate and Total P&L cards are unaffected. Optional trailing sentence, shown only in this state: "Your win rate and P&L don't depend on it."

Populated state (built now so the template needs no redesign later; unreachable until risk amounts exist):

| Element | String |
|---|---|
| Value | `1.11 R` (2 dp, mean of stored R values, domain §3; sign shown for negatives with the minus sign) |
| Sample line | `n=3 of 12 closed trades have a risk amount` |
| Body | Trades without a risk amount are left out, not counted as 0. |

### 6.4 "How these are calculated" (one `<details>` under the cards, closed by default)

Summary text: How these are calculated. Body:

- Win rate: wins divided by wins plus losses. Breakeven trades (exactly 0.00 after fees) are left out of both.
- Total P&L: the sum of net P&L after fees for closed trades. Open trades are not included.
- Average R: the average of net P&L divided by the risk amount you set for each trade. Trades without a risk amount are left out.
- Times are shown in {zone}. These numbers cover all your trades on all your accounts.

Native `<details>`, keyboard operable, visible focus ring.

---

## 7. Components

| Component | Notes |
|---|---|
| `AppShell` (`base.html`) | Skip link, `Nav`, flash region, `<main id="main">`, title block. Content width per page: 400px (auth card), 640px (forms), full (tables). |
| `Nav` | Logged-in and logged-out variants, `<details>` menu on mobile, POST logout form. |
| `AuthCard` | 400px card with h1, optional subtitle, form, footer link. |
| `FormField` | Label, input, help text, error message, `aria-describedby`, `aria-invalid`. Reuse for the import Account field where possible. |
| `PasswordField` | `FormField` plus Alpine show/hide toggle. |
| `ErrorSummary` | `Notice` variant `attention`, focusable, links to fields. |
| `Notice` | Existing component (import doc section 6): `info`, `success`, `attention`. Used for flash, login-required, summary. |
| `StatCard` | Label, value, sample line, extra lines; variants populated, n/a. One partial, three instances. |
| `TradeTable` | Desktop table with sort headers. |
| `TradeCardList` | Mobile cards, same context as the table. |
| `SortSelect` | Mobile GET form with `<select>`. |
| `Pager` | Previous, Next, "Showing a-b of n". Keeps query string. Reuse for `/imports/`. |
| `ResultBadge` | Icon plus word for Win, Loss, Breakeven, Open. Icons: check-circle, minus-circle, equals, dot-circle. |
| `EmptyState` | Title, body, one primary button. |

Tokens added (light and dark values each; extend the import doc's set): `gain`, `loss` (muted, AA on `surface`, never `danger`), `text-muted`, `surface-raised` (cards). No hard-coded colors in templates.

---

## 8. Accessibility checklist

- Every state is icon plus text: Result badges, sort indicators, error messages, card lines.
- Focus ring visible on every control; skip link first; one h1 per page; logical heading order (h1 page, h2 card labels).
- Error summary receives focus on load; fields use `aria-invalid` and `aria-describedby`; labels are real `<label for>`.
- Show-password toggle has `aria-pressed` and a changing label; password field remains usable without JS.
- Sortable headers use `aria-sort` and link text, not click-only cells.
- Tables have caption and `scope`; the mobile card list is a `<ul>` and only one of table or list is exposed at a time.
- Contrast: AA for text and icons in light and dark, including muted text, `gain`, `loss`, placeholder and disabled labels.
- Touch targets 44px on mobile; respects `prefers-reduced-motion` (no animation required).
- Autofill and password managers work: correct `autocomplete` values and `type`s; no paste blocking.

---

## 9. For frontend-engineer, and open questions

### Suggested files

| Purpose | Template | URL / view |
|---|---|---|
| Shell | `templates/base.html`, `templates/partials/nav.html`, `templates/partials/notice.html` | |
| Signup | `templates/accounts/signup.html` | `/signup/` |
| Login | `templates/accounts/login.html` (or `registration/login.html` if using `LoginView` defaults) | `/login/` |
| Logout | none | `POST /logout/` (Django `LogoutView`) |
| Root redirect | none | `/` |
| Trades list | `templates/journal/trade_list.html` | `/trades/` |
| Stat cards | `templates/journal/partials/stat_cards.html` | included, not fetched |
| Table and cards | `templates/journal/partials/trade_table.html`, `trade_cards.html` | included |
| Empty state | `templates/journal/partials/trades_empty.html` | included |
| Errors | `templates/404.html`, `500.html`, `403_csrf.html` | |

Settings: `LOGIN_URL="/login/"`, `LOGIN_REDIRECT_URL="/trades/"`, `LOGOUT_REDIRECT_URL="/login/"`.

### Build notes

1. **No htmx here.** The one exception is unchanged: the import delete dialog. That request must not swap a login page into the dialog when the session has expired. For htmx requests from a logged-out session return `401` with `HX-Redirect: /login/?next=...`, not a 302 to the login page.
2. Trade list is compute-on-read: `derive_trades()`, then sort and page in Python (ADR-0003 §5). Stats are computed from the same trade list in the same request, grouped by currency. Use `Decimal`; win rate to 1 dp and total P&L to the currency's minor unit are formatting of already-computed values.
3. Ordering: sort a Python list by the chosen key with the tie-breaks in 5.1; `pnl` puts `None` last both ways.
4. `Trade` (ADR-0003 §5) has no account label; derive it from the opening execution's `broker_account_label`. The "show Account column" check is a `len(set(labels)) >= 2` over all the user's trades, not the current page.
5. Signup: set `is_active`, log the user in with `login()` after `create_user()`. The time zone value goes through the existing `validate_timezone`. Map Django password-validator codes to the strings in 4.1.
6. Login: use the case-insensitive `get_by_natural_key` from schema.md; keep timing equal for unknown emails; do not distinguish inactive from unknown from wrong password.
7. `no-store` cache header on authenticated pages so the back button after logout does not show trade data.
8. Escape all user text (account labels, email).

### Not deferred silently (these come back with journaling)

- Filters (date range, symbol, rule-followed, win/loss), journal columns (rule-followed, note), inline journal entry, and Average R with n>0. Cards then follow the filtered set and their subtitle changes to say so.
- Settings screen (time zone, password change), password reset.

### Open questions

1. **Story 6 scope for this slice.** mvp.md story 6 lists filters and journal columns; this slice omits them because they depend on journaling. Confirm with `product-manager` (`import-and-list.md`, not yet written when I read).
2. **Rate limiting and enumeration** (`backend-engineer`, `security-reviewer`). Signup cannot fully hide whether an email exists without email verification (4.1). Recommend per-IP throttling on login and signup and a verification email before non-author users. Decide before release.
3. **Account label on trades** (`backend-engineer`). Confirm it can be derived from the opening execution for every trade, including multi-leg manual trades with mixed labels (not expected).
4. **Manual entry page** exists in this slice? If not, the empty state omits "add a trade by hand" and the import doc's `[ Add a trade ]` button in its empty state points nowhere.
5. **App name** for the nav and page titles ("Trading Journal" is a placeholder).
6. **No way to fix a wrong time zone** until a settings screen exists. Worth a tiny settings page next slice, or the field could move to the user menu.
7. **No password reset** (mvp story 1 flags it). A forgotten password locks a user out; before any non-author user, add reset or accept and say so.
8. **Docs index** still says "Not designed yet: signup/login, trades list, stat cards" (`docs/README.md` line 34). The orchestrator should update it when this is accepted.
9. **Cross-doc note, not a conflict:** mvp.md story 6 cites ADR-0001 for htmx list updates, but ADR-0001 is superseded by ADR-0002. Section 1 decision 9 (full-page sorting and paging) is allowed by story 6's "standard server-rendered pagination/requests" wording.

### Conflicts found

None between the docs read. `import-and-list.md` was absent at read time (checked twice); re-check when it lands.
