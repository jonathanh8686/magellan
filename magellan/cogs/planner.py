"""Plan detection from chat: react to a message with TRIGGER_EMOJI and the
bot asks Claude to extract a plan from it, offering to turn it into an
/event. No passive listening — extraction only ever runs on a message a
human explicitly flagged.
"""

from __future__ import annotations

import logging
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
TRIGGER_EMOJI = "📅"

SYSTEM_PROMPT = (
    "You read one Discord message from a group planning a trip together. "
    "A member flagged it as a possible plan by reacting to it, so treat "
    "that as a signal it's worth a close look, not proof it's usable. "
    "Decide whether the message actually contains enough to act on: a "
    "specific thing to do, at a specific time or day. If it does, extract "
    "a short title, when it is (keep the sender's own wording for the "
    "day/time), and a location if one is mentioned. If it's too vague to "
    "act on (no clear activity, or no sense of when), set is_plan to false."
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
            logger.warning(
                "ANTHROPIC_API_KEY not set — the %s reaction trigger is disabled.", TRIGGER_EMOJI
            )
        # Session-scoped only (not persisted): stops a second reaction on
        # the same message from firing a duplicate extraction/suggestion.
        self._handled_message_ids: set[int] = set()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self._client is None or payload.guild_id is None:
            return
        if payload.emoji.name != TRIGGER_EMOJI:
            return
        if payload.member is None or payload.member.bot:
            return

        role_id = self.bot.config.traveler_role_id
        if role_id is None or role_id not in {r.id for r in payload.member.roles}:
            return

        if payload.message_id in self._handled_message_ids:
            return
        self._handled_message_ids.add(payload.message_id)

        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(payload.channel_id)
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        if message.author.bot or not message.content:
            return

        await self._handle_trigger(message)

    async def _handle_trigger(self, message: discord.Message) -> None:
        try:
            await message.add_reaction("⏳")
        except discord.HTTPException:
            pass

        draft = await self._extract(message.content)

        if self.bot.user is not None:
            try:
                await message.remove_reaction("⏳", self.bot.user)
            except discord.HTTPException:
                pass

        if draft is None:
            await self._react_quietly(message, "⚠️")
            return

        if not draft.is_plan or not draft.title or not draft.when:
            await self._react_quietly(message, "❌")
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

    @staticmethod
    async def _react_quietly(message: discord.Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            pass

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
