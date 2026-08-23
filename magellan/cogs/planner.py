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
    "A message only counts as an actionable plan (is_plan: true) if it "
    "names a specific thing to do AND gives BOTH a date and a time for it. "
    "A bare time with no date ('at 10am', 'around 7') is not enough on its "
    "own, and neither is a bare date with no time ('Saturday', 'tomorrow') "
    "— require both. The date doesn't need to be a literal calendar date: "
    "a day name, 'tomorrow', or 'tonight' all count as a date. Never invent "
    "or guess a date or time that isn't stated or clearly implied by the "
    "message itself — if either is missing, set is_plan to false rather "
    "than filling in a plausible-sounding one.\n\n"
    "If it is a plan, extract:\n"
    "- title: a short, specific title. If the message describes a place "
    "rather than naming it (e.g. 'the big cathedral in Milan'), use your "
    "own knowledge of the destination to identify what it's most likely "
    "referring to (e.g. 'Duomo di Milano') instead of repeating the vague "
    "description verbatim.\n"
    "- when: the date and time together, in the sender's own wording where "
    "possible.\n"
    "- location: where it is, if mentioned or identifiable from context."
)


class PlanDraft(BaseModel):
    is_plan: bool = Field(
        description=(
            "True only if the message names a specific activity and gives "
            "both a date (a day name, 'tomorrow'/'tonight', or an actual "
            "date all count) and a time. Missing either one means false — "
            "never treat a bare time or a bare date as sufficient on its own."
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
    when: str | None = Field(
        default=None,
        description=(
            "The date AND time together, in the sender's own wording where "
            "possible. Never fill in a date or time that isn't actually "
            "stated or clearly implied."
        ),
    )
    location: str | None = Field(default=None, description="Where the plan is, if mentioned.")


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
