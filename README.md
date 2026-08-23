# Magellan

A Discord bot that listens to server messages and makes it easy to plan things
(events, trips, hangouts). Currently in use for planning the Europe 2026 trip.

## Setup

```bash
uv sync
cp .env.example .env   # fill in DISCORD_TOKEN, TRAVELER_ROLE_ID, optionally DEV_GUILD_ID
uv run magellan
```

`DEV_GUILD_ID` syncs slash commands to a single guild instantly, which is much
faster than the up-to-an-hour propagation for global commands. Use it while
developing; leave it blank in production.

`TRAVELER_ROLE_ID` is required for the bot to do anything at all: give
everyone on the trip a role in the server (e.g. `@Traveler`), then paste
that role's ID in. It's both the DM roster for `/event` and the permission
gate — every command and every button (RSVP, plan suggestions, even `/ping`)
only works for members with this role. Without it configured, the bot
declines every interaction.

Requirements in the Discord Developer Portal, under your bot's settings:
- **Privileged Gateway Intents** → enable **Server Members Intent** (needed
  to see who has the traveler role) and **Message Content Intent**.
- Invite the bot with the `bot` and `applications.commands` scopes and at
  least: Send Messages, Embed Links, Read Message History.

## Project layout

```
magellan/
├── __main__.py     # entrypoint: loads config, builds the bot, runs it
├── bot.py          # MagellanBot: intents, store lifecycle, cog loading, command sync
├── config.py       # env-based Config dataclass
├── store.py        # sqlite storage for plans (events) + RSVPs
├── permissions.py  # shared traveler-role gate for every command + button
└── cogs/           # one file per feature area, loaded in bot.py:INITIAL_COGS
    ├── general.py  # /ping health check
    ├── rsvp.py     # /event create|list|status|remind — plans + DM RSVPs
    └── planner.py  # 📅 reaction → Claude extraction → plan suggestion
```

## Permissions

Every command and every button (`/ping`, `/event ...`, RSVP Going/Not-going,
plan-suggestion Create/Ignore) only works for members with the
`TRAVELER_ROLE_ID` role — anyone else gets an ephemeral "you need the
traveler role" reply. This is enforced centrally in `permissions.py`, not
re-checked ad hoc per feature.

## RSVP flow

`/event create` (run in a server channel) posts a tally embed there and DMs
everyone with the `TRAVELER_ROLE_ID` role a matching embed with **Going** /
**Not going** buttons. Tapping a button anywhere (DM or the channel post)
records the RSVP and live-updates the channel embed. `/event list`,
`/event status <plan>`, and `/event remind <plan>` cover checking in and
nudging stragglers. See `AGENT.md` for the full design rationale.

## 📅 React to create a plan

With `ANTHROPIC_API_KEY` set: react to any message with 📅 and the bot sends
that message to Claude (`claude-sonnet-5`) to classify + extract a
title/when/location. There's no passive listening — extraction only ever
runs on a message a traveler explicitly flagged by reacting.

- While it's thinking, the bot adds ⏳ to the message (removed once done).
- If it found a usable plan, it replies with a **Create plan** / **Ignore**
  button — tapping Create runs the exact same post-and-DM flow as
  `/event create`. Nothing is ever created without that tap.
- If the message didn't have enough to act on (no clear activity or no
  sense of when), the bot reacts ❌ instead of replying.
- If the API call itself failed, the bot reacts ⚠️.
- Only reactions from travelers (people with the `TRAVELER_ROLE_ID` role)
  trigger it, and each message is only processed once per bot session even
  if reacted to multiple times.

Leave `ANTHROPIC_API_KEY` unset to disable this feature entirely; everything
else still works.

## Development

```bash
uv run ruff check .        # lint
uv run ruff check --fix .  # lint + autofix
uv run magellan             # run the bot
uv run magellan --reload    # run with hot reload (see below)
```

`--reload` watches `magellan/cogs/*.py` and calls discord.py's
`reload_extension()` on save — no gateway reconnect, no re-login, changes to
a command's *body* take effect in about a second. It does **not** pick up:
new commands/params/descriptions (those need a `tree.sync()`, i.e. a normal
restart) or changes outside `magellan/cogs/` (`bot.py`, `config.py`,
`store.py`) — restart normally for those. Dev-only; don't run it in
production.

## Deployment

Runs in production on **omashu** (`ssh omashu`, see `~/.zshrc`) as a systemd
service — not the same server as jonathanhsieh.dev's Docker setup, since
this bot has no ports to expose or proxy, just an outbound gateway
connection. Repo lives at `/home/jonathanh1386/magellan` on the server, unit
file is `deploy/magellan-bot.service` (installed at
`/etc/systemd/system/magellan-bot.service`).

**First-time setup** (already done — for reference if redoing from scratch):
```bash
# on omashu
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone git@github.com:jonathanh8686/magellan.git ~/magellan
cd ~/magellan && uv sync --no-dev
scp <local .env with real secrets> jonathanh1386@omashu:~/magellan/.env
chmod 600 ~/magellan/.env
sudo cp deploy/magellan-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now magellan-bot
```

**To deploy a code change**: push to `main`, then on omashu run
`./deploy/redeploy.sh` from inside `~/magellan` (`git pull` +
`uv sync --no-dev` + service restart). `.env` and `data/` (the sqlite
database — plans and RSVPs) aren't touched by a redeploy; they persist
across restarts and code updates.

**Logs**: `sudo journalctl -u magellan-bot -f` (follow) or `-n 100`
(last 100 lines). **Status**: `sudo systemctl status magellan-bot`.

**Only one instance of the bot should ever be connected at a time** — the
Discord gateway will happily accept a second connection on the same token,
but both instances then independently receive and handle every message/
reaction/interaction, causing duplicate DMs, duplicate plan suggestions,
etc. Don't leave a local `uv run magellan` running while omashu's instance
is also up.

See `CLAUDE.md` for coding standards and `AGENT.md` for a running log of work
done on this project.
