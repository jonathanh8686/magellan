"""Shared traveler-role gate, used by every cog and every interaction kind
(slash commands, component buttons, raw reactions) — this is the single
place "is this person allowed to use the bot" is decided.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands

if TYPE_CHECKING:
    from magellan.bot import MagellanBot

NOT_A_TRAVELER_MESSAGE = "You need the traveler role in the server to use this."


class NotATraveler(app_commands.CheckFailure):
    """Raised by `traveler_only()` when the invoking user lacks the traveler role."""


def is_traveler(
    bot: MagellanBot,
    guild: discord.Guild | None,
    user: discord.abc.User | discord.Member | None,
) -> bool:
    """True if `user` holds the configured traveler role in `guild`.

    `guild` must be passed explicitly rather than read off an interaction —
    `interaction.guild` is `None` for component interactions that originate
    from a DM (e.g. clicking an RSVP button in your DMs), so callers resolve
    the right guild themselves (a stored guild_id, or `interaction.guild`
    when already known to be in-guild).
    """
    role_id = bot.config.traveler_role_id
    if role_id is None or guild is None or user is None:
        return False

    member = user if isinstance(user, discord.Member) and user.guild.id == guild.id else None
    if member is None:
        member = guild.get_member(user.id)
    if member is None:
        return False

    return role_id in {role.id for role in member.roles}


def traveler_only() -> Callable[[app_commands.Command], app_commands.Command]:
    """Slash-command check: raises NotATraveler if the invoker lacks the role."""

    async def predicate(interaction: discord.Interaction) -> bool:
        bot: MagellanBot = interaction.client  # type: ignore[assignment]
        if is_traveler(bot, interaction.guild, interaction.user):
            return True
        raise NotATraveler(NOT_A_TRAVELER_MESSAGE)

    return app_commands.check(predicate)
