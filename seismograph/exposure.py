"""Quote-backed, revision-and-release-scoped exposure assessments. No inference."""

from __future__ import annotations

from datetime import UTC, datetime

from discord.utils import escape_markdown

from . import case_history, case_reviews
from .analysis import AnalysisError
from .report import jump_link

STATES = ("received", "not_received", "unknown")
MAX_EVENTS = 120


def _counts(observations, messages, marker, latest, corrections=()):
    by_id = {m.message_id: m for m in messages}
    overrides = {r["message_id"]: r["outcome"] for r in corrections if r["withdrawn_at"] is None}
    groups = {
        state: {"observations": 0, "authors": set(), "failure": set(), "success": set()}
        for state in STATES
    }
    excluded = before = 0
    for obs in observations:
        source = by_id[obs["message_id"]]
        if source.created_at <= marker:
            before += 1
            continue
        outcome = overrides.get(obs["message_id"], obs["outcome"])
        if outcome == "exclude":
            excluded += 1
            continue
        state = latest.get(obs["message_id"], {}).get("state", "unknown")
        group = groups[state]
        group["observations"] += 1
        group["authors"].add(source.author_hash)
        if outcome in {"failure", "success"}:
            group[outcome].add(source.author_hash)
    author_groups = [group["authors"] for group in groups.values()]
    overlap = set()
    for index, authors in enumerate(author_groups):
        for other in author_groups[index + 1 :]:
            overlap.update(authors & other)
    return {
        "groups": {
            state: {
                "observations": group["observations"],
                "reporters": len(group["authors"]),
                "failure_reporters": len(group["failure"]),
                "success_reporters": len(group["success"] - group["failure"]),
            }
            for state, group in groups.items()
        },
        "unique_reporters": len(set().union(*author_groups)),
        "mixed_exposure_reporters": len(overlap),
        "excluded_observations": excluded,
        "before_marker_observations": before,
    }


def view(connection, case_id, channels):
    connection.execute("SAVEPOINT exposure_read")
    try:
        review = case_reviews.view(connection, case_id, channels)
        case = connection.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not case["intervention_at"] or not case["intervention_key"]:
            raise AnalysisError("Record an intervention with /seismograph_release before exposure")
        row = connection.execute(
            "SELECT * FROM case_revisions WHERE id = ?", (review["revision_id"],)
        ).fetchone()
        snapshot = case_history._snapshot(connection, row, {str(c) for c in channels})
        stored = [
            dict(r)
            for r in connection.execute(
                "SELECT id, intervention_key, message_id, state, quote, reason, created_at"
                " FROM case_exposures WHERE revision_id = ? ORDER BY id LIMIT ?",
                (row["id"], MAX_EVENTS + 1),
            )
        ]
        if len(stored) > MAX_EVENTS:
            raise AnalysisError("Exposure audit exceeds its bound")
        events = [r for r in stored if r["intervention_key"] == case["intervention_key"]]
        latest = {r["message_id"]: r for r in events}
        by_id = {m.message_id: m for m in snapshot["messages"]}
        for event in events:
            source = by_id.get(event["message_id"])
            if (
                event["message_id"] not in review["observations"]
                or not source
                or source.created_at <= case["intervention_at"]
                or event["state"] not in STATES
                or (event["state"] == "unknown" and event["quote"] != "")
                or (
                    event["state"] != "unknown"
                    and (
                        not 8 <= len(event["quote"]) <= 350 or event["quote"] not in source.content
                    )
                )
            ):
                raise AnalysisError("Retained exposure evidence is invalid")
        args = (
            snapshot["payload"]["observations"],
            snapshot["messages"],
            case["intervention_at"],
            latest,
        )
        return {
            "case_id": case_id,
            "revision_id": row["id"],
            "scope_key": review["revision_key"] + case["intervention_key"],
            "marker": case["intervention_at"],
            "marker_note": case["intervention_note"],
            "intervention_key": case["intervention_key"],
            "events": events,
            "guard_ids": [r["id"] for r in stored],
            "latest": latest,
            "review_guard": review,
            "original": _counts(*args),
            "adjusted": _counts(*args, review["records"]),
            "eligible": {
                m.message_id: m.content
                for m in snapshot["messages"]
                if m.message_id in review["observations"] and m.created_at > case["intervention_at"]
            },
        }
    finally:
        connection.execute("RELEASE exposure_read")


def assess(
    connection, case_id, scope_key, message_id, state, quote, reason, reviewer_hash, channels
):
    reason = case_reviews._reason(reason)
    if state not in STATES:
        raise AnalysisError("Choose received, not_received, or unknown")
    if not isinstance(quote, str) or (state == "unknown" and quote):
        raise AnalysisError("Use no quote when resetting exposure to unknown")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        case_reviews._reviewer(connection, reviewer_hash)
        result = view(connection, case_id, channels)
        if result["scope_key"] != scope_key:
            raise AnalysisError("Revision or release changed; read /seismograph_exposures again")
        content = result["eligible"].get(message_id)
        if content is None:
            raise AnalysisError("Choose a post-marker observation in the current revision")
        if state != "unknown" and (not 8 <= len(quote) <= 350 or quote not in content):
            raise AnalysisError("Give an exact source excerpt between 8 and 350 characters")
        prior = result["latest"].get(message_id)
        if (prior["state"] if prior else "unknown") == state:
            raise AnalysisError("Exposure is already in that state")
        if len(result["guard_ids"]) >= MAX_EVENTS:
            raise AnalysisError("This revision has reached its 120-entry exposure audit limit")
        cursor = connection.execute(
            "INSERT INTO case_exposures"
            " (revision_id, intervention_key, message_id, state, quote, reason,"
            " reviewer_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result["revision_id"],
                result["intervention_key"],
                message_id,
                state,
                quote,
                reason,
                reviewer_hash,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        return cursor.lastrowid


def assert_current(connection, result):
    case_reviews.assert_current(connection, result["review_guard"])
    marker = connection.execute(
        "SELECT intervention_key FROM cases WHERE id = ?", (result["case_id"],)
    ).fetchone()
    ids = [
        r[0]
        for r in connection.execute(
            "SELECT id FROM case_exposures WHERE revision_id = ? ORDER BY id LIMIT ?",
            (result["revision_id"], MAX_EVENTS + 1),
        )
    ]
    if not marker or marker[0] != result["intervention_key"] or ids != result["guard_ids"]:
        raise AnalysisError("Release or exposure review changed; request the view again")


def banner(result):
    groups = result["original"]["groups"]
    return (
        "\n\nPATCH EXPOSURE: "
        f"{groups['received']['observations']} received / "
        f"{groups['not_received']['observations']} not received / "
        f"{groups['unknown']['observations']} unknown post-marker observations. "
        "Quote-backed staff assessments, not verified rollout telemetry. "
        "Read /seismograph_exposures; original case counts above are unchanged."
    )


def render(result, guild_id, page=1):
    pages = max(1, (len(result["events"]) + 7) // 8)
    if not 1 <= page <= pages:
        raise AnalysisError(f"Choose an exposure page from 1 to {pages}")
    esc = escape_markdown
    lines = [
        f"# Case {result['case_id']} patch exposure",
        f"Revision {result['revision_id']}; scope_key: `{result['scope_key']}`.",
        f"Release: {esc(result['marker_note'])} at {result['marker']}.",
        "Staff interpretation of quoted reports, not verified patch installation.",
        "Post-marker timestamps alone never establish exposure.",
    ]
    for key, title in (
        ("original", "Original model outcomes"),
        ("adjusted", "Staff-adjusted outcome preview"),
    ):
        counts = result[key]
        lines += ["", f"## {title}"]
        for state, group in counts["groups"].items():
            lines.append(
                f"- {state}: {group['observations']} observations; {group['reporters']} reporters; "
                f"{group['failure_reporters']} failure / {group['success_reporters']} "
                "success reporters without a conflicting failure in this group."
            )
        lines += [
            f"Unique post-marker reporters: {counts['unique_reporters']}; "
            f"reporters across multiple exposure groups: {counts['mixed_exposure_reporters']}.",
            f"Excluded by staff: {counts['excluded_observations']}; "
            f"at/before marker: {counts['before_marker_observations']} observations.",
        ]
    lines += [
        "",
        "Unknown-exposure failures remain unresolved evidence, "
        "not proof of patch failure or recovery.",
        "Reporter groups can overlap; do not add them or infer a rollout or success rate.",
        "No overall resolution status or causal claim is derived from these segments.",
        "",
        "## Exposure assessment history",
        f"Current release only. Page {page} of {pages}; up to 8 entries.",
    ]
    for event in result["events"][(page - 1) * 8 : page * 8]:
        current = result["latest"][event["message_id"]]["id"] == event["id"]
        channel = result["review_guard"]["channels"][event["message_id"]]
        lines += [
            f"- Assessment {event['id']} ({'current' if current else 'superseded'}): "
            f"{event['state']}, {event['created_at']}.",
            f"  {jump_link(guild_id, channel, event['message_id'])}",
            f"  Exposure excerpt: {esc(event['quote']) if event['quote'] else 'Reset to unknown.'}",
            f"  Reason: {esc(event['reason'])}",
        ]
    if not result["events"]:
        lines.append("No exposure assessments for this revision and release.")
    lines += [
        "",
        "A new revision or release resets exposure to unknown. No model call was made.",
        "Edits, deletion, retention, and reviewer opt-out can erase this retained audit.",
    ]
    return "\n".join(lines)
