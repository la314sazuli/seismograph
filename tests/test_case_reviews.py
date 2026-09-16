import asyncio
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import discord
import pytest
from test_case_history import data as data
from test_case_history import db as db
from test_case_history import install_case, interaction
from test_offline_pilot import pilot as pilot

from seismograph import case_reviews as reviews
from seismograph import cases, storage
from seismograph.case_demo import RELEASE
from seismograph.privacy import hash_author


@pytest.fixture
def case(db, data):
    cid = cases.save(db, data[1], data[0])
    cases.mark_release(db, cid, "Fictional patch", RELEASE)
    return cid


def view(db, case, data):
    return reviews.view(db, case, [data[0][0].channel_id])


def correct(
    db,
    case,
    data,
    *,
    outcome="unclear",
    reason="Needs a same-workflow retest",
    actor="reviewer-a",
    mid=None,
    key=None,
):
    return reviews.correct(
        db,
        case,
        key or view(db, case, data)["revision_key"],
        mid or data[0][-1].message_id,
        outcome,
        reason,
        actor,
        [data[0][0].channel_id],
    )


def withdraw(db, case, data, rid, actor="reviewer-b"):
    reviews.withdraw(
        db,
        case,
        view(db, case, data)["revision_key"],
        rid,
        "Withdrawal after checking the original report",
        actor,
        [data[0][0].channel_id],
    )


def test_correction_preserves_source_model_and_official_counts(db, case, data):
    original = db.execute("SELECT payload FROM case_revisions").fetchone()[0]
    source = [tuple(r) for r in db.execute("SELECT * FROM messages")]
    rid = correct(db, case, data)
    result = view(db, case, data)
    assert result["records"][0]["id"] == rid
    assert result["original"]["failure_authors"] == 1
    assert result["preview"]["failure_authors"] == 0
    assert result["active"] == 1
    assert db.execute("SELECT payload FROM case_revisions").fetchone()[0] == original
    assert [tuple(r) for r in db.execute("SELECT * FROM messages")] == source
    text = reviews.render(result, 123)
    assert "Staff-adjusted preview" in text and "not new evidence of recovery" in text
    assert "reviewer-a" not in text and "reviewer-a" not in json.dumps(result)
    assert "STAFF REVIEW" in reviews.banner(result)


@pytest.mark.parametrize(
    "outcome", ["success", "counterexample", "workaround", "unclear", "exclude"]
)
def test_supported_corrections_are_previewed_without_counting_as_failures(db, case, data, outcome):
    correct(db, case, data, outcome=outcome)
    result = view(db, case, data)
    assert result["preview"]["failure_authors"] == 0
    assert result["preview"]["success_authors"] == (2 if outcome == "success" else 1)
    assert result["original"]["failure_authors"] == 1


def test_withdrawal_retains_audit_and_restores_preview(db, case, data):
    rid = correct(db, case, data)
    withdraw(db, case, data, rid)
    result = view(db, case, data)
    assert result["active"] == 0 and result["original"] == result["preview"]
    assert result["records"][0]["reason"] == "Needs a same-workflow retest"
    assert result["records"][0]["withdrawal_reason"]
    assert "withdrawn" in reviews.render(result, 123)
    assert not reviews.banner(result)
    next_id = correct(db, case, data)
    assert next_id > rid
    assert len(view(db, case, data)["records"]) == 2


def test_duplicate_correction_and_withdrawal_fail_closed(db, case, data):
    rid = correct(db, case, data)
    with pytest.raises(cases.AnalysisError, match="Withdraw"):
        correct(db, case, data, outcome="exclude")
    withdraw(db, case, data, rid)
    with pytest.raises(cases.AnalysisError, match="No active"):
        withdraw(db, case, data, rid)
    assert not db.in_transaction


@pytest.mark.parametrize(
    "kwargs",
    [
        {"outcome": "resolved"},
        {"outcome": "failure"},
        {"mid": "not-an-observation"},
        {"reason": ""},
        {"reason": "short"},
        {"reason": "a" * 301},
        {"key": "stale-key"},
    ],
)
def test_invalid_writes_create_no_audit_rows(db, case, data, kwargs):
    with pytest.raises(cases.AnalysisError):
        correct(db, case, data, **kwargs)
    assert not db.execute("SELECT * FROM case_corrections").fetchall()
    assert not db.in_transaction


def test_refresh_does_not_inherit_corrections_and_rejects_stale_key(db, case, data):
    key = view(db, case, data)["revision_key"]
    correct(db, case, data)
    cases.save(db, data[1], data[0], case)
    result = view(db, case, data)
    assert result["active"] == 0 and result["records"] == []
    assert result["revision_key"] != key
    assert db.execute("SELECT COUNT(*) FROM case_corrections").fetchone()[0] == 1
    with pytest.raises(cases.AnalysisError, match="Revision changed"):
        correct(db, case, data, key=key)


def test_privacy_then_reused_numeric_revision_id_cannot_accept_stale_review(db, case, data):
    old = view(db, case, data)
    storage.forget_messages(db, [data[0][0].message_id])
    storage.store_messages(db, [asdict(m) for m in data[0]])
    cases.save(db, data[1], data[0], case)
    new = view(db, case, data)
    assert new["revision_id"] == old["revision_id"]  # SQLite may reuse a numeric ID.
    assert new["revision_key"] != old["revision_key"]
    with pytest.raises(cases.AnalysisError):
        correct(db, case, data, key=old["revision_key"])
    with pytest.raises(cases.AnalysisError):
        reviews.assert_current(db, old)


@pytest.mark.parametrize("action", ["edit", "delete", "optout", "retention"])
def test_source_privacy_erases_corrections_and_withdrawals(db, case, data, action):
    rid = correct(db, case, data)
    withdraw(db, case, data, rid)
    # Even an uncited context message invalidates the entire derived history.
    if action == "edit":
        row = asdict(data[0][0])
        row["content"] = "Edited source content with a different meaning."
        storage.store_messages(db, [row])
    elif action == "delete":
        storage.forget_messages(db, [data[0][0].message_id])
    elif action == "optout":
        storage.opt_out(db, data[0][0].author_hash)
    else:
        storage.prune(db, 1, datetime(2026, 9, 1, tzinfo=UTC))
    for table in ("case_corrections", "case_withdrawals"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    with pytest.raises(cases.AnalysisError):
        view(db, case, data)


@pytest.mark.parametrize("actor", ["reviewer-a", "reviewer-b"])
def test_reviewer_optout_cannot_resurrect_a_withdrawn_correction(db, case, data, actor):
    rid = correct(db, case, data)
    withdraw(db, case, data, rid)
    storage.opt_out(db, actor)
    assert view(db, case, data)["records"] == []
    assert not db.execute("SELECT * FROM case_withdrawals").fetchall()
    with pytest.raises(cases.AnalysisError, match="opted-out"):
        correct(db, case, data, actor=actor)


def test_active_reviewer_optout_removes_annotation_not_source(db, case, data):
    correct(db, case, data)
    prepared = view(db, case, data)
    storage.opt_out(db, "reviewer-a")
    result = view(db, case, data)
    assert result["records"] == []
    assert result["preview"] == result["original"]
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == len(data[0])
    with pytest.raises(cases.AnalysisError):
        reviews.assert_current(db, prepared)


def test_reasons_are_redacted_and_rendered_without_mentions(db, case, data):
    correct(db, case, data, reason="Ask <@123456789012345678> or alice@example.com; **unverified**")
    result = view(db, case, data)
    reason = result["records"][0]["reason"]
    assert "alice@example.com" not in reason and "<@" not in reason
    assert "[email]" in reason and "[user]" in reason
    assert r"\*\*unverified\*\*" in reviews.render(result, 123)


def test_scoped_read_and_write_reject_unapproved_context(db, case, data):
    key = view(db, case, data)["revision_key"]
    with pytest.raises(cases.AnalysisError, match="allowlist"):
        reviews.view(db, case, ["different-channel"])
    with pytest.raises(cases.AnalysisError, match="allowlist"):
        reviews.correct(
            db,
            case,
            key,
            data[0][-1].message_id,
            "unclear",
            "Needs another test",
            "reviewer",
            ["different-channel"],
        )
    assert not db.execute("SELECT * FROM case_corrections").fetchall()


@pytest.mark.parametrize("action", ["correct", "withdraw", "refresh", "marker"])
def test_prepared_reply_is_invalidated_by_changes(db, case, data, action):
    rid = correct(db, case, data)
    before = view(db, case, data)
    if action == "correct":
        correct(db, case, data, mid=data[0][0].message_id)
    elif action == "withdraw":
        withdraw(db, case, data, rid)
    elif action == "refresh":
        cases.save(db, data[1], data[0], case)
    else:
        cases.mark_release(db, case, "New intervention")
    with pytest.raises(cases.AnalysisError, match="changed"):
        reviews.assert_current(db, before)


def test_audit_limit_is_enforced_even_after_withdrawals(db, case, data, monkeypatch):
    monkeypatch.setattr(reviews, "MAX_CORRECTIONS", 2)
    for _ in range(2):
        rid = correct(db, case, data)
        withdraw(db, case, data, rid)
    with pytest.raises(cases.AnalysisError, match="audit limit"):
        correct(db, case, data)


def test_review_pagination_and_read_transaction(db, case, data):
    for _ in range(9):
        rid = correct(db, case, data)
        withdraw(db, case, data, rid)
    db.execute("BEGIN")
    result = view(db, case, data)
    assert db.in_transaction
    db.rollback()
    assert reviews.render(result, 123, 1).count("Source excerpt:") == 8
    assert reviews.render(result, 123, 2).count("Source excerpt:") == 1
    with pytest.raises(cases.AnalysisError, match="page"):
        reviews.render(result, 123, 3)


def test_v3_migration_preserves_revisions_and_initializes_unique_keys(tmp_path, data):
    path = str(tmp_path / "v3.db")
    old = sqlite3.connect(path)
    old.executescript(
        storage.SCHEMA
        + "ALTER TABLE runs ADD COLUMN status TEXT NOT NULL DEFAULT 'analyzing';"
        + "CREATE TABLE optouts (author_hash TEXT PRIMARY KEY);"
        + "CREATE INDEX messages_channel_time ON messages(channel_id, created_at);"
        + storage.CASE_SCHEMA
        + "PRAGMA user_version = 3;"
    )
    old.row_factory = sqlite3.Row
    storage.store_messages(old, [asdict(m) for m in data[0]])
    cid = cases.save(old, data[1], data[0])
    cases.save(old, data[1], data[0], cid)
    old.close()
    migrated = storage.connect(path)
    try:
        keys = [r[0] for r in migrated.execute("SELECT review_key FROM case_revisions")]
        assert len(set(keys)) == 2 and all(len(key) == 32 for key in keys)
        assert cases.load(migrated, cid)[1] == data[1]
        assert not migrated.execute("PRAGMA foreign_key_check").fetchall()
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
    finally:
        migrated.close()
    again = storage.connect(path)
    assert [r[0] for r in again.execute("SELECT review_key FROM case_revisions")] == keys
    again.close()


@pytest.mark.parametrize(
    "name", ["seismograph_reviews", "seismograph_correct", "seismograph_withdraw"]
)
def test_all_review_commands_require_runtime_admin(pilot, name):
    command = pilot.client.tree.get_command(name)
    assert command.guild_only and command.default_permissions.administrator
    for check in command.checks:
        with pytest.raises(discord.app_commands.MissingPermissions):
            check(SimpleNamespace(permissions=discord.Permissions.none()))
        assert check(SimpleNamespace(permissions=discord.Permissions(administrator=True)))


def test_commands_correct_read_and_withdraw_privately_without_model(pilot, data, monkeypatch):
    cid = install_case(pilot, data)
    client = pilot.client
    event = interaction(pilot)
    event.user = SimpleNamespace(id=12345)
    key = reviews.view(client.connection, cid, client.config.source_channel_ids)["revision_key"]
    monkeypatch.setattr(
        "seismograph.analysis.LLMClient.complete_json", Mock(side_effect=AssertionError("No model"))
    )
    asyncio.run(
        client.tree.get_command("seismograph_correct").callback(
            event, cid, key, data[0][-1].message_id, "unclear", "Needs a controlled retest"
        )
    )
    assert "Saved correction" in event.followup.send.call_args_list[0].args[0]
    record = client.connection.execute("SELECT * FROM case_corrections").fetchone()
    assert record["reviewer_hash"] == hash_author(event.user.id, client.config.author_hash_salt)
    asyncio.run(client.tree.get_command("seismograph_reviews").callback(event, cid))
    asyncio.run(
        client.tree.get_command("seismograph_withdraw").callback(
            event, cid, key, record["id"], "Keep the original interpretation after review"
        )
    )
    assert reviews.view(client.connection, cid, client.config.source_channel_ids)["active"] == 0
    for call in event.followup.send.call_args_list:
        assert call.kwargs["ephemeral"]
        assert not call.kwargs["allowed_mentions"].everyone
        assert len(call.args[0]) <= 2000


def test_review_commands_reject_wrong_guild_and_busy_lock(pilot, data):
    cid = install_case(pilot, data)
    command = pilot.client.tree.get_command("seismograph_reviews")
    event = interaction(pilot, 999)
    asyncio.run(command.callback(event, cid))
    event.response.defer.assert_not_called()
    event = interaction(pilot)

    async def execute():
        async with pilot.client.report_lock:
            await command.callback(event, cid)

    asyncio.run(execute())
    assert "Another report" in event.followup.send.call_args.args[0]


def test_reply_stops_after_reviewer_optout(pilot, data, monkeypatch):
    cid = install_case(pilot, data)
    client = pilot.client
    key = reviews.view(client.connection, cid, client.config.source_channel_ids)["revision_key"]
    reviews.correct(
        client.connection,
        cid,
        key,
        data[0][-1].message_id,
        "unclear",
        "Needs another retest",
        "reviewer",
        client.config.source_channel_ids,
    )
    from seismograph import review_commands

    monkeypatch.setattr(review_commands, "split_for_discord", lambda text: ["First", "Sensitive"])
    event = interaction(pilot)

    async def send(text, **kwargs):
        if text == "First":
            storage.opt_out(client.connection, "reviewer")

    event.followup.send.side_effect = send
    asyncio.run(client.tree.get_command("seismograph_reviews").callback(event, cid))
    assert "Sensitive" not in [call.args[0] for call in event.followup.send.call_args_list]


def test_demo_and_cli_run_without_model_or_network(monkeypatch, capsys):
    import socket

    from seismograph.__main__ import main
    from seismograph.case_review_demo import run

    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("No network")))
    monkeypatch.setattr(
        "seismograph.analysis.LLMClient.complete_json", Mock(side_effect=AssertionError("No model"))
    )
    text = run()
    assert "Staff corrects the label" in text
    assert "Continued failures reported; 1 failure" in text
    assert "Removing the withdrawal cannot reactivate" in text
    assert main(["review-demo"]) == 0
    assert "no model or Discord connection" in capsys.readouterr().out


def test_permission_preflight_failure_cannot_save_correction(pilot, data):
    cid = install_case(pilot, data)
    client = pilot.client
    client.preflight = Mock(side_effect=cases.AnalysisError("Source permission denied"))
    event = interaction(pilot)
    event.user = SimpleNamespace(id=12345)
    key = reviews.view(client.connection, cid, client.config.source_channel_ids)["revision_key"]
    asyncio.run(
        client.tree.get_command("seismograph_correct").callback(
            event, cid, key, data[0][-1].message_id, "unclear", "Needs another retest"
        )
    )
    assert not client.connection.execute("SELECT * FROM case_corrections").fetchall()
    assert "Source permission denied" in event.followup.send.call_args.args[0]


@pytest.mark.parametrize("name", ["seismograph_case", "seismograph_changes"])
def test_original_cards_warn_about_current_staff_corrections(pilot, data, name):
    cid = install_case(pilot, data)
    client = pilot.client
    key = reviews.view(client.connection, cid, client.config.source_channel_ids)["revision_key"]
    reviews.correct(
        client.connection,
        cid,
        key,
        data[0][-1].message_id,
        "unclear",
        "Needs another retest",
        "reviewer",
        client.config.source_channel_ids,
    )
    event = interaction(pilot)
    asyncio.run(client.tree.get_command(name).callback(event, case_id=cid))
    text = "\n".join(call.args[0] for call in event.followup.send.call_args_list)
    assert "STAFF REVIEW: 1 active correction" in text
    assert "Continued failures reported" in text
