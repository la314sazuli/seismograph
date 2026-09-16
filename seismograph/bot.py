from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import tasks

from . import storage
from .analysis import AnalysisError, LLMClient, prepare
from .case_commands import register as register_case_commands
from .config import Config
from .pipeline import analysis_period, generate_report, period_label, scheduled_period, utc_iso
from .privacy import hash_author, redact
from .report import FEEDBACK_REACTIONS, InsufficientEvidence, render_markdown, split_for_discord

log = logging.getLogger(__name__)
FEEDBACK_EMOJI = {emoji for emoji, _ in FEEDBACK_REACTIONS}


def build_client(config: Config) -> SeismographClient:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True
    intents.guild_reactions = True
    return SeismographClient(config, intents=intents)


class SeismographClient(discord.Client):
    def __init__(self, config: Config, *, intents: discord.Intents):
        super().__init__(
            intents=intents,
            member_cache_flags=discord.MemberCacheFlags.none(),
            chunk_guilds_at_startup=False,
            max_messages=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.connection = storage.connect(config.database_path)
        self.report_lock = asyncio.Lock()
        self.startup_failed = False
        self._register_commands()
        register_case_commands(self)

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.weekly_check.start()

    async def close(self) -> None:
        self.weekly_check.cancel()
        await super().close()
        self.connection.close()

    async def on_ready(self) -> None:
        try:
            await self.preflight()
        except (AnalysisError, discord.HTTPException) as exc:
            self.startup_failed = True
            log.error(
                "startup preflight failed: %s",
                str(exc) if isinstance(exc, AnalysisError) else type(exc).__name__,
            )
            await self.close()
            return
        log.info("ready; sources=%d, member cache disabled", len(self.config.source_channel_ids))

    async def preflight(self) -> discord.TextChannel:
        guild = self.get_guild(self.config.guild_id)
        if guild is None or guild.me is None:
            raise AnalysisError("Configured guild is unavailable to the bot")
        report = await self.fetch_channel(self.config.report_channel_id)
        if not isinstance(report, discord.TextChannel) or report.guild.id != guild.id:
            raise AnalysisError("Report destination must be a text channel in the configured guild")
        if report.permissions_for(guild.default_role).view_channel:
            raise AnalysisError("Report channel is visible to @everyone; restrict it to staff")
        permissions = report.permissions_for(guild.me)
        if not all(
            (
                permissions.view_channel,
                permissions.send_messages,
                permissions.read_message_history,
                permissions.add_reactions,
            )
        ):
            raise AnalysisError("Report channel needs view, send, history and reaction permissions")
        for channel_id in self.config.source_channel_ids:
            channel = await self.fetch_channel(channel_id)
            if (
                not isinstance(channel, (discord.TextChannel, discord.Thread))
                or channel.guild.id != guild.id
                or (isinstance(channel, discord.Thread) and channel.is_private())
            ):
                raise AnalysisError("Sources must be explicit text channels or public thread ids")
            permissions = channel.permissions_for(guild.me)
            if not permissions.view_channel or not permissions.read_message_history:
                raise AnalysisError(f"Source {channel_id} needs view and history permissions")
        return report

    def eligible(self, guild_id: int | None, channel_id: int) -> bool:
        return guild_id == self.config.guild_id and channel_id in self.config.source_channel_ids

    def row(self, message: discord.Message) -> dict:
        return {
            "message_id": str(message.id),
            "channel_id": str(message.channel.id),
            "author_hash": hash_author(message.author.id, self.config.author_hash_salt),
            "created_at": utc_iso(message.created_at),
            "content": redact(message.content or ""),
        }

    async def on_message(self, message: discord.Message) -> None:
        if not self.eligible(getattr(message.guild, "id", None), message.channel.id):
            return
        if message.author.bot:
            return
        if isinstance(message.channel, discord.Thread) and message.channel.is_private():
            return
        row = self.row(message)
        if row["content"].strip():
            storage.store_messages(self.connection, [row])

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if not self.eligible(payload.guild_id, payload.channel_id) or "content" not in payload.data:
            return
        # Invalidate previously derived signals even when the new message is empty.
        storage.forget_messages(self.connection, [str(payload.message_id)])
        try:
            channel = await self.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            await self.on_message(message)
        except discord.HTTPException as exc:
            log.warning("edit refresh failed: %s", type(exc).__name__)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if self.eligible(payload.guild_id, payload.channel_id):
            storage.forget_messages(self.connection, [str(payload.message_id)])

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if self.eligible(payload.guild_id, payload.channel_id):
            storage.forget_messages(self.connection, [str(i) for i in payload.message_ids])

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if (
            payload.guild_id != self.config.guild_id
            or payload.channel_id != self.config.report_channel_id
            or str(payload.emoji) not in FEEDBACK_EMOJI
            or payload.member is None
            or payload.member.bot
            or not payload.member.guild_permissions.administrator
        ):
            return
        run_id = storage.run_for_report_message(self.connection, str(payload.message_id))
        if run_id is not None:
            storage.record_feedback(
                self.connection,
                run_id,
                str(payload.emoji),
                hash_author(payload.user_id, self.config.author_hash_salt),
            )

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id != self.config.guild_id:
            return
        run_id = storage.run_for_report_message(self.connection, str(payload.message_id))
        if run_id is not None:
            self.connection.execute(
                "DELETE FROM feedback WHERE run_id = ? AND emoji = ? AND author_hash = ?",
                (
                    run_id,
                    str(payload.emoji),
                    hash_author(payload.user_id, self.config.author_hash_salt),
                ),
            )
            self.connection.commit()

    def _register_commands(self) -> None:
        @self.tree.command(name="seismograph", description="Generate a staff friction report.")
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        @app_commands.checks.has_permissions(administrator=True)
        async def seismograph(interaction: discord.Interaction) -> None:
            if interaction.guild_id != self.config.guild_id:
                return
            await interaction.response.defer(ephemeral=True)
            start, end = analysis_period(
                datetime.now(UTC), self.config.report_timezone, self.config.analysis_days
            )
            await interaction.followup.send(
                f"Report requested for [{start}, {end}). Results or failures appear in the "
                "operator logs; successful reports go to the configured staff channel.",
                ephemeral=True,
            )
            await self.attempt_report("manual", start, end)

        @self.tree.command(
            name="seismograph_optout", description="Delete my local data and opt out."
        )
        @app_commands.guild_only()
        async def optout(interaction: discord.Interaction) -> None:
            if interaction.guild_id != self.config.guild_id:
                return
            storage.opt_out(
                self.connection, hash_author(interaction.user.id, self.config.author_hash_salt)
            )
            await interaction.response.send_message(
                "Your local messages and feedback were deleted; future messages will be excluded. "
                "Ask staff to remove any already-posted reports or provider-retained data.",
                ephemeral=True,
            )

    async def collect_history(self, start: str, end: str) -> int:
        after, before = datetime.fromisoformat(start), datetime.fromisoformat(end)
        scanned = stored = 0
        for channel_id in self.config.source_channel_ids:
            channel = await self.fetch_channel(channel_id)
            batch, seen = [], set()
            async for message in channel.history(
                after=discord.Object(id=discord.utils.time_snowflake(after, high=False) - 1),
                before=before,
                oldest_first=True,
                limit=self.config.max_messages - scanned + 1,
            ):
                scanned += 1
                if scanned > self.config.max_messages:
                    raise AnalysisError(
                        "History exceeds MAX_MESSAGES; no partial report will be sent"
                    )
                if message.author.bot:
                    continue
                row = self.row(message)
                if not row["content"].strip():
                    continue
                seen.add(row["message_id"])
                batch.append(row)
                if len(batch) == 100:
                    stored += storage.store_messages(self.connection, batch)
                    batch.clear()
                    await asyncio.sleep(0)
            stored += storage.store_messages(self.connection, batch)
            existing = storage.messages_between(
                self.connection, start, end, (channel_id,), self.config.max_messages + 1
            )
            storage.forget_messages(
                self.connection, [r["message_id"] for r in existing if r["message_id"] not in seen]
            )
        log.info("history scanned=%d changed=%d", scanned, stored)
        return stored

    def analyze_period(self, kind: str, start: str, end: str, run_id: int):
        connection = storage.connect(self.config.database_path)
        try:
            llm = LLMClient.from_config(self.config)
            return generate_report(
                connection,
                llm,
                kind,
                start,
                end,
                period_label(start, end, self.config.report_timezone),
                self.config.source_channel_ids,
                self.config.max_messages,
                run_id,
            )
        finally:
            connection.close()

    async def publish(self, kind: str, start: str, end: str) -> None:
        if self.report_lock.locked():
            raise AnalysisError("A report is already running; request was not queued")
        async with self.report_lock:
            channel = await self.preflight()
            run_id = storage.start_run(self.connection, kind, start, end)
            sending = False
            try:
                await self.collect_history(start, end)
                _, report, messages, analysis = await asyncio.to_thread(
                    self.analyze_period, kind, start, end, run_id
                )
                for line in analysis.summary_lines():
                    log.info("%s", line)
                channel = await self.preflight()
                # An opt-out, deletion or edit during analysis invalidates its snapshot.
                current = storage.messages_between(
                    self.connection,
                    start,
                    end,
                    self.config.source_channel_ids,
                    self.config.max_messages + 1,
                )
                current_prepared = {m.message_id: m.content for m in prepare(current)}
                if any(current_prepared.get(m.message_id) != m.content for m in messages):
                    self.connection.execute("DELETE FROM signals WHERE run_id = ?", (run_id,))
                    self.connection.commit()
                    raise AnalysisError("Evidence changed during analysis; rerun the report")
                chunks = split_for_discord(render_markdown(report, self.config.guild_id, messages))
                # Persist before sending: a crash or ambiguous timeout must not trigger a repost.
                storage.set_status(self.connection, run_id, "sending")
                sending = True
                first = None
                for chunk in chunks:
                    sent = await channel.send(chunk, suppress_embeds=True)
                    first = first or sent
                    self.connection.execute(
                        "UPDATE runs SET report_message_id = ? WHERE id = ?",
                        (str(first.id), run_id),
                    )
                    self.connection.commit()
                storage.mark_published(self.connection, run_id, str(first.id))
            except InsufficientEvidence:
                storage.set_status(self.connection, run_id, "empty")
                raise
            except Exception:
                storage.set_status(self.connection, run_id, "uncertain" if sending else "failed")
                raise
            for emoji, _ in FEEDBACK_REACTIONS:
                try:
                    await first.add_reaction(emoji)
                except discord.HTTPException:
                    log.warning("report %d published, but reaction setup failed", run_id)
                    break
            log.info("published run=%d messages=%d", run_id, len(chunks))

    async def attempt_report(self, kind: str, start: str, end: str) -> None:
        try:
            await self.publish(kind, start, end)
        except InsufficientEvidence:
            log.info("report %s to %s empty; nothing posted", start, end)
        except (AnalysisError, discord.HTTPException) as exc:
            log.error(
                "report %s to %s failed: %s",
                start,
                end,
                str(exc) if isinstance(exc, AnalysisError) else type(exc).__name__,
            )
        except Exception as exc:
            log.error(
                "report %s to %s failed: %s; inspect run status", start, end, type(exc).__name__
            )

    @tasks.loop(minutes=5)
    async def weekly_check(self) -> None:
        try:
            if not self.report_lock.locked():
                storage.prune(self.connection, self.config.retention_days)
            start, end = scheduled_period(
                datetime.now(UTC),
                self.config.report_timezone,
                self.config.analysis_days,
                self.config.report_weekday,
                self.config.report_hour,
            )
            if not storage.scheduled_run_exists(self.connection, start, end):
                await self.attempt_report("scheduled", start, end)
        except Exception as exc:
            log.error("maintenance failed: %s", type(exc).__name__)

    @weekly_check.before_loop
    async def before_weekly_check(self) -> None:
        await self.wait_until_ready()
