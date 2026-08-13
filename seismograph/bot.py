"""Discord integration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

from . import storage
from .analysis import AnalysisError, LLMClient
from .config import Config
from .pipeline import analysis_period, generate_report, period_label, utc_iso
from .privacy import hash_author, redact
from .report import (
    FEEDBACK_REACTIONS,
    InsufficientEvidence,
    render_markdown,
    split_for_discord,
)

log = logging.getLogger(__name__)

# Weekly schedule: checked hourly, fires on the first matching hour of the week.
SCHEDULE_WEEKDAY = 0  # Monday
SCHEDULE_HOUR = 9

FEEDBACK_EMOJI = {emoji for emoji, _ in FEEDBACK_REACTIONS}


def build_client(config: Config) -> SeismographClient:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True
    intents.reactions = True
    return SeismographClient(config, intents=intents)


class SeismographClient(discord.Client):
    def __init__(self, config: Config, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.connection = storage.connect(config.database_path)
        self.llm = LLMClient(config.llm_base_url, config.llm_api_key, config.llm_model)
        self._register_commands()

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.weekly_check.start()

    async def on_ready(self) -> None:
        log.info(
            "connected as %s, watching %d channel(s), reporting to %s",
            self.user,
            len(self.config.source_channel_ids),
            self.config.report_channel_id,
        )

    async def on_message(self, message: discord.Message) -> None:
        """Store messages from allowlisted guild channels only."""
        if message.guild is None or message.guild.id != self.config.guild_id:
            return
        if message.channel.id not in self.config.source_channel_ids:
            return
        if message.author.bot:
            return
        content = redact(message.content or "")
        if not content.strip():
            return
        storage.store_messages(
            self.connection,
            [
                {
                    "message_id": str(message.id),
                    "channel_id": str(message.channel.id),
                    "author_hash": hash_author(message.author.id, self.config.author_hash_salt),
                    "created_at": utc_iso(message.created_at),
                    "content": content,
                }
            ],
        )

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Record maintainer feedback reactions on a published report."""
        emoji = str(payload.emoji)
        if emoji not in FEEDBACK_EMOJI or payload.user_id == getattr(self.user, "id", None):
            return
        run_id = storage.run_for_report_message(self.connection, str(payload.message_id))
        if run_id is None:
            return
        storage.record_feedback(
            self.connection,
            run_id,
            emoji,
            hash_author(payload.user_id, self.config.author_hash_salt),
        )
        log.info("recorded feedback %s for run %d", emoji, run_id)

    def _register_commands(self) -> None:
        @self.tree.command(
            name="seismograph",
            description="Generate a friction report for the current analysis period.",
        )
        @app_commands.default_permissions(administrator=True)
        @app_commands.checks.has_permissions(administrator=True)
        async def seismograph(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            start, end = analysis_period(
                datetime.now(UTC), self.config.report_timezone, self.config.analysis_days
            )
            label = period_label(start, end, self.config.report_timezone)
            try:
                await self.publish(kind="manual", start=start, end=end, label=label)
            except InsufficientEvidence:
                await interaction.followup.send(
                    f"Analyzed {label}. No signal met the evidence thresholds; nothing was posted.",
                    ephemeral=True,
                )
                return
            except AnalysisError as exc:
                log.error("manual report failed: %s", exc)
                await interaction.followup.send(
                    f"Analysis failed for {label}: {exc}. Nothing was posted.", ephemeral=True
                )
                return
            await interaction.followup.send(
                f"Posted a report for {label} in <#{self.config.report_channel_id}>.",
                ephemeral=True,
            )

    async def collect_history(self, start: str, end: str) -> int:
        """Backfill stored messages for the period from the allowlisted channels."""
        after = datetime.fromisoformat(start)
        before = datetime.fromisoformat(end)
        stored = 0
        for channel_id in self.config.source_channel_ids:
            channel = self.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                log.warning("channel %s is not a readable text channel, skipping", channel_id)
                continue
            batch = []
            async for message in channel.history(after=after, before=before, limit=None):
                if message.author.bot:
                    continue
                content = redact(message.content or "")
                if not content.strip():
                    continue
                batch.append(
                    {
                        "message_id": str(message.id),
                        "channel_id": str(channel_id),
                        "author_hash": hash_author(message.author.id, self.config.author_hash_salt),
                        "created_at": utc_iso(message.created_at),
                        "content": content,
                    }
                )
            if batch:
                stored += storage.store_messages(self.connection, batch)
        log.info("collected %d new message(s) for %s to %s", stored, start, end)
        return stored

    async def publish(self, kind: str, start: str, end: str, label: str) -> None:
        """Generate and post a report, or raise without posting anything."""
        channel = self.get_channel(self.config.report_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise AnalysisError(f"report channel {self.config.report_channel_id} is not writable")

        await self.collect_history(start, end)
        run_id, report, messages, analysis = generate_report(
            self.connection, self.llm, kind, start, end, label
        )
        for line in analysis.summary_lines():
            log.info("%s", line)
        for rejection in analysis.rejections:
            log.info("rejected candidate: %s", rejection)

        markdown = render_markdown(report, self.config.guild_id, messages)
        chunks = split_for_discord(markdown)
        first = None
        for chunk in chunks:
            sent = await channel.send(chunk)
            first = first or sent
        for emoji, _ in FEEDBACK_REACTIONS:
            await first.add_reaction(emoji)
        storage.mark_published(self.connection, run_id, str(first.id))
        log.info("published run %d as %d message(s)", run_id, len(chunks))

    @tasks.loop(hours=1)
    async def weekly_check(self) -> None:
        """Run the weekly report once per period, at most once per stored period."""
        local_now = datetime.now(ZoneInfo(self.config.report_timezone))
        if local_now.weekday() != SCHEDULE_WEEKDAY or local_now.hour != SCHEDULE_HOUR:
            return
        start, end = analysis_period(
            datetime.now(UTC), self.config.report_timezone, self.config.analysis_days
        )
        if storage.scheduled_run_exists(self.connection, start, end):
            log.info("scheduled report for %s to %s already exists, skipping", start, end)
            return
        label = period_label(start, end, self.config.report_timezone)
        try:
            await self.publish(kind="scheduled", start=start, end=end, label=label)
        except InsufficientEvidence:
            log.info("scheduled report for %s skipped: insufficient evidence", label)
        except AnalysisError as exc:
            log.error("scheduled report for %s failed: %s", label, exc)
        storage.prune(self.connection, self.config.retention_days)

    @weekly_check.before_loop
    async def before_weekly_check(self) -> None:
        await self.wait_until_ready()
