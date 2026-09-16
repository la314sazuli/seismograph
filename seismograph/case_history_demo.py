"""Recorded scenario exposing evidence changes and a misleading omission."""

from copy import deepcopy
from dataclasses import asdict, replace

from . import case_history, cases, storage
from .case_demo import RELEASE, scenario


def run():
    messages, original = scenario()
    success = replace(
        messages[4],
        message_id="300000000000000007",
        author_hash="fictional-author-f",
        created_at="2026-08-06T09:30:00+00:00",
        content="I retested PDF export after the patch and the citation links now open.",
    )
    all_messages = [*messages, success]
    extra = {
        "message_id": success.message_id,
        "quote": success.content,
        "outcome": "success",
        "condition": "PDF export after patch",
    }
    original = {**original, "observations": [*original["observations"], extra], "hypotheses": []}
    connection = storage.connect(":memory:")
    lines = [
        "# Seismograph Case Changes Demo",
        "",
        "All messages, identities, links, and interpretations are fictional.",
        "Recorded outputs exercise real case persistence, comparison, and privacy invalidation.",
        "No model or Discord connection is used. Analysis timestamps record this demo run.",
        "",
    ]
    try:
        storage.store_messages(connection, [asdict(m) for m in all_messages])

        def save(selected, observed, case_id=0):
            payload = deepcopy(original)
            payload["observations"] = [
                o for o in payload["observations"] if o["message_id"] in observed
            ]
            return cases.save(connection, payload, selected, case_id)

        first = messages[:4]
        case_id = save(first, {m.message_id for m in first})
        cases.mark_release(connection, case_id, "Fictional export patch", RELEASE)
        second = [*messages[:5], success]
        save(second, {m.message_id for m in second}, case_id)
        lines += [
            "## Two explicit follow-up successes",
            "",
            case_history.render(
                case_history.compare(connection, case_id, [success.channel_id]), 100000000000000001
            ),
            "",
        ]
        save(all_messages, {m.message_id for m in all_messages}, case_id)
        lines += [
            "## A later failure enters the selected evidence",
            "",
            case_history.render(
                case_history.compare(connection, case_id, [success.channel_id]), 100000000000000001
            ),
            "",
        ]
        # Same source context, but the recorded model output omits the failure.
        save(all_messages, {m.message_id for m in all_messages} - {messages[5].message_id}, case_id)
        delta = case_history.compare(connection, case_id, [success.channel_id])
        assert delta["same_context"] and delta["failure_evidence_lost"]
        lines += [
            "## False reassurance caused by an omitted observation",
            "",
            case_history.render(delta, 100000000000000001),
            "",
        ]
        storage.opt_out(connection, messages[0].author_hash)
        try:
            case_history.compare(connection, case_id, [success.channel_id])
        except cases.AnalysisError:
            lines += [
                "## Privacy check",
                "",
                "After a contributing author's opt-out, comparison is unavailable.",
                "Zero case revisions remain; no older explanation is substituted.",
                "This fictional export was created before deletion. Real exports and",
                "previously sent messages need separate operator deletion.",
                "",
            ]
        else:
            raise AssertionError("Deleted case history was restored")
        return "\n".join(lines)
    finally:
        connection.close()
