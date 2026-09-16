import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import httpx
import pytest
from test_offline_pilot import pilot as pilot

from seismograph import case_history as history
from seismograph import cases, storage
from seismograph.__main__ import main
from seismograph.case_demo import RELEASE, scenario
from seismograph.case_history_demo import run


@pytest.fixture
def data():
    messages, payload = scenario()
    payload["hypotheses"] = []
    return messages, payload


@pytest.fixture
def db(data):
    connection = storage.connect(":memory:")
    storage.store_messages(connection, [asdict(m) for m in data[0]])
    yield connection
    connection.close()


def save(connection, data, *, selected=None, observed=None, case_id=0):
    messages, payload = deepcopy(data)
    selected = messages if selected is None else selected
    observed = {m.message_id for m in selected} if observed is None else observed
    payload["observations"] = [o for o in payload["observations"] if o["message_id"] in observed]
    return cases.save(connection, payload, selected, case_id)


def pair(db, data):
    case_id = save(db, data)
    cases.mark_release(db, case_id, "Fictional patch", RELEASE)
    save(db, data, case_id=case_id)
    return case_id


def test_identical_snapshots_are_not_new_evidence(db, data):
    cid = pair(db, data)
    d = history.compare(db, cid, [data[0][0].channel_id])
    assert d["same_context"] and not d["interpretation_changed"]
    assert not d["evidence"] and not d["context_added"] and not d["context_removed"]
    assert d["before_status"] == d["after_status"]
    history.assert_current(db, d)
    assert not db.in_transaction


def test_new_selected_evidence_is_distinguished_from_old_context(db, data):
    cid = save(db, data, selected=data[0][:4])
    save(db, data, case_id=cid)
    d = history.compare(db, cid, [data[0][0].channel_id])
    assert d["context_added"] == [m.message_id for m in data[0][4:]]
    assert d["observations_added"] == d["context_added"]
    assert not d["promoted_from_existing_context"]
    assert not d["same_context"]


def test_new_observation_can_come_from_previously_selected_message(db, data):
    cid = save(db, data, observed={m.message_id for m in data[0][:-1]})
    save(db, data, case_id=cid)
    d = history.compare(db, cid, [data[0][0].channel_id])
    assert d["same_context"] and d["interpretation_changed"]
    assert d["promoted_from_existing_context"] == [data[0][-1].message_id]
    assert not d["context_added"]


def test_omitted_failure_is_not_recovery(db, data):
    cid = pair(db, data)
    save(db, data, observed={m.message_id for m in data[0][:-1]}, case_id=cid)
    d = history.compare(db, cid, [data[0][0].channel_id])
    assert d["failure_evidence_lost"] == [data[0][-1].message_id]
    assert d["same_context"]
    assert d["before_status"]["status"] == "Continued failures reported"
    assert d["after_status"]["status"].startswith("Insufficient")
    text = history.render(d, 123)
    assert "Source remains in selected context" in text
    assert "not evidence that those reporters recovered" in text


def test_window_loss_is_different_from_model_omission(db, data):
    cid = pair(db, data)
    save(db, data, selected=data[0][:-1], case_id=cid)
    d = history.compare(db, cid, [data[0][0].channel_id])
    assert d["context_removed"] == [data[0][-1].message_id]
    assert d["failure_evidence_lost"] == d["context_removed"]
    text = history.render(d, 123)
    assert "outside the new selected context" in text
    assert "not proof of deletion or resolution" in text


def test_reclassified_failure_and_changed_question_are_explicit(db, data):
    cid = pair(db, data)
    changed = deepcopy(data)
    changed[1]["observations"][-1]["outcome"] = "success"
    changed[1]["next_question"] = "What changed between the two tests?"
    save(db, changed, case_id=cid)
    d = history.compare(db, cid, [data[0][0].channel_id])
    assert d["observations_rewritten"] == [data[0][-1].message_id]
    assert d["failure_evidence_lost"] == d["observations_rewritten"]
    assert d["question_changed"]
    assert d["after_status"]["status"].startswith("Improvement")
    assert "Same source context" in history.render(d, 123)


def test_observation_reordering_is_not_reinterpretation(db, data):
    cid = pair(db, data)
    changed = deepcopy(data)
    changed[1]["observations"].reverse()
    save(db, changed, case_id=cid)
    assert not history.compare(db, cid, [data[0][0].channel_id])["interpretation_changed"]


@pytest.mark.parametrize("action", ["delete", "edit", "optout", "retention"])
def test_privacy_removes_comparison_without_falling_back(db, data, action):
    cid = pair(db, data)
    d = history.compare(db, cid, [data[0][0].channel_id])
    if action == "delete":
        storage.forget_messages(db, [data[0][0].message_id])
    elif action == "edit":
        storage.store_messages(db, [asdict(replace(data[0][0], content="Edited evidence text"))])
    elif action == "optout":
        storage.opt_out(db, data[0][0].author_hash)
    else:
        storage.prune(db, 1, now=datetime(2026, 9, 1, tzinfo=UTC))
    with pytest.raises(cases.AnalysisError):
        history.compare(db, cid, [data[0][0].channel_id])
    with pytest.raises(cases.AnalysisError):
        history.assert_current(db, d)
    assert db.execute("SELECT COUNT(*) FROM case_revisions").fetchone()[0] == 0


def test_removed_source_in_old_revision_cannot_leak(db, data):
    old = deepcopy(data)
    old[0][0] = replace(old[0][0], channel_id="999", message_id="400000000000000001")
    old[1]["observations"][0]["message_id"] = old[0][0].message_id
    storage.store_messages(db, [asdict(old[0][0])])
    cid = save(db, old)
    save(db, data, selected=data[0][1:], case_id=cid)
    with pytest.raises(cases.AnalysisError, match="allowlist"):
        history.compare(db, cid, [data[0][1].channel_id])


def test_zero_or_one_revision_refuses_comparison(db, data):
    with pytest.raises(cases.AnalysisError, match="Two retained"):
        history.compare(db, 999, [data[0][0].channel_id])
    cid = save(db, data)
    with pytest.raises(cases.AnalysisError, match="Two retained"):
        history.compare(db, cid, [data[0][0].channel_id])


def test_current_marker_is_used_for_both_snapshots(db, data):
    cid = pair(db, data)
    old = history.compare(db, cid, [data[0][0].channel_id])
    cases.mark_release(db, cid, "Later marker", "2026-09-01T00:00:00+00:00")
    new = history.compare(db, cid, [data[0][0].channel_id])
    assert new["before_status"] == new["after_status"]
    assert new["before_status"]["failure_authors"] == 0
    with pytest.raises(cases.AnalysisError, match="marker changed"):
        history.assert_current(db, old)
    assert "not an audit of statuses or markers" in history.render(new, 123)


def test_new_revision_invalidates_prepared_response(db, data):
    cid = pair(db, data)
    d = history.compare(db, cid, [data[0][0].channel_id])
    save(db, data, case_id=cid)
    with pytest.raises(cases.AnalysisError):
        history.assert_current(db, d)


def test_invalid_retained_payload_fails_closed(db, data):
    cid = pair(db, data)
    db.execute("UPDATE case_revisions SET payload = '{}'")
    db.commit()
    with pytest.raises(cases.AnalysisError, match="invalid"):
        history.compare(db, cid, [data[0][0].channel_id])
    assert not db.in_transaction


def test_read_does_not_commit_outer_transaction(db, data):
    cid = pair(db, data)
    db.execute("UPDATE cases SET intervention_note = 'uncommitted' WHERE id = ?", (cid,))
    history.compare(db, cid, [data[0][0].channel_id])
    assert db.in_transaction
    db.rollback()
    assert db.execute("SELECT intervention_note FROM cases").fetchone()[0] == "Fictional patch"


def test_history_is_bounded_even_if_direct_save_exceeds_context(db, data):
    cid = pair(db, data)
    extras = [replace(data[0][0], message_id=str(90000 + i)) for i in range(121)]
    storage.store_messages(db, [asdict(m) for m in extras])
    save(db, data, selected=[*data[0], *extras], case_id=cid)
    with pytest.raises(cases.AnalysisError, match="bound"):
        history.compare(db, cid, [data[0][0].channel_id])


def test_executable_demo_uses_no_network(monkeypatch, capsys):
    monkeypatch.setattr(httpx, "post", Mock(side_effect=AssertionError("No network")))
    text = run()
    assert "False reassurance caused by an omitted observation" in text
    assert "CAUTION" in text and "Zero case revisions remain" in text
    assert main(["changes-demo"]) == 0
    assert "No model or Discord connection" in capsys.readouterr().out


def interaction(pilot, guild_id=None):
    return SimpleNamespace(
        guild_id=pilot.client.config.guild_id if guild_id is None else guild_id,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def install_case(pilot, data):
    # Use an allowed channel from the simulated pilot guild.
    messages, payload = deepcopy(data)
    messages = [
        replace(m, channel_id=str(pilot.client.config.source_channel_ids[0])) for m in messages
    ]
    storage.store_messages(pilot.client.connection, [asdict(m) for m in messages])
    return pair(pilot.client.connection, (messages, payload))


def test_command_is_admin_only_and_ephemeral(pilot, data, monkeypatch):
    cid = install_case(pilot, data)
    command = pilot.client.tree.get_command("seismograph_changes")
    assert command.guild_only and command.default_permissions.administrator
    for check in command.checks:
        with pytest.raises(discord.app_commands.MissingPermissions):
            check(SimpleNamespace(permissions=discord.Permissions.none()))
        assert check(SimpleNamespace(permissions=discord.Permissions(administrator=True)))
    monkeypatch.setattr(
        "seismograph.analysis.LLMClient.complete_json", Mock(side_effect=AssertionError("No model"))
    )
    event = interaction(pilot)
    asyncio.run(command.callback(event, cid))
    assert event.followup.send.call_count
    assert "Retained revisions" in event.followup.send.call_args_list[0].args[0]
    for call in event.followup.send.call_args_list:
        assert call.kwargs["ephemeral"]
        assert call.kwargs["allowed_mentions"].everyone is False
        assert len(call.args[0]) <= 2000


def test_wrong_guild_gets_no_case_data(pilot, data):
    cid = install_case(pilot, data)
    event = interaction(pilot, 999)
    asyncio.run(pilot.client.tree.get_command("seismograph_changes").callback(event, cid))
    event.response.defer.assert_not_called()
    event.followup.send.assert_not_called()


def test_command_does_not_run_during_another_report(pilot, data):
    cid = install_case(pilot, data)
    event = interaction(pilot)

    async def execute():
        async with pilot.client.report_lock:
            await pilot.client.tree.get_command("seismograph_changes").callback(event, cid)

    asyncio.run(execute())
    assert "Another report" in event.followup.send.call_args.args[0]


def test_command_stops_remaining_chunks_after_invalidation(pilot, data, monkeypatch):
    cid = install_case(pilot, data)
    event = interaction(pilot)
    from seismograph import case_commands

    monkeypatch.setattr(
        case_commands, "split_for_discord", lambda text: ["First", "Sensitive second"]
    )

    async def send(text, **kwargs):
        if text == "First":
            storage.opt_out(pilot.client.connection, data[0][0].author_hash)

    event.followup.send.side_effect = send
    asyncio.run(pilot.client.tree.get_command("seismograph_changes").callback(event, cid))
    sent = [call.args[0] for call in event.followup.send.call_args_list]
    assert "First" in sent and "Sensitive second" not in sent
    assert any("evidence or marker changed" in text for text in sent)
