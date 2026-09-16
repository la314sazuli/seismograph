from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import httpx
import pytest
from test_offline_pilot import END, START
from test_offline_pilot import pilot as pilot

from seismograph import analysis, bot, case_commands, cases, research, storage
from seismograph.analysis import AnalysisError, InvalidOutput, LLMClient
from seismograph.case_demo import RELEASE, run, scenario
from seismograph.config import ConfigError, load_config


@pytest.fixture
def case_data():
    return scenario()


@pytest.fixture
def db(case_data):
    connection = storage.connect(":memory:")
    storage.store_messages(connection, [asdict(m) for m in case_data[0]])
    yield connection
    connection.close()


def test_recorded_case_is_valid_and_demo_is_network_free(monkeypatch, case_data):
    monkeypatch.setattr(httpx, "post", Mock(side_effect=AssertionError("Unexpected network")))
    assert cases.validate(case_data[1], case_data[0]) == case_data[1]
    output = run()
    assert "Continued failures reported" in output
    assert "silence is not success" in output
    assert "zero retained case revisions" in output


@pytest.mark.parametrize(
    "field,value",
    [
        ("message_id", "invented"),
        ("message_id", []),
        ("quote", "A fabricated source quote"),
        ("quote", ""),
        ("outcome", "resolved"),
        ("outcome", []),
        ("condition", None),
    ],
)
def test_invalid_observations_fail_closed(case_data, field, value):
    messages, payload = case_data
    payload["observations"][0][field] = value
    with pytest.raises(InvalidOutput):
        cases.validate(payload, messages)


@pytest.mark.parametrize(
    "field,value",
    [
        ("supporting_ids", []),
        ("supporting_ids", ["invented"]),
        ("supporting_ids", [{}]),
        ("contradicting_ids", None),
        ("falsification_test", ""),
        ("explanation", "x" * 251),
    ],
)
def test_invalid_hypotheses_fail_closed(case_data, field, value):
    messages, payload = case_data
    payload["hypotheses"][0][field] = value
    with pytest.raises(InvalidOutput):
        cases.validate(payload, messages)


def test_same_observation_cannot_support_and_contradict(case_data):
    messages, payload = case_data
    payload["hypotheses"][0]["contradicting_ids"] = [payload["hypotheses"][0]["supporting_ids"][0]]
    with pytest.raises(InvalidOutput, match="disjoint"):
        cases.validate(payload, messages)


@pytest.mark.parametrize(
    "field,value",
    [
        ("observations", None),
        ("hypotheses", [None]),
        ("title", "x" * 121),
        ("next_question", []),
    ],
)
def test_invalid_root_fields_fail_closed(case_data, field, value):
    messages, payload = case_data
    payload[field] = value
    with pytest.raises(InvalidOutput):
        cases.validate(payload, messages)


def test_duplicate_observations_rejected(case_data):
    messages, payload = case_data
    payload["observations"].append(payload["observations"][0])
    with pytest.raises(InvalidOutput, match="duplicate"):
        cases.validate(payload, messages)


def test_context_preserves_all_seed_evidence(case_data):
    messages, _ = case_data
    chosen = cases.context(messages, {messages[0].message_id})
    assert chosen == messages
    with pytest.raises(AnalysisError, match="unavailable"):
        cases.context(messages, {"absent"})
    with pytest.raises(AnalysisError, match="budget"):
        cases.context([replace(messages[0], content="x" * 25000)], {messages[0].message_id})


def test_case_gets_only_one_repair_and_no_author_hashes(case_data):
    messages, payload = case_data
    model = SimpleNamespace(complete_json=Mock(side_effect=[{}, payload]))
    assert cases.investigate(model, "test case", messages) == payload
    assert model.complete_json.call_count == 2
    for call in model.complete_json.call_args_list:
        assert not any(m.author_hash in call.args[1] for m in messages)
    model.complete_json = Mock(return_value={})
    with pytest.raises(AnalysisError, match="one repair"):
        cases.investigate(model, "test case", messages)
    assert model.complete_json.call_count == 2


def test_case_revisions_preserve_identity_and_marker(db, case_data):
    messages, payload = case_data
    case_id = cases.save(db, payload, messages)
    cases.mark_release(db, case_id, "patch", RELEASE)
    assert (
        cases.save(db, {**payload, "title": "Updated investigation"}, messages, case_id) == case_id
    )
    case, saved, _ = cases.load(db, case_id)
    assert saved["title"] == "Updated investigation"
    assert case["intervention_at"] == RELEASE
    assert db.execute("SELECT COUNT(*) FROM case_revisions").fetchone()[0] == 2


@pytest.mark.parametrize("mutation", ["delete", "edit", "optout", "prune"])
def test_evidence_changes_invalidate_all_affected_case_history(db, case_data, mutation):
    messages, payload = case_data
    case_id = cases.save(db, payload, messages)
    cases.save(db, payload, messages, case_id)
    if mutation == "delete":
        storage.forget_messages(db, [messages[0].message_id])
    elif mutation == "edit":
        storage.store_messages(db, [{**asdict(messages[0]), "content": "Corrected report text."}])
    elif mutation == "optout":
        storage.opt_out(db, messages[0].author_hash)
    else:
        from datetime import UTC, datetime

        storage.prune(db, 1, datetime(2026, 8, 10, tzinfo=UTC))
    assert db.execute("SELECT COUNT(*) FROM case_revisions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM case_inputs").fetchone()[0] == 0
    with pytest.raises(AnalysisError, match="No retained"):
        cases.load(db, case_id)


def test_uncited_context_also_invalidates_case(db, case_data):
    messages, payload = case_data
    payload = {
        "title": "Insufficient evidence",
        "observations": [],
        "hypotheses": [],
        "next_question": "Can staff reproduce this?",
    }
    case_id = cases.save(db, payload, messages)
    storage.forget_messages(db, [messages[-1].message_id])
    with pytest.raises(AnalysisError, match="No retained"):
        cases.load(db, case_id)


def test_deletion_never_falls_back_to_an_older_case_revision(db, case_data):
    messages, _ = case_data
    payload = {
        "title": "Selected evidence",
        "observations": [],
        "hypotheses": [],
        "next_question": "Can staff reproduce this?",
    }
    affected = cases.save(db, payload, messages[:1])
    cases.save(db, payload, messages[-1:], affected)
    independent = cases.save(db, payload, messages[2:3])
    storage.forget_messages(db, [messages[-1].message_id])
    with pytest.raises(AnalysisError, match="No retained"):
        cases.load(db, affected)
    assert cases.load(db, independent)[1] == payload


def test_v2_database_migrates_without_losing_message_data(tmp_path, case_data):
    import sqlite3

    path = str(tmp_path / "migration.db")
    old = sqlite3.connect(path)
    old.executescript(
        storage.SCHEMA
        + "ALTER TABLE runs ADD COLUMN status TEXT NOT NULL DEFAULT 'analyzing';"
        + "CREATE TABLE optouts (author_hash TEXT PRIMARY KEY);"
        + "CREATE INDEX messages_channel_time ON messages(channel_id, created_at);"
        + "PRAGMA user_version = 2;"
    )
    storage.store_messages(old, [asdict(m) for m in case_data[0]])
    old.close()
    migrated = storage.connect(path)
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 3
        case_id = cases.save(migrated, case_data[1], case_data[0])
        assert cases.load(migrated, case_id)[1] == case_data[1]
        assert not migrated.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        migrated.close()


def test_stale_case_snapshot_cannot_resurrect_deleted_evidence(db, case_data):
    messages, payload = case_data
    storage.opt_out(db, messages[0].author_hash)
    with pytest.raises(AnalysisError, match="Evidence changed"):
        cases.save(db, payload, messages)
    assert db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
    assert not db.in_transaction


def test_refresh_cannot_restore_title_derived_from_deleted_prior_revision(db, case_data):
    messages, _ = case_data
    payload = {
        "title": "Derived from old evidence",
        "observations": [],
        "hypotheses": [],
        "next_question": "What changed?",
    }
    case_id = cases.save(db, payload, messages[:1])
    revision = cases.load(db, case_id)[0]["revision_id"]
    storage.forget_messages(db, [messages[0].message_id])
    # The fresh message snapshot is intact, but the old case title was another
    # model input, so persistence must still fail.
    with pytest.raises(AnalysisError, match="Case evidence changed"):
        cases.save(db, payload, messages[-1:], case_id, expected_revision=revision)
    assert db.execute("SELECT COUNT(*) FROM case_revisions").fetchone()[0] == 0


def test_case_rollback_does_not_leave_partial_revision(db, case_data):
    messages, payload = case_data
    with pytest.raises(AnalysisError, match="does not exist"):
        cases.save(db, payload, messages, 123)
    assert db.execute("SELECT COUNT(*) FROM case_revisions").fetchone()[0] == 0
    assert not db.in_transaction


def test_marker_does_not_automatically_resolve_or_infer_release_adoption(db, case_data):
    messages, payload = case_data
    case_id = cases.save(db, payload, messages)
    assert cases.verification(payload, messages, None)["status"] == "No intervention recorded"
    cases.mark_release(db, case_id, "Patch", RELEASE)
    text = cases.render(db, case_id, 100000000000000001)
    assert "Continued failures reported" in text
    assert "actual release adoption is not verified" in text
    assert f"Last analyzed: {cases.load(db, case_id)[0]['analyzed_at']}" in text
    assert "saved snapshot, not a live monitor" in text
    assert f"Selected evidence window: {messages[0].created_at}" in text
    assert not any(m.author_hash in text for m in messages)


def test_silence_is_not_success(case_data):
    messages, payload = case_data
    result = cases.verification(payload, messages, "2026-09-01T00:00:00+00:00")
    assert result["status"] == "Insufficient follow-up; silence is not success"
    assert result["success_authors"] == result["failure_authors"] == 0


def test_success_requires_distinct_reporters_and_failure_wins(case_data):
    messages, payload = case_data
    payload["observations"][-1]["outcome"] = "success"
    assert cases.verification(payload, messages, RELEASE)["success_authors"] == 2
    assert cases.verification(payload, messages, RELEASE)["status"].startswith("Improvement")
    messages[-1] = replace(messages[-1], author_hash=messages[-2].author_hash)
    assert cases.verification(payload, messages, RELEASE)["success_authors"] == 1
    payload["observations"][-1]["outcome"] = "failure"
    result = cases.verification(payload, messages, RELEASE)
    assert result["success_authors"] == 0 and result["failure_authors"] == 1


def test_sonar_private_calls_disable_search_and_use_schema(monkeypatch, case_data):
    captured = []

    def post(url, **kwargs):
        captured.append(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": json.dumps(case_data[1])}}]},
        )

    monkeypatch.setattr(httpx, "post", post)
    model = LLMClient(
        "https://example.invalid",
        "test",
        "sonar",
        provider="sonar",
        response_schema=cases.SCHEMA,
        max_requests=1,
    )
    assert cases.investigate(model, "PDF citations", case_data[0]) == case_data[1]
    assert captured[0]["disable_search"] is True
    assert "search_domain_filter" not in captured[0]
    assert captured[0]["response_format"]["json_schema"]["schema"] == cases.SCHEMA
    with pytest.raises(AnalysisError, match="exhausted"):
        model.complete_json("test", "test")
    assert len(captured) == 1


def test_portable_provider_does_not_send_sonar_options(monkeypatch):
    def post(url, **kwargs):
        payload = kwargs["json"]
        assert payload["response_format"] == {"type": "json_object"}
        assert "disable_search" not in payload and "search_domain_filter" not in payload
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    monkeypatch.setattr(httpx, "post", post)
    assert LLMClient("https://example.invalid", "test", "local").complete_json("a", "b") == {}


@pytest.mark.parametrize(
    "query,domains,approved",
    [
        ("public docs", ("docs.example.com",), False),
        ("email user@example.com", ("docs.example.com",), True),
        ("message 123456789123456789", ("docs.example.com",), True),
        ("public docs", (), True),
        ("public docs", ("*.example.com",), True),
        ("public docs", ("https://example.com",), True),
    ],
)
def test_unapproved_or_unsafe_research_never_calls_provider(
    env, monkeypatch, query, domains, approved
):
    monkeypatch.setattr(httpx, "post", Mock(side_effect=AssertionError("Unexpected network")))
    config = replace(load_config(env), llm_provider="sonar")
    with pytest.raises(AnalysisError):
        research.research_public(config, query, domains, approved=approved)


def test_public_research_uses_only_returned_allowlisted_urls(env, monkeypatch):
    def post(url, **kwargs):
        payload = kwargs["json"]
        assert payload["disable_search"] is False
        assert payload["search_domain_filter"] == ["docs.example.com"]
        assert payload["messages"][1]["content"] == "Public PDF export documentation"
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "A public research lead.",
                                    "limitations": "Not a cause.",
                                    "sources": ["https://docs.example.com/invented"],
                                }
                            )
                        }
                    }
                ],
                "search_results": [
                    {"url": "https://docs.example.com/actual"},
                    {"url": "https://docs.example.com.evil.test/path"},
                    {"url": "http://docs.example.com/insecure"},
                    {"url": "https://user@docs.example.com/private"},
                    {"url": "https://["},
                ],
            },
        )

    monkeypatch.setattr(httpx, "post", post)
    config = replace(load_config(env), llm_provider="sonar")
    result = research.research_public(
        config, "Public PDF export documentation", ("docs.example.com",), approved=True
    )
    assert result["sources"] == ["https://docs.example.com/actual"]


def test_bad_provider_configuration_fails(env):
    with pytest.raises(ConfigError, match="LLM_PROVIDER"):
        load_config({**env, "LLM_PROVIDER": "random"})


def test_staff_case_command_runs_real_storage_and_private_response(pilot, monkeypatch):
    asyncio.run(pilot.client.publish("manual", START, END))
    signal_id = pilot.client.connection.execute("SELECT id FROM signals LIMIT 1").fetchone()[0]

    def complete(_self, system, prompt):
        data = json.loads(prompt)
        message = data["messages"][0]
        return {
            "title": "Staff investigation",
            "observations": [
                {
                    "message_id": message["message_id"],
                    "quote": message["content"][:100],
                    "outcome": "unclear",
                    "condition": "Needs reproduction",
                }
            ],
            "hypotheses": [],
            "next_question": "Can staff reproduce this behavior?",
        }

    monkeypatch.setattr(analysis.LLMClient, "complete_json", complete)
    interaction = SimpleNamespace(
        guild_id=pilot.client.config.guild_id,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    command = pilot.client.tree.get_command("seismograph_case")
    assert command.default_permissions.administrator and command.guild_only and command.checks
    sent_before = len(pilot.posted)
    asyncio.run(command.callback(interaction, signal_id=signal_id))
    assert pilot.client.connection.execute("SELECT COUNT(*) FROM case_revisions").fetchone()[0] == 1
    assert len(pilot.posted) == sent_before
    assert interaction.followup.send.call_count
    assert all(c.kwargs["ephemeral"] for c in interaction.followup.send.call_args_list)
    assert "Staff investigation" in interaction.followup.send.call_args_list[0].args[0]
    # Direct follow-up does not need another report or published signal.
    monkeypatch.setattr(case_commands, "datetime", bot.datetime)
    asyncio.run(command.callback(interaction, case_id=1, refresh=True))
    assert pilot.client.connection.execute("SELECT COUNT(*) FROM case_revisions").fetchone()[0] == 2
    assert len(pilot.posted) == sent_before


@pytest.mark.parametrize("name", ["seismograph_signals", "seismograph_case", "seismograph_release"])
def test_staff_commands_enforce_runtime_permissions_not_just_visibility(pilot, name):
    command = pilot.client.tree.get_command(name)
    denied = SimpleNamespace(permissions=discord.Permissions.none())
    allowed = SimpleNamespace(permissions=discord.Permissions(administrator=True))
    assert command.guild_only and command.default_permissions.administrator
    for check in command.checks:
        with pytest.raises(discord.app_commands.MissingPermissions):
            check(denied)
        assert check(allowed)


def test_cancelled_case_model_cannot_persist_in_background(pilot, monkeypatch):
    asyncio.run(pilot.client.publish("manual", START, END))
    signal_id = pilot.client.connection.execute("SELECT id FROM signals LIMIT 1").fetchone()[0]
    from threading import Event

    entered, finish = Event(), Event()

    def complete(*args):
        entered.set()
        assert finish.wait(3)
        return {"title": "test", "observations": [], "hypotheses": [], "next_question": "test?"}

    monkeypatch.setattr(analysis.LLMClient, "complete_json", complete)
    interaction = SimpleNamespace(
        guild_id=pilot.client.config.guild_id,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    async def exercise():
        task = asyncio.create_task(
            pilot.client.tree.get_command("seismograph_case").callback(interaction, signal_id)
        )
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            finish.set()

    asyncio.run(exercise())
    assert pilot.client.connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
    assert not interaction.followup.send.called
