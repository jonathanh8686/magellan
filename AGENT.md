# AGENT.md

Running log of work done on Magellan. Newest entries at the top. Standards
and conventions live in `CLAUDE.md`, not here — this file is history, not
rules.

## 2026-08-22 — Project bootstrap

Initialized the empty repo with a working discord.py boilerplate.

- Set up with `uv` (`uv init`, `uv add`) rather than pip/poetry — chosen
  because `uv` was already available in this environment and is the faster
  default going forward.
- Dependencies: `discord.py>=2.4`, `python-dotenv`. Dev: `ruff`.
- Structure:
  - `magellan/config.py` — `Config` dataclass loaded from env
    (`DISCORD_TOKEN`, `COMMAND_PREFIX`, `DEV_GUILD_ID`), validated once at
    startup via `Config.from_env()`.
  - `magellan/bot.py` — `MagellanBot(commands.Bot)`. Intents: `default()` +
    `message_content` (needed since the bot's core purpose is reading
    messages) + `members`. Loads cogs listed in `INITIAL_COGS` in
    `setup_hook`, then syncs slash commands — to `DEV_GUILD_ID` if set
    (instant, for dev), globally otherwise.
  - `magellan/cogs/general.py` — first cog, kept intentionally minimal:
    `/ping` slash command as a health check, and an `on_message` listener
    stub (currently just filters out bot messages) as the anchor point for
    future message-listening logic.
  - `magellan/__main__.py` — entrypoint, wired to `uv run magellan` via
    `project.scripts` in `pyproject.toml`.
- `.env.example` documents all three env vars; `.gitignore` excludes `.env`,
  `.venv/`, caches, etc.
- Verified: all modules import cleanly under `uv run python -c ...`, and
  `uv run ruff check .` is clean (fixed one auto-fixable finding —
  unnecessary quoted forward-reference in `config.py`).
- Not done yet: no `/ping`-adjacent test run against a real bot token (none
  provided at setup time), no actual planning/listening features, no
  tests. Next steps depend on what the user wants prioritized first —
  likely the planning feature (events/RSVPs) since that's the bot's
  differentiator vs. a generic message logger.
