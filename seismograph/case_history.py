"""Explain changes between retained case snapshots without a model or new storage."""

from __future__ import annotations

import json

from discord.utils import escape_markdown

from . import cases
from .analysis import AnalysisError, prepare
from .report import jump_link


def _snapshot(connection, row, allowed_channels):
    count = connection.execute(
        "SELECT COUNT(*) FROM case_inputs WHERE revision_id = ?", (row["id"],)
    ).fetchone()[0]
    if not 1 <= count <= cases.MAX_CONTEXT:
        raise AnalysisError("Retained case context is missing or exceeds its bound")
    rows = connection.execute(
        "SELECT m.* FROM messages m JOIN case_inputs i ON m.message_id = i.message_id"
        " WHERE i.revision_id = ? ORDER BY m.created_at, m.message_id LIMIT ?",
        (row["id"], cases.MAX_CONTEXT + 1),
    ).fetchall()
    messages = prepare([dict(r) for r in rows])
    if len(messages) != count:
        raise AnalysisError("Retained case evidence is incomplete; comparison stopped")
    if any(m.channel_id not in allowed_channels for m in messages):
        raise AnalysisError("Case history includes a source outside the current allowlist")
    try:
        payload = cases.validate(json.loads(row["payload"]), messages)
    except (ValueError, AnalysisError):
        raise AnalysisError("Retained case interpretation is invalid; comparison stopped") from None
    return {
        "id": row["id"],
        "analyzed_at": row["created_at"],
        "messages": messages,
        "payload": payload,
    }


def compare(connection, case_id: int, allowed_channels) -> dict:
    """Read two snapshots consistently; never fall back past invalidated history."""
    allowed_channels = {str(channel) for channel in allowed_channels}
    connection.execute("SAVEPOINT case_change_read")
    try:
        case = connection.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        rows = connection.execute(
            "SELECT * FROM case_revisions WHERE case_id = ? ORDER BY id DESC LIMIT 2",
            (case_id,),
        ).fetchall()
        if not case or len(rows) < 2:
            raise AnalysisError(
                "Two retained revisions are required. Refresh the case after new evidence "
                "is available; privacy-invalidated history is not restored."
            )
        after = _snapshot(connection, rows[0], allowed_channels)
        before = _snapshot(connection, rows[1], allowed_channels)
        marker = case["intervention_at"]
        result = difference(before, after, marker)
        return {
            **result,
            "case_id": case_id,
            "marker": marker,
            "marker_note": case["intervention_note"],
        }
    finally:
        connection.execute("RELEASE case_change_read")


def difference(before, after, marker):
    old, new = before["payload"], after["payload"]
    old_context = {m.message_id: m for m in before["messages"]}
    new_context = {m.message_id: m for m in after["messages"]}
    old_obs = {o["message_id"]: o for o in old["observations"]}
    new_obs = {o["message_id"]: o for o in new["observations"]}
    added = sorted(new_obs.keys() - old_obs.keys())
    removed = sorted(old_obs.keys() - new_obs.keys())
    rewritten = sorted(
        mid for mid in old_obs.keys() & new_obs.keys() if old_obs[mid] != new_obs[mid]
    )
    # These are post-marker failure observations no longer counted as failures,
    # not a claim that their reporters recovered or that the software regressed.
    lost_failures = sorted(
        mid
        for mid, o in old_obs.items()
        if marker
        and old_context[mid].created_at > marker
        and o["outcome"] == "failure"
        and new_obs.get(mid, {}).get("outcome") != "failure"
    )
    evidence = []
    for mid in sorted(set(added + removed + rewritten)):
        source = new_context.get(mid) or old_context[mid]
        evidence.append(
            {
                "message_id": mid,
                "channel_id": source.channel_id,
                "before": old_obs.get(mid),
                "after": new_obs.get(mid),
                "context_retained": mid in new_context,
            }
        )
    return {
        "title": new["title"],
        "before_revision": before["id"],
        "after_revision": after["id"],
        "before_analyzed_at": before["analyzed_at"],
        "after_analyzed_at": after["analyzed_at"],
        "before_status": cases.verification(old, before["messages"], marker),
        "after_status": cases.verification(new, after["messages"], marker),
        "context_added": sorted(new_context.keys() - old_context.keys()),
        "context_removed": sorted(old_context.keys() - new_context.keys()),
        "observations_added": added,
        "observations_removed": removed,
        "observations_rewritten": rewritten,
        "promoted_from_existing_context": sorted(set(added) & old_context.keys()),
        "same_context": old_context == new_context,
        "interpretation_changed": (
            old_obs != new_obs
            or old["title"] != new["title"]
            or old["hypotheses"] != new["hypotheses"]
            or old["next_question"] != new["next_question"]
        ),
        "hypotheses_changed": old["hypotheses"] != new["hypotheses"],
        "question_changed": old["next_question"] != new["next_question"],
        "title_changed": old["title"] != new["title"],
        "failure_evidence_lost": lost_failures,
        "evidence": evidence,
    }


def assert_current(connection, comparison):
    """Stop sending subsequent chunks if evidence or the current marker changed."""
    rows = connection.execute(
        "SELECT id FROM case_revisions WHERE case_id = ? ORDER BY id DESC LIMIT 2",
        (comparison["case_id"],),
    ).fetchall()
    marker = connection.execute(
        "SELECT intervention_at, intervention_note FROM cases WHERE id = ?",
        (comparison["case_id"],),
    ).fetchone()
    if (
        [r[0] for r in rows] != [comparison["after_revision"], comparison["before_revision"]]
        or not marker
        or tuple(marker) != (comparison["marker"], comparison["marker_note"])
    ):
        raise AnalysisError("Case evidence or marker changed; request the comparison again")


def render(comparison: dict, guild_id: int) -> str:
    d = comparison
    esc = escape_markdown
    before, after = d["before_status"], d["after_status"]
    lines = [
        f"# Case {d['case_id']} changes: {esc(d['title'])}",
        f"Retained revisions {d['before_revision']} to {d['after_revision']}.",
        f"Analyzed: {d['before_analyzed_at']} to {d['after_analyzed_at']}.",
        "Deterministic comparison; no model was called for this change log.",
        "",
        "## What changed",
        f"- Selected context: +{len(d['context_added'])} / -{len(d['context_removed'])} messages.",
        f"- Observations: +{len(d['observations_added'])} / -{len(d['observations_removed'])}; "
        f"{len(d['observations_rewritten'])} rewritten.",
        f"- Newly cited from prior context: {len(d['promoted_from_existing_context'])}.",
        f"- Hypothesis text or references changed: {'yes' if d['hypotheses_changed'] else 'no'}.",
        f"- Follow-up question changed: {'yes' if d['question_changed'] else 'no'}.",
        f"- Case title changed: {'yes' if d['title_changed'] else 'no'}.",
    ]
    if d["same_context"] and d["interpretation_changed"]:
        lines.append(
            "Same source context; the interpretation changed, not the underlying messages."
        )
    elif not d["interpretation_changed"]:
        lines.append("The validated interpretation is unchanged.")
    lines += [
        "",
        "## Follow-up comparison",
        "Both snapshots are re-evaluated against the CURRENT intervention marker.",
        "This is not an audit of statuses or markers as they existed historically.",
        f"Marker: {d['marker'] or 'none'}.",
        f"Before: {before['status']}; {before['failure_authors']} failure / "
        f"{before['success_authors']} success reporter(s).",
        f"After: {after['status']}; {after['failure_authors']} failure / "
        f"{after['success_authors']} success reporter(s).",
        "Counts describe selected evidence, not population-wide recovery or patch adoption.",
    ]
    if d["failure_evidence_lost"]:
        lines += [
            "",
            "CAUTION: prior post-marker failure observations were omitted or relabeled.",
            "An apparently better status is not evidence that those reporters recovered.",
            "Affected observation IDs: " + ", ".join(d["failure_evidence_lost"]),
        ]
    if d["context_removed"]:
        lines.append("A narrower selected context is not proof of deletion or resolution.")
    if d["evidence"]:
        lines += ["", "## Changed observations"]
    for item in d["evidence"][:8]:
        mid = item["message_id"]
        lines.append(f"- {jump_link(guild_id, item['channel_id'], mid)}")
        for key, label in (("before", "Before"), ("after", "After")):
            observation = item[key]
            text = (
                f"{observation['outcome']} / {esc(observation['condition'])}: "
                f"{esc(observation['quote'])}"
                if observation
                else "Not selected as an observation."
            )
            lines.append(f"  {label}: {text}")
        if item["before"] and not item["after"]:
            lines.append(
                "  Source remains in selected context; the observation was omitted."
                if item["context_retained"]
                else "  Source is outside the new selected context."
            )
    if len(d["evidence"]) > 8:
        lines.append(f"Showing 8 of {len(d['evidence'])} changed observations; counts cover all.")
    lines += [
        "",
        "Text/reference changes are structural, not proof of changed meaning or causality.",
        "Edits, deletions, opt-outs, and retention can invalidate the entire retained history.",
    ]
    return "\n".join(lines)
