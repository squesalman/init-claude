"""
User-facing auth strings, exact per docs/design/auth-and-trades-list.md section 4.1 / 4.2 /
4.4 / 5.4. The design doc owns the wording; change it there first. Templates use these via the
form/view context so the frontend never retypes them.
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
