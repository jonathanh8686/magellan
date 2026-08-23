# CLAUDE.md

Standards for working on Magellan. Read this before making changes. For a log
of what's already been built and why, see `AGENT.md`.

## What this is

A Discord bot (discord.py) that listens to messages in servers it's in and
helps members plan things — events, trips, hangouts. First deployed for the
Europe 2026 trip. Two feature areas will grow over time:

- **Listening**: reaction-triggered message parsing that feeds into
  planning features. `cogs/planner.py` is the first of these — react 📅 on
  a message and Claude extracts it into a suggested `/event`. See the
  dedicated section below.
- **Planning**: slash commands and flows for creating/joining/tracking plans
  (RSVPs, polls, scheduling). RSVP (`cogs/rsvp.py`) is the first of these —
  see the dedicated section below.

Both should end up as separate cogs (or families of cogs) rather than mixed
into one file — see Architecture below.

## Stack

- Python 3.12, managed with `uv` (not pip/poetry/conda). `uv add <pkg>` to add
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
- `anthropic` (official SDK) + `pydantic` for the one Claude-backed feature
  (`cogs/planner.py`). Use the SDK's structured-output helper
  (`client.messages.parse(..., output_format=SomeBaseModel)`) for anything
  that extracts structured data from text — don't hand-roll JSON parsing of
  a free-text response. Model is hardcoded to `claude-sonnet-5` in
  `planner.py` — the user explicitly chose Sonnet over the default Opus
  here for cost (a passive per-message classifier is high-volume). If a
  second Claude-backed feature shows up, don't assume Sonnet for it too —
  that choice was specific to this feature's call volume, not a blanket
  project preference; ask, or default back to Opus per Anthropic's own
  guidance, unless the same cost tradeoff clearly applies.

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
- **Hot reload** (`uv run magellan --reload`, `bot.py:_watch_cogs`) watches
  `magellan/cogs/*.py` with `watchfiles` and calls `bot.reload_extension()`
  on save — dev convenience, not a deployment mode. It only reloads
  extensions already in `INITIAL_COGS`; it won't pick up a brand-new cog
  file (still needs adding to `INITIAL_COGS` + a restart) or command
  signature changes (still need a `tree.sync()`, i.e. a restart). Any cog
  that registers global state on load (dynamic items, listeners added
  outside `__init__`) must undo it in `cog_unload()` — see `RSVP.cog_unload`
  — or reloading it will error/duplicate instead of cleanly swapping in the
  new code. `watchfiles` is a dev dependency; it's imported lazily inside
  `_watch_cogs` so a production install that skips the dev group doesn't
  need it.
- **Cross-cog calls must go through `bot.get_cog(...)`, never a direct
  module import of another cog's function.** `planner.py`'s "Create plan"
  button looks up `bot.get_cog("RSVP")` at click time and calls
  `.create_and_announce(...)` on it, rather than importing that function
  from `rsvp.py` at module load time. A direct import would keep a
  reference to the *old* function object after `--reload` reloads
  `rsvp.py`, silently running stale code. `get_cog()` always returns
  whatever's currently registered.

## Permissions (`magellan/permissions.py`)

- **Every operation is traveler-only, with no exceptions carved out.** The
  user's instruction was literal: "all operations should only be permitted
  by people with the travel role." That includes `/ping` — not just the
  RSVP/planning commands — and every button click (RSVP Going/Not-going,
  plan-suggestion Create/Ignore), not just slash commands. If you add a new
  slash command or interactive component, gate it too; don't assume
  something is low-stakes enough to skip.
- **One shared gate, `is_traveler(bot, guild, user)` + `traveler_only()`**,
  used everywhere instead of each cog re-deriving "does this user have the
  role" — this was worth extracting up front (not waiting for a third use)
  because getting the DM-guild-resolution edge case right in one place
  matters more than avoiding a small abstraction.
- **`is_traveler` takes `guild` as an explicit argument — never reads
  `interaction.guild` itself.** `interaction.guild` is `None` for a
  component interaction that originated in a DM, which is the *normal* case
  for `RSVPButton` (most people RSVP from their DMs). Passing `guild=None`
  there would silently lock every DM-based RSVP out. Callers resolve the
  right guild explicitly: `interaction.guild` when the interaction is
  known to be in-guild (slash commands, the plan-suggestion buttons, which
  are only ever posted in-channel), or `bot.get_guild(event.guild_id)` /
  `bot.get_guild(payload.guild_id)` when it might not be (RSVPButton, the
  📅 reaction listener).
- **`traveler_only()` is an `app_commands.check`**, applied as a decorator
  to every slash command (`@traveler_only()`, alongside `@event_group.
  command(...)` / `@app_commands.command(...)`). It raises `NotATraveler`
  (a `CheckFailure` subclass) rather than sending a response itself — the
  actual ephemeral reply comes from `MagellanBot._on_app_command_error`, a
  single global handler registered via `self.tree.error(...)` in
  `setup_hook`. Don't add per-cog `cog_app_command_error` handlers for this
  — the global one already covers every cog.
- **Component callbacks (buttons) check `is_traveler(...)` inline** at the
  top of the callback and send their own ephemeral denial — there's no
  equivalent global hook for component-interaction errors, so this can't
  be centralized the same way the slash-command check is.

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
  `None`. `refresh_all_messages()` therefore resolves the guild via
  `bot.get_guild(event.guild_id)` (stored on the event row at creation time),
  not via the interaction — don't assume `interaction.guild` is set inside
  `RSVPButton.callback`.
- **Every RSVP-embed DM is tracked (`dm_messages` table:
  event_id/user_id → channel_id/message_id) and gets edited on every RSVP
  change, not just the channel announcement.** `refresh_all_messages()`
  (called from `RSVPButton.callback` regardless of whether the click came
  from a DM or the channel) re-fetches and edits the channel post *and*
  every tracked DM. `record_dm_message()` is an upsert keyed on
  `(event_id, user_id)` — if a traveler gets DMed twice for the same event
  (`/event create` then later `/event remind`), the newer DM replaces the
  older one as the copy we keep live; the earlier DM is left as-is (not
  worth chasing every historical copy). Any new place that DMs a
  traveler an RSVP embed must call `record_dm_message()` too, or that copy
  silently goes stale forever.
- **RSVPs are upserts** (`ON CONFLICT ... DO UPDATE`) — someone can change
  their mind and tap the other button; the last tap wins. There's no "lock
  in your answer" step.
- Not implemented yet: closing/archiving a plan, editing a plan's details
  after creation, and reminding *specific* people rather than everyone
  pending. Add these when actually needed.

## Planner feature (`cogs/planner.py`)

- **Reaction-triggered, not passive listening.** Extraction only ever runs
  when a human reacts 📅 (`TRIGGER_EMOJI`) to a message — there is
  deliberately no `on_message` heuristic/keyword-filter path anymore (an
  earlier version had one; it was replaced because an explicit reaction is
  a better signal than a regex guess, and removes the need for cost-control
  pre-filtering — every trigger is already a human decision). Listens via
  `on_raw_reaction_add` (not `on_reaction_add`) so it fires even for
  messages not in the gateway cache.
- **Only reactions from members with the traveler role trigger it** — same
  role as RSVP's roster (`TRAVELER_ROLE_ID`), so this only fires in
  trip-planning contexts, not general server chatter. Checked against
  `payload.member`, not the reacted-to message's author — it's the
  *reactor's* intent that matters.
- **`_handled_message_ids` is a session-only dedup set** — a second 📅 on
  the same message (from the same or a different person) is a no-op. It's
  not persisted, so it resets on restart; that's fine, the cost of a rare
  duplicate suggestion after a restart is low.
- **`is_plan` requires BOTH a date and a time, never just one.** A bare
  time ("at 10am") or a bare date ("Saturday") alone is not enough —
  `SYSTEM_PROMPT` and every `PlanDraft` field's `Field(description=...)`
  both say so explicitly (the field descriptions flow into the JSON
  schema `messages.parse()` sends, reinforcing the prompt at the schema
  level too — confirmed via `PlanDraft.model_json_schema()`). This was a
  direct fix for a real false-negative-in-reverse: an earlier prompt let
  "cathedral at 10am" (no date at all) through as a valid plan. Don't
  loosen this back to "a time or a day" without the user asking.
- **Vaguely-described places should be named, not paraphrased.** The
  prompt tells Claude to use its own knowledge to identify what a
  descriptive reference ("the big cathedral in Milan") most likely names
  (e.g. "Duomo di Milano") for the `title`, rather than repeating the
  vague phrase verbatim.
- **Processing feedback is reactions, not text**: ⏳ while the Claude call
  is in flight (removed after), then either the Create/Ignore suggestion
  reply (plan found), ❌ (message didn't have enough to act on), or ⚠️ (the
  API call itself failed). Keep it to reactions for the non-suggestion
  cases — a text reply for every "couldn't find a plan" would be noisy in
  an active channel.
- **Claude only classifies + extracts; it never creates anything.** The
  `PlanDraft` structured output (`is_plan`, `title`, `when`, `location`) is
  shown to the user as a suggestion with **Create plan** / **Ignore**
  buttons. A human tap is what actually calls
  `RSVP.create_and_announce(...)` — don't change this to auto-create on a
  high-confidence extraction; false positives creating spam plans (and
  spam DMs to the whole trip) are worse than requiring a tap.
- **`PlanSuggestionView` is a plain `discord.ui.View`, not a
  `DynamicItem`** (contrast with `RSVPButton`). It's fine for a suggestion
  to go stale after a bot restart — it's tied to one specific message from
  a few minutes ago, not a standing artifact like an event announcement.
  `on_timeout` disables the buttons after 10 minutes either way.
- **Claude API failures are swallowed, not surfaced as an error** —
  `_extract()` catches `anthropic.APIError` broadly, returns `None`, and
  the caller reacts ⚠️. This is deliberate: a transient API hiccup should
  never look like a broken bot. If you add a feature that *requires* the
  Claude call to succeed (unlike this best-effort one), use a proper
  typed-exception chain instead — see the `shared` error-handling guidance
  in the Claude API docs.

## Deployment (`deploy/`)

- **Production runs on omashu as a systemd service** (`deploy/
  magellan-bot.service`, deployed to `/etc/systemd/system/` — the repo copy
  is the source of truth, keep them in sync if you edit one), not Docker.
  Deliberate choice over matching jonathanhsieh.dev's Docker setup on the
  same server: this bot has no ports to expose/proxy, just an outbound
  gateway connection, so a container adds build/rebuild overhead without
  buying anything. Don't containerize this later without a real reason
  (e.g. the bot gains a webhook/HTTP surface that needs Apache in front of
  it — that's the kind of thing that would justify it).
- **`ExecStart` points at the venv binary directly**
  (`.venv/bin/magellan`), not `uv run magellan`. `uv run` re-syncs the
  environment (including the dev dependency group — ruff, watchfiles) on
  every invocation, which is wasted work and installs dev-only tools in
  prod on every restart. `deploy/redeploy.sh` is where `uv sync --no-dev`
  actually runs — once per deploy, not once per process start.
- **`.env` lives only on the server**, copied there once via `scp`
  (`chmod 600`), never committed. `redeploy.sh` doesn't touch it — new env
  vars need a manual edit on omashu, not a code change.
- **Only one bot instance may hold the gateway connection at a time.**
  Running a local dev instance (`uv run magellan`) while omashu's is also
  live means both receive and independently handle every event — this bit
  us once already (had to remember to stop the local instance after first
  deploying to omashu). Check before starting a local instance for
  debugging.

## Style

- Type hints everywhere; `from __future__ import annotations` at the top of
  new modules (already in every existing file) so forward references and
  `X | None` work without quoting.
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
- Never print or log the bot token or `ANTHROPIC_API_KEY`. `bot.run(...,
  log_handler=None)` in `__main__.py` is deliberate — discord.py's default
  log handler is fine, but if that ever changes, make sure token values
  can't end up in logs.
- `ANTHROPIC_API_KEY` is optional at the `Config` level — unset just
  disables `planner.py`'s 📅 reaction trigger (logged once as a warning), it
  doesn't fail startup. Don't make it required; RSVP and the rest of the
  bot don't depend on it.

## Workflow expectations

- This project is developed almost entirely by Claude. After any nontrivial
  change (new cog, new command, config/schema change, dependency add),
  append an entry to `AGENT.md` — don't let it drift out of date.
- Verify changes actually work before calling them done: `uv run ruff check .`
  at minimum, and where practical, run the bot against a real test server/
  token rather than just checking that it imports.
- Commit messages and `AGENT.md` entries should explain *why*, not restate
  the diff.
