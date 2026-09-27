// App behaviour. External file (no inline script) so a strict CSP stays possible
// (follow-ups row 20). Alpine is the CSP build: templates name these properties and
// methods, never write expressions. Everything here is an enhancement; the pages work
// without JS.

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
    get invalid() {
      return this.count > this.limit ? "true" : "false";
    },
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
});

// htmx request lifecycle: aria-busy on the region being replaced, the polite
// "Importing your file" live region, and the busy button restored afterwards
// (base.html sets the busy label on submit).
document.addEventListener("htmx:beforeRequest", (e) => {
  e.detail.target.setAttribute("aria-busy", "true");
  const live = e.detail.elt.querySelector("[data-busy-live]");
  if (live) live.textContent = live.dataset.busyLive;
});

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
