"""Executable fictional case. No credentials, network, or model required."""

from __future__ import annotations

from dataclasses import asdict

from . import cases, storage
from .analysis import PreparedMessage

RELEASE = "2026-08-05T12:00:00+00:00"


def scenario() -> tuple[list[PreparedMessage], dict]:
    rows = [
        (
            "01",
            "a",
            "04T09",
            "Citations appear in my answer but disappear when I export a PDF.",
            "failure",
            "PDF export",
        ),
        (
            "02",
            "b",
            "04T10",
            "My PDF export has no citations; the original answer has them.",
            "failure",
            "PDF export",
        ),
        (
            "03",
            "c",
            "04T11",
            "Citations in the normal answer open correctly for me.",
            "success",
            "Normal answer, not PDF",
        ),
        (
            "04",
            "d",
            "04T12",
            "Sharing the answer link keeps the citations visible.",
            "workaround",
            "Shared answer link",
        ),
        (
            "05",
            "a",
            "06T09",
            "After the patch my PDF export now includes clickable citations.",
            "success",
            "PDF export after patch",
        ),
        (
            "06",
            "e",
            "06T10",
            "After the patch my PDF export still has no citations.",
            "failure",
            "PDF export after patch",
        ),
    ]
    messages, observations = [], []
    for suffix, author, time, content, outcome, condition in rows:
        mid = f"3000000000000000{suffix}"
        messages.append(
            PreparedMessage(
                mid,
                "200000000000000011",
                f"fictional-author-{author}",
                f"2026-08-{time}:00:00+00:00",
                content,
            )
        )
        observations.append(
            {
                "message_id": mid,
                "quote": content,
                "outcome": outcome,
                "condition": condition,
            }
        )
    ids = [m.message_id for m in messages]
    payload = {
        "title": "Citations lost in PDF export",
        "observations": observations,
        "hypotheses": [
            {
                "explanation": "The export path drops citations that exist in the answer.",
                "supporting_ids": ids[:2],
                "contradicting_ids": [ids[4]],
                "falsification_test": (
                    "Reproduce missing citations in the original answer before export."
                ),
            },
            {
                "explanation": "Citation generation is failing across answer formats.",
                "supporting_ids": [ids[1]],
                "contradicting_ids": [ids[0], ids[2], ids[3]],
                "falsification_test": (
                    "Show that the same answer has working citations before export."
                ),
            },
        ],
        "next_question": (
            "For the same answer, do citations work before PDF export, and which app "
            "version produced the exported file?"
        ),
    }
    return messages, payload


def run() -> str:
    messages, payload = scenario()
    connection = storage.connect(":memory:")
    try:
        storage.store_messages(connection, [asdict(m) for m in messages])
        case_id = cases.save(connection, payload, messages)
        cases.mark_release(connection, case_id, "Fictional PDF citation patch", RELEASE)
        card = cases.render(connection, case_id, 100000000000000001)
        before = cases.verification(
            {**payload, "observations": payload["observations"][:4]}, messages, RELEASE
        )
        after = cases.verification(payload, messages, RELEASE)
        storage.opt_out(connection, messages[0].author_hash)
        retained = connection.execute("SELECT COUNT(*) FROM case_revisions").fetchone()[0]
        assert retained == 0
        return (
            "# Seismograph Investigation Demo\n\n"
            "This is an executable synthetic scenario, not a report about a real product. "
            "All identities, message timestamps, message IDs, links, and model interpretations "
            "below are fictional. Recorded interpretations exercise real validation, storage, "
            "rendering, verification, and deletion code; no model or Discord was called. "
            "The last-analyzed timestamp records when this demo was run.\n\n"
            "## The distinction\n\n"
            "A mood summary would report unhappy users and missing citations. This case "
            "instead preserves the successful counterexamples, challenges a broad diagnosis, "
            "and proposes the next observation that could distinguish the explanations.\n\n"
            "## Lifecycle checks\n\n"
            f"- Immediately after the marker: {before['status']}.\n"
            f"- With mixed follow-up reports: {after['status']} "
            f"({after['failure_authors']} failure, {after['success_authors']} success reporter).\n"
            "- After a contributing author opts out: zero retained case revisions. "
            "An older interpretation is not silently substituted.\n\n"
            "The following card was rendered before the deletion check. This exported "
            "demo remains fictional; real exports and previously sent messages need "
            "separate operator deletion.\n\n---\n\n" + card + "\n"
        )
    finally:
        connection.close()
