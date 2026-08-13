"""Deterministic ranking.

The model classifies and extracts evidence. Everything that decides how a
signal is ranked lives here, in plain arithmetic that can be read and tested.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

CATEGORIES = ("broken", "blocked", "confusing", "missing", "disliked", "praise")
TRENDS = ("new", "emerging", "rising", "stable", "falling")

# Normalization caps. Values at or above the cap contribute a full component.
BREADTH_CAP_USERS = 50
REPEAT_CAP_MESSAGES = 20
VELOCITY_CAP_PER_DAY = 10
GROWTH_CAP_RATIO = 4.0

WEIGHTS = {
    "breadth": 0.40,
    "severity": 0.25,
    "growth": 0.13,
    "velocity": 0.12,
    "novelty": 0.05,
    "repeat": 0.05,
}

# Used when no comparable previous period is stored, so a first run neither
# inflates nor suppresses scores.
NEUTRAL_GROWTH = 0.35
NEUTRAL_NOVELTY = 0.5

# Emerging-signal thresholds.
EMERGING_MIN_USERS = 3
EMERGING_MAX_SPAN_HOURS = 48.0
EMERGING_MAX_SHARE_OF_LARGEST = 0.5
EMERGING_MIN_CONFIDENCE = 0.6
EMERGING_MAX_PREVIOUS_MESSAGES = 1


@dataclass(frozen=True)
class Signal:
    """A validated signal with the measured facts needed for ranking."""

    title: str
    category: str
    surface: str
    expected: str
    observed: str
    severity: int
    confidence: float
    evidence_rationale: str
    suggested_next_step: str
    supporting_message_ids: tuple[str, ...]
    representative_message_ids: tuple[str, ...]
    distinct_users: int
    message_count: int
    first_seen: str
    last_seen: str
    previous_message_count: int | None = None
    trend: str = "stable"
    tremor_score: int = 0


def _log_ratio(value: float, cap: float) -> float:
    if value <= 0 or cap <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(cap))


def breadth_component(distinct_users: int) -> float:
    return _log_ratio(distinct_users, BREADTH_CAP_USERS)


def repeat_component(distinct_users: int, message_count: int) -> float:
    """Extra messages beyond one per user, with a hard cap.

    A user repeating themselves adds a little evidence, never enough to
    outweigh a broader group.
    """
    return _log_ratio(max(0, message_count - distinct_users), REPEAT_CAP_MESSAGES)


def velocity_component(message_count: int, period_days: int) -> float:
    if period_days <= 0:
        raise ValueError("period_days must be positive")
    return _log_ratio(message_count / period_days, VELOCITY_CAP_PER_DAY)


def growth_component(message_count: int, previous_message_count: int | None) -> float:
    if previous_message_count is None:
        return NEUTRAL_GROWTH
    ratio = (message_count + 1) / (previous_message_count + 1)
    if ratio <= 1:
        return 0.0
    return min(1.0, math.log(ratio) / math.log(GROWTH_CAP_RATIO))


def novelty_component(previous_message_count: int | None) -> float:
    if previous_message_count is None:
        return NEUTRAL_NOVELTY
    return 1.0 if previous_message_count == 0 else 0.0


def confidence_multiplier(confidence: float) -> float:
    """Low confidence discounts a signal without erasing it."""
    return 0.4 + 0.6 * max(0.0, min(1.0, confidence))


def tremor_score(signal: Signal, period_days: int) -> int:
    """Return an integer 0-100. Deterministic for identical inputs."""
    if not 1 <= signal.severity <= 5:
        raise ValueError("severity must be between 1 and 5")
    components = {
        "breadth": breadth_component(signal.distinct_users),
        "severity": (signal.severity - 1) / 4,
        "growth": growth_component(signal.message_count, signal.previous_message_count),
        "velocity": velocity_component(signal.message_count, period_days),
        "novelty": novelty_component(signal.previous_message_count),
        "repeat": repeat_component(signal.distinct_users, signal.message_count),
    }
    weighted = sum(WEIGHTS[name] * value for name, value in components.items())
    score = 100 * weighted * confidence_multiplier(signal.confidence)
    return max(0, min(100, round(score)))


def classify_trend(message_count: int, previous_message_count: int | None) -> str:
    if previous_message_count is None or previous_message_count == 0:
        return "new"
    ratio = message_count / previous_message_count
    if ratio >= 1.5:
        return "rising"
    if ratio <= 0.67:
        return "falling"
    return "stable"


def span_hours(first_seen: str, last_seen: str) -> float:
    from datetime import datetime

    start = datetime.fromisoformat(first_seen)
    end = datetime.fromisoformat(last_seen)
    return abs((end - start).total_seconds()) / 3600.0


def is_emerging(signal: Signal, largest_message_count: int) -> bool:
    """Small, new, fast-forming, multi-user signals that the ranking would bury."""
    if signal.category == "praise":
        return False
    previous = signal.previous_message_count
    if previous is not None and previous > EMERGING_MAX_PREVIOUS_MESSAGES:
        return False
    if signal.distinct_users < EMERGING_MIN_USERS:
        return False
    if signal.confidence < EMERGING_MIN_CONFIDENCE:
        return False
    if span_hours(signal.first_seen, signal.last_seen) > EMERGING_MAX_SPAN_HOURS:
        return False
    if largest_message_count <= 0:
        return False
    return signal.message_count < EMERGING_MAX_SHARE_OF_LARGEST * largest_message_count


def rank(signals: list[Signal], period_days: int) -> list[Signal]:
    """Attach trend and tremor score to each signal, then order by score.

    Ties break on distinct users, then message count, then title, so repeated
    runs over the same data produce the same ordering.
    """
    scored = [
        replace(
            signal,
            trend=classify_trend(signal.message_count, signal.previous_message_count),
        )
        for signal in signals
    ]
    largest = max((s.message_count for s in scored if s.category != "praise"), default=0)
    scored = [
        replace(signal, trend="emerging") if is_emerging(signal, largest) else signal
        for signal in scored
    ]
    scored = [replace(signal, tremor_score=tremor_score(signal, period_days)) for signal in scored]
    scored.sort(
        key=lambda s: (-s.tremor_score, -s.distinct_users, -s.message_count, s.title),
    )
    return scored
