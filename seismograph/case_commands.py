"""Staff-only investigation commands. No autonomous external actions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.utils import escape_markdown

from . import (
    case_history,
    case_reviews,
    cases,
    exposure,
    exposure_commands,
    review_commands,
    storage,
)
from .analysis import AnalysisError, LLMClient, prepare
from .pipeline import utc_iso
from .report import split_for_discord

log = logging.getLogger(__name__)


def register(client) -> None:
    review_commands.register(client)
    exposure_commands.register(client)

    @client.tree.command(
        name="seismograph_changes",
        description="Compare the last two retained case revisions without a model call.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def changes(interaction: discord.Interaction, case_id: int) -> None:
        if interaction.guild_id != client.config.guild_id:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if client.report_lock.locked():
                raise AnalysisError("Another report or investigation is running; try later")
            async with client.report_lock:
                await client.preflight()
                comparison = case_history.compare(
                    client.connection, case_id, client.config.source_channel_ids
                )
                text = case_history.render(comparison, client.config.guild_id)
                review = case_reviews.view(
                    client.connection, case_id, client.config.source_channel_ids
                )
                text += case_reviews.banner(review)
                segments = (
                    exposure.view(client.connection, case_id, client.config.source_channel_ids)
                    if review["marker"][0]
                    else None
                )
                if segments:
                    text += exposure.banner(segments)
                for chunk in split_for_discord(text):
                    case_history.assert_current(client.connection, comparison)
                    case_reviews.assert_current(client.connection, review)
                    if segments:
                        exposure.assert_current(client.connection, segments)
                    await interaction.followup.send(
                        chunk,
                        ephemeral=True,
                        suppress_embeds=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
        except AnalysisError as exc:
            await interaction.followup.send(
                str(exc), ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            log.exception("Discord access failed during a case comparison")
            await interaction.followup.send(
                "Discord access failed; inspect operator logs.", ephemeral=True
            )

    @client.tree.command(name="seismograph_signals", description="List recent report signal IDs.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def signals(interaction: discord.Interaction) -> None:
        if interaction.guild_id != client.config.guild_id:
            return
        scope = ",".join("?" for _ in client.config.source_channel_ids)
        rows = client.connection.execute(
            "SELECT s.id, s.title FROM signals s JOIN runs r ON r.id = s.run_id"
            " WHERE r.published = 1 AND EXISTS (SELECT 1 FROM evidence WHERE signal_id = s.id)"
            " AND NOT EXISTS (SELECT 1 FROM evidence e LEFT JOIN messages m"
            " ON m.message_id = e.message_id WHERE e.signal_id = s.id"
            f" AND (m.message_id IS NULL OR m.channel_id NOT IN ({scope})))"
            " ORDER BY r.id DESC, s.tremor_score DESC LIMIT 10",
            tuple(str(i) for i in client.config.source_channel_ids),
        ).fetchall()
        text = "\n".join(f"{r['id']}: {escape_markdown(r['title'])}" for r in rows)
        await interaction.response.send_message(
            text or "No retained published signals yet.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @client.tree.command(
        name="seismograph_case", description="Investigate a signal, refresh a case, or read it."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def case(
        interaction: discord.Interaction,
        signal_id: int = 0,
        case_id: int = 0,
        refresh: bool = False,
    ) -> None:
        if interaction.guild_id != client.config.guild_id:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            if client.report_lock.locked():
                raise AnalysisError("Another report or investigation is running; try later")
            async with client.report_lock:
                await client.preflight()
                if refresh and (not case_id or signal_id):
                    raise AnalysisError("Refresh needs a case_id and no signal_id")
                if signal_id or refresh:
                    seed_ids = set()
                    expected_revision = None
                    if refresh:
                        prior, previous, retained = cases.load(client.connection, case_id)
                        expected_revision = prior["revision_id"]
                        if any(
                            int(m.channel_id) not in client.config.source_channel_ids
                            for m in retained
                        ):
                            raise AnalysisError(
                                "Case includes a source outside the current allowlist"
                            )
                        now = datetime.now(UTC)
                        row = {
                            "title": previous["title"],
                            "period_start": utc_iso(
                                now - timedelta(days=client.config.analysis_days)
                            ),
                            "period_end": utc_iso(now),
                        }
                        await client.collect_history(row["period_start"], row["period_end"])
                        current, _, _ = cases.load(client.connection, case_id)
                        if current["revision_id"] != expected_revision:
                            raise AnalysisError("Case evidence changed during collection")
                    else:
                        row = client.connection.execute(
                            "SELECT s.title, r.period_start, r.period_end FROM signals s"
                            " JOIN runs r ON s.run_id = r.id WHERE s.id = ? AND r.published = 1",
                            (signal_id,),
                        ).fetchone()
                        if not row:
                            raise AnalysisError("Choose an ID from /seismograph_signals")
                        seed_ids = {
                            r[0]
                            for r in client.connection.execute(
                                "SELECT message_id FROM evidence WHERE signal_id = ?", (signal_id,)
                            )
                        }
                    rows = storage.messages_between(
                        client.connection,
                        row["period_start"],
                        row["period_end"],
                        client.config.source_channel_ids,
                        client.config.max_messages + 1,
                    )
                    if len(rows) > client.config.max_messages:
                        raise AnalysisError("Stored period exceeds MAX_MESSAGES")
                    prepared = prepare(rows)
                    if refresh:
                        if not prepared:
                            raise AnalysisError("No recent evidence; no new case status was saved")
                        seed_ids = {prepared[-1].message_id}
                    evidence = cases.context(prepared, seed_ids)
                    config = replace(
                        client.config, max_llm_requests=min(6, client.config.max_llm_requests)
                    )
                    model = LLMClient.from_config(config, schema=cases.SCHEMA)
                    payload = await asyncio.to_thread(
                        cases.investigate, model, row["title"], evidence
                    )
                    await client.preflight()
                    case_id = cases.save(
                        client.connection,
                        payload,
                        evidence,
                        case_id,
                        expected_revision=expected_revision,
                    )
                _, _, retained = cases.load(client.connection, case_id)
                if any(int(m.channel_id) not in client.config.source_channel_ids for m in retained):
                    raise AnalysisError("Case includes a source outside the current allowlist")
                text = cases.render(client.connection, case_id, client.config.guild_id)
                review = case_reviews.view(
                    client.connection, case_id, client.config.source_channel_ids
                )
                text += case_reviews.banner(review)
                segments = (
                    exposure.view(client.connection, case_id, client.config.source_channel_ids)
                    if review["marker"][0]
                    else None
                )
                if segments:
                    text += exposure.banner(segments)
                for chunk in split_for_discord(text):
                    case_reviews.assert_current(client.connection, review)
                    if segments:
                        exposure.assert_current(client.connection, segments)
                    await interaction.followup.send(
                        chunk,
                        ephemeral=True,
                        suppress_embeds=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
        except AnalysisError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except discord.HTTPException:
            log.exception("Discord access failed during a case command")
            await interaction.followup.send(
                "Discord access failed; inspect operator logs.", ephemeral=True
            )

    @client.tree.command(
        name="seismograph_release", description="Mark an intervention; this does not close a case."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def release(interaction: discord.Interaction, case_id: int, note: str) -> None:
        if interaction.guild_id != client.config.guild_id:
            return
        try:
            cases.mark_release(client.connection, case_id, note)
            text = (
                "Intervention recorded, not marked resolved. Use /seismograph_case with this "
                "case_id and refresh:true to assess follow-up. A new marker replaces the old one."
            )
        except AnalysisError as exc:
            text = str(exc)
        await interaction.response.send_message(text, ephemeral=True)
