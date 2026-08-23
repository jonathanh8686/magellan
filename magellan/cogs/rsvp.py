"""Plans + RSVPs: /event create DMs everyone with the traveler role a Yes/No
button, tracks responses in sqlite, and keeps a live-updating tally embed in
the channel it was created in.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from magellan.permissions import NOT_A_TRAVELER_MESSAGE, is_traveler, traveler_only
from magellan.store import Event

if TYPE_CHECKING:
    from magellan.bot import MagellanBot

logger = logging.getLogger("magellan.cogs.rsvp")

RSVP_CUSTOM_ID_TEMPLATE = r"magellan:rsvp:(?P<event_id>\d+):(?P<choice>yes|no)"
_DM_ONLY_ERROR = "Use this in the server, not a DM."
_ROLE_NOT_CONFIGURED_ERROR = "The traveler role isn't configured — set TRAVELER_ROLE_ID in `.env`."


def _split_roster(
    role: discord.Role, rsvps: dict[int, str]
) -> tuple[list[int], list[int], list[int]]:
    """Return (going, not_going, pending) user id lists for a role's human members."""
    going = [uid for uid, status in rsvps.items() if status == "yes"]
    not_going = [uid for uid, status in rsvps.items() if status == "no"]
    pending = [m.id for m in role.members if not m.bot and m.id not in rsvps]
    return going, not_going, pending


def _mention_list(user_ids: list[int]) -> str:
    return " ".join(f"<@{uid}>" for uid in user_ids) if user_ids else "—"


def build_event_embed(event: Event, role: discord.Role, rsvps: dict[int, str]) -> discord.Embed:
    going, not_going, pending = _split_roster(role, rsvps)

    embed = discord.Embed(
        title=event.title,
        description=event.notes or None,
        color=discord.Color.blurple(),
    )
    if event.location:
        embed.add_field(name="Where", value=event.location, inline=True)
    if event.price:
        embed.add_field(name="Price (per person)", value=event.price, inline=True)
    embed.add_field(name=f"✅ Going ({len(going)})", value=_mention_list(going), inline=False)
    embed.add_field(
        name=f"❌ Not going ({len(not_going)})", value=_mention_list(not_going), inline=False
    )
    embed.add_field(name=f"⏳ Pending ({len(pending)})", value=_mention_list(pending), inline=False)
    if event.ai_comment:
        embed.add_field(name="🤖 Claude's Comments", value=event.ai_comment, inline=False)
    embed.set_footer(text=f"Plan #{event.id} · tap a button below to RSVP")
    return embed


def build_rsvp_view(event_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(RSVPButton(event_id, "yes"))
    view.add_item(RSVPButton(event_id, "no"))
    return view


class RSVPButton(discord.ui.DynamicItem[discord.ui.Button], template=RSVP_CUSTOM_ID_TEMPLATE):
    def __init__(self, event_id: int, choice: str) -> None:
        self.event_id = event_id
        self.choice = choice
        super().__init__(
            discord.ui.Button(
                label="Going" if choice == "yes" else "Not going",
                emoji="✅" if choice == "yes" else "❌",
                style=(
                    discord.ButtonStyle.success
                    if choice == "yes"
                    else discord.ButtonStyle.danger
                ),
                custom_id=f"magellan:rsvp:{event_id}:{choice}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: dict,
        /,
    ) -> RSVPButton:
        return cls(int(match["event_id"]), match["choice"])

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: MagellanBot = interaction.client  # type: ignore[assignment]

        event = await bot.store.get_event(self.event_id)
        if event is None or event.closed:
            await interaction.response.send_message(
                "This plan no longer accepts RSVPs.", ephemeral=True
            )
            return

        # interaction.guild is None when this button is clicked from a DM
        # (the common case) — resolve the guild via the event row instead.
        guild = bot.get_guild(event.guild_id)
        if not is_traveler(bot, guild, interaction.user):
            await interaction.response.send_message(NOT_A_TRAVELER_MESSAGE, ephemeral=True)
            return

        await bot.store.upsert_rsvp(self.event_id, interaction.user.id, self.choice)
        await interaction.response.send_message(
            f"You're marked as **{'going' if self.choice == 'yes' else 'not going'}** to "
            f"**{event.title}** {'✅' if self.choice == 'yes' else '❌'}",
            ephemeral=True,
        )
        await refresh_all_messages(bot, event)


async def refresh_all_messages(bot: MagellanBot, event: Event) -> None:
    """Re-render the tally embed everywhere it lives: the channel post AND
    every traveler's individual DM copy — not just wherever this particular
    RSVP came from. Each DM is a separate Discord message with its own
    stale tally otherwise; `store.get_dm_messages` is how we know which
    ones to go find and edit.
    """
    guild = bot.get_guild(event.guild_id)
    if guild is None or bot.config.traveler_role_id is None:
        return
    role = guild.get_role(bot.config.traveler_role_id)
    if role is None:
        return

    rsvps = await bot.store.get_rsvps(event.id)
    embed = build_event_embed(event, role, rsvps)

    if event.message_id is not None:
        try:
            channel = guild.get_channel(event.channel_id) or await bot.fetch_channel(
                event.channel_id
            )
            message = await channel.fetch_message(event.message_id)
            await message.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden):
            logger.warning("Could not refresh channel announcement for event %s", event.id)

    for user_id, dm_channel_id, dm_message_id in await bot.store.get_dm_messages(event.id):
        try:
            dm_channel = bot.get_channel(dm_channel_id) or await bot.fetch_channel(dm_channel_id)
            dm_message = await dm_channel.fetch_message(dm_message_id)
            await dm_message.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden):
            logger.warning(
                "Could not refresh DM for event %s, user %s (they may have deleted it "
                "or blocked the bot)",
                event.id,
                user_id,
            )


async def _event_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None:
        return []
    bot: MagellanBot = interaction.client  # type: ignore[assignment]
    events = await bot.store.list_events(interaction.guild_id)
    current_lower = current.lower()
    matches = [e for e in events if current_lower in e.title.lower()]
    return [
        app_commands.Choice(name=f"#{e.id} — {e.title}"[:100], value=str(e.id))
        for e in matches[:25]
    ]


class RSVP(commands.Cog):
    event_group = app_commands.Group(name="event", description="Plan things and track RSVPs")

    def __init__(self, bot: MagellanBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(RSVPButton)

    async def cog_unload(self) -> None:
        # Unregister so a hot reload (magellan --reload) can re-add the
        # freshly-imported RSVPButton class without clashing with this one.
        self.bot.remove_dynamic_items(RSVPButton)

    def _get_traveler_role(self, guild: discord.Guild) -> discord.Role | None:
        if self.bot.config.traveler_role_id is None:
            return None
        return guild.get_role(self.bot.config.traveler_role_id)

    async def _require_guild(self, interaction: discord.Interaction) -> discord.Guild | None:
        if interaction.guild is not None:
            return interaction.guild
        await interaction.response.send_message(_DM_ONLY_ERROR, ephemeral=True)
        return None

    async def _require_role(
        self, interaction: discord.Interaction, guild: discord.Guild
    ) -> discord.Role | None:
        role = self._get_traveler_role(guild)
        if role is None:
            await interaction.response.send_message(_ROLE_NOT_CONFIGURED_ERROR, ephemeral=True)
        return role

    async def create_and_announce(
        self,
        *,
        guild: discord.Guild,
        channel: discord.TextChannel | discord.Thread,
        title: str,
        location: str | None,
        price: str | None,
        notes: str | None,
        created_by: int,
        ai_comment: str | None = None,
    ) -> tuple[Event, int, list[discord.Member]]:
        """Create a plan, post the tally embed in `channel`, and DM the traveler role.

        The single place that fans a plan out to the roster — shared by
        `/event create` and the planner cog's "Create plan" button, so don't
        duplicate this DM-fanout logic at a second call site.
        """
        role = self._get_traveler_role(guild)
        if role is None:
            raise RuntimeError(_ROLE_NOT_CONFIGURED_ERROR)

        event = await self.bot.store.create_event(
            guild_id=guild.id,
            channel_id=channel.id,
            title=title,
            location=location,
            price=price,
            notes=notes,
            created_by=created_by,
            ai_comment=ai_comment,
        )

        embed = build_event_embed(event, role, {})
        message = await channel.send(embed=embed, view=build_rsvp_view(event.id))
        await self.bot.store.set_message(event.id, message.id)

        sent = 0
        failed: list[discord.Member] = []
        for member in role.members:
            if member.bot:
                continue
            try:
                dm = await member.send(embed=embed, view=build_rsvp_view(event.id))
                sent += 1
                await self.bot.store.record_dm_message(event.id, member.id, dm.channel.id, dm.id)
            except discord.Forbidden:
                failed.append(member)

        return event, sent, failed

    @event_group.command(name="create", description="Create a plan and DM every traveler to RSVP.")
    @app_commands.describe(
        title="What is this?",
        location="Where is it? (optional)",
        price="Price per person? (optional, e.g. '$80pp')",
        notes="Any extra details? (optional)",
    )
    @traveler_only()
    async def event_create(
        self,
        interaction: discord.Interaction,
        title: str,
        location: str | None = None,
        price: str | None = None,
        notes: str | None = None,
    ) -> None:
        guild = await self._require_guild(interaction)
        if guild is None or interaction.channel is None:
            return

        role = await self._require_role(interaction, guild)
        if role is None:
            return

        await interaction.response.defer(thinking=True)

        _event, sent, failed = await self.create_and_announce(
            guild=guild,
            channel=interaction.channel,
            title=title,
            location=location,
            price=price,
            notes=notes,
            created_by=interaction.user.id,
        )

        summary = f"Created **{title}** and posted it here. DMed {sent} traveler(s) to RSVP."
        if failed:
            summary += (
                f"\nCouldn't DM: {', '.join(m.mention for m in failed)} "
                "(they likely have DMs closed to server members)."
            )
        await interaction.followup.send(summary)

    @event_group.command(name="list", description="List open plans and their RSVP counts.")
    @traveler_only()
    async def event_list(self, interaction: discord.Interaction) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        events = await self.bot.store.list_events(guild.id)
        if not events:
            await interaction.response.send_message(
                "No plans yet — create one with `/event create`.", ephemeral=True
            )
            return

        role = self._get_traveler_role(guild)
        lines = []
        for event in events:
            rsvps = await self.bot.store.get_rsvps(event.id)
            going, not_going, pending = (
                _split_roster(role, rsvps) if role else (list(rsvps), [], [])
            )
            price_bit = f" ({event.price})" if event.price else ""
            lines.append(
                f"**#{event.id} — {event.title}**{price_bit} "
                f"— ✅ {len(going)}  ❌ {len(not_going)}  ⏳ {len(pending)}"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @event_group.command(name="status", description="See who has and hasn't responded to a plan.")
    @app_commands.describe(event="Which plan")
    @app_commands.autocomplete(event=_event_autocomplete)
    @traveler_only()
    async def event_status(self, interaction: discord.Interaction, event: str) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        record = await self._resolve_event(interaction, event)
        if record is None:
            return

        role = await self._require_role(interaction, guild)
        if role is None:
            return

        rsvps = await self.bot.store.get_rsvps(record.id)
        await interaction.response.send_message(
            embed=build_event_embed(record, role, rsvps), ephemeral=True
        )

    @event_group.command(
        name="remind", description="DM everyone who hasn't responded to a plan yet."
    )
    @app_commands.describe(event="Which plan")
    @app_commands.autocomplete(event=_event_autocomplete)
    @traveler_only()
    async def event_remind(self, interaction: discord.Interaction, event: str) -> None:
        guild = await self._require_guild(interaction)
        if guild is None:
            return

        record = await self._resolve_event(interaction, event)
        if record is None:
            return

        role = await self._require_role(interaction, guild)
        if role is None:
            return

        rsvps = await self.bot.store.get_rsvps(record.id)
        _, _, pending_ids = _split_roster(role, rsvps)
        pending_members = [m for m in role.members if m.id in pending_ids]

        if not pending_members:
            await interaction.response.send_message("Everyone's already responded.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        embed = build_event_embed(record, role, rsvps)
        sent = 0
        failed: list[discord.Member] = []
        for member in pending_members:
            try:
                dm = await member.send(embed=embed, view=build_rsvp_view(record.id))
                sent += 1
                await self.bot.store.record_dm_message(record.id, member.id, dm.channel.id, dm.id)
            except discord.Forbidden:
                failed.append(member)

        summary = f"Reminded {sent} traveler(s) about **{record.title}**."
        if failed:
            summary += f"\nCouldn't DM: {', '.join(m.mention for m in failed)}."
        await interaction.followup.send(summary)

    async def _resolve_event(
        self, interaction: discord.Interaction, raw_event_id: str
    ) -> Event | None:
        try:
            event_id = int(raw_event_id)
        except ValueError:
            await interaction.response.send_message(
                "Pick a plan from the autocomplete list.", ephemeral=True
            )
            return None

        record = await self.bot.store.get_event(event_id)
        if record is None or record.guild_id != interaction.guild_id:
            await interaction.response.send_message("That plan doesn't exist.", ephemeral=True)
            return None
        return record


async def setup(bot: MagellanBot) -> None:
    await bot.add_cog(RSVP(bot))
