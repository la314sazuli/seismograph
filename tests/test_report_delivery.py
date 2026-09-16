import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_offline_pilot import END, START
from test_offline_pilot import pilot as pilot

from seismograph import bot, storage
from seismograph.analysis import AnalysisError


@pytest.mark.parametrize("change", ["edit", "delete", "optout"])
def test_privacy_change_during_report_send_stops_remaining_chunks(pilot, monkeypatch, change):
    monkeypatch.setattr(
        bot, "split_for_discord", lambda text: ["First part", "Sensitive later part"]
    )
    sent = []

    async def send(content, **kwargs):
        sent.append(content)
        db = pilot.client.connection
        row = db.execute("SELECT * FROM messages ORDER BY message_id LIMIT 1").fetchone()
        if change == "edit":
            storage.store_messages(db, [{**dict(row), "content": "Changed source report content."}])
        elif change == "delete":
            storage.forget_messages(db, [row["message_id"]])
        else:
            storage.opt_out(db, row["author_hash"])
        return SimpleNamespace(id=800000000000000123, add_reaction=AsyncMock())

    pilot.report.send.side_effect = send
    with pytest.raises(AnalysisError, match="Evidence changed"):
        asyncio.run(pilot.client.publish("scheduled", START, END))
    assert sent == ["First part"]
    row = pilot.client.connection.execute("SELECT * FROM runs").fetchone()
    assert row["status"] == "uncertain" and row["published"] == 0
    assert row["report_message_id"] == "800000000000000123"
    assert storage.scheduled_run_exists(pilot.client.connection, START, END)
    assert not pilot.client.connection.execute("SELECT * FROM signals").fetchall()


def test_unchanged_report_snapshot_sends_all_chunks(pilot, monkeypatch):
    monkeypatch.setattr(bot, "split_for_discord", lambda text: ["First part", "Second part"])
    asyncio.run(pilot.client.publish("manual", START, END))
    assert [part[0] for part in pilot.posted] == ["First part", "Second part"]
    assert pilot.client.connection.execute("SELECT status FROM runs").fetchone()[0] == "published"
