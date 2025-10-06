# Repository Guidelines

## Project Structure & Module Organization
- `bot.py` — main Discord bot entrypoint.
- `digest/` — Hikari-based digest utilities (`config.py`, `fetch.py`, `summarize.py`, `publish.py`).
- `tui/` — Textual read-only validator (`app.py`, `__main__.py`).
- `data/`, `database/` — persisted assets and schemas.
- `requirements.txt`, `.env.example` — runtime deps and configuration template.

Keep modules cohesive and under 300 LOC. Prefer Hikari for Discord I/O and Textual for validation UIs. Reuse existing `digest/*` helpers rather than duplicating logic.

## Build, Test, and Development Commands
- Create venv: `python -m venv .venv && source .venv/bin/activate` (Windows: `.\.venv\Scripts\activate`)
- Install deps: `python -m pip install -U pip && python -m pip install -r requirements.txt`
- Optional (uv): `uv venv && source .venv/bin/activate && uv pip install -r requirements.txt`
- Run bot locally: `python bot.py`
- Read-only checks: `python -m digest --list-channels`, `python -m digest --dry-run --hours 24`, `python -m tui`
- Post preview (after validation): `python -m digest`

## Coding Style & Naming Conventions
- Python 3.11+; 4-space indentation; PEP 8.
- Use type hints and docstrings for public APIs.
- Names: `snake_case` for modules/functions, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- Format with Black and sort imports with isort (see CONTRIBUTING). Keep functions small and single-purpose.

## Testing Guidelines
- Use `pytest` with tests under `tests/` mirroring package paths (e.g., `tests/digest/test_fetch.py`).
- Target core digest logic with unit tests; mock network/Discord I/O.
- Run: `pytest -q` (add to dev deps if needed).

## Commit & Pull Request Guidelines
- Follow Conventional Commits (e.g., `feat: add TUI refresh key`).
- Branch from `main`, keep PRs focused and include:
  - What/why, linked issues, and screenshots/logs for TUI/digest output when helpful.
  - Checklist: formatted with Black, imports via isort, no secrets.

## Security & Configuration Tips
- Never commit secrets. Use `.env` (ignored by Git). Required: `TOKEN`/`DISCORD_TOKEN`, `DISCORD_TOKEN_TYPE=Bot`, `GUILD_ID`, `DIGEST_CHANNEL_ID`. Optional: `TIME_WINDOW_HOURS`, `TOP_N_CONVOS`, `GEMINI_API_KEY`.
- Validate access with the TUI or `--dry-run` before posting to Discord.
