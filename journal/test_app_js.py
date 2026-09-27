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
