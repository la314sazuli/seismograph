"""Message preparation, structured analysis, and output validation."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass

import httpx

from .privacy import redact
from .prompts import (
    ANALYSIS_SYSTEM_PROMPT,
    MERGE_SYSTEM_PROMPT,
    analysis_user_prompt,
    merge_user_prompt,
)
from .scoring import CATEGORIES, Signal

log = logging.getLogger(__name__)

MAX_BATCH_CHARS = 24_000
MAX_TITLE_CHARS = 120
MAX_REPRESENTATIVE_IDS = 3
MIN_CONTENT_CHARS = 8

_WHITESPACE = re.compile(r"\s+")
_COMMAND_ONLY = re.compile(r"^[/!$.>][\w-]{1,32}\s*$")
_NOISE = re.compile(
    r"^(?:\+1|\+\+|lol+|lmao+|ok+|okay|yes+|no+|ty|thx|thanks|thank you|gm|hi|hello|hey"
    r"|same|this|nice|wow|haha+|xd|👍|👀|🙏|\W+)$",
    re.IGNORECASE,
)


class AnalysisError(Exception):
    """Raised when analysis cannot produce a trustworthy result."""


@dataclass(frozen=True)
class PreparedMessage:
    message_id: str
    channel_id: str
    author_hash: str
    created_at: str
    content: str


def prepare(messages: list[dict]) -> list[PreparedMessage]:
    """Deterministic stage 1: drop what cannot carry a signal, keep lineage.

    Bot messages, empty messages, bare commands, and one-word acknowledgements
    are removed. Ids, timestamps, and anonymized authors always survive.
    """
    prepared: list[PreparedMessage] = []
    seen: set[str] = set()
    for message in messages:
        if message.get("is_bot"):
            continue
        message_id = str(message.get("message_id", "")).strip()
        if not message_id or message_id in seen:
            continue
        content = _WHITESPACE.sub(" ", str(message.get("content", ""))).strip()
        if len(content) < MIN_CONTENT_CHARS:
            continue
        if _COMMAND_ONLY.match(content) or _NOISE.match(content):
            continue
        seen.add(message_id)
        prepared.append(
            PreparedMessage(
                message_id=message_id,
                channel_id=str(message["channel_id"]),
                author_hash=str(message["author_hash"]),
                created_at=str(message["created_at"]),
                content=redact(content),
            )
        )
    prepared.sort(key=lambda m: (m.created_at, m.message_id))
    return prepared


def batch(
    messages: list[PreparedMessage], max_chars: int = MAX_BATCH_CHARS
) -> list[list[PreparedMessage]]:
    """Split messages into context-sized batches, preserving order."""
    batches: list[list[PreparedMessage]] = []
    current: list[PreparedMessage] = []
    size = 0
    for message in messages:
        cost = len(message.content) + len(message.message_id) + 40
        if current and size + cost > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(message)
        size += cost
    if current:
        batches.append(current)
    return batches


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_signals(
    raw: object,
    messages: list[PreparedMessage],
) -> tuple[list[Signal], list[str]]:
    """Turn model output into Signals, rejecting anything unsupported.

    Returns accepted signals and a rejection reason per discarded candidate.
    Counts, dates, and distinct users are measured from the referenced
    messages, never taken from the model.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("signals"), list):
        raise AnalysisError("model output is not an object with a 'signals' array")

    by_id = {message.message_id: message for message in messages}
    accepted: list[Signal] = []
    rejected: list[str] = []

    for index, candidate in enumerate(raw["signals"]):
        label = f"signal[{index}]"
        if not isinstance(candidate, dict):
            rejected.append(f"{label}: not an object")
            continue

        title = str(candidate.get("title", "")).strip()
        if not title:
            rejected.append(f"{label}: missing title")
            continue
        label = f"{title!r}"
        title = title[:MAX_TITLE_CHARS]

        category = str(candidate.get("category", "")).strip().lower()
        if category not in CATEGORIES:
            rejected.append(f"{label}: unknown category {category!r}")
            continue

        severity_value = _as_float(candidate.get("severity"))
        if severity_value is None or not 1 <= severity_value <= 5:
            rejected.append(f"{label}: severity outside 1-5")
            continue
        confidence = _as_float(candidate.get("confidence"))
        if confidence is None or not 0 <= confidence <= 1:
            rejected.append(f"{label}: confidence outside 0-1")
            continue

        supporting_raw = candidate.get("supporting_message_ids")
        if not isinstance(supporting_raw, list) or not supporting_raw:
            rejected.append(f"{label}: no supporting message ids")
            continue
        supporting = [str(value).strip() for value in supporting_raw]
        unknown = [value for value in supporting if value not in by_id]
        if unknown:
            rejected.append(
                f"{label}: {len(unknown)} supporting id(s) were not in the analyzed input"
            )
            continue
        supporting = list(dict.fromkeys(supporting))

        representative_raw = (
            candidate.get("representative_message_ids") or supporting[:MAX_REPRESENTATIVE_IDS]
        )
        if not isinstance(representative_raw, list):
            rejected.append(f"{label}: representative_message_ids is not a list")
            continue
        representative = [str(value).strip() for value in representative_raw]
        if any(value not in supporting for value in representative):
            rejected.append(f"{label}: representative ids are not a subset of supporting ids")
            continue
        representative = (
            list(dict.fromkeys(representative))[:MAX_REPRESENTATIVE_IDS] or supporting[:1]
        )

        supporting_messages = [by_id[value] for value in supporting]
        timestamps = sorted(message.created_at for message in supporting_messages)
        accepted.append(
            Signal(
                title=title,
                category=category,
                surface=str(candidate.get("product_surface") or "unknown").strip()[:80]
                or "unknown",
                expected=str(candidate.get("expected") or "unknown").strip(),
                observed=str(candidate.get("observed") or "unknown").strip(),
                severity=round(severity_value),
                confidence=round(confidence, 3),
                evidence_rationale=str(candidate.get("evidence_rationale") or "").strip(),
                suggested_next_step=str(candidate.get("suggested_next_step") or "unknown").strip(),
                supporting_message_ids=tuple(supporting),
                representative_message_ids=tuple(representative),
                distinct_users=len({message.author_hash for message in supporting_messages}),
                message_count=len(supporting_messages),
                first_seen=timestamps[0],
                last_seen=timestamps[-1],
            )
        )

    return accepted, rejected


def merge_candidates(signals: list[Signal]) -> list[Signal]:
    """Deterministic fallback merge: combine signals with an identical title."""
    grouped: dict[tuple[str, str], list[Signal]] = {}
    for signal in signals:
        grouped.setdefault((signal.title.lower(), signal.category), []).append(signal)

    merged: list[Signal] = []
    for group in grouped.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        supporting = list(dict.fromkeys(i for s in group for i in s.supporting_message_ids))
        representative = list(dict.fromkeys(i for s in group for i in s.representative_message_ids))
        first = min(s.first_seen for s in group)
        last = max(s.last_seen for s in group)
        merged.append(
            Signal(
                title=group[0].title,
                category=group[0].category,
                surface=Counter(s.surface for s in group).most_common(1)[0][0],
                expected=group[0].expected,
                observed=group[0].observed,
                severity=max(s.severity for s in group),
                confidence=min(s.confidence for s in group),
                evidence_rationale=group[0].evidence_rationale,
                suggested_next_step=group[0].suggested_next_step,
                supporting_message_ids=tuple(supporting),
                representative_message_ids=tuple(representative[:MAX_REPRESENTATIVE_IDS]),
                distinct_users=max(s.distinct_users for s in group),
                message_count=len(supporting),
                first_seen=first,
                last_seen=last,
            )
        )
    return merged


class LLMClient:
    """Minimal OpenAI-compatible chat-completions client."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete_json(self, system_prompt: str, user_prompt: str) -> object:
        """Return parsed JSON from one chat completion, or raise AnalysisError."""
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise AnalysisError(
                f"LLM request failed with status {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AnalysisError(f"LLM request failed: {type(exc).__name__}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AnalysisError("LLM response did not contain a message") from exc
        return parse_json_object(content)


def parse_json_object(content: str) -> object:
    """Parse a JSON object, tolerating a surrounding code fence."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"LLM output was not valid JSON: {exc.msg}") from exc


REPAIR_INSTRUCTION = (
    "Your previous response was rejected. Return only a JSON object matching the "
    "required schema. Every signal must include supporting_message_ids copied "
    "verbatim from the input. Problems found:\n"
)


def analyze(
    client: LLMClient,
    messages: list[PreparedMessage],
    period_label: str,
) -> tuple[list[Signal], list[str]]:
    """Stage 2: run structured analysis over batches and merge the results.

    Each batch is validated on its own with at most one repair attempt. If no
    batch produces a usable signal, the caller must not publish a report.
    """
    if not messages:
        return [], ["no messages to analyze"]

    batches = batch(messages)
    log.info("analyzing %d messages in %d batch(es)", len(messages), len(batches))

    signals: list[Signal] = []
    rejections: list[str] = []
    for number, chunk in enumerate(batches, start=1):
        user_prompt = analysis_user_prompt(period_label, [vars(m) for m in chunk])
        accepted, rejected = _analyze_once(
            client, ANALYSIS_SYSTEM_PROMPT, user_prompt, chunk, f"batch {number}"
        )
        signals.extend(accepted)
        rejections.extend(rejected)

    if len(batches) > 1 and signals:
        signals = _merge_with_model(client, signals, messages, rejections)

    log.info("accepted %d signal(s), rejected %d candidate(s)", len(signals), len(rejections))
    return signals, rejections


def _analyze_once(
    client: LLMClient,
    system_prompt: str,
    user_prompt: str,
    messages: list[PreparedMessage],
    label: str,
) -> tuple[list[Signal], list[str]]:
    raw = client.complete_json(system_prompt, user_prompt)
    accepted, rejected = validate_signals(raw, messages)
    if accepted or not rejected:
        return accepted, [f"{label}: {reason}" for reason in rejected]

    log.warning("%s: all candidates rejected, requesting one repair", label)
    repair_prompt = f"{user_prompt}\n\n{REPAIR_INSTRUCTION}" + "\n".join(f"- {r}" for r in rejected)
    raw = client.complete_json(system_prompt, repair_prompt)
    accepted, rejected_again = validate_signals(raw, messages)
    return accepted, [f"{label} (after repair): {reason}" for reason in rejected_again]


def _merge_with_model(
    client: LLMClient,
    signals: list[Signal],
    messages: list[PreparedMessage],
    rejections: list[str],
) -> list[Signal]:
    """Ask the model to merge duplicate clusters; fall back to a title merge."""
    candidates = [
        {
            "title": signal.title,
            "category": signal.category,
            "product_surface": signal.surface,
            "expected": signal.expected,
            "observed": signal.observed,
            "severity": signal.severity,
            "confidence": signal.confidence,
            "supporting_message_ids": list(signal.supporting_message_ids),
            "representative_message_ids": list(signal.representative_message_ids),
            "evidence_rationale": signal.evidence_rationale,
            "suggested_next_step": signal.suggested_next_step,
        }
        for signal in signals
    ]
    try:
        raw = client.complete_json(MERGE_SYSTEM_PROMPT, merge_user_prompt(candidates))
        merged, rejected = validate_signals(raw, messages)
    except AnalysisError as exc:
        log.warning("merge pass failed (%s), using deterministic merge", exc)
        return merge_candidates(signals)
    if not merged:
        rejections.extend(f"merge pass: {reason}" for reason in rejected)
        return merge_candidates(signals)
    rejections.extend(f"merge pass: {reason}" for reason in rejected)
    return merged
