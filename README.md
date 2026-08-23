# Magellan

A Discord bot that listens to server messages and makes it easy to plan things
(events, trips, hangouts).

## Setup

```bash
uv sync
cp .env.example .env   # fill in DISCORD_TOKEN, optionally DEV_GUILD_ID
uv run magellan
```

`DEV_GUILD_ID` syncs slash commands to a single guild instantly, which is much
faster than the up-to-an-hour propagation for global commands. Use it while
developing; leave it blank in production.

## Project layout

```
magellan/
├── __main__.py     # entrypoint: loads config, builds the bot, runs it
├── bot.py          # MagellanBot: intents, cog loading, command sync
├── config.py       # env-based Config dataclass
└── cogs/           # one file per feature area, loaded in bot.py:INITIAL_COGS
    └── general.py  # /ping health check + on_message listener stub
```

## Development

```bash
uv run ruff check .        # lint
uv run ruff check --fix .  # lint + autofix
uv run magellan             # run the bot
```

See `CLAUDE.md` for coding standards and `AGENT.md` for a running log of work
done on this project.
