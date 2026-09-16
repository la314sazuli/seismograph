"""Message preparation, structured analysis, and output validation."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, replace

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


class InvalidOutput(AnalysisError):
    pass


@dataclass(frozen=True)
class Rejection:
    """One discarded candidate: a stable kind for counting, plus a readable detail."""

    kind: str
    detail: str
    stage: str = ""

    def __str__(self) -> str:
        return f"{self.stage}: {self.detail}" if self.stage else self.detail


@dataclass
class AnalysisRun:
    """Result of stage 2 together with the counts needed to judge a live run."""

    signals: list[Signal]
    rejections: list[Rejection]
    messages: int = 0
    batches: int = 0
    requests: int = 0
    repair_attempts: int = 0
    repairs_recovered: int = 0
    signals_before_merge: int = 0
    merge_requested: bool = False
    merge_fallback: bool = False
    largest_batch_messages: int = 0
    largest_batch_chars: int = 0

    def rejection_counts(self) -> list[tuple[str, int]]:
        return Counter(rejection.kind for rejection in self.rejections).most_common()

    def summary_lines(self) -> list[str]:
        """Human-readable measurements of how well the model followed the schema."""
        failed_batches = {
            r.stage.split(" (")[0]
            for r in self.rejections
            if r.stage.startswith("batch ") and "first attempt" in r.stage
        }
        clean = self.batches - len(failed_batches)
        lines = [
            f"Messages analyzed: {self.messages} in {self.batches} batch(es); "
            f"largest batch {self.largest_batch_messages} message(s), "
            f"{self.largest_batch_chars} prompt characters",
            f"Model requests: {self.requests}",
            f"Batches valid on first attempt: {clean} of {self.batches}",
            f"Repair attempts: {self.repair_attempts}, recovered: {self.repairs_recovered}",
            f"Candidates rejected: {len(self.rejections)}",
        ]
        lines.extend(f"  {kind}: {count}" for kind, count in self.rejection_counts())
        if self.merge_requested:
            how = "deterministic fallback" if self.merge_fallback else "model"
            lines.append(
                f"Signals: {self.signals_before_merge} before merge, "
                f"{len(self.signals)} after ({how})"
            )
        else:
            lines.append(f"Signals accepted: {len(self.signals)} (no merge pass needed)")
        return lines


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
        cost = len(message.content) + len(message.message_id) + len(message.created_at) + 8
        if cost > max_chars:
            raise AnalysisError("One message exceeds the batch budget; no content was truncated")
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
) -> tuple[list[Signal], list[Rejection]]:
    """Turn model output into Signals, rejecting anything unsupported.

    Returns accepted signals and a rejection reason per discarded candidate.
    Counts, dates, and distinct users are measured from the referenced
    messages, never taken from the model.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("signals"), list):
        raise InvalidOutput("model output is not an object with a 'signals' array")

    by_id = {message.message_id: message for message in messages}
    accepted: list[Signal] = []
    rejected: list[Rejection] = []

    for index, candidate in enumerate(raw["signals"]):
        label = f"signal[{index}]"
        if not isinstance(candidate, dict):
            rejected.append(Rejection("not_an_object", f"{label}: not an object"))
            continue

        title = str(candidate.get("title", "")).strip()
        if not title:
            rejected.append(Rejection("missing_title", f"{label}: missing title"))
            continue
        title = redact(title)[:MAX_TITLE_CHARS]

        category = str(candidate.get("category", "")).strip().lower()
        if category not in CATEGORIES:
            rejected.append(Rejection("unknown_category", f"{label}: unknown category"))
            continue

        severity_value = _as_float(candidate.get("severity"))
        if (
            severity_value is None
            or not 1 <= severity_value <= 5
            or not severity_value.is_integer()
        ):
            rejected.append(Rejection("severity_out_of_range", f"{label}: severity outside 1-5"))
            continue
        confidence = _as_float(candidate.get("confidence"))
        if confidence is None or not 0 <= confidence <= 1:
            rejected.append(
                Rejection("confidence_out_of_range", f"{label}: confidence outside 0-1")
            )
            continue

        supporting_raw = candidate.get("supporting_message_ids")
        if not isinstance(supporting_raw, list) or not supporting_raw:
            rejected.append(Rejection("no_supporting_ids", f"{label}: no supporting message ids"))
            continue
        supporting = [str(value).strip() for value in supporting_raw]
        unknown = [value for value in supporting if value not in by_id]
        if unknown:
            rejected.append(
                Rejection(
                    "unknown_message_ids",
                    f"{label}: {len(unknown)} supporting id(s) were not in the analyzed input",
                )
            )
            continue
        supporting = list(dict.fromkeys(supporting))

        representative_raw = (
            candidate.get("representative_message_ids") or supporting[:MAX_REPRESENTATIVE_IDS]
        )
        if not isinstance(representative_raw, list):
            rejected.append(
                Rejection(
                    "representative_not_a_list",
                    f"{label}: representative_message_ids is not a list",
                )
            )
            continue
        representative = [str(value).strip() for value in representative_raw]
        if any(value not in supporting for value in representative):
            rejected.append(
                Rejection(
                    "representative_not_a_subset",
                    f"{label}: representative ids are not a subset of supporting ids",
                )
            )
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
                surface=redact(str(candidate.get("product_surface") or "unknown")).strip()[:80]
                or "unknown",
                expected=redact(str(candidate.get("expected") or "unknown")).strip()[:400],
                observed=redact(str(candidate.get("observed") or "unknown")).strip()[:400],
                severity=round(severity_value),
                confidence=round(confidence, 3),
                evidence_rationale=redact(str(candidate.get("evidence_rationale") or "")).strip()[
                    :200
                ],
                suggested_next_step=redact(
                    str(candidate.get("suggested_next_step") or "unknown")
                ).strip()[:200],
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

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        max_requests: int = 200,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_requests = max_requests
        self.requests_used = 0

    def complete_json(self, system_prompt: str, user_prompt: str) -> object:
        """Return parsed JSON from one chat completion, or raise AnalysisError."""
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": 8192,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            for attempt in range(3):
                if self.requests_used >= self.max_requests:
                    raise AnalysisError(
                        "MAX_LLM_REQUESTS exhausted; no partial report will be sent"
                    )
                self.requests_used += 1
                try:
                    response = httpx.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                        timeout=self.timeout,
                    )
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt == 2:
                        raise
                    time.sleep(2**attempt)
                    continue
                if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                    break
                try:
                    delay = float(response.headers.get("retry-after", 2**attempt))
                except ValueError:
                    delay = 2**attempt
                time.sleep(max(0, min(delay, 30)))
            response.raise_for_status()
            body = response.json()
        except ValueError as exc:
            raise InvalidOutput("LLM response was not JSON") from exc
        except httpx.HTTPStatusError as exc:
            raise AnalysisError(
                f"LLM request failed with status {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AnalysisError(f"LLM request failed: {type(exc).__name__}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InvalidOutput("LLM response did not contain a message") from exc
        return parse_json_object(content)


def parse_json_object(content: str) -> object:
    """Parse a JSON object, tolerating a surrounding code fence."""
    if not isinstance(content, str):
        raise InvalidOutput("LLM response content was not text")
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidOutput("LLM output was not valid JSON") from exc


REPAIR_INSTRUCTION = (
    "Your previous response was rejected. Return only a JSON object matching the "
    "required schema. Every signal must include supporting_message_ids copied "
    "verbatim from the input. Problems found:\n"
)


def analyze(
    client: LLMClient,
    messages: list[PreparedMessage],
    period_label: str,
) -> AnalysisRun:
    """Stage 2: run structured analysis over batches and merge the results.

    Each batch is validated on its own with at most one repair attempt. If no
    batch produces a usable signal, the caller must not publish a report. The
    returned run carries the counts needed to judge how well the model followed
    the schema.
    """
    if not messages:
        return AnalysisRun(
            signals=[], rejections=[Rejection("no_messages", "no messages to analyze")]
        )

    batches = batch(messages)
    log.info("analyzing %d messages in %d batch(es)", len(messages), len(batches))

    run = AnalysisRun(signals=[], rejections=[], messages=len(messages), batches=len(batches))
    for number, chunk in enumerate(batches, start=1):
        user_prompt = analysis_user_prompt(period_label, [vars(m) for m in chunk])
        run.largest_batch_messages = max(run.largest_batch_messages, len(chunk))
        run.largest_batch_chars = max(run.largest_batch_chars, len(user_prompt))
        run.signals.extend(
            _analyze_once(
                client, ANALYSIS_SYSTEM_PROMPT, user_prompt, chunk, f"batch {number}", run
            )
        )

    run.signals_before_merge = len(run.signals)
    if len(batches) > 1 and run.signals:
        run.merge_requested = True
        run.signals = _merge_with_model(client, run, messages)

    log.info(
        "accepted %d signal(s), rejected %d candidate(s) in %d request(s)",
        len(run.signals),
        len(run.rejections),
        run.requests,
    )
    return run


def _analyze_once(
    client: LLMClient,
    system_prompt: str,
    user_prompt: str,
    messages: list[PreparedMessage],
    label: str,
    run: AnalysisRun,
) -> list[Signal]:
    """Analyze one batch, recording every rejection on the run as it happens."""
    prompt = user_prompt
    for attempt in range(2):
        run.requests += 1
        try:
            raw = client.complete_json(system_prompt, prompt)
            accepted, rejected = validate_signals(raw, messages)
        except InvalidOutput:
            accepted, rejected = [], [Rejection("invalid_output", "invalid JSON or schema")]
        if not rejected:
            if attempt:
                run.repairs_recovered += 1
            return accepted
        stage = f"{label} ({'after repair' if attempt else 'first attempt'})"
        run.rejections.extend(replace(r, stage=stage) for r in rejected)
        if attempt == 0:
            run.repair_attempts += 1
            prompt = (
                user_prompt
                + "\n\n"
                + REPAIR_INSTRUCTION
                + "\n".join(sorted({r.kind for r in rejected}))
            )
    raise AnalysisError(f"{label}: invalid output after one repair; entire report stopped")


def _merge_with_model(
    client: LLMClient, run: AnalysisRun, messages: list[PreparedMessage]
) -> list[Signal]:
    """Merge bounded descriptors; evidence unions and counts stay in Python."""
    by_id = {m.message_id: m for m in messages}
    signals = sorted(merge_candidates(run.signals), key=lambda s: (s.category, s.surface, s.title))
    merged = []
    for offset in range(0, len(signals), 20):
        chunk = signals[offset : offset + 20]
        if len(chunk) == 1:
            merged.extend(chunk)
            continue
        candidates = [
            {
                "id": i,
                "title": s.title,
                "category": s.category,
                "surface": s.surface,
                "observed": s.observed,
            }
            for i, s in enumerate(chunk)
        ]
        prompt = merge_user_prompt(candidates)
        for attempt in range(2):
            run.requests += 1
            try:
                raw = client.complete_json(MERGE_SYSTEM_PROMPT, prompt)
                groups = raw.get("groups") if isinstance(raw, dict) else None
                if not isinstance(groups, list) or not all(
                    isinstance(g, list) and g and all(type(i) is int for i in g) for g in groups
                ):
                    raise InvalidOutput("merge must return groups of integer candidate ids")
                if sorted(i for g in groups for i in g) != list(range(len(chunk))):
                    raise InvalidOutput("merge must partition all candidate ids exactly once")
                if any(len({chunk[i].category for i in g}) != 1 for g in groups):
                    raise InvalidOutput("merge mixed signal categories")
                if attempt:
                    run.repairs_recovered += 1
                break
            except InvalidOutput:
                if attempt:
                    raise AnalysisError("merge invalid after one repair; report stopped") from None
                run.repair_attempts += 1
                prompt += "\nReturn a partition of every candidate id exactly once."
        for group in groups:
            members = [chunk[i] for i in group]
            ids = tuple(dict.fromkeys(i for s in members for i in s.supporting_message_ids))
            evidence = [by_id[i] for i in ids]
            merged.append(
                replace(
                    members[0],
                    supporting_message_ids=ids,
                    representative_message_ids=tuple(
                        dict.fromkeys(i for s in members for i in s.representative_message_ids)
                    )[:3],
                    distinct_users=len({m.author_hash for m in evidence}),
                    message_count=len(ids),
                    first_seen=min(m.created_at for m in evidence),
                    last_seen=max(m.created_at for m in evidence),
                    severity=max(s.severity for s in members),
                    confidence=min(s.confidence for s in members),
                )
            )
    return [
        replace(s, distinct_users=len({by_id[i].author_hash for i in s.supporting_message_ids}))
        for s in merged
    ]
