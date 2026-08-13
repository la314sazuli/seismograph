from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seismograph.analysis import PreparedMessage
from seismograph.scoring import Signal

BASE = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)


def iso(hours: float) -> str:
    return (BASE + timedelta(hours=hours)).isoformat(timespec="seconds")


def message(
    message_id: str, author: str, hours: float, content: str = "a report about a thing"
) -> PreparedMessage:
    return PreparedMessage(
        message_id=message_id,
        channel_id="200000000000000011",
        author_hash=author,
        created_at=iso(hours),
        content=content,
    )


def signal(**overrides) -> Signal:
    """A valid signal with plausible defaults; override only what a test needs."""
    defaults = {
        "title": "Saved filters reset after reopening",
        "category": "broken",
        "surface": "saved filters",
        "expected": "Filters should persist.",
        "observed": "Users report filters are lost.",
        "severity": 3,
        "confidence": 0.8,
        "evidence_rationale": "Independent reports of the same behaviour.",
        "suggested_next_step": "Attempt reproduction.",
        "supporting_message_ids": ("1", "2", "3"),
        "representative_message_ids": ("1", "2", "3"),
        "distinct_users": 3,
        "message_count": 3,
        "first_seen": iso(0),
        "last_seen": iso(10),
    }
    defaults.update(overrides)
    return Signal(**defaults)


@pytest.fixture
def env() -> dict[str, str]:
    return {
        "DISCORD_TOKEN": "test-token",
        "DISCORD_GUILD_ID": "100000000000000001",
        "SOURCE_CHANNEL_IDS": "200000000000000011,200000000000000012",
        "REPORT_CHANNEL_ID": "200000000000000099",
        "LLM_API_KEY": "test-key",
        "LLM_BASE_URL": "https://llm.example.invalid/v1",
        "LLM_MODEL": "test-model",
        "AUTHOR_HASH_SALT": "a-sufficiently-long-salt",
    }
