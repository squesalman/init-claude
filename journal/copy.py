"""
User-facing import strings, per docs/design/import-account-label.md section 5. The design doc
owns the wording; change it there first. PR C2 adds the "Delete this import" block (design
3.3, 3.4, 5; ADR-0005), and the blank conflict banner now says "Delete this import first"
next to its delete button.
Importer and row reasons live in journal/importers/topstep.py and journal/services.py.
"""

# Upload form (3.1, 4)
PAGE_TITLE = "Import trades"
PAGE_INTRO = "Upload your TopstepX trade export (CSV). Nothing is imported twice."
BROKER_LABEL = "Broker"
FILE_LABEL = "File"
ACCOUNT_SUMMARY = "Trade more than one Topstep account?"
ACCOUNT_LABEL = "Account (optional)"
ACCOUNT_HELP = (
    "Only needed if you trade more than one Topstep account. Use the same name each time."
)
ACCOUNT_PLACEHOLDER_EMPTY = "e.g. Combine 50K"
ACCOUNT_PLACEHOLDER_SAVED = "Choose or type a name"
# Django MaxLengthValidator params.
ACCOUNT_TOO_LONG = (
    "That name is %(show_value)d characters and the limit is %(limit_value)d. "
    "Shorten it a little."
)
# Ruled final by ux-designer 2026-09-27 (docs/design/import-account-label.md section 5).
ACCOUNT_CONTROL_CHARACTERS = (
    "Account names can't contain line breaks or other control characters. Remove them and "
    "try again."
)
FILE_MISSING = "Choose a CSV file to upload."
UPLOAD_BUTTON = "Upload"
UPLOAD_BUSY = "Importing..."
UPLOAD_BUSY_LIVE = "Importing your file"
# Ruled final by ux-designer 2026-09-27 (docs/design/import-account-label.md section 5).
UPLOAD_DONE = "Import finished. Here's what happened to each row."

# Imports list (3.5)
LIST_HEADING = "Your imports"
LIST_EMPTY_HEADING = "No imports yet"
LIST_EMPTY_BODY = "Your uploads will show up here, with what was imported and what was skipped."
LIST_ACCOUNT_BLANK = "no name"
LIST_ACCOUNT_NOT_RECORDED = "not recorded"  # visually hidden, next to "-"

# Import detail (3.2)
BACK_TO_IMPORTS = "Imports"
ACCOUNT_LINE = "Account: {label}"
ACCOUNT_LINE_BLANK = "Account: no account name"
ACCOUNT_LINE_NOT_RECORDED = "Account: not recorded (nothing was imported)"

CONFLICT_TITLE_ONE = "1 row was not imported"
CONFLICT_TITLE_MANY = "{n} rows were not imported"
CONFLICT_BODY_BLANK = (
    "Their IDs match trades you already imported, but the details differ. This usually "
    "means the file is from a different account. Nothing was lost or overwritten. Delete "
    "this import first, then upload again with an Account name, so nothing is counted twice."
)
CONFLICT_BODY_FILLED = (
    "Under the Account name '{label}' those IDs already exist with different details. "
    "Check that the name is the one you meant. Nothing was lost or overwritten."
)
# The batch imported nothing, so its Account name is not recorded (no label column, H1):
# neutral wording for blank and filled alike. Ruled final by ux-designer 2026-09-27
# (docs/design/import-account-label.md section 5, section 3.2 "when it's used").
CONFLICT_BODY_NOT_RECORDED = (
    "Their IDs match trades you already imported, but the details differ. Nothing was lost "
    "or overwritten. Check the Account name you used, then upload the file again."
)

SUMMARY_CLEAN_ONE = "All 1 row imported."
SUMMARY_CLEAN = "All {n} rows imported."
SUMMARY_DUPLICATES_ONLY = (
    "These rows were already imported earlier, so nothing was added twice."
)
SUMMARY_NOTHING_ADDED = "No new trades were added."
FAILED_NOTICE_ONE = '1 row could not be read. See it under "Failed" below.'
FAILED_NOTICE_MANY = '{n} rows could not be read. See them under "Failed" below.'

HINT_PLAIN = "You uploaded this exact file on {date}."
HINT_PLAIN_LINK = "View that import"
HINT_MISMATCH_TITLE = "You uploaded this exact file before"
HINT_MISMATCH_BODY = (
    "On {date}, with Account '{label}'. Uploading it again with a different name adds these "
    "trades a second time."
)
HINT_MISMATCH_BODY_BLANK = (
    "On {date}, with no Account name. Uploading it again with a different name adds these "
    "trades a second time."
)
HINT_MISMATCH_LINK = "View the {date} import"

FILTER_LABELS = {
    "needs_attention": "Needs attention",
    "all": "All",
    "imported": "Imported",
    "skipped_duplicate": "Skipped: duplicate",
    "skipped_conflict": "Skipped: conflict",
    "failed": "Failed",
}
EMPTY_FILTER = "No rows with this status."
SHOWING = "Showing {start}-{end} of {total}"
PREVIOUS = "Previous"
NEXT = "Next"

# Single-word headings/labels from the wireframes (design 3.2, 3.5; not in section 5) are
# written directly in the templates, not held here — accounts/copy.py's own convention is
# full sentences only, and a one-word constant used in one place buys nothing.

# Shown by static/js/app.js when the upload request fails before any page comes back (a 413
# from the body cap, or a network error). PROVISIONAL, pending ux-designer confirmation
# (added after the 2026-09-27 ruling on the other 3 provisional strings, so not covered by it).
UPLOAD_ERROR = "That file couldn't be uploaded. Try a smaller file, or try again in a moment."

# Delete import (design 3.3, 3.4, 5; ADR-0005)
DELETE_ACTION = "Delete this import"
DELETE_TITLE = "Delete this import?"
TRADES_ONE = "1 trade"
TRADES_MANY = "{n} trades"
ENTRIES_ONE = "1 journal entry"
ENTRIES_MANY = "{n} journal entries"
NOTED_NONE = "none with written notes"
NOTED_ONE = "1 with a written note"
NOTED_MANY = "{n} with written notes"
DELETE_BODY = (
    "This removes the {trades} that came from this file, and the upload record. Your other "
    "imports stay as they are."
)
DELETE_BODY_WITH_SKIPPED_ONE = (
    "This removes the {trades} that came from this file, and the upload record (including "
    "1 row that was skipped or could not be read). Your other imports stay as they are."
)
DELETE_BODY_WITH_SKIPPED = (
    "This removes the {trades} that came from this file, and the upload record (including "
    "{k} rows that were skipped or could not be read). Your other imports stay as they are."
)
DELETE_BODY_NO_TRADES = (
    "This import added no trades (every row was skipped or failed). Deleting it only removes "
    "the upload record. Your trades are not affected."
)
# Replaced by docs/design/journaling.md 7 and 12 (stop/risk mention, the trade page link).
DELETE_JOURNAL_BODY_ONE = (
    "You've journaled 1 of these trades ({noted}). Deleting the import deletes that journal "
    "entry too, along with any stop or planned risk on it."
)
DELETE_JOURNAL_BODY_MANY = (
    "You've journaled {n} of these trades ({noted}). Deleting the import deletes those "
    "journal entries too, along with any stop or planned risk on them."
)
DELETE_LIST_HEADING = "Journal entries that will be deleted"
DELETE_LIST_HELPER = (
    "Want to keep any of your writing? Copy it from the list, or use View trade to open that "
    "trade's journal in a new tab."
)
DELETE_HAS_RISK = "Has a stop or planned risk amount."
DELETE_VIEW_TRADE = "View trade"
DELETE_VIEW_SR = "for {symbol}, opens in a new tab"
DELETE_NO_NOTE = "(no written note)"
DELETE_SHOW_ALL = "Show all {n}"
RULES_FOLLOWED = "followed"
RULES_NOT_FOLLOWED = "not followed"
RULES_NOT_ANSWERED = "not answered"
DELETE_CHECKBOX = "Also delete my {entries} ({noted}). This can't be undone."
DELETE_REUPLOAD_LINE = (
    "You uploaded this file again on {date}, but that upload added nothing new, so these "
    "trades exist only through this import. Deleting it removes them."
)
DELETE_PERMANENT = "This can't be undone."
DELETE_CANCEL = "Cancel"
DELETE_BUTTON = "Delete import"
DELETE_BUTTON_WITH_ENTRIES = "Delete import and entries"
DELETE_BUSY = "Deleting..."
DELETE_NOT_TICKED = "Tick the box to confirm. Nothing was deleted."
DELETE_STALE = (
    "Your journal changed while this was open, so nothing was deleted. The list below is up "
    "to date."
)
DELETE_FAILED = "Something went wrong and nothing was deleted. Try again in a moment."
DELETE_LIVE_ENABLED = "Delete button is now available"
DELETE_LIVE_DISABLED = "Delete button is not available"

# After delete (3.4)
DELETED_TRADES = "Import deleted. {trades} removed. Ready when you are."
DELETED_TRADES_AND_ENTRIES = "Import deleted. {trades} and {entries} removed. Ready when you are."
DELETED_NO_TRADES = "Import deleted. No trades were affected. Ready when you are."
DELETED_FROM_BANNER = "If you meant to add an Account name, it's open below."
DELETE_ALREADY_GONE = "That import was already deleted."
DELETED_BEFORE = "That import was deleted."  # old /imports/<id>/ link (3.4, 4)

# Journal page, My rules, trades list journal column, Avg R card lines
# (docs/design/journaling.md section 12; verbatim, the design doc owns the wording).
JOURNAL_TITLE = "Journal: {symbol}"
JOURNAL_BACK = "Trades"
TRADE_ENTRIES = "{n} entries"
JOURNAL_INTRO = (
    "Nothing here is required. Save whatever you have; you can come back and change it."
)
NOT_FOUND_TITLE = "We couldn't find that trade"
NOT_FOUND_BODY = "It may have been removed along with an import."
NOT_FOUND_LINK = "Back to trades"

RULES_HEADING = "My rules"
RULES_ACTION_ADD = "Add"
RULES_ACTION_EDIT = "Edit"
RULES_ACTION_CLOSE = "Close"
RULES_PROMPT = "Add your rules so you can check each trade against them."
RULES_LABEL = "Your rules"
RULES_HELP = (
    "In your own words. Your rules aren't saved with each answer, so if you reword them, past "
    "answers still reflect the earlier wording."
)
RULES_SAVE = "Save rules"
RULES_SAVE_BUSY = "Saving..."
RULES_SAVED = "Your rules are saved."
# Django MaxLengthValidator params.
RULES_TOO_LONG = (
    "Your rules are %(show_value)d characters and the limit is %(limit_value)d. Shorten them a "
    "little."
)
RULES_SAVE_FAILED = (
    "We couldn't save your rules just now. Your text is still here, so try again in a moment."
)

FOLLOWED_LEGEND = "Did you follow your rules on this trade?"
FOLLOWED_HELP = (
    "This is what marks a trade as journaled. You can leave it for later and change your "
    "answer any time."
)
FOLLOWED_YES = "Followed my rules"
FOLLOWED_NO = "Didn't follow my rules"

NOTE_LABEL = "Note"
NOTE_HELP = (
    "What was your thinking going in, and how did it go? Anything you'd want to remember."
)
NOTE_TOO_LONG = (
    "That note is %(show_value)d characters and the limit is %(limit_value)d. Shorten it a "
    "little."
)
COUNTER = "{n} / {limit}"
COUNTER_LIVE_NEAR = "You're close to the {limit}-character limit."
COUNTER_LIVE_OVER = "Over the {limit}-character limit by {n}."

RISK_SUMMARY = "Risk for Avg R (optional)"
RISK_INTRO = "Add a stop or a planned risk to include this trade in Avg R. Both are optional."
STOP_LABEL = "Stop price"
STOP_HELP_LONG = (
    "Your initial stop, as a price. For this long trade it sits below your entry of {entry}."
)
STOP_HELP_SHORT = (
    "Your initial stop, as a price. For this short trade it sits above your entry of {entry}."
)
STOP_HELP_MULTI_LEG = (
    "This trade has more than one entry, so a stop can't give R. Enter planned risk to get R on "
    "this trade."
)
RISK_LABEL = "Planned risk"
RISK_HELP = "The amount you planned to risk on this trade, in {currency}, before fees."
RISK_BOTH_SET = "Planned risk is used for R when both are set."
STOP_NOT_NUMBER = "Enter a number like 19845.50. Use a period for decimals."
STOP_DECIMALS = "Use up to 10 decimal places."
STOP_WRONG_SIDE = "Your stop should sit on the loss side of your entry price."
RISK_NOT_NUMBER = "Enter a number like 62.50. Use a period for decimals."
RISK_DECIMALS = "Use up to 4 decimal places."
RISK_NOT_POSITIVE = "Planned risk needs to be more than 0."
NUMBER_TOO_LARGE = "That number is too large."

R_STATUS_OK = "This trade counts in Avg R."
R_STATUS_TRADE_OPEN = "This trade is still open. R is worked out once it closes."
R_STATUS_RISK_NOT_POSITIVE = (
    "The saved planned risk isn't more than 0, so this trade is left out of Avg R. Enter a new "
    "amount to fix it."
)
R_STATUS_CURRENCY_MISMATCH = (
    "Your saved planned risk is in {risk_currency}, but this trade is in {trade_currency}, so "
    "it's left out of Avg R. Save a new amount to update it."
)
R_STATUS_STOP_NOT_A_RISK = (
    "Your saved stop isn't on the loss side of this trade's entry price, so it's left out of "
    "Avg R."
)
R_STATUS_STOP_MULTI_LEG = (
    "This trade has more than one entry, so a stop can't give R. Enter planned risk to get R on "
    "this trade."
)

JOURNAL_SAVE = "Save journal"
JOURNAL_SAVE_BUSY = "Saving..."
JOURNAL_BACK_TO_TRADES = "Back to trades"
JOURNAL_SAVED = "Journal saved."
JOURNAL_NOTHING_TO_SAVE = "Nothing to save yet. Add a note, an answer, or a risk amount."
JOURNAL_SAVE_FAILED = (
    "We couldn't save that just now. Your text is still here, so try again in a moment."
)

LIST_JOURNAL_ADD = "Add journal"
LIST_JOURNAL_FOLLOWED = "Followed rules"
LIST_JOURNAL_NOT_FOLLOWED = "Didn't follow rules"
LIST_JOURNAL_NOTE_ONLY = "Note only"
LIST_JOURNAL_RISK_ONLY = "Risk only"
LIST_JOURNAL_NOTE_AND_RISK = "Note and risk"
LIST_JOURNAL_SR_CONTEXT = "{state} for {symbol}, opened {opened}"

AVG_R_LEFT_OUT_ONE = "Left out: 1 closed trade without usable risk"
AVG_R_LEFT_OUT_MANY = "Left out: {n} closed trades without usable risk"
