from __future__ import annotations

import pytest
from conftest import iso, message
from conftest import signal as base_signal

from seismograph.report import (
    DISCORD_MESSAGE_LIMIT,
    InsufficientEvidence,
    build_report,
    jump_link,
    render_markdown,
    split_for_discord,
)
from seismograph.scoring import rank

GUILD = 100000000000000001
CHANNEL = "200000000000000011"


def signal(**kwargs):
    kwargs.setdefault("supporting_message_ids", ("1000", "1001", "1002"))
    kwargs.setdefault("representative_message_ids", kwargs["supporting_message_ids"][:3])
    return base_signal(**kwargs)


def messages(count: int = 12):
    return [message(str(1000 + index), f"user-{index % 6}", index) for index in range(count)]


def test_jump_link_format():
    assert (
        jump_link(GUILD, CHANNEL, "900000000000000137")
        == f"https://discord.com/channels/{GUILD}/{CHANNEL}/900000000000000137"
    )


@pytest.mark.parametrize(
    "args",
    [
        (GUILD, CHANNEL, "not-an-id"),
        (GUILD, "channel", "900000000000000137"),
        (0, CHANNEL, "900000000000000137"),
        (GUILD, CHANNEL, "-5"),
    ],
)
def test_jump_link_refuses_non_ids(args):
    with pytest.raises(ValueError):
        jump_link(*args)


def test_report_requires_evidence():
    thin = signal(distinct_users=1, message_count=1, supporting_message_ids=("1000",))
    with pytest.raises(InsufficientEvidence):
        build_report([thin], messages(), "August 3–9")


def test_empty_analysis_does_not_produce_a_report():
    with pytest.raises(InsufficientEvidence):
        build_report([], messages(), "August 3–9")


def test_low_confidence_signals_are_labelled_unconfirmed_not_ranked():
    strong = signal(title="Saved filters reset", distinct_users=6, message_count=9)
    weak = signal(
        title="Search returns stale results", confidence=0.3, distinct_users=2, message_count=4
    )
    report = build_report(rank([strong, weak], 7), messages(), "August 3–9")
    assert [item.title for item in report.tremors] == ["Saved filters reset"]
    assert [item.title for item in report.needs_review] == ["Search returns stale results"]

    markdown = render_markdown(report, GUILD, messages())
    assert "Needs Human Review" in markdown
    assert "Unconfirmed" in markdown
    review_section = markdown.split("## Needs Human Review")[1]
    assert "Tremor Score" not in review_section


def test_single_user_signal_is_not_ranked_but_is_surfaced_for_review():
    loud = signal(
        title="Bulk export produces no file",
        distinct_users=1,
        message_count=14,
        supporting_message_ids=tuple(str(1000 + i) for i in range(12)),
    )
    broad = signal(title="Saved filters reset", distinct_users=6, message_count=9)
    report = build_report(rank([loud, broad], 7), messages(), "August 3–9")
    assert [item.title for item in report.tremors] == ["Saved filters reset"]
    assert [item.title for item in report.needs_review] == ["Bulk export produces no file"]


def test_praise_needs_independent_breadth():
    complaint = signal(title="File uploads fail", distinct_users=8, message_count=18)
    thin_praise = signal(
        title="Nice upload speed", category="praise", distinct_users=2, message_count=2
    )
    strong_praise = signal(
        title="Upload speed improved",
        category="praise",
        distinct_users=6,
        message_count=6,
        severity=1,
    )
    report = build_report(
        rank([complaint, thin_praise, strong_praise], 7), messages(), "August 3–9"
    )
    assert [item.title for item in report.counter_signals] == ["Upload speed improved"]
    assert [item.title for item in report.tremors] == ["File uploads fail"]


def test_praise_does_not_reduce_a_complaint_score():
    complaint = signal(title="File uploads fail", distinct_users=8, message_count=18)
    praise = signal(
        title="Upload speed improved", category="praise", distinct_users=20, message_count=24
    )
    alone = rank([complaint], 7)[0].tremor_score
    together = next(s for s in rank([complaint, praise], 7) if s.category == "broken").tremor_score
    assert alone == together


def test_emerging_signals_get_their_own_section():
    dominant = signal(
        title="Saved filters reset", distinct_users=12, message_count=40, previous_message_count=30
    )
    emerging = signal(
        title="Two-factor setup rejects valid codes",
        distinct_users=4,
        message_count=6,
        previous_message_count=0,
        first_seen=iso(100),
        last_seen=iso(110),
    )
    report = build_report(rank([dominant, emerging], 7), messages(), "August 3–9")
    assert [item.title for item in report.emerging] == ["Two-factor setup rejects valid codes"]
    assert [item.title for item in report.tremors] == ["Saved filters reset"]


def test_rendered_report_exposes_counts_separately_and_hides_author_hashes():
    strong = signal(
        distinct_users=6, message_count=9, supporting_message_ids=("1000", "1001", "1002")
    )
    report = build_report(rank([strong], 7), messages(), "August 3–9")
    markdown = render_markdown(report, GUILD, messages())
    assert "Distinct users: 6" in markdown
    assert "Supporting messages: 9" in markdown
    for candidate in messages():
        assert candidate.author_hash not in markdown


def test_evidence_links_only_reference_analyzed_messages():
    strong = signal(
        distinct_users=6,
        message_count=9,
        supporting_message_ids=("1000", "1001", "1002"),
        representative_message_ids=("1000", "1001", "1002"),
    )
    report = build_report(rank([strong], 7), messages(), "August 3–9")
    markdown = render_markdown(report, GUILD, messages())
    assert markdown.count("https://discord.com/channels/") == 3
    for message_id in ("1000", "1001", "1002"):
        assert jump_link(GUILD, CHANNEL, message_id) in markdown


def test_synthetic_notice_is_only_added_when_requested():
    report = build_report(rank([signal(distinct_users=6, message_count=9)], 7), messages(), "demo")
    assert "synthetic" in render_markdown(report, GUILD, messages(), synthetic=True)
    assert "synthetic" not in render_markdown(report, GUILD, messages(), synthetic=False)


def test_split_keeps_every_chunk_within_the_discord_limit():
    signals = [
        signal(title=f"Signal number {index}", distinct_users=5, message_count=9)
        for index in range(12)
    ]
    report = build_report(rank(signals, 7), messages(), "August 3–9")
    markdown = render_markdown(report, GUILD, messages())
    chunks = split_for_discord(markdown)
    assert len(chunks) > 1
    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)
    assert "".join(chunk.replace("\n", "") for chunk in chunks) == markdown.replace("\n", "")


def test_split_handles_a_single_overlong_line():
    chunks = split_for_discord("x" * 4500, limit=1000)
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "".join(chunks) == "x" * 4500


def test_split_refuses_an_unusable_limit():
    with pytest.raises(ValueError):
        split_for_discord("anything", limit=10)
