"""Regression coverage for two defects reproduced on PR #2 commit 564a518."""

import asyncio
from datetime import datetime
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from test_offline_pilot import END, START
from test_offline_pilot import pilot as pilot

from seismograph import storage
from seismograph.analysis import AnalysisError
from seismograph.privacy import hash_author


def test_optout_cannot_leave_resurrected_signals_after_preflight_failure(pilot, monkeypatch):
    author = pilot.messages[0].author.id
    excluded = {str(m.id) for m in pilot.messages if m.author.id == author}
    original_save = storage.save_signals

    def save_after_optout(connection, run_id, signals, *, snapshot):
        # Deterministic interleaving: opt-out commits after the worker captured
        # its snapshot but before it saves the derived signals.
        separate = storage.connect(pilot.client.config.database_path)
        try:
            storage.opt_out(separate, hash_author(author, pilot.client.config.author_hash_salt))
        finally:
            separate.close()
        original_save(connection, run_id, signals, snapshot=snapshot)

    monkeypatch.setattr(storage, "save_signals", save_after_optout)
    preflight = pilot.client.preflight
    calls = 0

    async def fail_second_preflight():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AnalysisError("Report permissions changed during analysis")
        return await preflight()

    monkeypatch.setattr(pilot.client, "preflight", fail_second_preflight)
    # The transactional snapshot check now rejects the stale worker before
    # a later permissions failure could bypass publication-time cleanup.
    with pytest.raises(AnalysisError, match="Evidence changed"):
        asyncio.run(pilot.client.publish("manual", START, END))

    assert not pilot.posted
    remaining_messages = {
        row[0] for row in pilot.client.connection.execute("SELECT message_id FROM messages")
    }
    assert not excluded & remaining_messages
    stale_evidence = [
        tuple(row)
        for row in pilot.client.connection.execute(
            "SELECT s.id, e.message_id FROM signals s JOIN evidence e ON e.signal_id = s.id"
        )
        if row["message_id"] in excluded
    ]
    assert not stale_evidence, f"Opted-out evidence was restored: {stale_evidence}"


def test_real_history_iterator_keeps_inclusive_start_boundary(pilot):
    channel = pilot.channels[pilot.client.config.source_channel_ids[0]]
    boundary = SimpleNamespace(**vars(pilot.messages[0]))
    boundary.created_at = datetime.fromisoformat(START)
    boundary.id = discord.utils.time_snowflake(boundary.created_at) + 123
    boundary.content = "Synthetic feedback exactly at the reporting window start."
    pilot.messages.append(boundary)
    by_id = {m.id: m for m in pilot.messages if m.channel.id == channel.id}

    async def logs_from(channel_id, retrieve, *, after=None):
        assert channel_id == channel.id
        ids = sorted(mid for mid in by_id if after is None or mid > after)[:retrieve]
        return [{"id": str(mid)} for mid in reversed(ids)]

    channel._get_channel = AsyncMock(return_value=channel)
    channel._state = SimpleNamespace(
        http=SimpleNamespace(logs_from=logs_from),
        create_message=lambda *, channel, data: by_id[int(data["id"])],
    )
    # Exercise discord.py's actual datetime-to-snowflake conversion rather
    # than reproducing its behavior in a mock history implementation.
    channel.history = MethodType(discord.abc.Messageable.history, channel)

    async def exercise():
        await pilot.client.on_message(boundary)
        assert pilot.client.connection.execute(
            "SELECT 1 FROM messages WHERE message_id = ?", (str(boundary.id),)
        ).fetchone()
        await pilot.client.collect_history(START, END)

    asyncio.run(exercise())
    assert pilot.client.connection.execute(
        "SELECT 1 FROM messages WHERE message_id = ?", (str(boundary.id),)
    ).fetchone(), "History reconciliation deleted a valid start-boundary message"
