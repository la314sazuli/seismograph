from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from conftest import iso, signal

from seismograph import storage
from seismograph.pipeline import analysis_period, apply_history, period_label


@pytest.fixture
def connection():
    with storage.connect(":memory:") as connection:
        yield connection


def rows(count: int = 3) -> list[dict]:
    return [
        {
            "message_id": str(1000 + index),
            "channel_id": "200000000000000011",
            "author_hash": f"user-{index}",
            "created_at": iso(index * 6),
            "content": f"report number {index}",
        }
        for index in range(count)
    ]


def test_messages_are_stored_once(connection):
    assert storage.store_messages(connection, rows()) == 3
    assert storage.store_messages(connection, rows()) == 0
    assert len(storage.messages_between(connection, iso(-1), iso(100))) == 3


def test_messages_between_respects_the_window(connection):
    storage.store_messages(connection, rows(4))
    inside = storage.messages_between(connection, iso(0), iso(12))
    assert [row["message_id"] for row in inside] == ["1000", "1001"]


def test_scheduled_runs_cannot_be_duplicated(connection):
    start, end = iso(0), iso(168)
    assert not storage.scheduled_run_exists(connection, start, end)
    storage.start_run(connection, "scheduled", start, end)
    assert storage.scheduled_run_exists(connection, start, end)
    with pytest.raises(sqlite3.IntegrityError):
        storage.start_run(connection, "scheduled", start, end)


def test_manual_runs_may_repeat_a_period(connection):
    start, end = iso(0), iso(168)
    storage.start_run(connection, "manual", start, end)
    storage.start_run(connection, "manual", start, end)
    assert not storage.scheduled_run_exists(connection, start, end)


def test_signals_and_evidence_are_persisted(connection):
    run_id = storage.start_run(connection, "manual", iso(0), iso(168))
    storage.save_signals(connection, run_id, [signal(tremor_score=61)])
    stored = connection.execute("SELECT * FROM signals WHERE run_id = ?", (run_id,)).fetchall()
    assert len(stored) == 1 and stored[0]["tremor_score"] == 61
    evidence = connection.execute(
        "SELECT message_id FROM evidence WHERE signal_id = ?", (stored[0]["id"],)
    ).fetchall()
    assert {row["message_id"] for row in evidence} == {"1", "2", "3"}


def test_history_comes_from_the_last_published_run(connection):
    first = storage.start_run(connection, "scheduled", iso(0), iso(168))
    storage.save_signals(connection, first, [signal(message_count=5)])
    storage.mark_published(connection, first, "900000000000000001")

    unpublished = storage.start_run(connection, "manual", iso(24), iso(192))
    storage.save_signals(connection, unpublished, [signal(message_count=999)])

    current = storage.start_run(connection, "scheduled", iso(168), iso(336))
    history = storage.previous_message_counts(connection, current)
    assert history == {("saved filters reset after reopening", "broken"): 5}


def test_apply_history_marks_unseen_signals_as_new(connection):
    first = storage.start_run(connection, "scheduled", iso(0), iso(168))
    storage.save_signals(connection, first, [signal(message_count=5)])
    storage.mark_published(connection, first, "900000000000000001")

    current = storage.start_run(connection, "scheduled", iso(168), iso(336))
    known = signal(message_count=9)
    fresh = signal(title="Two-factor setup rejects valid codes", category="blocked")
    updated = {
        s.title: s.previous_message_count
        for s in apply_history(connection, current, [known, fresh])
    }
    assert updated["Saved filters reset after reopening"] == 5
    assert updated["Two-factor setup rejects valid codes"] == 0


def test_apply_history_leaves_counts_unknown_on_a_first_run(connection):
    run_id = storage.start_run(connection, "scheduled", iso(0), iso(168))
    assert apply_history(connection, run_id, [signal()])[0].previous_message_count is None


def test_feedback_is_recorded_once_per_reactor(connection):
    run_id = storage.start_run(connection, "manual", iso(0), iso(168))
    storage.mark_published(connection, run_id, "900000000000000042")
    assert storage.run_for_report_message(connection, "900000000000000042") == run_id
    assert storage.run_for_report_message(connection, "900000000000000043") is None

    storage.record_feedback(connection, run_id, "✅", "hash-a")
    storage.record_feedback(connection, run_id, "✅", "hash-a")
    storage.record_feedback(connection, run_id, "❌", "hash-b")
    stored = connection.execute("SELECT emoji, author_hash FROM feedback").fetchall()
    assert len(stored) == 2


def test_prune_removes_only_old_messages(connection):
    storage.store_messages(connection, rows(4))
    now = datetime.fromisoformat(iso(0)) + timedelta(days=40)
    assert storage.prune(connection, retention_days=39, now=now) == 4
    assert storage.messages_between(connection, iso(-1), iso(100)) == []


def test_prune_keeps_recent_messages(connection):
    storage.store_messages(connection, rows(4))
    now = datetime.fromisoformat(iso(24))
    assert storage.prune(connection, retention_days=30, now=now) == 0


def test_prune_refuses_a_zero_window(connection):
    with pytest.raises(ValueError):
        storage.prune(connection, retention_days=0)


def test_analysis_period_covers_whole_local_days():
    now = datetime(2026, 8, 10, 9, 30, tzinfo=UTC)
    start, end = analysis_period(now, "Europe/Lisbon", 7)
    assert start == "2026-08-02T23:00:00+00:00"
    assert end == "2026-08-09T23:00:00+00:00"
    assert period_label(start, end, "Europe/Lisbon") == "August 3–9"


def test_analysis_period_is_stable_within_a_day():
    morning = analysis_period(datetime(2026, 8, 10, 6, 0, tzinfo=UTC), "UTC", 7)
    evening = analysis_period(datetime(2026, 8, 10, 22, 0, tzinfo=UTC), "UTC", 7)
    assert morning == evening


def test_period_label_spans_months():
    start, end = analysis_period(datetime(2026, 9, 2, 12, 0, tzinfo=UTC), "UTC", 7)
    assert period_label(start, end, "UTC") == "August 26 – September 1"
