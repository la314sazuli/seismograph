"""Fictional human correction of a deliberately wrong recorded outcome."""

from dataclasses import asdict

from . import case_reviews as reviews
from . import cases, storage
from .case_demo import RELEASE, scenario


def run():
    messages, payload = scenario()
    # Deliberate recorded model error: an explicit failure is labeled success.
    payload["observations"][-1]["outcome"] = "success"
    connection = storage.connect(":memory:")
    channels = [messages[0].channel_id]
    lines = [
        "# Seismograph Staff Review Demo",
        "",
        "Fictional messages and recorded interpretations; no model or Discord connection.",
        "The recorded model deliberately mislabels an explicit failure as success.",
        "Timestamps and opaque revision keys are generated during this demo run.",
        "",
    ]
    try:
        storage.store_messages(connection, [asdict(m) for m in messages])
        cid = cases.save(connection, payload, messages)
        cases.mark_release(connection, cid, "Fictional patch", RELEASE)
        original = reviews.view(connection, cid, channels)
        key = original["revision_key"]
        lines += ["## Before review", "", reviews.render(original, 100000000000000001), ""]
        rid = reviews.correct(
            connection,
            cid,
            key,
            messages[-1].message_id,
            "failure",
            "The exact excerpt says the PDF export still has no citations.",
            "fictional-reviewer-a",
            channels,
        )
        corrected = reviews.view(connection, cid, channels)
        assert corrected["original"]["success_authors"] == 2
        assert corrected["preview"]["failure_authors"] == 1
        assert cases.load(connection, cid)[1] == payload
        lines += [
            "## Staff corrects the label",
            "",
            reviews.render(corrected, 100000000000000001),
            "",
        ]
        reviews.withdraw(
            connection,
            cid,
            key,
            rid,
            "Demonstrating withdrawal only; this does not establish "
            "that the report was successful.",
            "fictional-reviewer-b",
            channels,
        )
        withdrawn = reviews.view(connection, cid, channels)
        assert withdrawn["active"] == 0
        lines += [
            "## Withdrawal retains both reasons",
            "",
            reviews.render(withdrawn, 100000000000000001),
            "",
        ]
        storage.opt_out(connection, "fictional-reviewer-b")
        assert reviews.view(connection, cid, channels)["records"] == []
        lines += [
            "## Reviewer opt-out",
            "",
            "The withdrawn correction and its withdrawal are both erased.",
            "Removing the withdrawal cannot reactivate the correction.",
            "The original source messages and recorded model interpretation remain unchanged.",
            "",
        ]
        storage.forget_messages(connection, [messages[0].message_id])
        assert connection.execute("SELECT COUNT(*) FROM case_revisions").fetchone()[0] == 0
        lines += [
            "## Source deletion",
            "",
            "Deleting contributing evidence removes the retained case history.",
            "No derived correction is restored. This earlier fictional export is not automatically",
            "deleted; actual exports and delivered Discord messages require operator cleanup.",
            "",
        ]
        return "\n".join(lines)
    finally:
        connection.close()
