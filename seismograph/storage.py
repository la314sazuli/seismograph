"""SQLite storage. Direct sqlite3, no ORM."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from .scoring import Signal

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE messages (
    message_id  TEXT PRIMARY KEY,
    channel_id  TEXT NOT NULL,
    author_hash TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    content     TEXT NOT NULL
);
CREATE INDEX messages_created_at ON messages (created_at);

CREATE TABLE runs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    published    INTEGER NOT NULL DEFAULT 0,
    report_message_id TEXT
);
CREATE UNIQUE INDEX runs_scheduled_period
    ON runs (period_start, period_end) WHERE kind = 'scheduled';

CREATE TABLE signals (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    category      TEXT NOT NULL,
    surface       TEXT NOT NULL,
    severity      INTEGER NOT NULL,
    confidence    REAL NOT NULL,
    trend         TEXT NOT NULL,
    tremor_score  INTEGER NOT NULL,
    distinct_users INTEGER NOT NULL,
    message_count  INTEGER NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);
CREATE INDEX signals_run ON signals (run_id);
CREATE INDEX signals_title ON signals (title, category);

CREATE TABLE evidence (
    signal_id  INTEGER NOT NULL REFERENCES signals (id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    PRIMARY KEY (signal_id, message_id)
);

CREATE TABLE feedback (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    emoji      TEXT NOT NULL,
    author_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, emoji, author_hash)
);
"""


def connect(path: str) -> sqlite3.Connection:
    """Open a connection and apply the schema if the database is new."""
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        connection.executescript(SCHEMA)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    elif version != SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {version} does not match expected {SCHEMA_VERSION}"
        )
    return connection


def store_messages(connection: sqlite3.Connection, messages: list[dict]) -> int:
    """Insert messages, ignoring ones already stored. Returns rows inserted."""
    cursor = connection.executemany(
        "INSERT OR IGNORE INTO messages (message_id, channel_id, author_hash, created_at, content)"
        " VALUES (:message_id, :channel_id, :author_hash, :created_at, :content)",
        messages,
    )
    connection.commit()
    return cursor.rowcount


def messages_between(connection: sqlite3.Connection, start: str, end: str) -> list[dict]:
    rows = connection.execute(
        "SELECT message_id, channel_id, author_hash, created_at, content FROM messages"
        " WHERE created_at >= ? AND created_at < ? ORDER BY created_at, message_id",
        (start, end),
    ).fetchall()
    return [dict(row) for row in rows]


def scheduled_run_exists(connection: sqlite3.Connection, start: str, end: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM runs WHERE kind = 'scheduled' AND period_start = ? AND period_end = ?",
        (start, end),
    ).fetchone()
    return row is not None


def start_run(connection: sqlite3.Connection, kind: str, start: str, end: str) -> int:
    cursor = connection.execute(
        "INSERT INTO runs (kind, period_start, period_end, started_at) VALUES (?, ?, ?, ?)",
        (kind, start, end, datetime.now(UTC).isoformat(timespec="seconds")),
    )
    connection.commit()
    return int(cursor.lastrowid)


def previous_message_counts(
    connection: sqlite3.Connection,
    before_run_id: int,
) -> dict[tuple[str, str], int]:
    """Message counts per signal from the most recent earlier published run.

    Signals are matched across runs by normalized title and category, which is
    coarse but transparent. Titles that drift between runs are simply treated
    as new, which the score handles with the neutral growth default.
    """
    row = connection.execute(
        "SELECT id FROM runs WHERE id < ? AND published = 1 ORDER BY id DESC LIMIT 1",
        (before_run_id,),
    ).fetchone()
    if row is None:
        return {}
    rows = connection.execute(
        "SELECT title, category, message_count FROM signals WHERE run_id = ?",
        (row["id"],),
    ).fetchall()
    return {(r["title"].strip().lower(), r["category"]): r["message_count"] for r in rows}


def save_signals(connection: sqlite3.Connection, run_id: int, signals: list[Signal]) -> None:
    for signal in signals:
        cursor = connection.execute(
            "INSERT INTO signals (run_id, title, category, surface, severity, confidence, trend,"
            " tremor_score, distinct_users, message_count, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                signal.title,
                signal.category,
                signal.surface,
                signal.severity,
                signal.confidence,
                signal.trend,
                signal.tremor_score,
                signal.distinct_users,
                signal.message_count,
                signal.first_seen,
                signal.last_seen,
            ),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO evidence (signal_id, message_id) VALUES (?, ?)",
            [(cursor.lastrowid, message_id) for message_id in signal.supporting_message_ids],
        )
    connection.commit()


def mark_published(connection: sqlite3.Connection, run_id: int, report_message_id: str) -> None:
    connection.execute(
        "UPDATE runs SET published = 1, report_message_id = ? WHERE id = ?",
        (report_message_id, run_id),
    )
    connection.commit()


def run_for_report_message(connection: sqlite3.Connection, report_message_id: str) -> int | None:
    row = connection.execute(
        "SELECT id FROM runs WHERE report_message_id = ?", (report_message_id,)
    ).fetchone()
    return None if row is None else int(row["id"])


def record_feedback(
    connection: sqlite3.Connection,
    run_id: int,
    emoji: str,
    author_hash: str,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO feedback (run_id, emoji, author_hash, created_at)"
        " VALUES (?, ?, ?, ?)",
        (run_id, emoji, author_hash, datetime.now(UTC).isoformat(timespec="seconds")),
    )
    connection.commit()


def prune(connection: sqlite3.Connection, retention_days: int, now: datetime | None = None) -> int:
    """Delete messages older than the retention window. Returns rows deleted."""
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=retention_days)).isoformat(timespec="seconds")
    cursor = connection.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
    connection.commit()
    log.info("pruned %d message(s) older than %s", cursor.rowcount, cutoff)
    return cursor.rowcount
