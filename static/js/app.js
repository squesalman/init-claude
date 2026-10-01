// App behaviour. External file (no inline script) so a strict CSP stays possible
// (follow-ups row 20). Alpine is the CSP build: templates name these properties and
// methods, never write expressions. Everything here is an enhancement; the pages work
// without JS.

// Flash close button (no auto-dismiss).
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-dismiss]");
  if (btn) btn.closest('[role="status"]').remove();
});
// Busy label + disable after first submit; without JS the label just stays.
document.addEventListener("submit", (e) => {
  const btn = e.target.querySelector("button[data-busy]");
  if (btn) { btn.dataset.label = btn.textContent; btn.textContent = btn.dataset.busy; btn.disabled = true; }
});
// Back button (bfcache) must not bring back a stuck, disabled button.
window.addEventListener("pageshow", () => {
  document.querySelectorAll("button[data-label]").forEach((btn) => { btn.textContent = btn.dataset.label; btn.disabled = false; });
});

document.addEventListener("alpine:init", () => {
  // AccountField (design import-account-label 3.1, 4, 7): counter from 50 characters
  // ("52 / 64"); over the limit, one polite message when crossing it and on blur, not
  // on every keystroke. The input is never truncated.
  window.Alpine.data("accountField", () => ({
    count: 0,
    message: "",
    init() {
      const data = this.$root.dataset;
      this.limit = Number(data.limit);
      this.from = Number(data.from);
      this.tooLong = data.tooLong;
      this.count = this.$refs.input.value.length;
    },
    get counter() {
      return this.count >= this.from ? `${this.count} / ${this.limit}` : "";
    },
    // No aria-invalid binding: the server renders it (too long, control characters) and it
    // stays until the next page load or swap; the role=status message carries the live state.
    update() {
      const wasOver = this.count > this.limit;
      this.count = this.$refs.input.value.length;
      if (this.count <= this.limit) this.message = "";
      else if (!wasOver) this.announce();
    },
    announce() {
      this.message = this.count > this.limit
        ? this.tooLong.replace("%(show_value)d", this.count).replace("%(limit_value)d", this.limit)
        : "";
    },
  }));

  // CharCounter (docs/design/journaling.md 3.4): note and rules textareas. The x-data sits on
  // a wrapper (Django renders the textarea), so input/focusout bubble up to it. Hidden until
  // data-from characters ("9,412 / 10,000"); over the limit it shows the server's too-long
  // message. The live region speaks only on reaching data-from, on going over, and on blur
  // while over. Code points, like the server; a server-set aria-invalid is kept.
  const fmt = (n) => n.toLocaleString("en-US");
  window.Alpine.data("charCounter", () => ({
    count: 0,
    live: "",
    init() {
      const data = this.$root.dataset;
      this.limit = Number(data.limit);
      this.from = Number(data.from);
      this.field = this.$root.querySelector("textarea");
      this.serverInvalid = this.field.getAttribute("aria-invalid") === "true";
      this.count = [...this.field.value.trim()].length;
    },
    get counter() {
      const data = this.$root.dataset;
      if (this.count < this.from) return "";
      if (this.count > this.limit) {
        return data.tooLong.replace("%(show_value)d", this.count).replace("%(limit_value)d", this.limit);
      }
      return data.counter.replace("{n}", fmt(this.count)).replace("{limit}", fmt(this.limit));
    },
    get overText() {
      return this.$root.dataset.over
        .replace("{limit}", fmt(this.limit)).replace("{n}", fmt(this.count - this.limit));
    },
    update() {
      const before = this.count;
      this.count = [...this.field.value.trim()].length;
      if (this.count < this.from) this.live = "";
      else if (this.count > this.limit && before <= this.limit) this.live = this.overText;
      else if (this.count <= this.limit && before > this.limit) this.live = "";
      else if (before < this.from) this.live = this.$root.dataset.near.replace("{limit}", fmt(this.limit));
      if (this.count > this.limit || this.serverInvalid) this.field.setAttribute("aria-invalid", "true");
      else this.field.removeAttribute("aria-invalid");
    },
    leave(e) {
      // focusout bubbles from anything in the wrapper (the focused error message too).
      if ((e && e.target !== this.field) || this.count <= this.limit) return;
      this.live = ""; // the same text again would not be re-announced
      this.$nextTick(() => { this.live = this.overText; });
    },
  }));

  // ConfirmDialog tick gate (design 3.3, 4, 7): with journal entries the Delete button stays
  // disabled until the box is ticked, and a polite region says when that changes. Without
  // JS the button is enabled and the server refuses an unticked submit.
  window.Alpine.data("deleteConfirm", () => ({
    locked: false,
    live: "",
    init() {
      const tick = this.$refs.tick;
      this.locked = Boolean(tick) && !tick.checked;
    },
    toggle() {
      this.locked = !this.$refs.tick.checked;
      this.live = this.locked ? this.$root.dataset.liveOff : this.$root.dataset.liveOn;
    },
    hold() {}, // Enter on the box must not submit (x-on:keydown.enter.prevent)
  }));
});

// ConfirmDialog open/close (design 3.3, 7). "Delete this import" links hx-get the body into
// the page's <dialog>; it opens once the body is in (showModal focuses its [autofocus]).
// Esc, Cancel and the close button close it; focus goes back to the control that opened it
// (a row menu's button), or the page h1 if that control is gone.
let dialogOpener = null;
document.addEventListener("htmx:beforeRequest", (e) => {
  const elt = e.detail.elt;
  if (!elt.hasAttribute || !elt.hasAttribute("data-opens-dialog")) return;
  const menu = elt.closest("details");
  if (menu) menu.open = false;
  dialogOpener = menu ? menu.querySelector("summary") : elt;
});
document.addEventListener("htmx:afterSwap", (e) => {
  const dialog = e.detail.target.closest && e.detail.target.closest("dialog");
  if (dialog && !dialog.open) dialog.showModal();
});
document.addEventListener("close", (e) => {
  if (e.target.id !== "delete-dialog") return;
  e.target.querySelector("#delete-dialog-body").replaceChildren();
  (dialogOpener && dialogOpener.isConnected ? dialogOpener : document.querySelector("h1")).focus();
  dialogOpener = null;
}, true); // close does not bubble
// The body could not be fetched: fall back to the no-JS confirm page.
document.addEventListener("htmx:responseError", (e) => {
  const elt = e.detail.elt;
  if (elt.hasAttribute && elt.hasAttribute("data-opens-dialog")) window.location.href = elt.href;
});

document.addEventListener("click", (e) => {
  // Cancel is a link back to the import (no-JS page); inside the dialog it just closes it.
  const cancel = e.target.closest("[data-cancel]");
  const dialog = cancel && cancel.closest("dialog");
  if (dialog) { e.preventDefault(); dialog.close(); }
  // RowActionsMenu (design 3.5, 7): a native <details> disclosure; a click outside closes it.
  document.querySelectorAll("details[data-menu][open]").forEach((m) => { if (!m.contains(e.target)) m.open = false; });
});
document.addEventListener("keydown", (e) => {
  const menu = e.key === "Escape" && e.target.closest && e.target.closest("details[data-menu][open]");
  if (menu) { menu.open = false; menu.querySelector("summary").focus(); }
});
// Tab past the last item closes the menu.
document.addEventListener("focusout", (e) => {
  const menu = e.target.closest && e.target.closest("details[data-menu][open]");
  // Safari/Firefox on macOS don't focus a clicked link, so relatedTarget is null mid-click:
  // while the pointer is over the menu, leave it open so the click lands.
  if (menu && !menu.contains(e.relatedTarget) && !menu.matches(":hover")) menu.open = false;
});

// htmx request lifecycle: aria-busy on the region being replaced, the polite
// "Importing your file" live region, and the busy button restored afterwards
// (base.html sets the busy label on submit).
document.addEventListener("htmx:beforeRequest", (e) => {
  e.detail.target.setAttribute("aria-busy", "true");
  const live = e.detail.elt.querySelector("[data-busy-live]");
  if (live) live.textContent = live.dataset.busyLive;
  showRequestError(e.detail.elt, false);
});

// htmx does not swap a non-2xx response (e.g. the 413 from RequestBodyLimitMiddleware) and
// a network failure has no response at all, so the form would look like nothing happened.
// An element that holds a [data-request-error] region shows its message instead. The busy
// state is reset by htmx:afterRequest, which htmx fires on these paths too.
function showRequestError(elt, show) {
  const region = elt.querySelector("[data-request-error]");
  if (!region) return;
  region.querySelector("p").hidden = !show;
  region.querySelector("[data-request-error-text]").textContent = show ? region.dataset.requestError : "";
}
document.addEventListener("htmx:responseError", (e) => showRequestError(e.detail.elt, true));
document.addEventListener("htmx:sendError", (e) => showRequestError(e.detail.elt, true));

document.addEventListener("htmx:afterRequest", (e) => {
  e.detail.target.removeAttribute("aria-busy");
  if (e.detail.xhr && e.detail.xhr.getResponseHeader("HX-Redirect")) return; // leaving the page
  const live = e.detail.elt.querySelector("[data-busy-live]");
  if (live) live.textContent = "";
  e.detail.elt.querySelectorAll("button[data-label]").forEach((btn) => {
    btn.textContent = btn.dataset.label;
    btn.disabled = false;
    delete btn.dataset.label;
  });
});

// htmx refocuses an element with the same id after a swap. When that is not possible
// (the focused control is gone), focus the element the new content marks.
document.addEventListener("htmx:afterSettle", () => {
  if (document.activeElement && document.activeElement !== document.body) return;
  const next = document.querySelector("[data-focus-after-swap]");
  if (next) next.focus();
});
