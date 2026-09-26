# init-claude
Trading Journal + behaviour analysis. Aim to use proper SDLC + Agile Methodology. Started the projects with establishing team of agents. 

Dev: run tests and `manage.py` via `uv run --env-file .env ...` (settings fail closed without `DJANGO_SECRET_KEY`).

Lint: `uv run ruff check .` (ruff is in the `dev` group; lint only, no formatter).

CSS: Tailwind standalone CLI, no Node. `static/css/app.css` is committed, so the app runs without it; rebuild after changing templates or `static/src/input.css`:
- `scripts/tailwind.sh build` (one-off, minified; downloads the pinned binary into `bin/` on first run)
- `scripts/tailwind.sh watch` (rebuild on save while developing)

The script is Linux-only (it fetches the Linux binary and checks it with `sha256sum`); on macOS it exits with an error.

## Starting on a new machine
1. Install Docker and [uv](https://docs.astral.sh/uv/) (uv fetches Python 3.12 itself).
2. `git clone` the repo, then `cp .env.example .env` and set `DJANGO_SECRET_KEY` (any value works in dev while `DJANGO_DEBUG=true`).
3. `docker compose up -d` (Postgres only), then `uv sync`.
4. `uv run --env-file .env manage.py migrate`, then `uv run --env-file .env manage.py runserver`.
5. Check it works: `uv run --env-file .env pytest -q` (about 5 min: real password hashing) and `uv run ruff check .`.

Not in the repo, on purpose:
- `.env` (secrets) and the dev database: create them fresh; sign up again for a dev user.
- `all_trades_export.csv` and any real TopstepX export: real trade data, gitignored. Copy it by hand; never commit it. Test fixtures go in `tests/fixtures/` and must be synthetic.
- Claude Code plugins and session memory (`~/.claude`): install the plugins on the new machine (CLAUDE.md expects `i-have-adhd`, superpowers, ponytail, code-review). Project state lives in `CLAUDE.md`, `docs/README.md` (index), `docs/plan.md` (what is next) and `docs/data/follow-ups.md` (deferred work), so a fresh session catches up from those.
- `bin/tailwindcss`: `scripts/tailwind.sh` downloads it, on Linux only (use WSL on Windows). The compiled CSS is committed, so the app runs without it.

