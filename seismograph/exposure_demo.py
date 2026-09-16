"""Fictional patch-exposure replay using the production assessment logic."""

from dataclasses import asdict, replace

from . import cases, exposure, storage
from .case_demo import RELEASE, scenario


def run():
    messages, payload = scenario()
    for index, text in (
        (-2, "I installed build 42 with the PDF patch. Exported citations now work."),
        (-1, "I am still on build 41 without the patch. PDF citations are missing."),
    ):
        messages[index] = replace(messages[index], content=text)
        payload["observations"][index]["quote"] = text
    # A third post-marker report supplies no patch or build information.
    unknown = replace(
        messages[-1],
        message_id="300000000000000007",
        author_hash="fictional-author-f",
        content="PDF citations are still missing for me. I do not know my build.",
    )
    messages.append(unknown)
    payload["observations"].append(
        {
            "message_id": unknown.message_id,
            "quote": unknown.content,
            "outcome": "failure",
            "condition": "PDF export; build unknown",
        }
    )
    connection = storage.connect(":memory:")
    channels = [messages[0].channel_id]
    lines = [
        "# Seismograph Patch Exposure Demo",
        "",
        "Fictional sources and recorded outcomes; no Sonar or Discord connection.",
        "Staff assessments are interpretations of quoted reports, not verified installation.",
        "The opaque scope key and audit times are generated during the replay.",
        "",
    ]
    try:
        storage.store_messages(connection, [asdict(m) for m in messages])
        cid = cases.save(connection, payload, messages)
        cases.mark_release(connection, cid, "Fictional build 42 PDF patch", RELEASE)
        before = exposure.view(connection, cid, channels)
        assert before["original"]["groups"]["unknown"]["observations"] == 3
        lines += [
            "## A release timestamp is not exposure",
            "",
            "All three post-marker reports initially remain unknown, even when they name a build.",
            "There is no automatic rollout assumption or model-generated exposure label.",
            "",
        ]
        for message, state in ((messages[-3], "received"), (messages[-2], "not_received")):
            exposure.assess(
                connection,
                cid,
                before["scope_key"],
                message.message_id,
                state,
                message.content,
                "The reporter explicitly names the installed build.",
                "fictional-staff-a",
                channels,
            )
        segmented = exposure.view(connection, cid, channels)
        assert segmented["original"]["groups"]["received"]["success_reporters"] == 1
        assert segmented["original"]["groups"]["not_received"]["failure_reporters"] == 1
        assert segmented["original"]["groups"]["unknown"]["failure_reporters"] == 1
        assert cases.load(connection, cid)[1] == payload
        lines += [
            "## Three different follow-up groups",
            "",
            exposure.render(segmented, 100000000000000001),
            "",
        ]
        exposure.assess(
            connection,
            cid,
            before["scope_key"],
            messages[-3].message_id,
            "unknown",
            "",
            "Reconfirm whether this build includes the same marked patch.",
            "fictional-staff-b",
            channels,
        )
        reset = exposure.view(connection, cid, channels)
        assert reset["original"]["groups"]["received"]["observations"] == 0
        assert len(reset["events"]) == 3
        lines += [
            "## Retract an assessment without hiding its history",
            "",
            "The received assessment is superseded by an explicit unknown reset.",
            "Both reasons remain in the bounded audit; this is not evidence of recovery.",
            "",
        ]
        storage.opt_out(connection, "fictional-staff-b")
        erased = exposure.view(connection, cid, channels)
        assert messages[-3].message_id not in erased["latest"]
        assert len(erased["events"]) == 1
        lines += [
            "## Reviewer opt-out does not restore an earlier claim",
            "",
            "The entire affected observation's exposure ledger is erased, including the",
            "superseded received entry. The unrelated not-received assessment remains.",
            "",
        ]
        cases.mark_release(connection, cid, "Fictional build 42 PDF patch", RELEASE)
        fresh = exposure.view(connection, cid, channels)
        assert fresh["scope_key"] != before["scope_key"]
        assert fresh["original"]["groups"]["unknown"]["observations"] == 3
        try:
            exposure.assess(
                connection,
                cid,
                before["scope_key"],
                messages[-3].message_id,
                "received",
                messages[-3].content,
                "This obsolete scope must be rejected.",
                "fictional-staff-a",
                channels,
            )
        except cases.AnalysisError:
            pass
        else:
            raise AssertionError("A stale release scope was accepted")
        lines += [
            "## Re-recording a release starts a new scope",
            "",
            "Even the same release note and timestamp produce a new key. All exposure",
            "returns to unknown; old commands cannot silently apply to the new marker.",
            "",
        ]
        storage.forget_messages(connection, [messages[0].message_id])
        assert not connection.execute("SELECT * FROM case_exposures").fetchall()
        lines += [
            "## Source deletion",
            "",
            "Removing contributing evidence deletes dependent revisions and exposure audits.",
            "Already-exported files and delivered or in-flight Discord text still require",
            "operator cleanup. This synthetic replay is not a live-model or capacity result.",
            "",
        ]
        return "\n".join(lines)
    finally:
        connection.close()
