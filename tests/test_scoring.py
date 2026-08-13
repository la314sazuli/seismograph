from __future__ import annotations

import pytest
from conftest import iso, signal

from seismograph.scoring import (
    breadth_component,
    classify_trend,
    is_emerging,
    rank,
    repeat_component,
    tremor_score,
)

PERIOD = 7


def test_one_loud_user_does_not_outrank_broader_evidence():
    loud = signal(
        title="Bulk export produces no file",
        distinct_users=1,
        message_count=30,
        supporting_message_ids=tuple(str(i) for i in range(30)),
    )
    broad = signal(
        title="Saved filters reset",
        distinct_users=10,
        message_count=10,
        supporting_message_ids=tuple(str(i) for i in range(10)),
    )
    assert tremor_score(broad, PERIOD) > tremor_score(loud, PERIOD)


def test_repeat_messages_are_capped():
    modest = repeat_component(distinct_users=1, message_count=25)
    extreme = repeat_component(distinct_users=1, message_count=5000)
    assert modest == pytest.approx(1.0)
    assert extreme == pytest.approx(1.0)


def test_breadth_is_bounded_for_large_communities():
    assert breadth_component(0) == 0.0
    assert breadth_component(50) == pytest.approx(1.0)
    assert breadth_component(100_000) == pytest.approx(1.0)


def test_scores_stay_within_bounds():
    weakest = signal(
        severity=1,
        confidence=0.0,
        distinct_users=1,
        message_count=1,
        previous_message_count=1000,
    )
    strongest = signal(
        severity=5,
        confidence=1.0,
        distinct_users=5000,
        message_count=50_000,
        previous_message_count=0,
    )
    assert 0 <= tremor_score(weakest, PERIOD) <= 5
    assert tremor_score(strongest, PERIOD) == 100


def test_score_is_deterministic():
    candidate = signal(distinct_users=6, message_count=11, previous_message_count=4)
    assert len({tremor_score(candidate, PERIOD) for _ in range(20)}) == 1


def test_score_increases_with_distinct_users():
    scores = [
        tremor_score(signal(distinct_users=users, message_count=users * 2), PERIOD)
        for users in (1, 3, 8, 20, 40)
    ]
    assert scores == sorted(scores)
    assert scores[0] < scores[-1]


def test_score_increases_with_severity():
    scores = [tremor_score(signal(severity=level), PERIOD) for level in (1, 2, 3, 4, 5)]
    assert scores == sorted(scores)


def test_low_confidence_is_penalised():
    confident = signal(confidence=1.0)
    unsure = signal(confidence=0.2)
    assert tremor_score(unsure, PERIOD) < tremor_score(confident, PERIOD)


def test_missing_history_uses_neutral_growth_and_novelty():
    unknown_history = signal(previous_message_count=None, message_count=10, distinct_users=5)
    shrinking = signal(previous_message_count=40, message_count=10, distinct_users=5)
    brand_new = signal(previous_message_count=0, message_count=10, distinct_users=5)
    unknown_score = tremor_score(unknown_history, PERIOD)
    assert tremor_score(shrinking, PERIOD) < unknown_score < tremor_score(brand_new, PERIOD)


def test_small_but_fast_growing_signal_beats_a_larger_flat_one():
    growing = signal(
        title="Two-factor setup rejects valid codes",
        distinct_users=6,
        message_count=12,
        previous_message_count=1,
        severity=4,
    )
    flat = signal(
        title="Long-standing layout complaint",
        distinct_users=6,
        message_count=13,
        previous_message_count=13,
        severity=4,
    )
    assert tremor_score(growing, PERIOD) > tremor_score(flat, PERIOD)


def test_invalid_severity_is_rejected():
    with pytest.raises(ValueError):
        tremor_score(signal(severity=7), PERIOD)


def test_trend_classification():
    assert classify_trend(10, None) == "new"
    assert classify_trend(10, 0) == "new"
    assert classify_trend(10, 4) == "rising"
    assert classify_trend(10, 10) == "stable"
    assert classify_trend(4, 10) == "falling"


def test_emerging_requires_breadth_recency_and_confidence():
    candidate = signal(
        distinct_users=4,
        message_count=6,
        previous_message_count=0,
        confidence=0.8,
        first_seen=iso(100),
        last_seen=iso(112),
    )
    assert is_emerging(candidate, largest_message_count=40)

    from dataclasses import replace

    assert not is_emerging(replace(candidate, distinct_users=2), 40)
    assert not is_emerging(replace(candidate, confidence=0.4), 40)
    assert not is_emerging(replace(candidate, last_seen=iso(400)), 40)
    assert not is_emerging(replace(candidate, previous_message_count=25), 40)
    assert not is_emerging(candidate, largest_message_count=8)
    assert not is_emerging(replace(candidate, category="praise"), 40)


def test_rank_marks_emerging_and_orders_by_score():
    dominant = signal(
        title="Saved filters reset",
        distinct_users=12,
        message_count=40,
        previous_message_count=20,
        severity=4,
    )
    emerging = signal(
        title="Two-factor setup rejects valid codes",
        distinct_users=4,
        message_count=6,
        previous_message_count=0,
        confidence=0.8,
        first_seen=iso(100),
        last_seen=iso(112),
    )
    ranked = rank([emerging, dominant], PERIOD)
    trends = {item.title: item.trend for item in ranked}
    assert trends["Two-factor setup rejects valid codes"] == "emerging"
    assert trends["Saved filters reset"] == "rising"
    assert [item.tremor_score for item in ranked] == sorted(
        (item.tremor_score for item in ranked), reverse=True
    )


def test_rank_is_stable_across_input_order():
    a = signal(title="Alpha", distinct_users=5, message_count=8)
    b = signal(title="Beta", distinct_users=5, message_count=8)
    assert [s.title for s in rank([a, b], PERIOD)] == [s.title for s in rank([b, a], PERIOD)]
