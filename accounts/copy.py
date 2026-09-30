"""
User-facing auth and /trades/ strings, exact per docs/design/auth-and-trades-list.md section
4.1 / 4.2 / 4.4 / 5.1 / 5.3 / 5.4 / 6. The design doc owns the wording; change it there
first. Templates use these via the form/view context so the frontend never retypes them.
"""

# Signup (4.1)
SIGNUP_WELCOME = "Welcome. Your account is ready."
SIGNUP_EMAIL_MISSING = "Enter your email address."
SIGNUP_EMAIL_MALFORMED = "Enter an email address like name@example.com."
SIGNUP_PASSWORD_MISSING = "Choose a password."
SIGNUP_PASSWORD_TOO_SHORT = "Use at least 8 characters."
SIGNUP_PASSWORD_TOO_COMMON = (
    "That password is easy to guess. Try a longer one, or a few unrelated words together."
)
SIGNUP_PASSWORD_ALL_DIGITS = "Add some letters as well as numbers."
SIGNUP_PASSWORD_LIKE_EMAIL = "That password is too close to your email. Try something different."
SIGNUP_CONFIRM_MISSING = "Type your password again to confirm it."
SIGNUP_CONFIRM_MISMATCH = "The two passwords don't match. Type the same password in both boxes."
SIGNUP_TIMEZONE_INVALID = "Choose a time zone from the list, for example America/New_York."
# Form-level only; must never say "registered", "taken" or "in use".
SIGNUP_EMAIL_UNUSABLE = (
    "We couldn't create an account with those details. If you already have one, try logging in."
)
SIGNUP_ERROR_SUMMARY_TITLE = "Please fix the items below."
SIGNUP_SERVER_FAILURE = "We couldn't finish creating your account. Try again in a moment."
SIGNUP_TIMEZONE_HELP = (
    "Used to show your trade times. Type your time zone, for example America/New_York."
)
SIGNUP_TIMEZONE_HELP_DETECTED = "Used to show your trade times. We detected this from your browser."
SIGNUP_BUSY = "Creating..."

# Django password-validator error code -> design string (4.1)
PASSWORD_VALIDATOR_COPY = {
    "password_too_short": SIGNUP_PASSWORD_TOO_SHORT,
    "password_too_common": SIGNUP_PASSWORD_TOO_COMMON,
    "password_entirely_numeric": SIGNUP_PASSWORD_ALL_DIGITS,
    "password_too_similar": SIGNUP_PASSWORD_LIKE_EMAIL,
}

# Login (4.2, 4.4)
LOGIN_REQUIRED = "Log in to continue."
LOGIN_EMAIL_MISSING = "Enter your email address."
LOGIN_PASSWORD_MISSING = "Enter your password."
LOGIN_BAD_CREDENTIALS = "That email and password don't match. Check them and try again."
LOGIN_SERVER_FAILURE = "We couldn't log you in just now. Try again in a moment."
LOGIN_BUSY = "Logging in..."
LOGOUT_FLASH = "You're logged out."

# /trades/ empty state A (5.4)
TRADES_EMPTY_HEADING = "No trades yet"
TRADES_EMPTY_BODY = "Upload your TopstepX 'Trades' export and your trades show up here."
TRADES_EMPTY_ACTION = "Import trades"
# /trades/ empty state B (5.4): has imports, none produced a trade. Heading as A.
TRADES_EMPTY_B_BODY = "Your last upload didn't add any trades."
TRADES_EMPTY_B_ACTION = "See what happened to that upload"

# Sort headers, visually hidden text on the active column (5.1)
SORTED_NEWEST = "sorted newest first"
SORTED_OLDEST = "sorted oldest first"
SORTED_HIGHEST = "sorted highest first"
SORTED_LOWEST = "sorted lowest first"
SORTED_A_TO_Z = "A to Z"
SORTED_Z_TO_A = "Z to A"

# Multi-account (5.3)
ACROSS_ALL_ACCOUNTS = "Across all accounts."

# Stat cards (6.1-6.3). NO_VALUE is Total P&L and Avg R with n=0.
NO_VALUE = "— (n=0)"
WIN_RATE_VALUE = "{rate}% (n={n})"
WIN_RATE_EMPTY = "n/a (n=0)"
BREAKEVEN_NOTE_ONE = "Excludes 1 breakeven trade."
BREAKEVEN_NOTE_MANY = "Excludes {n} breakeven trades."
TOTAL_PNL_VALUE = "{amount} (n={n})"
AVG_R_VALUE = "{r} (n={n})"
# Replaced by docs/design/journaling.md 6 and 12 (journaling ships).
AVG_R_HELP = (
    "Avg R shows your results in units of what you risked on each trade. Open a trade's "
    "journal and add a stop or a planned risk amount to include it. Trades without one are "
    "left out, never counted as zero."
)
# Screen-reader labels (6.1-6.3). {sign} is "plus ", "minus " or "" for zero.
WIN_RATE_SR = "Win rate: {rate} percent, based on {n} trades"
WIN_RATE_SR_EMPTY = "Win rate: not available, based on 0 trades"
TOTAL_PNL_SR = "Total P&L: {sign}{amount} dollars, based on {n} trades"
TOTAL_PNL_SR_EMPTY = "Total P&L: not available, based on 0 trades"
AVG_R_SR = "Avg R: {r}, based on {n} trades"
AVG_R_SR_EMPTY = "Avg R: not available, based on 0 trades"

# "How these are calculated" disclosure (6.4)
CALC_SUMMARY = "How these are calculated"
CALC_WIN_RATE = (
    "Win rate: wins divided by wins plus losses. Breakeven trades (exactly $0.00 after fees) "
    "are left out of both."
)
CALC_TOTAL_PNL = (
    "Total P&L: the sum of net P&L after fees for closed trades. Open trades are not included."
)
CALC_AVG_R = (
    "Avg R: net P&L after fees divided by the risk you entered, averaged over closed trades "
    "that have one. Risk is your planned risk amount if you set one, otherwise the distance "
    "from your entry to your stop times your size (trades with one entry only). Trades without "
    "usable risk are left out, never counted as zero."
)
CALC_TIMES = (
    "Times are shown in {zone}. These numbers cover all your trades on all your accounts."
)

# /trades/ page chrome (5.1, 5.2). Column headers, Result and Side words stay in the templates,
# as on the import pages. SORT_OPTIONS values are the mobile select's combined sort_dir param.
TRADES_TITLE = "Trades"
IMPORT_TRADES = "Import trades"
TRADES_CAPTION = "Your trades"
TIMES_IN = "Times in {zone}"
SORT_LABEL = "Sort by"
SORT_APPLY = "Apply"
SORT_OPTIONS = (
    ("opened_desc", "Newest opened"),
    ("opened_asc", "Oldest opened"),
    ("symbol_asc", "Symbol A to Z"),
    ("symbol_desc", "Symbol Z to A"),
    ("pnl_desc", "Highest P&L"),
    ("pnl_asc", "Lowest P&L"),
)
EXIT_OPEN_SR = "not closed yet"
CARDS_LABEL = "Summary of your closed trades"
