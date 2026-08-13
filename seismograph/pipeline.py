"""Orchestration shared by the slash command, the weekly schedule, and the demo."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import storage
from .analysis import LLMClient, PreparedMessage, prepare
from .analysis import analyze as run_analysis
from .report import Report, build_report
from .scoring import Signal, rank

log = logging.getLogger(__name__)


def utc_iso(moment: datetime) -> str:
    """Normalize any datetime to a sortable UTC ISO string."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def analysis_period(now: datetime, report_timezone: str, analysis_days: int) -> tuple[str, str]:
    """Return the [start, end) window as UTC ISO strings.

    The window ends at midnight of the current local day so that a report always
    covers whole days in the operator's timezone and the boundary is stable
    regardless of when the run is triggered.
    """
    local_now = now.astimezone(ZoneInfo(report_timezone))
    end_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_local = end_local - timedelta(days=analysis_days)
    return utc_iso(start_local), utc_iso(end_local)


def period_label(start: str, end: str, report_timezone: str) -> str:
    zone = ZoneInfo(report_timezone)
    start_local = datetime.fromisoformat(start).astimezone(zone)
    last_day = datetime.fromisoformat(end).astimezone(zone) - timedelta(days=1)
    if start_local.month == last_day.month:
        return f"{start_local:%B %-d}–{last_day:%-d}"
    return f"{start_local:%B %-d} – {last_day:%B %-d}"


def generate_report(
    connection: sqlite3.Connection,
    client: LLMClient,
    kind: str,
    start: str,
    end: str,
    label: str,
) -> tuple[int, Report, list[PreparedMessage], list[str]]:
    """Run the full pipeline for one period.

    Raises InsufficientEvidence when nothing clears the evidence thresholds and
    AnalysisError when the model output cannot be trusted.
    """
    stored = storage.messages_between(connection, start, end)
    messages = prepare(stored)
    log.info("period %s to %s: %d stored, %d usable", start, end, len(stored), len(messages))

    run_id = storage.start_run(connection, kind, start, end)
    signals, rejections = run_analysis(client, messages, label)
    signals = apply_history(connection, run_id, signals)
    signals = rank(signals, period_days=max(1, _period_days(start, end)))
    report = build_report(signals, messages, label)
    storage.save_signals(connection, run_id, signals)
    return run_id, report, messages, rejections


def apply_history(
    connection: sqlite3.Connection,
    run_id: int,
    signals: list[Signal],
) -> list[Signal]:
    """Attach previous-period message counts so growth and novelty can be scored."""
    from dataclasses import replace

    history = storage.previous_message_counts(connection, run_id)
    if not history:
        return signals
    return [
        replace(
            signal,
            previous_message_count=history.get((signal.title.strip().lower(), signal.category), 0),
        )
        for signal in signals
    ]


def _period_days(start: str, end: str) -> int:
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return max(1, round(delta.total_seconds() / 86400))
