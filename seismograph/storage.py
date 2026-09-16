"""SQLite storage. Direct sqlite3, no ORM."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from .scoring import Signal

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4

REVIEW_SCHEMA = """
ALTER TABLE case_revisions ADD COLUMN review_key TEXT NOT NULL DEFAULT '';
UPDATE case_revisions SET review_key = lower(hex(randomblob(16)));
CREATE UNIQUE INDEX case_revision_review_key ON case_revisions(review_key);
CREATE INDEX case_revisions_case ON case_revisions(case_id, id);
CREATE TRIGGER assign_review_key AFTER INSERT ON case_revisions
BEGIN
    UPDATE case_revisions SET review_key = lower(hex(randomblob(16))) WHERE id = NEW.id;
END;
CREATE TABLE case_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id INTEGER NOT NULL REFERENCES case_revisions(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN
        ('failure', 'success', 'counterexample', 'workaround', 'unclear', 'exclude')),
    reason TEXT NOT NULL,
    reviewer_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX case_corrections_revision ON case_corrections(revision_id, id);
CREATE TABLE case_withdrawals (
    correction_id INTEGER PRIMARY KEY REFERENCES case_corrections(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    reviewer_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER erase_reviewer_corrections AFTER INSERT ON optouts
BEGIN
    DELETE FROM case_corrections WHERE reviewer_hash = NEW.author_hash
        OR id IN (SELECT correction_id FROM case_withdrawals
                  WHERE reviewer_hash = NEW.author_hash);
END;
"""

CASE_SCHEMA = """
CREATE TABLE cases (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    intervention_at TEXT,
    intervention_note TEXT
);
CREATE TABLE case_revisions (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE case_inputs (
    revision_id INTEGER NOT NULL REFERENCES case_revisions(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL REFERENCES messages(message_id),
    PRIMARY KEY(revision_id, message_id)
);
CREATE INDEX case_inputs_message ON case_inputs(message_id);
CREATE TRIGGER invalidate_case_delete BEFORE DELETE ON messages
BEGIN
    DELETE FROM case_revisions WHERE case_id IN (
        SELECT r.case_id FROM case_revisions r JOIN case_inputs i ON i.revision_id = r.id
        WHERE i.message_id = OLD.message_id
    );
END;
CREATE TRIGGER invalidate_case_edit BEFORE UPDATE OF content ON messages
WHEN OLD.content != NEW.content
BEGIN
    DELETE FROM case_revisions WHERE case_id IN (
        SELECT r.case_id FROM case_revisions r JOIN case_inputs i ON i.revision_id = r.id
        WHERE i.message_id = OLD.message_id
    );
END;
"""

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
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA secure_delete = ON")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version in (0, 1):
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + (SCHEMA if version == 0 else "")
            + """
        ALTER TABLE runs ADD COLUMN status TEXT NOT NULL DEFAULT 'analyzing';
        UPDATE runs SET status = CASE WHEN published = 1 THEN 'published' ELSE 'uncertain' END;
        CREATE TABLE optouts (author_hash TEXT PRIMARY KEY);
        CREATE INDEX messages_channel_time ON messages(channel_id, created_at);
        """
            + "\nPRAGMA user_version = 2;\nCOMMIT;"
        )
        version = 2
    elif version not in (2, 3, SCHEMA_VERSION):
        raise RuntimeError(
            f"database schema version {version} does not match expected {SCHEMA_VERSION}"
        )
    if version == 2:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + CASE_SCHEMA + "\nPRAGMA user_version = 3;\nCOMMIT;"
        )
        version = 3
    if version == 3:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + REVIEW_SCHEMA + "\nPRAGMA user_version = 4;\nCOMMIT;"
        )
    return connection


def store_messages(connection: sqlite3.Connection, messages: list[dict]) -> int:
    """Upsert changed content, excluding opted-out authors. Return changed rows."""
    cursor = connection.executemany(
        "INSERT OR IGNORE INTO messages (message_id, channel_id, author_hash, created_at, content)"
        " SELECT :message_id, :channel_id, :author_hash, :created_at, :content"
        " WHERE NOT EXISTS (SELECT 1 FROM optouts WHERE author_hash = :author_hash)"
        " ON CONFLICT(message_id) DO UPDATE SET content = excluded.content"
        " WHERE messages.content != excluded.content",
        messages,
    )
    connection.commit()
    return cursor.rowcount


def messages_between(
    connection: sqlite3.Connection,
    start: str,
    end: str,
    channel_ids: tuple[int, ...] | None = None,
    limit: int = 20_001,
) -> list[dict]:
    scope = ""
    params: list = [start, end]
    if channel_ids is not None:
        scope = f" AND channel_id IN ({','.join('?' for _ in channel_ids)})"
        params.extend(str(i) for i in channel_ids)
    rows = connection.execute(
        "SELECT message_id, channel_id, author_hash, created_at, content FROM messages"
        f" WHERE created_at >= ? AND created_at < ?{scope} ORDER BY created_at, message_id LIMIT ?",
        (*params, limit),
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
    current = connection.execute("SELECT * FROM runs WHERE id = ?", (before_run_id,)).fetchone()
    start = datetime.fromisoformat(current["period_start"])
    duration = datetime.fromisoformat(current["period_end"]) - start
    row = connection.execute(
        "SELECT id FROM runs WHERE id < ? AND published = 1 AND period_start = ?"
        " AND period_end = ? ORDER BY id DESC LIMIT 1",
        (before_run_id, (start - duration).isoformat(timespec="seconds"), current["period_start"]),
    ).fetchone()
    if row is None:
        return {}
    rows = connection.execute(
        "SELECT title, category, message_count FROM signals WHERE run_id = ?",
        (row["id"],),
    ).fetchall()
    return {(r["title"].strip().lower(), r["category"]): r["message_count"] for r in rows}


def validate_snapshot(connection: sqlite3.Connection, snapshot: list) -> None:
    """Must run inside the same write transaction as the derived-data save."""
    from .analysis import AnalysisError, prepare

    for message in snapshot:
        row = connection.execute(
            "SELECT * FROM messages WHERE message_id = ?", (message.message_id,)
        ).fetchone()
        current = prepare([dict(row)]) if row else []
        if not current or current[0] != message:
            raise AnalysisError("Evidence changed during analysis; rerun the report")


def save_signals(
    connection: sqlite3.Connection, run_id: int, signals: list[Signal], snapshot: list | None = None
) -> None:
    with connection:
        if snapshot is not None:
            connection.execute("BEGIN IMMEDIATE")
            validate_snapshot(connection, snapshot)
        _insert_signals(connection, run_id, signals)


def _insert_signals(connection: sqlite3.Connection, run_id: int, signals: list[Signal]) -> None:
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


def mark_published(connection: sqlite3.Connection, run_id: int, report_message_id: str) -> None:
    connection.execute(
        "UPDATE runs SET published = 1, status = 'published', report_message_id = ? WHERE id = ?",
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
    deleted = cursor.rowcount
    connection.execute("DELETE FROM runs WHERE period_end < ?", (cutoff,))
    connection.execute(
        "DELETE FROM cases WHERE created_at < ? AND id NOT IN (SELECT case_id FROM case_revisions)",
        (cutoff,),
    )
    connection.execute(
        "DELETE FROM evidence WHERE message_id NOT IN (SELECT message_id FROM messages)"
    )
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    log.info("pruned %d message(s) older than %s", deleted, cutoff)
    return deleted


def set_status(connection: sqlite3.Connection, run_id: int, status: str) -> None:
    connection.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
    connection.commit()


def forget_messages(connection: sqlite3.Connection, ids: list[str]) -> None:
    for message_id in ids:
        connection.execute(
            "DELETE FROM signals WHERE id IN (SELECT signal_id FROM evidence WHERE message_id = ?)",
            (message_id,),
        )
        connection.execute("DELETE FROM messages WHERE message_id = ?", (message_id,))
    connection.commit()


def opt_out(connection: sqlite3.Connection, author_hash: str) -> None:
    ids = [
        r[0]
        for r in connection.execute(
            "SELECT message_id FROM messages WHERE author_hash = ?", (author_hash,)
        )
    ]
    forget_messages(connection, ids)
    connection.execute("DELETE FROM feedback WHERE author_hash = ?", (author_hash,))
    connection.execute("INSERT OR IGNORE INTO optouts VALUES (?)", (author_hash,))
    connection.commit()
