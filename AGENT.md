# AGENT.md

Running log of work done on Magellan. Newest entries at the top. Standards
and conventions live in `CLAUDE.md`, not here — this file is history, not
rules.

## 2026-08-22 — RSVP feature: plans + DM Yes/No buttons

Built the first real feature: `/event create|list|status|remind`, for the
Europe 2026 trip (per `~/europe2026/data.json`, 9 confirmed travelers as of
this writing — that file has names only, no Discord identities).

- **Roster decision**: asked the user how to map "everyone on the trip" to
  Discord accounts (role vs. self-registration vs. manual admin mapping) —
  went with **a Discord role** (`TRAVELER_ROLE_ID` in `.env`). No manual ID
  entry, stays correct as the role's membership changes. See CLAUDE.md's
  "RSVP feature" section for the full rationale — don't relitigate this
  without a real reason (e.g. a feature that needs a different audience than
  the whole trip).
- **Storage**: added `magellan/store.py`, a thin `aiosqlite` wrapper (no
  ORM) with `events` and `rsvps` tables. `MagellanBot` owns one `Store`
  instance (`bot.store`), connected in `setup_hook`, closed in `close()`.
  Path is `DB_PATH` env var, default `data/magellan.db` (gitignored).
- **Persistent per-event buttons**: `RSVPButton` is a `discord.ui.DynamicItem`
  with the event ID encoded in its `custom_id` and parsed back out via regex
  — this is what lets buttons on events created *after* a bot restart still
  work, which a plain `bot.add_view()` at startup can't do. Registered once
  via `bot.add_dynamic_items(RSVPButton)` in `RSVP.cog_load()`.
- **Flow**: `/event create` (guild-only) posts a tally embed in the invoking
  channel and DMs every non-bot member of the traveler role the same embed
  + Going/Not-going buttons. Any tap (DM or channel) upserts the RSVP in
  sqlite and live-edits the channel embed. `/event status`/`/event remind`
  use autocomplete over open events in the guild.
- Verified: `ruff check .` clean, all modules import, and `store.py`'s
  create/upsert/list logic smoke-tested directly against a temp sqlite file
  (including the "change your mind" upsert path) — no real bot token
  available in this session, so the Discord-facing half (actual DMs,
  button interactions, embed edits) is untested against the live API.
- Not done yet: closing/archiving a plan, editing plan details after
  creation, targeted reminders to specific people. `data/` dir and role
  setup instructions are in README.md.

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
