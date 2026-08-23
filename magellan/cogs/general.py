"""Baseline cog: health check command and a message listener stub."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("magellan.cogs.general")


class General(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Check that the bot is alive and responsive.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"Pong! ({latency_ms}ms)")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        # Message-listening logic (parsing, planning triggers, etc.) goes here.


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
