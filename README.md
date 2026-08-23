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

`TRAVELER_ROLE_ID` is required for `/event` (RSVP): give everyone on the trip
a role in the server (e.g. `@Traveler`), then paste that role's ID in. This
is how the bot knows who to DM.

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
└── cogs/           # one file per feature area, loaded in bot.py:INITIAL_COGS
    ├── general.py  # /ping health check + on_message listener stub
    └── rsvp.py     # /event create|list|status|remind — plans + DM RSVPs
```

## RSVP flow

`/event create` (run in a server channel) posts a tally embed there and DMs
everyone with the `TRAVELER_ROLE_ID` role a matching embed with **Going** /
**Not going** buttons. Tapping a button anywhere (DM or the channel post)
records the RSVP and live-updates the channel embed. `/event list`,
`/event status <plan>`, and `/event remind <plan>` cover checking in and
nudging stragglers. See `AGENT.md` for the full design rationale.

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

See `CLAUDE.md` for coding standards and `AGENT.md` for a running log of work
done on this project.
