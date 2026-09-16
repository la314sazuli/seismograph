"""Report assembly and Markdown rendering."""

from __future__ import annotations

from dataclasses import dataclass

from discord.utils import escape_markdown

from .analysis import PreparedMessage
from .scoring import Signal

DISCORD_MESSAGE_LIMIT = 2000

# Evidence thresholds for the main ranking.
MIN_DISTINCT_USERS = 2
MIN_SUPPORTING_MESSAGES = 3
MIN_CONFIDENCE = 0.5

# A praise signal needs independent breadth before it is shown as context.
PRAISE_MIN_DISTINCT_USERS = 3
PRAISE_MIN_CONFIDENCE = 0.6

MAX_REVIEW_SIGNALS = 3
REVIEW_MIN_MESSAGES = 2

FEEDBACK_REACTIONS = (
    ("✅", "accurate and useful"),
    ("❌", "incorrect or misleading"),
    ("🔀", "should be merged with another signal"),
    ("🧭", "requires investigation"),
)

SYNTHETIC_NOTICE = "_Generated from synthetic fixture data. Not real community feedback._"


class InsufficientEvidence(Exception):
    """Raised when nothing in the analysis clears the evidence thresholds."""


@dataclass(frozen=True)
class Report:
    period_label: str
    message_count: int
    contributor_count: int
    tremors: list[Signal]
    emerging: list[Signal]
    counter_signals: list[Signal]
    needs_review: list[Signal]

    @property
    def published_signal_count(self) -> int:
        return len(self.tremors) + len(self.emerging) + len(self.counter_signals)


def jump_link(guild_id: int, channel_id: int | str, message_id: int | str) -> str:
    """Build a Discord jump link. Ids must already be known to be real."""
    for name, value in (
        ("guild_id", guild_id),
        ("channel_id", channel_id),
        ("message_id", message_id),
    ):
        if not str(value).isdigit() or int(value) <= 0:
            raise ValueError(f"{name} must be a positive numeric Discord id, got {value!r}")
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _meets_evidence_bar(signal: Signal) -> bool:
    return (
        signal.distinct_users >= MIN_DISTINCT_USERS
        and signal.message_count >= MIN_SUPPORTING_MESSAGES
        and signal.confidence >= MIN_CONFIDENCE
    )


def build_report(
    signals: list[Signal],
    messages: list[PreparedMessage],
    period_label: str,
) -> Report:
    """Sort ranked signals into report sections.

    Raises InsufficientEvidence when no section would contain anything, so the
    bot stays silent rather than publishing a report with nothing behind it.
    """
    tremors: list[Signal] = []
    emerging: list[Signal] = []
    counter_signals: list[Signal] = []
    needs_review: list[Signal] = []

    for signal in signals:
        if signal.category == "praise":
            if (
                signal.distinct_users >= PRAISE_MIN_DISTINCT_USERS
                and signal.confidence >= PRAISE_MIN_CONFIDENCE
            ):
                counter_signals.append(signal)
            continue
        if not _meets_evidence_bar(signal):
            # Thin breadth or low confidence. Surfaced separately as unconfirmed
            # rather than dropped, so a narrow but repeated report is not lost.
            if signal.message_count >= REVIEW_MIN_MESSAGES:
                needs_review.append(signal)
            continue
        if signal.trend == "emerging":
            emerging.append(signal)
        else:
            tremors.append(signal)

    report = Report(
        period_label=period_label,
        message_count=len(messages),
        contributor_count=len({message.author_hash for message in messages}),
        tremors=tremors[:10],
        emerging=emerging[:3],
        counter_signals=counter_signals[:3],
        needs_review=sorted(
            needs_review, key=lambda s: (-s.distinct_users, -s.message_count, s.title)
        )[:MAX_REVIEW_SIGNALS],
    )
    if report.published_signal_count == 0:
        raise InsufficientEvidence("no signal met the evidence thresholds")
    return report


def _evidence_lines(
    signal: Signal,
    guild_id: int,
    channel_by_message: dict[str, str],
) -> list[str]:
    lines = []
    for message_id in signal.representative_message_ids:
        channel_id = channel_by_message.get(message_id)
        if channel_id is None:
            raise ValueError("Report evidence is missing from the analyzed input")
        lines.append(f"- {jump_link(guild_id, channel_id, message_id)}")
    return lines


def _render_signal(
    signal: Signal,
    position: int,
    guild_id: int,
    channel_by_message: dict[str, str],
    ranked: bool = True,
) -> str:
    parts = [
        f"### {position}. {escape_markdown(signal.title)}",
        "",
        f"Category: {signal.category}",
        f"Product surface: {escape_markdown(signal.surface)}",
        f"Tremor Score: {signal.tremor_score}/100",
        f"Distinct users: {signal.distinct_users}",
        f"Supporting messages: {signal.message_count}",
        f"Trend: {signal.trend}",
        f"Severity: {signal.severity}/5",
        f"Confidence: {signal.confidence:.2f}",
        f"First observed: {signal.first_seen}",
        f"Last observed: {signal.last_seen}",
        "",
        f"Expected: {escape_markdown(signal.expected)}",
        f"Reported: {escape_markdown(signal.observed)}",
    ]
    if not ranked:
        parts = [line for line in parts if not line.startswith("Tremor Score:")]
    if signal.evidence_rationale:
        parts.append(f"Why these messages: {escape_markdown(signal.evidence_rationale)}")
    evidence = _evidence_lines(signal, guild_id, channel_by_message)
    if evidence:
        parts += ["", "Evidence:", *evidence]
    if signal.suggested_next_step:
        parts += ["", f"Suggested next step: {escape_markdown(signal.suggested_next_step)}"]
    return "\n".join(parts)


def render_markdown(
    report: Report,
    guild_id: int,
    messages: list[PreparedMessage],
    synthetic: bool = False,
) -> str:
    """Render the full report as Markdown."""
    channel_by_message = {message.message_id: message.channel_id for message in messages}

    blocks = [
        "\n".join(
            [
                "# Seismograph — Weekly Friction Report",
                "",
                f"Period: {report.period_label}",
                f"Messages analyzed: {report.message_count:,}",
                f"Distinct contributors: {report.contributor_count:,}",
                f"Signals with sufficient evidence: {report.published_signal_count}",
                "Showing up to 10 tremors, 3 emerging signals and 3 counter-signals.",
            ]
        )
    ]
    if synthetic:
        blocks.append(SYNTHETIC_NOTICE)

    if report.tremors:
        blocks.append("## Strongest Tremors")
        blocks += [
            _render_signal(signal, index, guild_id, channel_by_message)
            for index, signal in enumerate(report.tremors, start=1)
        ]

    if report.emerging:
        blocks.append(
            "## Emerging Signals\n\nSmaller, recent clusters forming quickly across several users."
        )
        blocks += [
            _render_signal(signal, index, guild_id, channel_by_message)
            for index, signal in enumerate(report.emerging, start=1)
        ]

    if report.counter_signals:
        blocks.append("## Counter-Signals\n\nIndependently supported positive reports.")
        blocks += [
            _render_signal(signal, index, guild_id, channel_by_message)
            for index, signal in enumerate(report.counter_signals, start=1)
        ]

    if report.needs_review:
        review = [
            "## Needs Human Review",
            "",
            "Unconfirmed. Below the evidence threshold and excluded from the ranking.",
            "",
        ]
        review += [
            _render_signal(signal, i, guild_id, channel_by_message, ranked=False)
            for i, signal in enumerate(report.needs_review, 1)
        ]
        blocks.append("\n".join(review))

    reactions = ", ".join(f"{emoji} {meaning}" for emoji, meaning in FEEDBACK_REACTIONS)
    blocks.append(f"React to this report to record feedback: {reactions}")

    return "\n\n".join(blocks)


def split_for_discord(markdown: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split rendered Markdown into messages that fit Discord's length limit.

    Splits on blank lines first, then on single newlines, and only breaks a
    line mid-way when a single line exceeds the limit on its own.
    """
    if limit < 100:
        raise ValueError("limit is too small to produce readable messages")

    chunks: list[str] = []
    current = ""
    for block in markdown.split("\n\n"):
        for piece in _fit(block, limit):
            candidate = piece if not current else f"{current}\n\n{piece}"
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _fit(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    pieces: list[str] = []
    current = ""
    for line in block.split("\n"):
        while len(line) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
        else:
            pieces.append(current)
            current = line
    if current:
        pieces.append(current)
    return pieces
