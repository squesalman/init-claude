// Read by the Tailwind standalone CLI (scripts/tailwind.sh). No node_modules (ADR-0002).
// Colors are semantic tokens (docs/design/auth-and-trades-list.md section 7); values live
// in static/src/input.css as CSS variables with light and dark sets. No raw colors in templates.
module.exports = {
  content: ["./templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        page: "var(--page)",
        surface: "var(--surface)",
        "surface-raised": "var(--surface-raised)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        text: "var(--text)",
        "text-muted": "var(--text-muted)",
        link: "var(--link)",
        primary: "var(--primary)",
        "on-primary": "var(--on-primary)",
        focus: "var(--focus)",
        info: "var(--info)",
        "info-bg": "var(--info-bg)",
        success: "var(--success)",
        "success-bg": "var(--success-bg)",
        attention: "var(--attention)",
        "attention-bg": "var(--attention-bg)",
        danger: "var(--danger)",
        gain: "var(--gain)",
        loss: "var(--loss)",
      },
      fontSize: { base: ["14px", "20px"], title: ["20px", "28px"] },
      maxWidth: { auth: "400px", form: "640px", shell: "1200px" },
    },
  },
  plugins: [],
};
