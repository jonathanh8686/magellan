# AGENT.md

Running log of work done on Magellan. Newest entries at the top. Standards
and conventions live in `CLAUDE.md`, not here — this file is history, not
rules.

## 2026-08-23 — Planner: reaction trigger instead of passive listening

User asked to replace the passive on_message + keyword-heuristic trigger
with an explicit one: react 📅 to a message and *that* sends it to Claude,
instead of the bot silently scanning every message from a traveler for
plan-shaped keywords.

- Removed `_TRIGGER_PATTERN` and the `on_message` listener entirely — no
  passive path left in `planner.py`. Added `on_raw_reaction_add` (raw, not
  `on_reaction_add`, so it works even on messages outside the gateway
  cache) gated on: emoji is `TRIGGER_EMOJI` (📅, a module constant), the
  reactor is a non-bot member with the traveler role, and the message
  hasn't already been handled this session (`_handled_message_ids`, an
  in-memory `set[int]` — intentionally not persisted).
- Added reaction-based processing feedback since silence would be
  confusing for an explicit action (unlike the old passive path, where
  silence on a non-plan message was the expected/correct behavior): ⏳
  while extracting (added then removed), ❌ if Claude decided there wasn't
  enough to act on, ⚠️ if the API call itself failed. A real plan still
  gets the same Create/Ignore button reply as before.
- Rewrote `SYSTEM_PROMPT` to reflect the new framing — the message was
  already flagged plan-shaped by a human, so the prompt now tells Claude
  that's a signal worth weighing, not something to re-verify from scratch,
  while still keeping the bar for `is_plan` at "enough to act on."
- Everything downstream of extraction is unchanged: `PlanDraft`, the
  Create/Ignore button flow, and `RSVP.create_and_announce()` reuse are
  all exactly as before this change.
- Verified: `ruff check .` clean, all modules import,
  `RawReactionActionEvent`'s attribute names (`guild_id`, `emoji`,
  `member`, `message_id`, `channel_id`) confirmed against the installed
  discord.py rather than assumed, and confirmed `PartialEmoji.name` holds
  the raw unicode character for a standard emoji (so the `==` comparison
  against `TRIGGER_EMOJI` is correct). Still not tested against a live
  bot/API — no credentials available in this session.

## 2026-08-23 — Switch planner's model to Sonnet

User hit usage limits (checked `/usage-credits`) and asked to switch the
planner's model. Changed `MODEL` in `cogs/planner.py` from `claude-opus-5`
to `claude-sonnet-5`. Deliberate cost tradeoff specific to this feature —
`planner.py` calls the API on every message from a traveler that passes the
keyword filter, so volume/cost matters more here than for a one-off call.
Doesn't imply a project-wide preference for Sonnet; if another Claude-backed
feature gets added later, don't assume it should also default to Sonnet —
ask, or default to Opus per Anthropic's own guidance unless the same
high-volume tradeoff applies. Not yet re-tested against the live API
(same "no credentials in this session" gap as before) — the prompt was
written without a specific model in mind, so it's worth a spot-check once
a key is available to make sure Sonnet's extraction quality is good enough
for this use case, not just cheaper.

## 2026-08-22 — Claude-powered plan detection (`cogs/planner.py`)

User asked to "hook this up to Claude API" — clarified into three candidate
features (parse chat into plans / natural-language trip Q&A / general chat)
and the user picked the first: passively detect plan-shaped messages and
offer to turn them into an `/event`. This is the "Listening" half of the
bot's original stated purpose, previously just a no-op stub.

- **Flow**: a regex pre-filter (`_TRIGGER_PATTERN` — day names, "tonight"/
  "tomorrow", time-like tokens, "let's...", "who's down", meal words) gates
  every message from a traveler-role member before it's sent to Claude at
  all — cost/noise control, not a feature. Only pre-filtered messages go to
  `claude-opus-5` via `client.messages.parse(..., output_format=PlanDraft)`
  (Pydantic structured output: `is_plan`, `title`, `when`, `location`). If
  `is_plan` and both `title`/`when` are present, the bot replies with a
  **Create plan** / **Ignore** button pair. Tapping Create calls the exact
  same fan-out logic as `/event create` — extracted into a new shared
  `RSVP.create_and_announce()` method so the DM-fanout code isn't
  duplicated between the slash command and the button (see refactor
  below). Nothing is created without a human tap.
- **Refactor**: pulled the body of `RSVP.event_create` (create the DB row,
  post the channel embed, DM the traveler role, collect failures) into
  `RSVP.create_and_announce(...)`, callable from any cog. `planner.py`
  reaches it via `bot.get_cog("RSVP")` at click time, *not* a module-level
  `from magellan.cogs.rsvp import ...` — a direct import would hold a
  reference to the pre-reload function object once `--reload` swaps
  `rsvp.py`'s module out, silently running stale code. `get_cog()` doesn't
  have that problem.
- **Model/config choices**: `claude-opus-5` hardcoded (current Anthropic
  default per the `/claude-api` skill — "always use unless the user names a
  different model," which didn't happen here), `client.messages.parse()`
  with a Pydantic `PlanDraft` model rather than hand-parsing raw JSON
  (skill-recommended structured-output path). `ANTHROPIC_API_KEY` added to
  `Config` as optional — unset just disables the feature (logged once),
  doesn't fail startup, since nothing else in the bot depends on it.
- **Design choice — suggest, don't auto-create.** Considered
  auto-creating on high-confidence extraction; rejected because a false
  positive would spam-DM the entire trip roster with a bogus plan. A
  human tap is cheap and the failure mode of requiring it (someone ignores
  a real plan suggestion) is much less costly than the alternative.
- **Design choice — non-persistent suggestion buttons.** Unlike
  `RSVPButton` (a `DynamicItem`, survives restarts), `PlanSuggestionView`
  is a plain `View` with a 10-minute timeout. A suggestion is tied to one
  specific recent message; going stale across a rare bot restart is an
  acceptable tradeoff for not needing per-suggestion persistence/cleanup.
- Verified: `ruff check .` clean, all modules import, and the regex
  pre-filter + `PlanDraft` schema were smoke-tested directly (5 trigger/
  non-trigger cases, all correct; Pydantic round-trip and JSON schema
  both look right). **Not verified**: an actual live call to Claude — no
  `ANTHROPIC_API_KEY` or `ant` CLI credentials available in this session,
  so the extraction quality/prompt (`SYSTEM_PROMPT` in `planner.py`) is
  untested against the real model. Worth a deliberate test pass once a key
  is available — try borderline phrasings ("maybe cooking class Saturday?"
  vs. a firm plan) to see if `is_plan` is well-calibrated.
- Not implemented yet: rate limiting/dedup on the Claude calls (documented
  as an accepted gap at this trip-group's scale — see CLAUDE.md).

## 2026-08-22 — Hot reload for cogs (`--reload`)

Added `uv run magellan --reload`: `watchfiles.awatch()` watches
`magellan/cogs/`, and on a file change calls `bot.reload_extension()` for
that cog — swaps the code in-process, no gateway reconnect, no
re-`tree.sync()`, so command *bodies* update in about a second.

- Added `watchfiles` as a dev dependency (imported lazily inside
  `bot.py:_watch_cogs`, not at module top-level, so a prod install that
  skips the dev dependency group never needs it).
- `MagellanBot` takes a `reload: bool` kwarg (not part of `Config` —
  it's a CLI toggle, not a deployment setting); `setup_hook` spawns the
  watcher task when it's set.
- Had to add `RSVP.cog_unload()` calling `bot.remove_dynamic_items(RSVPButton)`
  — without it, reloading the RSVP cog would try to register a second
  `RSVPButton` class against the same custom_id template while the old one
  was still active. Any future cog that registers global state outside
  `__init__` (dynamic items, manually-added listeners) needs the same
  cleanup in `cog_unload` or hot reload will break for it.
- Known limits (documented in README + CLAUDE.md): doesn't pick up new
  commands/params (needs `tree.sync()` → restart), doesn't watch files
  outside `magellan/cogs/`, and only reloads extensions already in
  `INITIAL_COGS`.
- Verified: `ruff check .` clean, all modules import, `--help` shows the
  flag, and `MagellanBot(cfg, reload=True)` wires up correctly with
  `COGS_DIR` resolving to the real cogs folder. Didn't verify an actual
  live save-triggers-reload cycle against the gateway — no bot token in
  this session.

## 2026-08-22 — Bump to Python 3.12

Switched from 3.10 to 3.12 (`.python-version`, `requires-python`, ruff
`target-version`) at the user's request. `uv` already had 3.12.9 installed
locally, so `uv sync` after deleting `.venv/` just worked. Ruff's `UP017`
then flagged `datetime.now(timezone.utc)` → `datetime.now(UTC)` in
`store.py` (the `UTC` alias only exists from 3.11+) — auto-fixed. Re-ran the
import check and the `store.py` smoke test on 3.12, both clean.

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
