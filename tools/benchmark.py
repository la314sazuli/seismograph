"""Synthetic local throughput check. No Discord traffic or model inference."""

import argparse
import json
import resource
import tempfile
import time
from pathlib import Path

from seismograph import storage
from seismograph.analysis import LLMClient
from seismograph.pipeline import generate_report
from seismograph.privacy import hash_author
from seismograph.report import render_markdown, split_for_discord


class RecordedClassifier(LLMClient):
    def __init__(self):
        super().__init__("https://example.invalid/v1", "synthetic", "synthetic")

    def complete_json(self, system, user):
        ids = [
            line.split(" | ")[0]
            for line in user.splitlines()
            if " | " in line and line.split(" | ")[0].isdigit()
        ]
        return {
            "signals": [
                {
                    "title": "Saved filters disappear",
                    "category": "broken",
                    "product_surface": "filters",
                    "severity": 3,
                    "confidence": 0.8,
                    "expected": "Filters persist.",
                    "observed": "Synthetic users report missing filters.",
                    "supporting_message_ids": ids,
                    "representative_message_ids": ids[:3],
                    "evidence_rationale": "Synthetic independent reports.",
                    "suggested_next_step": "Reproduce filter persistence.",
                }
            ]
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=int, default=20_000)
    args = parser.parse_args()
    if args.messages < 3:
        parser.error("--messages must be at least 3")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "benchmark.db"
        db = storage.connect(str(path))
        for offset in range(0, args.messages, 100):
            storage.store_messages(
                db,
                [
                    {
                        "message_id": str(900000000000000000 + i),
                        "channel_id": "200000000000000001",
                        "author_hash": hash_author(i % 500_000, "synthetic-benchmark-secret"),
                        "created_at": "2026-08-03T12:00:00+00:00",
                        "content": "My saved filters disappeared after reopening the workspace.",
                    }
                    for i in range(offset, min(offset + 100, args.messages))
                ],
            )
        ingested = time.perf_counter()
        _, report, messages, run = generate_report(
            db,
            RecordedClassifier(),
            "manual",
            "2026-08-03T00:00:00+00:00",
            "2026-08-10T00:00:00+00:00",
            "synthetic week",
            channel_ids=(200000000000000001,),
            max_messages=args.messages,
        )
        chunks = split_for_discord(render_markdown(report, 100000000000000001, messages, True))
        elapsed = time.perf_counter() - started
        assert report.tremors[0].message_count == args.messages
        assert report.tremors[0].distinct_users == min(args.messages, 500_000)
        assert all(len(c) <= 2000 for c in chunks)
        db.close()
        print(
            json.dumps(
                {
                    "synthetic": True,
                    "live_discord": False,
                    "live_llm": False,
                    "messages": args.messages,
                    "ingestion_seconds": round(ingested - started, 3),
                    "total_seconds": round(elapsed, 3),
                    "peak_rss_mib_linux": round(
                        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
                    ),
                    "database_mib": round(path.stat().st_size / 1024**2, 2),
                    "analysis_batches": run.batches,
                    "classifier_calls": run.requests,
                    "discord_chunks": len(chunks),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
