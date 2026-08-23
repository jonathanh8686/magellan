# AGENT.md

Running log of work done on Magellan. Newest entries at the top. Standards
and conventions live in `CLAUDE.md`, not here — this file is history, not
rules.

## 2026-08-23 — Fix redeploy.sh self-modifying-script bug; add reaction debug logging

User reported the 📅 reaction "isn't working." Two real bugs surfaced while
investigating (neither was the actual root cause, which is still open —
see below).

- **`redeploy.sh` ran `git pull` on itself, then `uv: command not found`
  at the line number the *old* (pre-fix) file had `uv sync` on** — classic
  self-modifying-script bug: a running bash script reading `git pull`
  rewriting its own file mid-execution gets stale in-memory content for
  the rest of the run, silently reading garbled/wrong line offsets. Hit
  this twice in a row (once for a real fix, once because the fix itself
  still had `git pull` inside the script). Fixed by removing `git pull`
  from `redeploy.sh` entirely — it now only does `uv sync --no-dev` +
  restart; the deploy flow is `git pull && ./deploy/redeploy.sh` as two
  separate commands, documented in the script's own comment and in
  README.md. Also fixed a separate, smaller bug in the same script: `uv`
  isn't on `PATH` for non-interactive `ssh host 'cmd'` invocations (only
  added via shell rc sourcing), so it now calls `$HOME/.local/bin/uv`
  explicitly.
- **Added diagnostic logging to `planner.py`'s `on_raw_reaction_add`** —
  every check in that handler silently returns on failure (wrong emoji, no
  role, already handled), which made it impossible to tell from logs *why*
  a reaction didn't trigger anything. Now logs once the emoji matches
  `TRIGGER_EMOJI` (so unrelated reactions elsewhere in the server don't
  spam the log), then logs which specific check bailed if any did. This is
  meant to stay in permanently, not just for this investigation — it's the
  only feature with a "did nothing, silently" failure mode, and low-volume
  enough (reactions are rare) not to be noisy.
**Resolution — two separate, compounding root causes, both config, no code
was actually wrong:**

1. `TRAVELER_ROLE_ID` in `.env` was `1540944965616668682`, a role from a
   *different* guild — a "magellan testing" server with generic channels
   the bot was invited to early on. The real trip server is "monkey mecca"
   (`254698074323157013`), which has the actual `#europe` channel and an
   `europeans` role at `1540959256445067264`. User supplied the correct ID
   directly; updated `.env` on both omashu and locally (not committed —
   real secret).
2. **The bot had no permission to view `#europe` at all** in "monkey
   mecca" — confirmed by querying `GET /channels/{id}` directly via the
   Discord API and getting back `{"message": "Missing Access", "code":
   50001}`. This is why even the broadest possible debug log (logging
   *every* raw reaction, any emoji, before any filtering) never fired —
   Discord's gateway doesn't dispatch events for a channel the bot can't
   see, so this wasn't a code-path issue at all. The `Bologna`/`Milan`/
   `Zurich`/`Interlaken` threads under `#europe` inherited the same
   invisibility. Fixed by the user granting the `Magellan` role View
   Channel / Read Message History / Send Messages / Add Reactions on
   `#europe` directly in Discord (channel permissions aren't something the
   bot can grant itself via the API without already having Manage Roles
   there, so this had to be a human action, not something to script).
3. To debug (2), used the bot's own token to query the Discord REST API
   directly from omashu (`/users/@me/guilds`, `/guilds/{id}/channels`,
   `/guilds/{id}/roles`, `/guilds/{id}/threads/active`, `/channels/{id}`)
   via small one-off Python scripts written to `/tmp` on the server and
   run there — never printed the token itself, only read it server-side
   into a curl `Authorization` header. This is a generally useful pattern
   for future "is the bot actually seeing X" questions — faster and more
   certain than guessing from gateway log absence alone.
4. **Confirmed fully working end-to-end** after the fix, without needing
   the user to report back: queried the reacted message's thread via the
   API and found the bot's own follow-up messages there — a plan
   suggestion got posted, "Create plan" was tapped, and "QC Terme Spa" was
   created and DMed to 9 travelers. Removed the broadest debug log
   afterward (it was explicitly temporary); kept the narrower
   emoji-matched one from earlier in this entry, which stays permanent.
5. **Takeaway for future permission issues**: a bot being a guild *member*
   doesn't mean it can see every channel — per-channel permission
   overwrites are independent of guild membership, and a channel it can't
   see produces total silence (no gateway events at all), not an error
   anywhere in the bot's own logs. If a feature "does nothing" for a
   specific channel/thread and the broad debug-log technique above (log
   before any filtering) shows literally nothing, check channel visibility
   via the API before suspecting the bot's code.

## 2026-08-23 — Deploy to omashu as a systemd service

User wants this running in production, on their existing server "omashu"
(see [[reference-omashu-server]] memory — GCP instance, SSH via
`jonathanh1386@35.193.217.32`, alias in `~/.zshrc`). Asked and got answers
on the two real decisions: **systemd, not Docker** (unlike jonathanhsieh.dev
on the same server — this bot has no ports to expose/proxy, just an
outbound gateway connection, so a container buys nothing here), and **OK
to push to GitHub** (5 local commits were ahead of `origin/main`; pushed to
`e51ace8` before deploying so omashu could clone).

- omashu had no `uv`, Python 3.11 system-wide, but did already have working
  SSH access to the `jonathanh8686` GitHub account (verified with `ssh -T
  git@github.com` before assuming it — didn't guess). Installed `uv` via
  the official installer, cloned to `~/magellan`, `uv sync --no-dev`
  (skips ruff/watchfiles in prod) — uv auto-provisioned Python 3.12 on the
  server the same way it did locally.
- Copied the local `.env` (which already had real working
  `DISCORD_TOKEN`/`TRAVELER_ROLE_ID`/`ANTHROPIC_API_KEY`) to the server via
  `scp`, `chmod 600`. Never printed its contents anywhere.
- `deploy/magellan-bot.service` (also installed to `/etc/systemd/system/`
  on the server — keep both in sync) runs `.venv/bin/magellan` directly
  rather than `uv run magellan`, specifically to avoid `uv run`'s
  re-sync-on-every-launch behavior reinstalling dev dependencies (ruff,
  watchfiles) into the prod venv on every restart — caught this on the
  first `systemctl status` (saw "Downloading ruff" in the logs for what
  should've been a plain bot start) and fixed by pointing `ExecStart` at
  the venv binary instead.
- **Caught and fixed a real duplicate-connection risk**: after confirming
  omashu's instance was live and logged in (`Logged in as Magellan#3652`),
  checked whether the local dev `uv run magellan` process from earlier in
  this session was still running — it had already been stopped (by the
  user, presumably, since I never restarted it after the two code changes
  that needed one). Documented in CLAUDE.md as a standing rule: never run
  a local instance while omashu's is up, since Discord doesn't dedupe
  events across multiple live sessions on the same token — both would
  independently handle every message/reaction/interaction.
- `deploy/redeploy.sh`: `git pull` + `uv sync --no-dev` + `systemctl
  restart`, run manually on omashu after a push. Doesn't touch `.env` or
  `data/` (the sqlite db) — those persist across deploys by design (same
  as `jonathanhsieh.dev`'s `data/` bind-mount convention, minus the Docker
  part).
- Verified: `systemctl status` showed `active (running)`, journalctl logs
  showed all three cogs loading, slash commands syncing globally (no
  `DEV_GUILD_ID` set in the copied `.env` — global sync can take up to an
  hour to propagate, expected for prod), and a successful gateway login as
  `Magellan#3652`. Did not yet verify an actual end-to-end interaction
  (an `/event create` or a 📅 reaction) against the deployed instance from
  within this session.

## 2026-08-23 — Gate every operation behind the traveler role

User: "All operations should only be permitted by people with the travel
role." Before this, only the 📅 reaction trigger checked the invoker's
role — `/event create|list|status|remind`, `/ping`, the RSVP Going/Not-going
buttons, and the plan-suggestion Create/Ignore buttons were all open to
anyone in the server.

- New `magellan/permissions.py`: `is_traveler(bot, guild, user)` +
  `traveler_only()` (an `app_commands.check` that raises `NotATraveler`,
  a `CheckFailure` subclass), used by every cog. Extracted this as a
  shared module immediately rather than waiting for a third duplicate —
  the DM-guild-resolution subtlety below is exactly the kind of thing
  that's cheap to get right once and easy to get wrong three times
  separately.
- **Real bug caught before it shipped**: `is_traveler` takes `guild` as an
  explicit parameter rather than reading `interaction.guild`.
  `interaction.guild` is `None` for a component interaction that
  originated in a DM — which is the *normal* case for `RSVPButton`, since
  most people RSVP from their DMs, not the channel post. A naive
  `interaction.guild`-based check would have silently broken every
  DM-based RSVP. Caught this by writing out the DM code path explicitly
  rather than assuming; verified with a smoke test using duck-typed stand-ins
  for `Guild`/`Member` exercising exactly that resolution path (`isinstance`
  correctly falls through to `guild.get_member(user.id)` for a plain,
  non-Member `user`, which is what a DM-originated `interaction.user`
  actually is).
- Wired a single global handler for the friendly denial message:
  `self.tree.error(self._on_app_command_error)` in `MagellanBot.setup_hook`
  (confirmed via `inspect.getsource` that `CommandTree.error()` just does
  `self.on_error = coro` — didn't guess the registration pattern). Slash
  commands get this for free by decorating with `@traveler_only()`; button
  callbacks don't have an equivalent hook, so `RSVPButton.callback`,
  `PlanSuggestionView.create`, and `.ignore` each call `is_traveler(...)`
  inline and send their own ephemeral denial.
- `/ping` is gated too — the instruction was "all operations," no carve-out
  mentioned, so applied literally rather than assuming a health check
  should stay open.
- Verified: `ruff check .` clean, all modules import, and `is_traveler`
  smoke-tested directly against duck-typed stubs (traveler via DM-resolved
  guild lookup, non-traveler, guild=None, unknown member, role
  unconfigured — all five behave correctly). Not tested against the live
  bot/API in this session.

## 2026-08-23 — Planner: require date+time, identify vague place names

First live test of the 📅 reaction trigger worked end to end (confirmed
the earlier debugging concern was moot — bot was running current code).
But it created a plan from "what if we go to that big cathedral in milan
at 10am", which had two problems the user flagged:

- Only a time was given ("10am"), no date at all — the old prompt's "a
  specific time or day" was disjunctive and let a bare time through as
  sufficient. Reworked `SYSTEM_PROMPT` to require BOTH a date (day name,
  "tomorrow"/"tonight", or an actual date all count) and a time — either
  one missing means `is_plan: false`, no guessing/filling in a plausible
  date.
- The title just repeated "that big cathedral in milan" verbatim instead
  of recognizing it as (most likely) the Duomo di Milano. Added explicit
  instruction + a `title` field description telling Claude to use its own
  knowledge to name a vaguely-described landmark rather than paraphrase
  the description.
- Also added `pydantic.Field(description=...)` to every `PlanDraft` field
  (previously bare `= None` defaults, no descriptions) — confirmed via
  `PlanDraft.model_json_schema()` that these descriptions flow into the
  JSON schema `client.messages.parse()` sends as the structured-output
  constraint, so the requirement is reinforced at the schema level, not
  just in prose in the system prompt.
- Verified: `ruff check .` clean, imports clean, schema output inspected
  directly. **Not yet tested against the live API** — the bot process
  running in this session (PID 9393/9396, started before this edit,
  without `--reload`) is still running the old prompt; it needs a restart
  to pick this up. Told the user rather than restarting it myself, since
  it's running in their own foreground terminal.

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
