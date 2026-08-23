"""Passive message listening: when a traveler posts something plan-shaped
in chat, ask Claude to extract it and offer to turn it into an /event.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import anthropic
import discord
from discord.ext import commands
from pydantic import BaseModel

if TYPE_CHECKING:
    from magellan.bot import MagellanBot
    from magellan.cogs.rsvp import RSVP

logger = logging.getLogger("magellan.cogs.planner")

MODEL = "claude-sonnet-5"

# Cheap keyword pre-filter so we don't send every message in the server to
# Claude — only messages that already look plan-shaped get extracted at all.
_TRIGGER_PATTERN = re.compile(
    r"\b("
    r"mon(day)?|tue(s(day)?)?|wed(nesday)?|thu(rs(day)?)?|fri(day)?|sat(urday)?|sun(day)?"
    r"|tonight|tomorrow|today"
    r"|\d{1,2}\s?(am|pm)"
    r"|let'?s (go|do|meet|grab)"
    r"|who'?s (down|in|up)"
    r"|dinner|lunch|breakfast|brunch"
    r")\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = (
    "You read one Discord message from a group planning a trip together. "
    "Decide whether it is proposing a concrete plan: a specific thing to do, "
    "at a specific time or day. Casual chat, questions with no proposal, and "
    "talk about something already booked are not new plans. If it is a plan, "
    "extract a short title, when it is (keep the sender's own wording for "
    "the day/time), and a location if one is mentioned. Be conservative — "
    "if you're not sure, set is_plan to false."
)


class PlanDraft(BaseModel):
    is_plan: bool
    title: str | None = None
    when: str | None = None
    location: str | None = None


class PlanSuggestionView(discord.ui.View):
    """Not a persistent DynamicItem like RSVPButton — this suggestion is
    only meaningful right after the triggering message, so it's fine for it
    to go stale (buttons disabled via on_timeout) across a bot restart.
    """

    def __init__(self, draft: PlanDraft) -> None:
        super().__init__(timeout=600)
        self.draft = draft
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Create plan", emoji="✅", style=discord.ButtonStyle.success)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot: MagellanBot = interaction.client  # type: ignore[assignment]
        rsvp_cog: RSVP | None = bot.get_cog("RSVP")  # type: ignore[assignment]
        if rsvp_cog is None or interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Can't create that plan right now.", ephemeral=True
            )
            return

        await interaction.response.defer()
        for item in self.children:
            item.disabled = True

        try:
            event, sent, failed = await rsvp_cog.create_and_announce(
                guild=interaction.guild,
                channel=interaction.channel,
                title=self.draft.title or "Untitled plan",
                when_text=self.draft.when or "TBD",
                location=self.draft.location,
                notes=None,
                created_by=interaction.user.id,
            )
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        summary = f"Created **{event.title}** — DMed {sent} traveler(s)."
        if failed:
            summary += f" Couldn't DM: {', '.join(m.mention for m in failed)}."
        await interaction.edit_original_response(content=summary, embed=None, view=self)

    @discord.ui.button(label="Ignore", emoji="❌", style=discord.ButtonStyle.secondary)
    async def ignore(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Dismissed.", embed=None, view=self)


class Planner(commands.Cog):
    def __init__(self, bot: MagellanBot) -> None:
        self.bot = bot
        self._client: anthropic.AsyncAnthropic | None = None
        if bot.config.anthropic_api_key:
            self._client = anthropic.AsyncAnthropic(api_key=bot.config.anthropic_api_key)
        else:
            logger.warning("ANTHROPIC_API_KEY not set — plan detection from chat is disabled.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if self._client is None or message.author.bot or message.guild is None:
            return
        if not _TRIGGER_PATTERN.search(message.content):
            return

        role_id = self.bot.config.traveler_role_id
        if role_id is None or not isinstance(message.author, discord.Member):
            return
        if role_id not in {r.id for r in message.author.roles}:
            return

        draft = await self._extract(message.content)
        if draft is None or not draft.is_plan or not draft.title or not draft.when:
            return

        embed = discord.Embed(
            title="Looks like a plan",
            description=f"**{draft.title}**",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="When", value=draft.when, inline=True)
        if draft.location:
            embed.add_field(name="Where", value=draft.location, inline=True)
        embed.set_footer(text="Detected from your message — Create to post it and DM everyone.")

        view = PlanSuggestionView(draft)
        view.message = await message.reply(embed=embed, view=view, mention_author=False)

    async def _extract(self, text: str) -> PlanDraft | None:
        assert self._client is not None
        # Best-effort: any API failure just means no suggestion this time,
        # never a broken message-handling path.
        try:
            response = await self._client.messages.parse(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
                output_format=PlanDraft,
            )
        except anthropic.APIError:
            logger.exception("Claude plan extraction failed")
            return None
        return response.parsed_output


async def setup(bot: MagellanBot) -> None:
    await bot.add_cog(Planner(bot))
