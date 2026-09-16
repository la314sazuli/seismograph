import asyncio
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import discord
import pytest
from test_case_history import data as data
from test_case_history import db as db
from test_case_history import install_case, interaction
from test_offline_pilot import pilot as pilot

from seismograph import case_reviews, cases, exposure, storage
from seismograph.case_demo import RELEASE
from seismograph.privacy import hash_author


@pytest.fixture
def case(db, data):
    cid = cases.save(db, data[1], data[0])
    cases.mark_release(db, cid, "Fictional patch", RELEASE)
    return cid


def view(db, case, data):
    return exposure.view(db, case, [data[0][0].channel_id])


def assess(db, case, data, **kwargs):
    values = {
        "scope_key": view(db, case, data)["scope_key"],
        "message_id": data[0][-1].message_id,
        "state": "received",
        "quote": data[0][-1].content,
        "reason": "Staff interprets this exact source excerpt",
        "reviewer_hash": "staff-a",
        "channels": [data[0][0].channel_id],
    }
    values.update(kwargs)
    return exposure.assess(db, case, **values)


def test_post_release_is_unknown_without_explicit_staff_assessment(db, case, data):
    result = view(db, case, data)
    assert result["original"]["groups"]["unknown"] == {
        "observations": 2,
        "reporters": 2,
        "failure_reporters": 1,
        "success_reporters": 1,
    }
    assert result["original"]["before_marker_observations"] == 4
    assert result["original"] == result["adjusted"]
    assert len(result["scope_key"]) == 64
    text = exposure.render(result, 123)
    assert "not verified patch installation" in text
    assert "timestamps alone never establish exposure" in text
    assert "No overall resolution status" in text
    assert "fictional-author" not in json.dumps(result)
    exposure.assert_current(db, result)
    assert not db.in_transaction


def test_exposure_requires_release(db, data):
    cid = cases.save(db, data[1], data[0])
    with pytest.raises(cases.AnalysisError, match="intervention"):
        view(db, cid, data)


def test_segments_preserve_original_and_separate_corrected_outcomes(db, case, data):
    original = cases.load(db, case)[1]
    assess(db, case, data, state="not_received")
    assess(db, case, data, message_id=data[0][-2].message_id, quote=data[0][-2].content)
    result = view(db, case, data)
    assert result["original"]["groups"]["not_received"]["failure_reporters"] == 1
    assert result["original"]["groups"]["received"]["success_reporters"] == 1
    assert result["original"]["groups"]["unknown"]["observations"] == 0
    case_reviews.correct(
        db,
        case,
        result["review_guard"]["revision_key"],
        data[0][-1].message_id,
        "exclude",
        "Wrong workflow for this investigation",
        "staff-b",
        [data[0][0].channel_id],
    )
    adjusted = view(db, case, data)
    assert adjusted["original"] == result["original"]
    assert adjusted["adjusted"]["groups"]["not_received"]["failure_reporters"] == 0
    assert adjusted["adjusted"]["excluded_observations"] == 1
    assert cases.load(db, case)[1] == original
    assert "staff-a" not in exposure.render(adjusted, 123)
    with pytest.raises(cases.AnalysisError):
        exposure.assert_current(db, result)


def test_reporter_overlap_and_failure_precedence_are_explicit(data):
    messages, payload = data
    messages[-1] = replace(messages[-1], author_hash=messages[-2].author_hash)
    latest = {messages[-1].message_id: {"state": "received"}}
    counts = exposure._counts(payload["observations"], messages, RELEASE, latest)
    assert counts["unique_reporters"] == 1
    assert counts["mixed_exposure_reporters"] == 1
    assert counts["groups"]["unknown"]["success_reporters"] == 1
    latest[messages[-2].message_id] = {"state": "received"}
    counts = exposure._counts(payload["observations"], messages, RELEASE, latest)
    assert counts["mixed_exposure_reporters"] == 0
    assert counts["groups"]["received"]["success_reporters"] == 0
    assert counts["groups"]["received"]["failure_reporters"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope_key": "stale"},
        {"state": "resolved"},
        {"quote": ""},
        {"quote": "short"},
        {"quote": "Not in the retained source"},
        {"quote": "a" * 351},
        {"quote": None},
        {"message_id": "missing"},
        {"reason": "short"},
        {"state": "unknown"},
        {"state": "unknown", "quote": ""},
        {"channels": ["unapproved"]},
    ],
)
def test_invalid_assessments_leave_no_rows(db, case, data, kwargs):
    with pytest.raises(cases.AnalysisError):
        assess(db, case, data, **kwargs)
    assert not db.execute("SELECT * FROM case_exposures").fetchall()
    assert not db.in_transaction


def test_quote_must_come_from_selected_post_marker_observation(db, case, data):
    with pytest.raises(cases.AnalysisError, match="post-marker"):
        assess(db, case, data, message_id=data[0][0].message_id, quote=data[0][0].content)
    with pytest.raises(cases.AnalysisError, match="exact source"):
        assess(db, case, data, quote=data[0][-2].content)
    with pytest.raises(cases.AnalysisError, match="allowlist"):
        exposure.view(db, case, ["unapproved"])


def test_reset_is_append_only_not_a_hidden_reactivation(db, case, data):
    first = assess(db, case, data)
    second = assess(db, case, data, state="unknown", quote="")
    result = view(db, case, data)
    assert [e["id"] for e in result["events"]] == [first, second]
    assert result["latest"][data[0][-1].message_id]["state"] == "unknown"
    assert result["original"]["groups"]["unknown"]["observations"] == 2
    assert "superseded" in exposure.render(result, 123)
    with pytest.raises(cases.AnalysisError, match="already"):
        assess(db, case, data, state="unknown", quote="")


@pytest.mark.parametrize("change", ["revision", "identical_marker", "new_marker", "marker_back"])
def test_new_scope_never_inherits_exposure(db, case, data, change):
    assess(db, case, data)
    before = view(db, case, data)
    if change == "revision":
        cases.save(db, data[1], data[0], case)
    elif change == "identical_marker":
        cases.mark_release(db, case, "Fictional patch", RELEASE)
    else:
        cases.mark_release(db, case, "Another patch", RELEASE)
        if change == "marker_back":
            cases.mark_release(db, case, "Fictional patch", RELEASE)
    after = view(db, case, data)
    assert before["scope_key"] != after["scope_key"]
    assert after["events"] == []
    assert after["original"]["groups"]["unknown"]["observations"] == 2
    assert db.execute("SELECT COUNT(*) FROM case_exposures").fetchone()[0] == 1
    with pytest.raises(cases.AnalysisError, match="changed"):
        assess(db, case, data, scope_key=before["scope_key"])
    with pytest.raises(cases.AnalysisError, match="changed"):
        exposure.assert_current(db, before)


@pytest.mark.parametrize("action", ["delete", "edit", "optout", "retention"])
def test_source_privacy_erases_all_release_and_revision_audits(db, case, data, action):
    assess(db, case, data)
    cases.mark_release(db, case, "Another patch", RELEASE)
    assess(db, case, data)
    cases.save(db, data[1], data[0], case)
    assess(db, case, data)
    before = view(db, case, data)
    if action == "delete":
        storage.forget_messages(db, [data[0][0].message_id])
    elif action == "edit":
        row = asdict(data[0][0])
        row["content"] = "The source meaning was changed."
        storage.store_messages(db, [row])
    elif action == "optout":
        storage.opt_out(db, data[0][0].author_hash)
    else:
        storage.prune(db, 1, datetime(2026, 9, 1, tzinfo=UTC))
    assert not db.execute("SELECT * FROM case_exposures").fetchall()
    with pytest.raises(cases.AnalysisError):
        view(db, case, data)
    with pytest.raises(cases.AnalysisError):
        exposure.assert_current(db, before)


@pytest.mark.parametrize("actor", ["staff-a", "staff-b"])
def test_reviewer_optout_cannot_restore_a_superseded_assessment(db, case, data, actor):
    assess(db, case, data)
    assess(db, case, data, state="unknown", quote="", reviewer_hash="staff-b")
    assess(
        db,
        case,
        data,
        message_id=data[0][-2].message_id,
        quote=data[0][-2].content,
        reviewer_hash="unrelated-staff",
    )
    before = view(db, case, data)
    storage.opt_out(db, actor)
    result = view(db, case, data)
    assert len(result["events"]) == 1
    assert data[0][-1].message_id not in result["latest"]
    assert result["original"]["groups"]["unknown"]["failure_reporters"] == 1
    with pytest.raises(cases.AnalysisError):
        exposure.assert_current(db, before)
    with pytest.raises(cases.AnalysisError, match="opted-out"):
        assess(db, case, data, reviewer_hash=actor)


def test_audit_limit_applies_across_marker_changes(db, case, data, monkeypatch):
    monkeypatch.setattr(exposure, "MAX_EVENTS", 2)
    assess(db, case, data)
    cases.mark_release(db, case, "Another patch", RELEASE)
    assess(db, case, data)
    assess_result = view(db, case, data)
    with pytest.raises(cases.AnalysisError, match="audit limit"):
        assess(db, case, data, state="unknown", quote="")
    assert view(db, case, data) == assess_result
    monkeypatch.setattr(exposure, "MAX_EVENTS", 1)
    with pytest.raises(cases.AnalysisError, match="bound"):
        view(db, case, data)


def test_pagination_reason_redaction_and_transaction(db, case, data):
    for index in range(9):
        assess(
            db,
            case,
            data,
            state="received" if index % 2 == 0 else "unknown",
            quote=data[0][-1].content if index % 2 == 0 else "",
            reason="Ask alice@example.com or <@123456789012345678>; **unverified**",
        )
    db.execute("BEGIN")
    result = view(db, case, data)
    assert db.in_transaction
    db.rollback()
    text = exposure.render(result, 123)
    assert "alice@example.com" not in text and "<@" not in text
    assert r"\*\*unverified\*\*" in text
    assert text.count("Exposure excerpt:") == 8
    assert exposure.render(result, 123, 2).count("Exposure excerpt:") == 1
    for page in (0, 3):
        with pytest.raises(cases.AnalysisError, match="page"):
            exposure.render(result, 123, page)


def test_v4_migration_preserves_existing_cases_reviews_and_markers(tmp_path, data):
    path = str(tmp_path / "v4.db")
    old = sqlite3.connect(path)
    old.row_factory = sqlite3.Row
    old.executescript(
        storage.SCHEMA
        + "ALTER TABLE runs ADD COLUMN status TEXT NOT NULL DEFAULT 'analyzing';"
        + "CREATE TABLE optouts (author_hash TEXT PRIMARY KEY);"
        + "CREATE INDEX messages_channel_time ON messages(channel_id, created_at);"
        + storage.CASE_SCHEMA
        + storage.REVIEW_SCHEMA
        + "PRAGMA user_version = 4;"
    )
    storage.store_messages(old, [asdict(m) for m in data[0]])
    cid = cases.save(old, data[1], data[0])
    cases.mark_release(old, cid, "Existing patch", RELEASE)
    key = case_reviews.view(old, cid, [data[0][0].channel_id])["revision_key"]
    case_reviews.correct(
        old,
        cid,
        key,
        data[0][-1].message_id,
        "unclear",
        "Needs a controlled retest",
        "staff",
        [data[0][0].channel_id],
    )
    old.close()
    migrated = storage.connect(path)
    result = view(migrated, cid, data)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 5
    assert result["review_guard"]["revision_key"] == key
    assert result["review_guard"]["active"] == 1
    assert cases.load(migrated, cid)[1] == data[1]
    assert result["original"]["groups"]["unknown"]["observations"] == 2
    assert not migrated.execute("PRAGMA foreign_key_check").fetchall()
    migrated.close()
    again = storage.connect(path)
    assert view(again, cid, data)["scope_key"] == result["scope_key"]
    again.close()


@pytest.mark.parametrize("name", ["seismograph_exposures", "seismograph_exposure"])
def test_commands_require_runtime_admin(pilot, name):
    command = pilot.client.tree.get_command(name)
    assert command.guild_only and command.default_permissions.administrator
    for check in command.checks:
        with pytest.raises(discord.app_commands.MissingPermissions):
            check(SimpleNamespace(permissions=discord.Permissions.none()))
        assert check(SimpleNamespace(permissions=discord.Permissions(administrator=True)))


def test_commands_assess_and_read_privately_without_model(pilot, data, monkeypatch):
    cid = install_case(pilot, data)
    client = pilot.client
    event = interaction(pilot)
    event.user = SimpleNamespace(id=12345)
    key = exposure.view(client.connection, cid, client.config.source_channel_ids)["scope_key"]
    monkeypatch.setattr(
        "seismograph.analysis.LLMClient.complete_json", Mock(side_effect=AssertionError("No model"))
    )
    asyncio.run(
        client.tree.get_command("seismograph_exposure").callback(
            event,
            cid,
            key,
            data[0][-1].message_id,
            "received",
            "Staff interprets this source excerpt",
            data[0][-1].content,
        )
    )
    assert "Saved exposure assessment" in event.followup.send.call_args_list[0].args[0]
    record = client.connection.execute("SELECT * FROM case_exposures").fetchone()
    assert record["reviewer_hash"] == hash_author(event.user.id, client.config.author_hash_salt)
    asyncio.run(client.tree.get_command("seismograph_exposures").callback(event, cid))
    for call in event.followup.send.call_args_list:
        assert call.kwargs["ephemeral"] and not call.kwargs["allowed_mentions"].everyone
        assert len(call.args[0]) <= 2000


def test_command_wrong_guild_busy_lock_and_preflight(pilot, data):
    cid = install_case(pilot, data)
    command = pilot.client.tree.get_command("seismograph_exposures")
    event = interaction(pilot, 999)
    asyncio.run(command.callback(event, cid))
    event.response.defer.assert_not_called()
    event = interaction(pilot)

    async def execute():
        async with pilot.client.report_lock:
            await command.callback(event, cid)

    asyncio.run(execute())
    assert "Another report" in event.followup.send.call_args.args[0]
    pilot.client.preflight = Mock(side_effect=cases.AnalysisError("Source permission denied"))
    event.user = SimpleNamespace(id=12345)
    asyncio.run(
        pilot.client.tree.get_command("seismograph_exposure").callback(
            event,
            cid,
            "stale-key",
            data[0][-1].message_id,
            "received",
            "Needs another retest",
            data[0][-1].content,
        )
    )
    assert "Source permission denied" in event.followup.send.call_args.args[0]
    assert not pilot.client.connection.execute("SELECT * FROM case_exposures").fetchall()


@pytest.mark.parametrize("action", ["reviewer_optout", "source_delete", "marker", "assessment"])
def test_reply_rechecks_each_chunk(pilot, data, monkeypatch, action):
    from seismograph import review_commands

    cid = install_case(pilot, data)
    client = pilot.client
    assess(client.connection, cid, data)
    monkeypatch.setattr(review_commands, "split_for_discord", lambda text: ["First", "Sensitive"])
    event = interaction(pilot)

    async def send(text, **kwargs):
        if text != "First":
            return
        if action == "reviewer_optout":
            storage.opt_out(client.connection, "staff-a")
        elif action == "source_delete":
            storage.forget_messages(client.connection, [data[0][0].message_id])
        elif action == "marker":
            cases.mark_release(client.connection, cid, "Another patch", RELEASE)
        else:
            assess(client.connection, cid, data, state="unknown", quote="")

    event.followup.send.side_effect = send
    asyncio.run(client.tree.get_command("seismograph_exposures").callback(event, cid))
    assert "Sensitive" not in [call.args[0] for call in event.followup.send.call_args_list]


@pytest.mark.parametrize("name", ["seismograph_case", "seismograph_changes"])
def test_case_cards_warn_that_post_marker_does_not_establish_exposure(pilot, data, name):
    cid = install_case(pilot, data)
    event = interaction(pilot)
    asyncio.run(pilot.client.tree.get_command(name).callback(event, case_id=cid))
    text = "\n".join(call.args[0] for call in event.followup.send.call_args_list)
    assert "PATCH EXPOSURE: 0 received / 0 not received / 2 unknown" in text
    assert "Continued failures reported" in text


def test_demo_and_cli_need_no_credentials_model_or_network(monkeypatch, capsys):
    import socket

    from seismograph.__main__ import main
    from seismograph.exposure_demo import run

    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("No network")))
    monkeypatch.setattr(
        "seismograph.analysis.LLMClient.complete_json", Mock(side_effect=AssertionError("No model"))
    )
    text = run()
    assert "Three different follow-up groups" in text
    assert "Reviewer opt-out does not restore an earlier claim" in text
    assert main(["exposure-demo"]) == 0
    assert "no Sonar or Discord connection" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["bad_quote", "wrong_observation", "unknown_with_quote"])
def test_corrupt_retained_assessment_fails_closed(db, case, data, change):
    assess(db, case, data)
    if change == "bad_quote":
        db.execute("UPDATE case_exposures SET quote = 'Fabricated source excerpt'")
    elif change == "wrong_observation":
        db.execute("UPDATE case_exposures SET message_id = 'missing'")
    else:
        db.execute("UPDATE case_exposures SET state = 'unknown'")
    db.commit()
    with pytest.raises(cases.AnalysisError, match="evidence is invalid"):
        view(db, case, data)


def test_withdrawn_outcome_correction_invalidates_exposure_preview(db, case, data):
    key = view(db, case, data)["review_guard"]["revision_key"]
    correction = case_reviews.correct(
        db,
        case,
        key,
        data[0][-1].message_id,
        "unclear",
        "Needs a controlled retest",
        "staff-b",
        [data[0][0].channel_id],
    )
    before = view(db, case, data)
    case_reviews.withdraw(
        db,
        case,
        key,
        correction,
        "Keep the original reported outcome",
        "staff-c",
        [data[0][0].channel_id],
    )
    with pytest.raises(cases.AnalysisError, match="changed"):
        exposure.assert_current(db, before)
    after = view(db, case, data)
    assert after["original"] == after["adjusted"]


def test_reused_sqlite_revision_id_cannot_reuse_exposure_scope(db, case, data):
    before = view(db, case, data)
    assess(db, case, data)
    storage.forget_messages(db, [data[0][0].message_id])
    storage.store_messages(db, [asdict(m) for m in data[0]])
    cases.save(db, data[1], data[0], case)
    after = view(db, case, data)
    assert before["revision_id"] == after["revision_id"]
    assert before["scope_key"] != after["scope_key"]
    assert after["events"] == []
    with pytest.raises(cases.AnalysisError, match="changed"):
        assess(db, case, data, scope_key=before["scope_key"])
