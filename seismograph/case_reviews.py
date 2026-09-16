"""Revision-bound human annotations. Never rewrite model output or source evidence."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from discord.utils import escape_markdown

from . import case_history, cases
from .analysis import AnalysisError
from .privacy import redact
from .report import jump_link

MAX_CORRECTIONS = 120
OUTCOMES = cases.OUTCOMES | {"exclude"}


def _reason(value):
    if not isinstance(value, str) or not 8 <= len(value.strip()) <= 300:
        raise AnalysisError("Give a reason between 8 and 300 characters; omit personal details")
    return redact(" ".join(value.split()))


def _reviewer(connection, reviewer_hash):
    if (
        not reviewer_hash
        or connection.execute(
            "SELECT 1 FROM optouts WHERE author_hash = ?", (reviewer_hash,)
        ).fetchone()
    ):
        raise AnalysisError("An opted-out reviewer cannot create review records")


def view(connection, case_id, allowed_channels):
    """A bounded, consistent view; old revisions never become active again."""
    connection.execute("SAVEPOINT case_review_read")
    try:
        row = connection.execute(
            "SELECT * FROM case_revisions WHERE case_id = ? ORDER BY id DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        if not row:
            raise AnalysisError("No retained case evidence to review")
        snapshot = case_history._snapshot(connection, row, {str(c) for c in allowed_channels})
        marker = connection.execute(
            "SELECT intervention_at, intervention_note FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
        records = [
            dict(r)
            for r in connection.execute(
                "SELECT c.id, c.message_id, c.outcome, c.reason, c.created_at,"
                " w.reason AS withdrawal_reason, w.created_at AS withdrawn_at"
                " FROM case_corrections c LEFT JOIN case_withdrawals w ON w.correction_id = c.id"
                " WHERE c.revision_id = ? ORDER BY c.id LIMIT ?",
                (row["id"], MAX_CORRECTIONS + 1),
            )
        ]
        if len(records) > MAX_CORRECTIONS:
            raise AnalysisError("Review history exceeds its bound; comparison stopped")
        original = snapshot["payload"]
        observations = {o["message_id"]: o for o in original["observations"]}
        active = [r for r in records if r["withdrawn_at"] is None]
        corrected = deepcopy(original)
        by_id = {r["message_id"]: r for r in active}
        if any(r["message_id"] not in observations for r in records):
            raise AnalysisError("Review references unavailable observations")
        corrected["observations"] = [
            {**o, "outcome": by_id[o["message_id"]]["outcome"]} if o["message_id"] in by_id else o
            for o in corrected["observations"]
            if by_id.get(o["message_id"], {}).get("outcome") != "exclude"
        ]
        # Only the outcome-counting function receives this preview. Hypotheses
        # are not reinterpreted or presented as if the model agreed with staff.
        return {
            "case_id": case_id,
            "revision_id": row["id"],
            "revision_key": row["review_key"],
            "marker": tuple(marker),
            "records": records,
            "active": len(active),
            "observations": observations,
            "channels": {m.message_id: m.channel_id for m in snapshot["messages"]},
            "original": cases.verification(original, snapshot["messages"], marker[0]),
            "preview": cases.verification(corrected, snapshot["messages"], marker[0]),
        }
    finally:
        connection.execute("RELEASE case_review_read")


def _check_key(review, revision_key):
    if review["revision_key"] != revision_key:
        raise AnalysisError("Revision changed; read /seismograph_reviews again before correcting")


def correct(
    connection, case_id, revision_key, message_id, outcome, reason, reviewer_hash, channels
):
    reason = _reason(reason)
    if outcome not in OUTCOMES:
        raise AnalysisError("Choose a supported outcome or exclude")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        _reviewer(connection, reviewer_hash)
        review = view(connection, case_id, channels)
        _check_key(review, revision_key)
        observation = review["observations"].get(message_id)
        if not observation:
            raise AnalysisError("Choose an observation in this exact revision")
        if observation["outcome"] == outcome:
            raise AnalysisError("The proposed outcome already matches the model")
        if any(
            r["message_id"] == message_id and r["withdrawn_at"] is None for r in review["records"]
        ):
            raise AnalysisError("Withdraw the existing correction before replacing it")
        if len(review["records"]) >= MAX_CORRECTIONS:
            raise AnalysisError("This revision has reached its 120-correction audit limit")
        cursor = connection.execute(
            "INSERT INTO case_corrections"
            " (revision_id, message_id, outcome, reason, reviewer_hash, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                review["revision_id"],
                message_id,
                outcome,
                reason,
                reviewer_hash,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        return cursor.lastrowid


def withdraw(connection, case_id, revision_key, correction_id, reason, reviewer_hash, channels):
    reason = _reason(reason)
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        _reviewer(connection, reviewer_hash)
        review = view(connection, case_id, channels)
        _check_key(review, revision_key)
        record = next((r for r in review["records"] if r["id"] == correction_id), None)
        if not record or record["withdrawn_at"] is not None:
            raise AnalysisError("No active correction with that ID in this revision")
        connection.execute(
            "INSERT INTO case_withdrawals VALUES (?, ?, ?, ?)",
            (correction_id, reason, reviewer_hash, datetime.now(UTC).isoformat(timespec="seconds")),
        )


def assert_current(connection, review):
    row = connection.execute(
        "SELECT review_key FROM case_revisions WHERE case_id = ? ORDER BY id DESC LIMIT 1",
        (review["case_id"],),
    ).fetchone()
    marker = connection.execute(
        "SELECT intervention_at, intervention_note FROM cases WHERE id = ?", (review["case_id"],)
    ).fetchone()
    records = connection.execute(
        "SELECT c.id, w.created_at FROM case_corrections c LEFT JOIN case_withdrawals w"
        " ON w.correction_id = c.id WHERE c.revision_id = ? ORDER BY c.id LIMIT ?",
        (review["revision_id"], MAX_CORRECTIONS + 1),
    ).fetchall()
    if (
        not row
        or row[0] != review["revision_key"]
        or not marker
        or tuple(marker) != review["marker"]
        or [tuple(r) for r in records] != [(r["id"], r["withdrawn_at"]) for r in review["records"]]
    ):
        raise AnalysisError("Evidence, marker, or review changed; request the view again")


def banner(review):
    if not review["active"]:
        return ""
    return (
        f"\n\nSTAFF REVIEW: {review['active']} active correction(s) on this revision. "
        "Model counts and hypotheses above are unchanged. Read /seismograph_reviews "
        "for the staff interpretation and its separately labeled preview."
    )


def render(review, guild_id, page=1):
    pages = max(1, (len(review["records"]) + 7) // 8)
    if not 1 <= page <= pages:
        raise AnalysisError(f"Choose a review page from 1 to {pages}")
    esc = escape_markdown
    lines = [
        f"# Case {review['case_id']} staff review",
        f"Revision {review['revision_id']}; revision_key: `{review['revision_key']}`.",
        "Human annotations, not verified facts. Original evidence and model output are unchanged.",
        f"Active corrections: {review['active']}; "
        f"retained audit entries: {len(review['records'])}.",
        "",
        "## Follow-up counts",
        "Both views use the current intervention marker. A preview is not an official status.",
    ]
    for key, label in (
        ("original", "Original model selection"),
        ("preview", "Staff-adjusted preview"),
    ):
        status = review[key]
        lines.append(
            f"{label}: {status['status']}; {status['failure_authors']} failure / "
            f"{status['success_authors']} success reporter(s)."
        )
    if review["active"]:
        lines.append(
            "CAUTION: relabeling or excluding failures is a human interpretation, "
            "not new evidence of recovery. Hypotheses are not recalculated."
        )
    lines += ["", "## Correction history", f"Page {page} of {pages}; up to 8 entries per page."]
    for record in review["records"][(page - 1) * 8 : page * 8]:
        obs = review["observations"][record["message_id"]]
        state = "withdrawn" if record["withdrawn_at"] else "active"
        source = jump_link(guild_id, review["channels"][record["message_id"]], record["message_id"])
        lines += [
            f"- Correction {record['id']} ({state}), {record['created_at']}: "
            f"{obs['outcome']} -> {record['outcome']}",
            f"  {source}",
            f"  Source excerpt: {esc(obs['quote'])}",
            f"  Reason: {esc(record['reason'])}",
        ]
        if record["withdrawn_at"]:
            lines.append(
                f"  Withdrawn {record['withdrawn_at']}: {esc(record['withdrawal_reason'])}"
            )
    if not review["records"]:
        lines.append("No corrections on this revision.")
    lines += [
        "",
        "Refreshes do not inherit corrections. Privacy invalidation can erase this audit.",
        "No reviewer identities are displayed; no model call was made.",
    ]
    return "\n".join(lines)
