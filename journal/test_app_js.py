"""
Behaviour checks for static/js/app.js, run in Node's `vm` with tiny fakes for the DOM,
Alpine and htmx events (no npm packages). Skipped when `node` is not installed. The
browser check of the same flows is manual (see the PR notes).
"""

import json
import shutil
import subprocess

import pytest
from django.conf import settings

APP_JS = settings.BASE_DIR / "static" / "js" / "app.js"
NODE = shutil.which("node")

HARNESS = r"""
const vm = require("vm");
const src = require("fs").readFileSync(process.argv[1], "utf8");
const listeners = {};
const on = (type, fn) => (listeners[type] = listeners[type] || []).push(fn);
const pageButtons = [];
const document = {
  addEventListener: on, activeElement: null, body: {}, querySelector: () => null,
  querySelectorAll: (s) => (s === "button[data-label]" ? pageButtons : []),
};
const window = { addEventListener: on, Alpine: { data: (n, f) => (registered[n] = f) } };
const registered = {};
vm.runInNewContext(src, { document, window });
(listeners["alpine:init"] || []).forEach((f) => f());
const fire = (type, detail) => (listeners[type] || []).forEach((f) => f({ detail }));
const fireEvent = (type, event) => (listeners[type] || []).forEach((f) => f(event));

const TOO_LONG = "That name is %(show_value)d / %(limit_value)d";
function field(value) {
  const input = { value };
  const c = registered.accountField();
  c.$root = { dataset: { limit: "64", from: "50", tooLong: TOO_LONG } };
  c.$refs = { input };
  c.init();
  return { c, input };
}

const out = {};
// aria-invalid belongs to the server: the component offers nothing to bind it to.
out.has_invalid = "invalid" in registered.accountField();
// Counter and the role=status message.
let { c, input } = field("A".repeat(49));
out.counter_49 = c.counter;
input.value = "A".repeat(52); c.update();
out.counter_52 = c.counter;
input.value = "A".repeat(65); c.update();
out.message_65 = c.message;
input.value = "A".repeat(64); c.update();
out.message_fixed = c.message;

// Upload request errors: visible message in the form's error region, cleared on retry.
function region() {
  const text = { textContent: "" };
  const p = { hidden: true };
  return {
    dataset: { requestError: "That file couldn't be uploaded." },
    querySelector: (s) => (s === "[data-request-error-text]" ? text : p), text, p,
  };
}
const r = region();
const btn = { textContent: "Importing...", disabled: true, dataset: { label: "Upload" } };
const live = { textContent: "Importing your file", dataset: { busyLive: "Importing your file" } };
const form = {
  querySelector: (s) => (s === "[data-request-error]" ? r : s === "[data-busy-live]" ? live : null),
  querySelectorAll: (s) => (s === "button[data-label]" && btn.dataset.label ? [btn] : []),
};
const target = { setAttribute() {}, removeAttribute() {} };
const noHeaders = { getResponseHeader: () => null, status: 413 };
for (const kind of ["responseError", "sendError"]) {
  r.p.hidden = true; r.text.textContent = "";
  btn.textContent = "Importing..."; btn.disabled = true; btn.dataset.label = "Upload";
  fire("htmx:beforeRequest", { elt: form, target });
  // htmx order: responseError before afterRequest; sendError after it.
  if (kind === "responseError") fire("htmx:responseError", { elt: form, target, xhr: noHeaders });
  fire("htmx:afterRequest", { elt: form, target, xhr: noHeaders, failed: true });
  if (kind === "sendError") fire("htmx:sendError", { elt: form, target, xhr: noHeaders });
  out[kind] = {
    shown: !r.p.hidden, text: r.text.textContent, button: btn.textContent,
    disabled: btn.disabled, live: live.textContent,
  };
}
fire("htmx:beforeRequest", { elt: form, target });
out.cleared_on_retry = [r.p.hidden, r.text.textContent];
// A failing request from an element without an error region is ignored (filter tabs).
const link = { querySelector: () => null, querySelectorAll: () => [] };
fire("htmx:responseError", { elt: link, target, xhr: noHeaders });
out.link_ok = true;

// Handlers moved out of base.html: flash close, busy label on submit, bfcache reset.
let removed = false;
const flash = { remove: () => (removed = true) };
const closeBtn = { closest: (s) => (s === '[role="status"]' ? flash : null) };
fireEvent("click", { target: { closest: (s) => (s === "[data-dismiss]" ? closeBtn : null) } });
out.flash_removed = removed;
const submitBtn = { textContent: "Log in", disabled: false, dataset: { busy: "Logging in..." } };
const f2 = { querySelector: (s) => (s === "button[data-busy]" ? submitBtn : null) };
fireEvent("submit", { target: f2 });
out.busy = [submitBtn.textContent, submitBtn.disabled];
pageButtons.push(submitBtn);
fireEvent("pageshow", {});
out.pageshow = [submitBtn.textContent, submitBtn.disabled];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result():
    if NODE is None:
        pytest.skip("node is not installed")
    run = subprocess.run(
        [NODE, "-e", HARNESS, str(APP_JS)], capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_account_component_does_not_own_aria_invalid(result):
    # The server renders aria-invalid; a client subset of its rules must not overwrite it.
    assert result["has_invalid"] is False


def test_counter_and_live_message_unchanged(result):
    assert result["counter_49"] == ""
    assert result["counter_52"] == "52 / 64"
    assert result["message_65"] == "That name is 65 / 64"
    assert result["message_fixed"] == ""


@pytest.mark.parametrize("kind", ["responseError", "sendError"])
def test_failed_upload_shows_the_message_and_resets_the_busy_state(result, kind):
    got = result[kind]
    assert got["shown"] and got["text"] == "That file couldn't be uploaded."
    assert (got["button"], got["disabled"], got["live"]) == ("Upload", False, "")


def test_error_message_is_cleared_on_retry_and_other_elements_are_ignored(result):
    assert result["cleared_on_retry"] == [True, ""]
    assert result["link_ok"]


def test_base_handlers_live_in_app_js(result):
    assert result["flash_removed"] is True
    assert result["busy"] == ["Logging in...", True]
    assert result["pageshow"] == ["Log in", False]


# --- PR C2: delete dialog, confirm tick gate, row menu (design 3.3, 3.5, 7) ----------------

DIALOG_HARNESS = r"""
const vm = require("vm");
const src = require("fs").readFileSync(process.argv[1], "utf8");
const listeners = {};
const on = (type, fn) => (listeners[type] = listeners[type] || []).push(fn);
let focused = null;
const el = (props) => ({ focus() { focused = this; }, closest: () => null, ...props });
const h1 = el({ id: "h1" });
const openMenus = [];
const document = {
  addEventListener: on, activeElement: null, body: {},
  querySelector: (s) => (s === "h1" ? h1 : null),
  querySelectorAll: (s) =>
    (s === "details[data-menu][open]" ? openMenus.filter((m) => m.open) : []),
};
const window = {
  addEventListener: on, Alpine: { data: (n, f) => (registered[n] = f) }, location: {},
};
const registered = {};
vm.runInNewContext(src, { document, window });
(listeners["alpine:init"] || []).forEach((f) => f());
const fire = (type, event) => (listeners[type] || []).forEach((f) => f(event));
const out = {};

// deleteConfirm: locked until ticked; live region says so; no box means never locked.
const data = {
  liveOn: "Delete button is now available", liveOff: "Delete button is not available",
};
const tick = { checked: false };
let c = registered.deleteConfirm();
c.$root = { dataset: data }; c.$refs = { tick }; c.init();
out.locked_start = c.locked;
tick.checked = true; c.toggle(); out.ticked = [c.locked, c.live];
tick.checked = false; c.toggle(); out.unticked = [c.locked, c.live];
c = registered.deleteConfirm(); c.$root = { dataset: data }; c.$refs = {}; c.init();
out.no_box = c.locked;

// Dialog: a row-menu link opens it after the body swap; the menu closes; Esc/close returns
// focus to the menu's button; the body is emptied.
const bodyEl = { children: 1, replaceChildren() { this.children = 0; } };
const dialog = el({ id: "delete-dialog", open: false, showModal() { this.open = true; },
  close() { this.open = false; fire("close", { target: this }); },
  querySelector: (s) => (s === "#delete-dialog-body" ? bodyEl : null) });
bodyEl.closest = (s) => (s === "dialog" ? dialog : null);
const summary = el({ id: "summary", isConnected: true });
const menu = { open: true, querySelector: () => summary, contains: () => false };
openMenus.push(menu);
const link = el({ href: "/imports/1/delete/", hasAttribute: (a) => a === "data-opens-dialog",
  closest: (s) => (s === "details" ? menu : null),
  querySelector: () => null, querySelectorAll: () => [] });
const target = { setAttribute() {}, removeAttribute() {}, closest: bodyEl.closest };
fire("htmx:beforeRequest", { detail: { elt: link, target } });
fire("htmx:afterSwap", { detail: { elt: link, target } });
out.opened = [dialog.open, menu.open];
dialog.close();
out.closed = [focused && focused.id, bodyEl.children];

// A header button that is gone after a re-render: focus falls back to the h1.
const gone = el({ id: "btn", isConnected: false, hasAttribute: () => true,
  querySelector: () => null, querySelectorAll: () => [] });
fire("htmx:beforeRequest", { detail: { elt: gone, target } });
fire("htmx:afterSwap", { detail: { elt: gone, target } });
dialog.close();
out.fallback = focused && focused.id;

// Cancel inside the dialog closes it instead of navigating.
dialog.open = true;
let prevented = false;
const cancel = {
  closest: (s) => (s === "[data-cancel]" ? cancel : s === "dialog" ? dialog : null),
};
fire("click", { target: cancel, preventDefault: () => (prevented = true) });
out.cancel = [prevented, dialog.open];

// The dialog body could not be fetched: go to the no-JS page instead.
fire("htmx:responseError", { detail: { elt: link, target, xhr: { status: 500 } } });
out.fallback_nav = window.location.href;

// Row menu: Esc closes it and focuses its button; a click outside closes it.
menu.open = true;
const inside = { closest: (s) => (s === "details[data-menu][open]" ? menu : null) };
fire("keydown", { key: "Escape", target: inside });
out.esc = [menu.open, focused && focused.id];
menu.open = true;
fire("click", { target: { closest: () => null } });
out.outside = menu.open;

// Focus leaving the menu closes it, except mid-click on macOS Safari/Firefox (relatedTarget
// null while the pointer is over the menu).
let hovered = false;
const item = {};
const fm = { open: true, matches: (s) => s === ":hover" && hovered, contains: (n) => n === item };
const inMenu = { closest: (s) => (s === "details[data-menu][open]" ? fm : null) };
const blur = (relatedTarget, hover) => {
  fm.open = true; hovered = hover;
  fire("focusout", { target: inMenu, relatedTarget });
  return fm.open;
};
out.focusout = {
  null_hovered: blur(null, true), null_not_hovered: blur(null, false),
  inside: blur(item, false), outside: blur({}, false),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def dialog():
    if NODE is None:
        pytest.skip("node is not installed")
    run = subprocess.run(
        [NODE, "-e", DIALOG_HARNESS, str(APP_JS)], capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_delete_button_is_locked_until_the_box_is_ticked(dialog):
    assert dialog["locked_start"] is True
    assert dialog["ticked"] == [False, "Delete button is now available"]
    assert dialog["unticked"] == [True, "Delete button is not available"]
    assert dialog["no_box"] is False


def test_dialog_opens_after_the_body_arrives_and_returns_focus_to_the_menu_button(dialog):
    assert dialog["opened"] == [True, False]
    assert dialog["closed"] == ["summary", 0]
    assert dialog["fallback"] == "h1"


def test_cancel_closes_the_dialog_and_a_failed_fetch_opens_the_full_page(dialog):
    assert dialog["cancel"] == [True, False]
    assert dialog["fallback_nav"] == "/imports/1/delete/"


def test_row_menu_closes_on_escape_and_outside_click(dialog):
    assert dialog["esc"] == [False, "summary"]
    assert dialog["outside"] is False


@pytest.mark.parametrize(
    "case, still_open",
    [("null_hovered", True), ("null_not_hovered", False), ("inside", True), ("outside", False)],
)
def test_row_menu_focusout_closes_unless_focus_or_pointer_stays_in_it(dialog, case, still_open):
    assert dialog["focusout"][case] is still_open
