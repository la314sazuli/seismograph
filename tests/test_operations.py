from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from conftest import iso, message, signal
from test_storage import rows
from test_validation import FakeClient, candidate

from seismograph import analysis, storage
from seismograph.analysis import AnalysisError, InvalidOutput, LLMClient, analyze
from seismograph.bot import build_client
from seismograph.config import ConfigError, load_config
from seismograph.pipeline import generate_report, scheduled_period
from seismograph.report import InsufficientEvidence, build_report


@pytest.mark.parametrize("raw", [None, [], {"wrong": []}])
def test_malformed_schema_gets_one_repair(raw):
    client = FakeClient([raw, {"signals": []}])
    assert analyze(client, [message("101", "a", 0)], "test").signals == []
    assert len(client.calls) == 2


def test_malformed_json_gets_one_repair():
    class BadJSON(FakeClient):
        def complete_json(self, system_prompt, user_prompt):
            if not self.calls:
                self.calls.append((system_prompt, user_prompt))
                raise InvalidOutput("not JSON")
            return super().complete_json(system_prompt, user_prompt)

    client = BadJSON([{"signals": []}])
    assert analyze(client, [message("101", "a", 0)], "test").repairs_recovered == 1


def test_mixed_valid_and_invalid_candidates_do_not_publish_partial_output():
    raw = {"signals": [candidate(), candidate(supporting_message_ids=["invented"])]}
    client = FakeClient([raw, raw])
    with pytest.raises(AnalysisError, match="entire report stopped"):
        analyze(client, [message(str(i), str(i), i) for i in (101, 102, 103)], "test")
    assert len(client.calls) == 2


@pytest.mark.parametrize("groups", [[[0]], [[0, 0]], [[0, 99]], [[True, 1]], []])
def test_merge_rejects_lost_duplicated_or_invented_candidates(groups):
    messages = [message(str(i), str(i), i) for i in (101, 102, 103)]
    signals, _ = analysis.validate_signals(
        {
            "signals": [
                candidate(supporting_message_ids=["101"], representative_message_ids=["101"]),
                candidate(
                    title="Filters vanish",
                    supporting_message_ids=["102", "103"],
                    representative_message_ids=["102"],
                ),
            ]
        },
        messages,
    )
    run = analysis.AnalysisRun(signals=signals, rejections=[])
    client = FakeClient([{"groups": groups}, {"groups": groups}])
    with pytest.raises(AnalysisError, match="merge invalid"):
        analysis._merge_with_model(client, run, messages)
    assert len(client.calls) == 2


def test_model_merge_unions_evidence_and_distinct_users_without_exposing_authors():
    messages = [message(str(i), "a" if i != 103 else "b", i) for i in (101, 102, 103)]
    signals, _ = analysis.validate_signals(
        {
            "signals": [
                candidate(
                    supporting_message_ids=["101", "102"], representative_message_ids=["101"]
                ),
                candidate(
                    title="Filters vanish",
                    supporting_message_ids=["103"],
                    representative_message_ids=["103"],
                ),
            ]
        },
        messages,
    )
    client = FakeClient([{"groups": [[0, 1]]}])
    result = analysis._merge_with_model(
        client, analysis.AnalysisRun(signals=signals, rejections=[]), messages
    )
    assert len(result) == 1
    assert result[0].distinct_users == 2 and result[0].message_count == 3
    assert set(result[0].supporting_message_ids) == {"101", "102", "103"}
    assert "author_hash" not in client.calls[0][1]
    assert "supporting_message_ids" not in client.calls[0][1]


def test_http_request_budget_includes_transient_retries(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(analysis.time, "sleep", lambda _: None)
    client = LLMClient("https://llm.example.invalid/v1", "test", "test", max_requests=2)
    with pytest.raises(AnalysisError, match="MAX_LLM_REQUESTS"):
        client.complete_json("system", "data")
    assert len(calls) == 2


def test_permanent_http_errors_are_not_retried(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(401, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(AnalysisError, match="401"):
        LLMClient("https://llm.example.invalid/v1", "test", "test").complete_json("s", "u")
    assert len(calls) == 1


def test_oversized_message_is_not_silently_truncated():
    with pytest.raises(AnalysisError, match="no content was truncated"):
        analysis.batch([message("1", "a", 0, "x" * 25_000)])


def test_scheduled_period_catches_up_and_stays_stable():
    monday = datetime(2026, 9, 14, 9, tzinfo=UTC)
    thursday = datetime(2026, 9, 17, 22, tzinfo=UTC)
    assert scheduled_period(monday, "UTC", 7) == scheduled_period(thursday, "UTC", 7)
    assert scheduled_period(monday.replace(hour=8), "UTC", 7) != scheduled_period(monday, "UTC", 7)


def test_schedule_uses_local_dst_boundaries():
    start, end = scheduled_period(datetime(2026, 3, 30, 9, tzinfo=UTC), "Europe/Belgrade", 7)
    assert start == "2026-03-22T23:00:00+00:00"
    assert end == "2026-03-29T22:00:00+00:00"


@pytest.mark.parametrize(
    "key,value",
    [
        ("SOURCE_CHANNEL_IDS", "-1"),
        ("SOURCE_CHANNEL_IDS", "200000000000000099"),
        ("LLM_BASE_URL", "http://remote.example.invalid/v1"),
        ("LLM_BASE_URL", "https://user:password@example.invalid/v1"),
        ("LLM_BASE_URL", "https://example.invalid/v1?token=secret"),
        ("REPORT_WEEKDAY", "7"),
        ("REPORT_HOUR", "24"),
        ("MAX_MESSAGES", "0"),
        ("MAX_LLM_REQUESTS", "0"),
        ("RETENTION_DAYS", "1"),
    ],
)
def test_unsafe_configuration_is_rejected(env, key, value):
    with pytest.raises(ConfigError):
        load_config({**env, key: value})


def test_storage_scope_limit_edits_and_optout(tmp_path):
    db = storage.connect(str(tmp_path / "test.db"))
    try:
        data = rows()
        data[1]["channel_id"] = "99"
        storage.store_messages(db, data)
        assert len(storage.messages_between(db, iso(-1), iso(100), (99,))) == 1
        assert len(storage.messages_between(db, iso(-1), iso(100), limit=1)) == 1
        data[0]["content"] = "Updated report"
        storage.store_messages(db, [data[0]])
        assert storage.messages_between(db, iso(-1), iso(100))[0]["content"] == "Updated report"
        storage.opt_out(db, "user-0")
        assert storage.store_messages(db, [data[0]]) == 0
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    finally:
        db.close()


def test_retention_prunes_reports_evidence_and_feedback(tmp_path):
    db = storage.connect(str(tmp_path / "test.db"))
    try:
        storage.store_messages(db, rows())
        run = storage.start_run(db, "manual", iso(0), iso(168))
        storage.save_signals(db, run, [signal()])
        storage.record_feedback(db, run, "test", "user-0")
        storage.prune(db, 30, datetime(2027, 1, 1, tzinfo=UTC))
        for table in ("messages", "runs", "signals", "evidence", "feedback"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        db.close()


def test_overlapping_manual_report_is_not_a_weekly_baseline(tmp_path):
    db = storage.connect(str(tmp_path / "test.db"))
    try:
        old = storage.start_run(db, "manual", iso(24), iso(192))
        storage.save_signals(db, old, [signal()])
        storage.mark_published(db, old, "99")
        current = storage.start_run(db, "scheduled", iso(168), iso(336))
        assert storage.previous_message_counts(db, current) == {}
    finally:
        db.close()


def test_message_cap_prevents_any_llm_call(tmp_path):
    db = storage.connect(str(tmp_path / "test.db"))
    try:
        storage.store_messages(db, rows())
        client = FakeClient([])
        with pytest.raises(AnalysisError, match="MAX_MESSAGES"):
            generate_report(db, client, "manual", iso(0), iso(168), "test", max_messages=2)
        assert not client.calls
    finally:
        db.close()


def make_bot(env, tmp_path):
    return build_client(replace(load_config(env), database_path=str(tmp_path / "bot.db")))


def test_bot_does_not_request_members_presences_or_dms(env, tmp_path):
    client = make_bot(env, tmp_path)
    assert not client.intents.members and not client.intents.presences
    assert not client.intents.dm_messages and not client.intents.dm_reactions
    assert client.intents.message_content
    assert not client.allowed_mentions.everyone and not client.allowed_mentions.users
    assert client._connection.max_messages is None
    assert not client._connection.member_cache_flags.value
    asyncio.run(client.close())


def test_live_collection_ignores_dms_other_guilds_channels_and_bots(env, tmp_path):
    client = make_bot(env, tmp_path)

    async def exercise():
        for guild, channel, bot in [
            (None, client.config.source_channel_ids[0], False),
            (1, client.config.source_channel_ids[0], False),
            (client.config.guild_id, 1, False),
            (client.config.guild_id, client.config.source_channel_ids[0], True),
        ]:
            await client.on_message(
                SimpleNamespace(
                    guild=None if guild is None else SimpleNamespace(id=guild),
                    channel=SimpleNamespace(id=channel),
                    author=SimpleNamespace(bot=bot),
                )
            )
        assert client.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        await client.close()

    asyncio.run(exercise())


def test_slow_analysis_does_not_block_event_loop(env, tmp_path, monkeypatch):
    client = make_bot(env, tmp_path)
    monkeypatch.setattr(client, "preflight", AsyncMock())
    monkeypatch.setattr(client, "collect_history", AsyncMock())

    def slow(*_):
        time.sleep(0.12)
        raise InsufficientEvidence()

    monkeypatch.setattr(client, "analyze_period", slow)

    async def exercise():
        task = asyncio.create_task(client.attempt_report("scheduled", iso(0), iso(168)))
        ticks = 0
        while not task.done():
            await asyncio.sleep(0.01)
            ticks += 1
        await task
        assert ticks >= 5
        assert client.connection.execute("SELECT status FROM runs").fetchone()[0] == "empty"
        await client.close()

    asyncio.run(exercise())


def test_history_flushes_small_batches_and_aborts_instead_of_sampling(env, tmp_path, monkeypatch):
    client = make_bot(env, tmp_path)
    client.config = replace(client.config, max_messages=101)

    class Channel:
        async def history(self, **kwargs):
            assert kwargs["limit"] == 102
            for i in range(102):
                yield SimpleNamespace(
                    id=1000 + i,
                    channel=SimpleNamespace(id=client.config.source_channel_ids[0]),
                    author=SimpleNamespace(id=i, bot=False),
                    created_at=datetime(2026, 8, 3, tzinfo=UTC),
                    content="A useful problem report",
                )

    monkeypatch.setattr(client, "fetch_channel", AsyncMock(return_value=Channel()))

    async def exercise():
        with pytest.raises(AnalysisError, match="no partial report"):
            await client.collect_history(iso(0), iso(168))
        assert client.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 100
        await client.close()

    asyncio.run(exercise())


def test_large_merge_requests_are_bounded():
    messages = [message(str(i), str(i), i) for i in range(1000)]
    signals = [
        signal(
            title=f"Distinct issue {i}",
            supporting_message_ids=(str(i),),
            representative_message_ids=(str(i),),
        )
        for i in range(1000)
    ]

    class PartitionClient(FakeClient):
        def complete_json(self, system, user):
            self.calls.append((system, user))
            candidates = json.loads(user.removeprefix("Candidate signals:\n"))["signals"]
            assert len(candidates) <= 20
            assert len(user) < 24_000
            return {"groups": [[s["id"]] for s in candidates]}

    client = PartitionClient([])
    merged = analysis._merge_with_model(
        client, analysis.AnalysisRun(signals=signals, rejections=[]), messages
    )
    assert len(merged) == 1000 and len(client.calls) == 50
    assert {i for s in merged for i in s.supporting_message_ids} == {str(i) for i in range(1000)}


def test_schema_v1_migration_preserves_data_and_marks_old_unpublished_run_uncertain(tmp_path):
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript(storage.SCHEMA)
    db.execute("PRAGMA user_version = 1")
    db.executemany(
        "INSERT INTO messages VALUES "
        "(:message_id, :channel_id, :author_hash, :created_at, :content)",
        rows(),
    )
    storage.start_run(db, "scheduled", iso(0), iso(168))
    db.close()
    db = storage.connect(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3
        assert db.execute("SELECT status FROM runs").fetchone()[0] == "uncertain"
    finally:
        db.close()


def publication_fixture(env, tmp_path, monkeypatch):
    client = make_bot(env, tmp_path)
    data = rows()
    storage.store_messages(client.connection, data)
    messages = analysis.prepare(data)
    item = signal(
        supporting_message_ids=("1000", "1001", "1002"),
        representative_message_ids=("1000", "1001", "1002"),
    )
    report = build_report([item], messages, "test")
    sent = SimpleNamespace(id=900, add_reaction=AsyncMock())
    channel = SimpleNamespace(send=AsyncMock(return_value=sent))
    monkeypatch.setattr(client, "preflight", AsyncMock(return_value=channel))
    monkeypatch.setattr(client, "collect_history", AsyncMock())
    monkeypatch.setattr(
        client,
        "analyze_period",
        lambda *args: (
            args[-1],
            report,
            messages,
            analysis.AnalysisRun(signals=[item], rejections=[]),
        ),
    )
    return client, channel, sent


def test_successful_publication_is_recorded_before_reaction_failures(env, tmp_path, monkeypatch):
    client, channel, sent = publication_fixture(env, tmp_path, monkeypatch)
    import discord

    sent.add_reaction.side_effect = discord.HTTPException(
        SimpleNamespace(status=403, reason="Forbidden"), "missing reaction permission"
    )

    async def exercise():
        await client.publish("scheduled", iso(0), iso(168))
        row = client.connection.execute("SELECT status, report_message_id FROM runs").fetchone()
        assert tuple(row) == ("published", "900")
        assert channel.send.await_count >= 1
        assert sent.add_reaction.await_count == 1
        await client.close()

    asyncio.run(exercise())


def test_ambiguous_send_failure_is_not_retried(env, tmp_path, monkeypatch):
    client, channel, _ = publication_fixture(env, tmp_path, monkeypatch)
    channel.send.side_effect = TimeoutError("delivery unknown")

    async def exercise():
        with pytest.raises(TimeoutError):
            await client.publish("scheduled", iso(0), iso(168))
        assert client.connection.execute("SELECT status FROM runs").fetchone()[0] == "uncertain"
        with pytest.raises(sqlite3.IntegrityError):
            await client.publish("scheduled", iso(0), iso(168))
        assert channel.send.await_count == 1
        await client.close()

    asyncio.run(exercise())


def test_evidence_deleted_during_analysis_prevents_publication(env, tmp_path, monkeypatch):
    client, channel, _ = publication_fixture(env, tmp_path, monkeypatch)
    original = client.analyze_period

    def deleted(*args):
        db = storage.connect(client.config.database_path)
        storage.forget_messages(db, ["1000"])
        db.close()
        return original(*args)

    monkeypatch.setattr(client, "analyze_period", deleted)

    async def exercise():
        with pytest.raises(AnalysisError, match="Evidence changed"):
            await client.publish("scheduled", iso(0), iso(168))
        assert channel.send.await_count == 0
        assert client.connection.execute("SELECT status FROM runs").fetchone()[0] == "failed"
        await client.close()

    asyncio.run(exercise())


def test_unreadable_source_stops_before_analysis(env, tmp_path, monkeypatch):
    client, channel, _ = publication_fixture(env, tmp_path, monkeypatch)
    monkeypatch.setattr(client, "preflight", AsyncMock(side_effect=AnalysisError("source denied")))

    async def exercise():
        with pytest.raises(AnalysisError, match="source denied"):
            await client.publish("scheduled", iso(0), iso(168))
        assert channel.send.await_count == 0
        assert client.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        await client.close()

    asyncio.run(exercise())


def test_concurrent_report_is_rejected_not_queued(env, tmp_path):
    client = make_bot(env, tmp_path)

    async def exercise():
        async with client.report_lock:
            with pytest.raises(AnalysisError, match="already running"):
                await client.publish("manual", iso(0), iso(168))
        await client.close()

    asyncio.run(exercise())
