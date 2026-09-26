# Design: signup / login / logout, trades list, three stat cards

Specs: stories 1 and 6 of [`mvp.md`](../product/features/mvp.md) and [`import-and-list.md`](../product/features/import-and-list.md) (slice 1). Formulas: [`pnl-and-matching.md`](../domain/pnl-and-matching.md) (§1 trades, §2 win rate, §3 R). Read path: [ADR-0003 §5](../adr/0003-data-model.md) (trades derived on read, no trade table). Layout, tokens, `Notice`, flash and tone conventions: [`import-account-label.md`](import-account-label.md) ("Conventions used" and section 6). This doc reuses them and adds only what is new.
Status: final for `frontend-engineer`. User rulings of 2026-09-26 are applied (section 1, decisions 6, 7, 12, 13; stat card strings follow the PM spec section 3). This doc is the single source for layout and copy; `import-and-list.md` owns behavior.

Slice scope: journaling does not exist yet. So the list has no rule-followed or note columns, **no filters, no pagination**, and Avg R has n=0. Filters and Avg R with n>0 come back in a later slice (section 9). Pages come back when a user passes about 1,000 trades.

---

## 1. Decisions

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | Landing | `/` redirects: logged out to `/login/`, logged in to `/trades/`. No marketing page. | Nothing to sell yet; fewest screens. |
| 2 | After signup | Auto log in, land on `/trades/` (empty state points to `/imports/`). | One click from "no data" to upload. Seeing the empty list first tells the user where trades will appear. |
| 3 | After login | `?next=` if it is a safe same-site path, else `/trades/`. | Standard. Validate with `url_has_allowed_host_and_scheme`. |
| 4 | Logged-in user opens `/login/` or `/signup/` | Redirect to `/trades/`. | |
| 5 | Logout | POST button in the nav. Redirect to `/login/` with flash "You're logged out." No GET route. | Django 5 logout is POST-only; avoids logout-by-link. |
| 6 | Time zone at signup (ruled 2026-09-26) | Visible text field, prefilled from the browser, default `UTC` without JS. | `accounts_user.timezone` drives every displayed time and there is **no settings screen** in this slice to fix a wrong guess later. |
| 7 | Password at signup (ruled 2026-09-26) | Password field **and** Confirm password field. **No show-password toggle** (signup or login). | There is no password reset (mvp story 1), so a typo lock-out is a real cost; typing it twice is the typo guard. |
| 8 | Trades list date | "Opened" time, sorted newest opened first. | Traders think in entry time. Open trades sort naturally with the rest. |
| 9 | htmx | **None on these screens.** Sorting is plain links, forms are plain POSTs. | Compute-on-read loads the whole execution history for any request (ADR-0003 §5), so a partial swap saves nothing and costs back-button and no-JS behavior. |
| 10 | Stats scope | Cards cover **all** the user's closed trades. | There are no filters or pages in this slice, so the cards and the table never disagree. When filters exist, cards follow the filtered set (mvp story 6). |
| 11 | Account column | Shown only if the user's trades span 2 or more distinct account labels (a blank label counts as one value, "no name"). Hidden otherwise. | A single-account user never needs it; a multi-account user needs it at once. |
| 12 | Pagination (ruled 2026-09-26) | **None.** One page with every trade. Sorting stays (Opened, Symbol, Net P&L). | The real export is 177 rows. Add pages when a user passes about 1,000 trades. |
| 13 | Filters (ruled 2026-09-26) | **None** in slice 1. | They depend on journaling and are cuttable in the PM spec. |
| 14 | Signup email leak, login throttling (ruled 2026-09-26) | Signup email-leak accepted for now; login throttling deferred. No UI for either. | See the one-line note in 4.1. |

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
| [ ...................                  ] |
| At least 8 characters.                   |
|                                          |
| Confirm password                         |
| [ ...................                  ] |
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
| Password | Required. `type="password"`, `autocomplete="new-password"`. Django validators (min 8, not too common, not all digits, not too like the email). No show/hide toggle. |
| Confirm password | Required. `type="password"`, `autocomplete="new-password"`. Must equal Password exactly (no trimming). Its error shows under this field. No paste blocking. |
| Time zone | Required, prefilled by Alpine from `Intl.DateTimeFormat().resolvedOptions().timeZone`; `UTC` when JS is off. `<input list="timezones">` with a `<datalist>` of `zoneinfo.available_timezones()` sorted. Help text without JS: "Used to show your trade times. Type your time zone, for example America/New_York." Validated server-side against the same set. |

**Wording** (exact):

| Where | String |
|---|---|
| Page title / h1 | Create your account |
| Subtitle | A private journal for your trades. |
| Email label | Email |
| Password label / help | Password / At least 8 characters. |
| Confirm password label | Confirm password |
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
| Confirm missing | Type your password again to confirm it. |
| Confirm mismatch | The two passwords don't match. Type the same password in both boxes. |
| Time zone missing / unknown | Choose a time zone from the list, for example America/New_York. |
| Email cannot be used (see below) | We couldn't create an account with those details. If you already have one, try logging in. |
| Error summary title (any error) | Please fix the items below. |
| Welcome flash | Welcome. Your account is ready. |

**Do not reveal whether an email exists in copy.** On a duplicate email (any case), show the single message "We couldn't create an account with those details. If you already have one, try logging in." as a **form-level** message in the error summary, not under the Email field, and keep the Email value. Do not say "already registered", "taken", or "in use". Both password fields are cleared on any error.

Note (ruled 2026-09-26): signup still lets a visitor tell a new email from an existing one (no email verification), and login throttling is deferred; both accepted for now, no UI, do not promise more in copy.

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
| [ ...................                  ] |
|                                          |
| [ Log in ]                               |
|                                          |
| New here? Create an account              |
+------------------------------------------+
```

- Email `autocomplete="username"`, password `autocomplete="current-password"`. Autofocus Email only when no error. No show/hide toggle.
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
| Logout flash | You're logged out. |
| Session expired | Same as login-required notice: Log in to continue. |

### 4.3 Form error presentation (both forms)

- On error the page re-renders (full page, status 200). An error summary `Notice` (`attention` variant, not red) sits above the form with the title and one line per problem, each a link to its field. It has `tabindex="-1"` and receives focus on load, so screen readers and keyboard users land on it.
- Each field also shows its message directly beneath it, tied with `aria-describedby`, with `aria-invalid="true"` on the input. Message text is preceded by an icon and the word "Error:" is **not** used; the icon is decorative (`aria-hidden`), the text carries the meaning.
- Form-level messages (bad credentials, cannot create account) appear only in the summary.
- No `role="alert"` (consistent with the import screens). Focus move does the announcing.
- CSRF failure (custom 403): "That page expired. Go back, refresh it, and try again." with a link to `/login/`.

### 4.4 States

| State | Signup | Login |
|---|---|---|
| Default | Form as 4.1 (email, password, confirm, time zone prefilled) | Form as 4.2 |
| Submitting | Button "Creating...", disabled | Button "Logging in...", disabled |
| Validation error | 4.3. Mismatch shows under Confirm password; both password fields cleared. | 4.3 |
| Server failure | Above the form: "We couldn't finish creating your account. Try again in a moment." | Above the form: "We couldn't log you in just now. Try again in a moment." |
| Loading, empty | Not applicable (server-rendered forms) | Not applicable |

---

## 5. Trades list (`/trades/`)

One page, every trade, no filters, no pagination.

### 5.1 Populated, desktop

Full width of the shell (max about 1200px), 24px gutters.

```
+--------------------------------------------------------------------------------------+
| Trades                                                        [ Import trades ]      |
|                                                                                      |
| +---------------------+ +---------------------+ +----------------------------------+ |
| | Win rate            | | Total P&L           | | Avg R                            | |
| | 60.00% (n=5)        | | +$25.00 (n=7)       | | — (n=0)                          | |
| | Excludes 2          | |                     | | Avg R shows your results in      | |
| | breakeven trades.   | |                     | | units of what you risked on each | |
| |                     | |                     | | trade. It needs a stop or a      | |
| |                     | |                     | | planned risk amount ... (full    | |
| |                     | |                     | | text in 6.3)                     | |
| +---------------------+ +---------------------+ +----------------------------------+ |
| > How these are calculated                                                           |
|                                                                                      |
| Opened (America/New_York) v  Symbol  Side   Qty  Entry   Exit  Duration Result   Net P&L Account |
| --------------------------------------------------------------------------------------|
| Sep 26, 2:31 PM   MNQZ6    Long    2   19,850.25 19,862.00 12m  (+) Win   +$23.50    Combine 50K |
| Sep 26, 9:10 AM   CLZ6     Short   1   80.15     80.30  1h 05m  (-) Loss  -$150.00   Combine 50K |
| Sep 25, 3:58 PM   ESZ6     Long    1   5,801.00  5,801.00 45s   (=) Breakeven $0.00  Live        |
| Sep 25, 3:40 PM   MNQZ6    Long    1   19,840.00  -      Open   (o) Open    Open     Live        |
| ... every remaining trade, one page ...                                              |
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
| 9 | Net P&L | Stored net P&L after fees, USD, explicit sign, `$`, thousands separator, 2 decimals: `+$23.50`, `-$150.00`, `$0.00` (same formatter as the Total P&L card; PM wording). Open trades: the word "Open". | right | yes |
| 10 | Account | Label, or "no name" for a blank label. Column present only under decision 11. | left | no |

Formatting rules:
- Numbers use `font-variant-numeric: tabular-nums`. Prices and quantities are shown at up to 4 decimals with trailing zeros trimmed to a minimum of 2 for prices. This is a display filter only; no value is computed from a displayed string.
- **Win, loss, breakeven are never color alone.** The Result column has icon plus word (check-circle, minus-circle, equals, dot-circle), and the P&L cell carries the sign. Color is reinforcement only. Use `gain` and `loss` tokens (muted green and muted rose, AA against the row surface in light and dark). Do **not** use the `danger` token (reserved for delete) and no red backgrounds on loss rows. Breakeven and Open use the neutral text color.
- Header sort links: each sortable `<th>` has `aria-sort="ascending|descending|none"` and contains a link whose text is the column name plus an arrow glyph on the active column, with visually hidden text "sorted newest first" (or "oldest first", "highest first", "lowest first", "A to Z", "Z to A"). Clicking the active column flips direction.
- Sort URL: `?sort=opened|symbol|pnl&dir=asc|desc`. Unknown values fall back to the default without an error. Ties: opened time descending, then a stable id. **Open trades sort last for `pnl` in both directions.**
- No page controls and no "Showing a-b of n" footer. Sticky header keeps the columns visible while scrolling 177 rows.
- Trade rows are **not links** in this slice: a trade's identity is derived and can shift (ADR-0003), and there is no trade page yet. Do not fake a link.
- `<table>` has `<caption class="sr-only">Your trades</caption>`, real `<th scope="col">`, sticky header on desktop.

### 5.2 Populated, mobile (under 640px)

Cards replace the table (from the same data; table is `hidden sm:block`, the list is `sm:hidden`, so screen readers get one copy).

```
+--------------------------------------+
| Trades                               |
| [ Import trades ]  (full width)      |
|                                      |
| Win rate       60.00% (n=5)          |
| Excludes 2 breakeven trades.         |
| ------------------------------------ |
| Total P&L      +$25.00 (n=7)         |
| ------------------------------------ |
| Avg R          — (n=0)               |
| Avg R shows your results in units    |
| of what you risked on each trade.    |
| It needs a stop or a planned risk    |
| amount ... (full text in 6.3)        |
| > How these are calculated           |
|                                      |
| Sort by [ Newest opened v ] [Apply]  |   <- <select> in a GET form
|                                      |
| CLZ6  Short                 -$150.00 |
| (-) Loss                             |
| Sep 26, 9:10 AM (America/New_York)   |
| 1h 05m  -  Qty 1  -  80.15 to 80.30  |
| Account: Combine 50K                 |
| ------------------------------------ |
| MNQZ6  Long                  +$23.50 |
| ...  (every trade, no page break)    |
+--------------------------------------+
```

- Line 1: symbol, side in words, signed P&L right-aligned. Line 2: Result icon + word. Line 3: opened time; zone name shown once above the list ("Times in America/New_York"), not per card. Line 4: duration, quantity, entry to exit. Account line only under decision 11.
- Sort control on mobile is a labelled `<select>` inside a GET form with an "Apply" button (native, no JS; Alpine may auto-submit on change). Options: Newest opened, Oldest opened, Symbol A to Z, Symbol Z to A, Highest P&L, Lowest P&L.
- Stat cards stack in one column, compact (label and value-with-n on one line). 44px targets for Apply and the import button.
- Text scales to 200% without horizontal scroll.

### 5.3 Multi-account

- Account labels come from the opening execution's `broker_account_label` (see open question 3). Blank displays as "no name". The value is user text: escape it, show full text in `title` if truncated at 24 characters.
- No account filter in this slice. The sort options do not include Account.
- Trades from different accounts sit in one list and one set of stats. That is intended (one trader, one journal). When the Account column is showing (decision 11), the cards carry one quiet line under them: "Across all accounts." (PM wording). Hidden for a single account.
- Currency: slice 1 is USD only (TopstepX), and card and cell strings use `$`. Non-USD trades are open question 10.

### 5.4 Empty and other states

**A. No imports and no trades** (new user). Cards and table are not rendered.

```
+--------------------------------------------------------------------------+
| Trades                                                                   |
|                                                                          |
|   No trades yet                                                          |
|   Upload your TopstepX 'Trades' export and your trades show up here.     |
|                                                                          |
|   [ Import trades ]                                                      |
+--------------------------------------------------------------------------+
```

- Button links to `/imports/`. It is the one and only action on the page (manual entry does not exist in slice 1, so there is no "add by hand" option or link).

**B. Has imports, but none produced a trade** (every row was skipped or failed).

```
|   No trades yet                                                          |
|   Your last upload didn't add any trades.                                |
|   [ See what happened to that upload ]   -> that import's detail page    |
```

Same heading as A; body and link per PM spec §2.

**C. Only open trades** (no closed trades yet). Table shows the open rows. Cards render their n=0 strings:

| Card | String |
|---|---|
| Win rate | `n/a (n=0)` |
| Total P&L | `— (n=0)` |
| Avg R | `— (n=0)` plus the help text (6.3) |

**D. Errors and loading.**

| State | What the user sees |
|---|---|
| Loading | Server-rendered; no skeleton. Sorting is a full page load. |
| Server error (500) | Standard app error page: "This page didn't load. Nothing was changed. Try again in a moment." with a "Try again" link to the same URL. |
| Bad sort param | Silent fallback (5.1). No error. |
| Logged out | Redirect to `/login/?next=...` (section 4.2). |
| Not applicable | Stale-data or partial states: none, since the page is a pure read. |

---

## 6. Stat cards

Strings below are the PM spec's section 3, exactly. Markup: a `<div role="group" aria-label="Summary of your closed trades">` holding three `<section aria-labelledby>` cards, each with an `<h2>` label and **one line carrying the value and its sample size together** (`58.33% (n=120)`), plus optional small text lines. Desktop: three equal columns with a 16px gap. Under 640px: one column. The population is **all closed trades** (open trades are excluded from all three, domain §1); there are no filters in this slice.

Tone rules: neutral descriptions only. No adjectives that grade the number ("good", "low", "poor", "only"), no color grading by threshold, no trend arrows, no comparison with other traders.

Definitions come from the domain doc and are shown in the "How these are calculated" disclosure (6.4).

### 6.1 Win rate

| State | Value line | Second line |
|---|---|---|
| Populated | `58.33% (n=120)` (rate quantized once to 2 dp at computation; `n` = wins + losses, domain §2) | `Excludes N breakeven trades.` shown only when N > 0 (singular: `Excludes 1 breakeven trade.`). Explains why this `n` can differ from Total P&L's. |
| No wins and no losses (no closed trades, or all breakeven) | `n/a (n=0)` (never `0%`) | Same breakeven line if N > 0, otherwise none. |

Screen-reader label: "Win rate: 58.33 percent, based on 120 trades" / "Win rate: not available, based on 0 trades".

### 6.2 Total P&L

| State | Value line | Second line |
|---|---|---|
| Populated | `+$1,234.56 (n=177)`, `-$312.40 (n=177)`, `$0.00 (n=7)` (explicit sign, `$`, thousands separator, 2 dp; zero has no sign). `n` = closed trades, breakeven included. | none |
| No closed trades | `— (n=0)` | none |

Negative value uses the loss token plus the printed minus sign; positive uses the gain token plus the plus sign; never color alone. Screen-reader label spells the sign: "Total P&L: minus 312.40 dollars, based on 177 trades" / "Total P&L: not available, based on 0 trades". "After fees" and "open trades not included" live in the disclosure (6.4), not on the card.

### 6.3 Avg R (this slice: always `— (n=0)`)

| Element | String |
|---|---|
| Heading | Avg R |
| Value line, no data | `— (n=0)` (never `0`, `0.00` or `NaN`) |
| Value line, populated (later slice; build now) | `1.40 (n=32)` (mean of per-trade R where R is not null, 2 dp, domain §3; `n` = trades with non-null R) |
| Help text, shown **only when n = 0** | Avg R shows your results in units of what you risked on each trade. It needs a stop or a planned risk amount on the trade, which you'll be able to add once trade journaling is available. Trades without one are left out, never counted as zero. |
| Screen-reader value | Avg R: not available, based on 0 trades |

No date, no "coming soon", no advice, no link, no styling that reads as broken (same card frame, value in the normal text color, help text in the muted text token that still meets AA). The Win rate and Total P&L cards are unaffected. The card is built for both states now so the later slice does not touch it.

### 6.4 "How these are calculated" (one `<details>` under the cards, closed by default)

Summary text: How these are calculated. Body:

- Win rate: wins divided by wins plus losses. Breakeven trades (exactly $0.00 after fees) are left out of both.
- Total P&L: the sum of net P&L after fees for closed trades. Open trades are not included.
- Avg R: the average of net P&L divided by the risk amount you set for each trade. Trades without a risk amount are left out.
- Times are shown in {zone}. These numbers cover all your trades on all your accounts.

Native `<details>`, keyboard operable, visible focus ring.

---

## 7. Components

| Component | Notes |
|---|---|
| `AppShell` (`base.html`) | Skip link, `Nav`, flash region, `<main id="main">`, title block. Content width per page: 400px (auth card), 640px (forms), full (tables). |
| `Nav` | Logged-in and logged-out variants, `<details>` menu on mobile, POST logout form. |
| `AuthCard` | 400px card with h1, optional subtitle, form, footer link. |
| `FormField` | Label, input, help text, error message, `aria-describedby`, `aria-invalid`. Reuse for the import Account field where possible. Signup uses it for Password and Confirm password too. |
| `ErrorSummary` | `Notice` variant `attention`, focusable, links to fields. |
| `Notice` | Existing component (import doc section 6): `info`, `success`, `attention`. Used for flash, login-required, summary. |
| `StatCard` | Label, one value-with-n line, optional extra lines; variants populated, n/a. One partial, three instances. |
| `TradeTable` | Desktop table with sort headers, all rows. |
| `TradeCardList` | Mobile cards, same context as the table. |
| `SortSelect` | Mobile GET form with `<select>`. |
| `ResultBadge` | Icon plus word for Win, Loss, Breakeven, Open. Icons: check-circle, minus-circle, equals, dot-circle. |
| `EmptyState` | Title, body, one primary button. |

Removed from the earlier draft: `PasswordField` (no toggle) and `Pager` (no pagination).

Tokens added (light and dark values each; extend the import doc's set): `gain`, `loss` (muted, AA on `surface`, never `danger`), `text-muted`, `surface-raised` (cards). No hard-coded colors in templates.

---

## 8. Accessibility checklist

- Every state is icon plus text: Result badges, sort indicators, error messages, card lines.
- Focus ring visible on every control; skip link first; one h1 per page; logical heading order (h1 page, h2 card labels).
- Error summary receives focus on load; fields use `aria-invalid` and `aria-describedby`; labels are real `<label for>`, including Confirm password.
- Sortable headers use `aria-sort` and link text, not click-only cells.
- Tables have caption and `scope`; the mobile card list is a `<ul>` and only one of table or list is exposed at a time.
- Contrast: AA for text and icons in light and dark, including muted text, `gain`, `loss`, placeholder and disabled labels.
- Touch targets 44px on mobile; respects `prefers-reduced-motion` (no animation required).
- Autofill and password managers work: correct `autocomplete` values and `type`s (both signup password fields `new-password`); no paste blocking.

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
2. Trade list is compute-on-read: `derive_trades()`, then sort in Python (ADR-0003 §5). No pagination, no filters. Stats are computed from the same trade list in the same request. Use `Decimal`; the win rate is quantized once to 2 dp at computation and total P&L to 2 dp; display only adds sign, `$` and separators to already-computed values.
3. Ordering: sort a Python list by the chosen key with the tie-breaks in 5.1; `pnl` puts `None` last both ways.
4. `Trade` (ADR-0003 §5) has no account label; derive it from the opening execution's `broker_account_label`. The "show Account column" check is a `len(set(labels)) >= 2` over all the user's trades.
5. Signup: set `is_active`, log the user in with `login()` after `create_user()`. Compare `password1` and `password2` in the form's `clean()` and attach the mismatch to `password2`. The time zone value goes through the existing `validate_timezone`. Map Django password-validator codes to the strings in 4.1.
6. Login: use the case-insensitive `get_by_natural_key` from schema.md; keep timing equal for unknown emails; do not distinguish inactive from unknown from wrong password.
7. `no-store` cache header on authenticated pages so the back button after logout does not show trade data.
8. Escape all user text (account labels, email).

### Not deferred silently (these come back later)

- Filters (date range, symbol, rule-followed, win/loss), journal columns (rule-followed, note), inline journal entry, and Avg R with n>0. Cards then follow the filtered set and their subtitle changes to say so.
- Pagination, when a user passes about 1,000 trades or render time passes the PM spec budget.
- Login throttling; a fix for the signup email leak (needs email verification).
- Settings screen (time zone, password change), password reset.

### Closed by user rulings (2026-09-26)

- Former Q1, story 6 scope: no filters in slice 1, no pagination (decisions 12, 13).
- Former Q2, rate limiting and enumeration: signup leak accepted for now, login throttling deferred (decision 14).
- Former Q4, manual entry page: does not exist in slice 1; the empty state has no "add by hand" option (5.4). The import doc's own empty state (`import-account-label.md`) had the same option; removed 2026-09-26.
- Former Q6, wrong time zone at signup: visible prefilled field stays (decision 6). A settings screen remains out of scope.

### Open questions

1. **Account label on trades** (`backend-engineer`). Confirm it can be derived from the opening execution for every trade, including multi-leg manual trades with mixed labels (not expected).
2. **App name** for the nav and page titles ("Trading Journal" is a placeholder) (`product-manager`, user).
3. **No password reset** (mvp story 1 flags it) (`product-manager`, user). A forgotten password locks a user out; before any non-author user, add reset or accept and say so. The confirm field reduces typos but does not remove this.
5. **Cross-doc note, not a conflict:** mvp.md story 6 cites ADR-0001 for htmx list updates, but ADR-0001 is superseded by ADR-0002. Decision 9 (full-page sorting) is allowed by story 6's "standard server-rendered pagination/requests" wording (`product-manager` to fix the citation).
6. **Non-USD trades** (`architect`, `trading-domain-expert`). Card and cell strings hard-code `$` per the PM spec. If a non-TopstepX or non-USD import ever arrives, the strings and per-currency grouping need a decision. Not needed for slice 1.

### Conflicts found

None. Resolved 2026-09-26 by user ruling: this doc is the single source for trades-table layout and auth/error copy (date format and header, blank account label, Duration and Result columns, duplicate-email wording); `import-and-list.md` keeps behavior and the stat card strings and points here.
