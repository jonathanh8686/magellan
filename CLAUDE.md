# CLAUDE.md

Standards for working on Magellan. Read this before making changes. For a log
of what's already been built and why, see `AGENT.md`.

## What this is

A Discord bot (discord.py) that listens to messages in servers it's in and
helps members plan things — events, trips, hangouts. First deployed for the
Europe 2026 trip. Two feature areas will grow over time:

- **Listening**: passive message observation/parsing (mentions, keywords,
  reactions) that feeds into planning features or logging.
- **Planning**: slash commands and flows for creating/joining/tracking plans
  (RSVPs, polls, scheduling). RSVP (`cogs/rsvp.py`) is the first of these —
  see the dedicated section below.

Both should end up as separate cogs (or families of cogs) rather than mixed
into one file — see Architecture below.

## Stack

- Python 3.10, managed with `uv` (not pip/poetry/conda). `uv add <pkg>` to add
  a dependency, `uv add --dev <pkg>` for dev-only tools, `uv run <cmd>` to run
  anything in the project's venv.
- `discord.py` 2.x — prefer slash commands (`app_commands`) over prefix
  commands for new user-facing features. The legacy prefix (`COMMAND_PREFIX`
  in `.env`) exists for future utility/owner commands, not the main surface.
- `python-dotenv` for local config; real secrets never get committed.
- `aiosqlite` for persistence (`magellan/store.py`) — one connection, shared
  across the bot, wrapped in a small `Store` class with explicit methods
  (no ORM). This is the pattern for any future feature that needs to persist
  state: add tables/methods to `store.py`, don't spin up a second storage
  mechanism.
- `ruff` for lint + format. No mypy/pytest set up yet — add them when the
  project actually needs type-checking or a test suite, not preemptively.

## Architecture conventions

- **One cog per feature area** in `magellan/cogs/`, registered in
  `magellan/bot.py:INITIAL_COGS`. Each cog file has an async `setup(bot)`
  function per discord.py convention (see `cogs/general.py`).
- **Config lives in `magellan/config.py`** as the single `Config` dataclass,
  built once from env vars via `Config.from_env()` in `__main__.py` and
  threaded through explicitly (currently via `bot.config`). Don't call
  `os.getenv` scattered around the codebase — add a field to `Config`
  instead, so every required var is validated in one place at startup.
- **`MagellanBot` (`magellan/bot.py`)** owns intents, extension loading, and
  slash command sync. If a feature needs a new intent (e.g. `presences`,
  `voice_states`), add it here with a one-line comment on *why* it's needed —
  Discord requires privileged intents to be enabled in the Developer Portal
  too, so flag that in the PR/commit description.
- **Entrypoint is `magellan/__main__.py`**, invoked via `uv run magellan`
  (the `project.scripts` entry in `pyproject.toml`) — don't add a second
  entrypoint script at the repo root.

## RSVP feature (`cogs/rsvp.py`)

- **The roster is a Discord role, not a hardcoded list.** "Everyone on the
  trip" = everyone with the `TRAVELER_ROLE_ID` role in the guild. This was a
  deliberate choice over a manual name↔Discord-ID mapping or a self-register
  command, because it needs zero maintenance as people join/leave the role.
  If a future feature needs a *different* audience than "the whole trip"
  (e.g. a sub-group going on one excursion), don't repurpose this role —
  that's a real second use case for a proper attendee-list concept.
- **Plans ("events") and RSVPs live in sqlite** (`events` / `rsvps` tables),
  not in memory — the bot restarts (deploys, crashes) shouldn't lose RSVP
  state or force everyone to re-respond.
- **Buttons are `discord.ui.DynamicItem`**, not a plain `View` with a fixed
  `custom_id`. The event ID is encoded into the custom_id
  (`magellan:rsvp:<event_id>:<yes|no>`) and parsed back out via the regex
  `template=` on `RSVPButton`. This is what makes per-event buttons survive
  a bot restart — a plain view registered at startup can't know about events
  created after that point. `RSVP.cog_load()` registers the class once via
  `bot.add_dynamic_items(RSVPButton)`; don't switch this back to a
  `@bot.event` on_interaction hack or a per-event `bot.add_view()` call.
- **A button click can happen in a DM**, where `interaction.guild` is
  `None`. `refresh_announcement()` therefore resolves the guild via
  `bot.get_guild(event.guild_id)` (stored on the event row at creation time),
  not via the interaction — don't assume `interaction.guild` is set inside
  `RSVPButton.callback`.
- **RSVPs are upserts** (`ON CONFLICT ... DO UPDATE`) — someone can change
  their mind and tap the other button; the last tap wins. There's no "lock
  in your answer" step.
- Not implemented yet: closing/archiving a plan, editing a plan's details
  after creation, and reminding *specific* people rather than everyone
  pending. Add these when actually needed.

## Style

- Type hints everywhere; `from __future__ import annotations` at the top of
  new modules (already in every existing file) so forward references and
  `X | None` work on 3.10 without quoting.
- Keep `ruff check .` clean — run `uv run ruff check --fix .` before
  considering a change done. No linter config changes without a reason.
- No comments that restate what the code does. Comments are for *why*
  something non-obvious is there (a Discord API quirk, a rate-limit
  workaround, an intent requirement).
- Don't add abstractions (base classes, plugin registries, generic "manager"
  layers) ahead of an actual second use case. Two similar cogs is fine; a
  shared base class is worth it on the third.

## Secrets & environment

- `.env` is gitignored and must never be committed. `.env.example` documents
  every variable `Config` reads — keep them in sync when adding a new one.
- Never print or log the bot token. `bot.run(..., log_handler=None)` in
  `__main__.py` is deliberate — discord.py's default log handler is fine, but
  if that ever changes, make sure token values can't end up in logs.

## Workflow expectations

- This project is developed almost entirely by Claude. After any nontrivial
  change (new cog, new command, config/schema change, dependency add),
  append an entry to `AGENT.md` — don't let it drift out of date.
- Verify changes actually work before calling them done: `uv run ruff check .`
  at minimum, and where practical, run the bot against a real test server/
  token rather than just checking that it imports.
- Commit messages and `AGENT.md` entries should explain *why*, not restate
  the diff.
