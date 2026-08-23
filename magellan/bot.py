"""Bot subclass: intents, cog loading, and slash command sync."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from magellan.config import Config

logger = logging.getLogger("magellan.bot")

INITIAL_COGS = (
    "magellan.cogs.general",
)


class MagellanBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        self.config = config

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(command_prefix=config.command_prefix, intents=intents)

    async def setup_hook(self) -> None:
        for extension in INITIAL_COGS:
            await self.load_extension(extension)
            logger.info("Loaded extension %s", extension)

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
