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
DELETE_JOURNAL_BODY_ONE = (
    "You've journaled 1 of these trades ({noted}). Deleting the import deletes that journal "
    "entry too."
)
DELETE_JOURNAL_BODY_MANY = (
    "You've journaled {n} of these trades ({noted}). Deleting the import deletes those "
    "journal entries too."
)
DELETE_LIST_HEADING = "Journal entries that will be deleted"
DELETE_LIST_HELPER = (
    "Want to keep any of your writing? Copy it from the list or open the trade first."
)
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
