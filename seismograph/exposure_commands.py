"""Private staff assessments of patch exposure, without model calls."""

from typing import Literal

import discord
from discord import app_commands

from . import exposure
from .privacy import hash_author
from .review_commands import _respond


def register(client):
    @client.tree.command(
        name="seismograph_exposures", description="Read quote-backed patch exposure segments."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def exposures(interaction: discord.Interaction, case_id: int, page: int = 1):
        await _respond(client, interaction, case_id, page=page, module=exposure)

    @client.tree.command(
        name="seismograph_exposure", description="Assess one report's exposure to the marked patch."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def assess(
        interaction: discord.Interaction,
        case_id: int,
        scope_key: str,
        message_id: str,
        state: Literal["received", "not_received", "unknown"],
        reason: str,
        quote: str = "",
    ):
        def operation():
            eid = exposure.assess(
                client.connection,
                case_id,
                scope_key,
                message_id,
                state,
                quote,
                reason,
                hash_author(interaction.user.id, client.config.author_hash_salt),
                client.config.source_channel_ids,
            )
            return f"Saved exposure assessment {eid}; original model output is unchanged.\n\n"

        await _respond(client, interaction, case_id, operation, module=exposure)
