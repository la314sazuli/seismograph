"""Private administrator controls for revision-bound human review."""

from __future__ import annotations

import logging
from typing import Literal

import discord
from discord import app_commands

from . import case_reviews
from .analysis import AnalysisError
from .privacy import hash_author
from .report import split_for_discord

log = logging.getLogger(__name__)


async def _respond(client, interaction, case_id, operation=None, page=1, *, module=case_reviews):
    if interaction.guild_id != client.config.guild_id:
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if client.report_lock.locked():
            raise AnalysisError("Another report or investigation is running; try later")
        async with client.report_lock:
            await client.preflight()
            prefix = operation() if operation else ""
            review = module.view(client.connection, case_id, client.config.source_channel_ids)
            text = prefix + module.render(review, client.config.guild_id, page)
            for chunk in split_for_discord(text):
                module.assert_current(client.connection, review)
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
        log.exception("Discord access failed during staff review")
        await interaction.followup.send(
            "Discord access failed. A submitted annotation may already be saved; "
            "read its current review or exposure view before retrying.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def register(client):
    @client.tree.command(
        name="seismograph_reviews", description="Read staff corrections and preview."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def reviews(interaction: discord.Interaction, case_id: int, page: int = 1):
        await _respond(client, interaction, case_id, page=page)

    @client.tree.command(
        name="seismograph_correct",
        description="Annotate one observation; preserve the model output.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def correct(
        interaction: discord.Interaction,
        case_id: int,
        revision_key: str,
        message_id: str,
        outcome: Literal[
            "failure", "success", "counterexample", "workaround", "unclear", "exclude"
        ],
        reason: str,
    ):
        def operation():
            cid = case_reviews.correct(
                client.connection,
                case_id,
                revision_key,
                message_id,
                outcome,
                reason,
                hash_author(interaction.user.id, client.config.author_hash_salt),
                client.config.source_channel_ids,
            )
            return f"Saved correction {cid}; original model output is unchanged.\n\n"

        await _respond(client, interaction, case_id, operation)

    @client.tree.command(
        name="seismograph_withdraw", description="Withdraw a correction with a retained reason."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def withdraw(
        interaction: discord.Interaction,
        case_id: int,
        revision_key: str,
        correction_id: int,
        reason: str,
    ):
        def operation():
            case_reviews.withdraw(
                client.connection,
                case_id,
                revision_key,
                correction_id,
                reason,
                hash_author(interaction.user.id, client.config.author_hash_salt),
                client.config.source_channel_ids,
            )
            return f"Withdrew correction {correction_id}; audit reason retained.\n\n"

        await _respond(client, interaction, case_id, operation)
