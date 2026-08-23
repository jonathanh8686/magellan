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
from pydantic import BaseModel, Field

from magellan.permissions import NOT_A_TRAVELER_MESSAGE, is_traveler

if TYPE_CHECKING:
    from magellan.bot import MagellanBot
    from magellan.cogs.rsvp import RSVP

logger = logging.getLogger("magellan.cogs.planner")

MODEL = "claude-sonnet-5"
TRIGGER_EMOJI = "📅"

SYSTEM_PROMPT = (
    "You read one Discord message from a group planning a trip together. "
    "A member flagged it as a possible plan by reacting to it, so treat "
    "that as a signal it's worth a close look, not proof it's usable.\n\n"
    "A message counts as an actionable plan (is_plan: true) if it proposes "
    "a specific, identifiable activity or place to visit. Timing doesn't "
    "matter — this group doesn't schedule around exact times, so never let "
    "the presence or absence of a time/date affect is_plan. Vague chatter "
    "with no concrete activity or place, or a question with no proposal, "
    "is not a plan.\n\n"
    "If it is a plan, extract:\n"
    "- title: a short, specific title. If the message describes a place "
    "rather than naming it (e.g. 'the big cathedral in Milan'), use your "
    "own knowledge of the destination to identify what it's most likely "
    "referring to (e.g. 'Duomo di Milano') instead of repeating the vague "
    "description verbatim.\n"
    "- location: where it is, if mentioned or identifiable from context.\n"
    "- price: the cost PER PERSON, if mentioned (convert a total/group "
    "price to a per-person figure if you can tell the group size). If no "
    "price is mentioned but this is a well-known paid activity/attraction, "
    "give your best-guess typical per-person price prefixed with '~' to "
    "mark it as an estimate (e.g. '~€15/person'). Leave null if you have "
    "no reasonable basis to guess.\n"
    "- comment: a short blurb (1-3 sentences) with your best guess at what "
    "this actually is — background, why it's notable, what to expect. This "
    "is shown to the group explicitly labeled as AI-generated, so it's "
    "fine to be informative even when you're inferring from a vague "
    "reference — just don't state a guess as if it were certain fact."
)


class PlanDraft(BaseModel):
    is_plan: bool = Field(
        description=(
            "True only if the message names a specific, identifiable "
            "activity or place. Timing is irrelevant to this decision — "
            "never require or check for a date/time."
        )
    )
    title: str | None = Field(
        default=None,
        description=(
            "A short, specific title. If the message describes a place "
            "rather than naming it, identify what it most likely refers to "
            "(e.g. 'the big cathedral in Milan' -> 'Duomo di Milano') "
            "rather than repeating the vague description."
        ),
    )
    location: str | None = Field(default=None, description="Where the plan is, if mentioned.")
    price: str | None = Field(
        default=None,
        description=(
            "Cost PER PERSON. If a total/group price is mentioned, convert "
            "it to per-person. If nothing is mentioned but this is a "
            "well-known paid activity, give a best-guess estimate prefixed "
            "with '~' (e.g. '~€15/person'). Null if there's no reasonable "
            "basis to guess."
        ),
    )
    comment: str | None = Field(
        default=None,
        description=(
            "1-3 sentence best-guess blurb on what this actually is — "
            "background, why it's notable, what to expect. Shown to the "
            "group labeled as AI-generated, so inferring from a vague "
            "reference is fine."
        ),
    )


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

        if not is_traveler(bot, interaction.guild, interaction.user):
            await interaction.response.send_message(NOT_A_TRAVELER_MESSAGE, ephemeral=True)
            return

        await interaction.response.defer()
        for item in self.children:
            item.disabled = True

        try:
            event, sent, failed = await rsvp_cog.create_and_announce(
                guild=interaction.guild,
                channel=interaction.channel,
                title=self.draft.title or "Untitled plan",
                location=self.draft.location,
                price=self.draft.price,
                notes=None,
                created_by=interaction.user.id,
                ai_comment=self.draft.comment,
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
        bot: MagellanBot = interaction.client  # type: ignore[assignment]
        if not is_traveler(bot, interaction.guild, interaction.user):
            await interaction.response.send_message(NOT_A_TRAVELER_MESSAGE, ephemeral=True)
            return

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

        logger.info(
            "%s reaction from user=%s on message=%s in guild=%s",
            TRIGGER_EMOJI,
            payload.user_id,
            payload.message_id,
            payload.guild_id,
        )

        if payload.member is None or payload.member.bot:
            logger.info("Ignoring: no member data on the payload, or reactor is a bot")
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not is_traveler(self.bot, guild, payload.member):
            logger.info("Ignoring: user=%s does not have the traveler role", payload.user_id)
            return

        if payload.message_id in self._handled_message_ids:
            logger.info("Ignoring: message=%s was already handled this session", payload.message_id)
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

        if not draft.is_plan or not draft.title:
            await self._react_quietly(message, "❌")
            return

        embed = discord.Embed(
            title="Looks like a plan",
            description=f"**{draft.title}**",
            color=discord.Color.blurple(),
        )
        if draft.location:
            embed.add_field(name="Where", value=draft.location, inline=True)
        if draft.price:
            embed.add_field(name="Price (per person)", value=draft.price, inline=True)
        if draft.comment:
            embed.add_field(name="🤖 Claude's Comments", value=draft.comment, inline=False)
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
