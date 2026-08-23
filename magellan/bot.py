"""Bot subclass: intents, cog loading, slash command sync, and dev hot reload."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from magellan.config import Config
from magellan.permissions import NotATraveler
from magellan.store import Store

logger = logging.getLogger("magellan.bot")

INITIAL_COGS = (
    "magellan.cogs.general",
    "magellan.cogs.rsvp",
    "magellan.cogs.planner",
)

COGS_DIR = Path(__file__).parent / "cogs"


class MagellanBot(commands.Bot):
    def __init__(self, config: Config, *, reload: bool = False) -> None:
        self.config = config
        self.store = Store(config.db_path)
        self.reload = reload

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(command_prefix=config.command_prefix, intents=intents)

    async def setup_hook(self) -> None:
        await self.store.connect()
        self.tree.error(self._on_app_command_error)

        for extension in INITIAL_COGS:
            await self.load_extension(extension)
            logger.info("Loaded extension %s", extension)

        if self.reload:
            asyncio.create_task(_watch_cogs(self))
            logger.info("Hot reload enabled — watching %s", COGS_DIR)

        if self.config.guild_id is not None:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced slash commands to dev guild %s", self.config.guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced slash commands globally")

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")

    async def close(self) -> None:
        await self.store.close()
        await super().close()

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, NotATraveler):
            message = str(error)
        else:
            logger.exception("Unhandled app command error", exc_info=error)
            message = "Something went wrong running that command."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def _watch_cogs(bot: MagellanBot) -> None:
    """Reload a cog's extension whenever its file changes.

    Only touches already-loaded extensions under INITIAL_COGS — it doesn't
    load new cogs on the fly. A slash command's name/description/params only
    take effect after the next `tree.sync()`, i.e. a full restart; this is
    for iterating on command *bodies* and other cog logic without dropping
    the gateway connection or re-syncing commands every save.
    """
    from watchfiles import Change, awatch

    async for changes in awatch(COGS_DIR):
        for change, raw_path in changes:
            path = Path(raw_path)
            if path.suffix != ".py" or path.stem == "__init__":
                continue

            extension = f"magellan.cogs.{path.stem}"
            if extension not in bot.extensions:
                continue
            if change == Change.deleted:
                continue

            try:
                await bot.reload_extension(extension)
                logger.info("Hot-reloaded %s", extension)
            except commands.ExtensionError:
                logger.exception("Failed to hot-reload %s", extension)
