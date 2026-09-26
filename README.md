# init-claude
Trading Journal + behaviour analysis. Aim to use proper SDLC + Agile Methodology. Started the projects with establishing team of agents. 

Dev: run tests and `manage.py` via `uv run --env-file .env ...` (settings fail closed without `DJANGO_SECRET_KEY`).

CSS: Tailwind standalone CLI, no Node. `static/css/app.css` is committed, so the app runs without it; rebuild after changing templates or `static/src/input.css`:
- `scripts/tailwind.sh build` (one-off, minified; downloads the pinned binary into `bin/` on first run)
- `scripts/tailwind.sh watch` (rebuild on save while developing)

The script is Linux-only (it fetches the Linux binary and checks it with `sha256sum`); on macOS it exits with an error.
