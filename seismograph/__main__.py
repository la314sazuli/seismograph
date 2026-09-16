"""Command line entry point: run, demo, prune."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from . import storage
from .analysis import AnalysisError, AnalysisRun, LLMClient, prepare, validate_signals
from .config import ConfigError, load_config
from .pipeline import analysis_period, period_label
from .report import InsufficientEvidence, build_report, render_markdown
from .scoring import rank

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO_GUILD_ID = 100000000000000001
DEMO_PERIOD_DAYS = 7

log = logging.getLogger("seismograph")


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Library debug output can include request URLs and raw gateway payloads.
    for name in ("discord", "httpx", "httpcore", "aiohttp"):
        logging.getLogger(name).setLevel(logging.WARNING)


def cmd_run(_: argparse.Namespace) -> int:
    import fcntl

    from .bot import build_client

    config = load_config()
    if os.environ.get("LLM_PROCESSING_APPROVED", "").lower() != "true":
        raise ConfigError(
            "Set LLM_PROCESSING_APPROVED=true only after completing the rollout checklist"
        )
    os.umask(0o077)
    Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)
    log.info(
        "starting: %d source channel(s), timezone %s, window %d day(s), retention %d day(s)",
        len(config.source_channel_ids),
        config.report_timezone,
        config.analysis_days,
        config.retention_days,
    )
    with open(config.database_path + ".lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigError("Another bot process is using DATABASE_PATH") from None
        client = build_client(config)
        client.run(config.discord_token, log_handler=None)
    return 1 if client.startup_failed else 0


def cmd_prune(args: argparse.Namespace) -> int:
    connection = storage.connect(os.environ.get("DATABASE_PATH", "seismograph.db"))
    days = args.days if args.days is not None else int(os.environ.get("RETENTION_DAYS", "30"))
    try:
        deleted = storage.prune(connection, days)
    finally:
        connection.close()
    print(f"Deleted {deleted} message(s) older than {days} day(s).")
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    connection = storage.connect(os.environ.get("DATABASE_PATH", "seismograph.db"))
    try:
        if args.retry is not None:
            cursor = connection.execute(
                "DELETE FROM runs WHERE id = ? AND status = 'failed' AND kind = 'scheduled'",
                (args.retry,),
            )
            connection.commit()
            if cursor.rowcount != 1:
                print(
                    "Only a failed, pre-publication scheduled run can be retried.", file=sys.stderr
                )
                return 2
            print("Failed run cleared; the next scheduler tick can retry it.")
        for row in connection.execute(
            "SELECT id, kind, period_start, period_end, status FROM runs ORDER BY id DESC LIMIT 20"
        ):
            print(" | ".join(str(v) for v in row))
    finally:
        connection.close()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Render a report from synthetic fixtures with no Discord or paid API call."""
    messages = json.loads((FIXTURES / "synthetic_messages.json").read_text())["messages"]
    prepared = prepare(messages)
    print(
        f"Loaded {len(messages)} synthetic message(s); "
        f"{len(prepared)} remained after deterministic preparation.\n",
        file=sys.stderr,
    )

    if args.live:
        try:
            config = load_config()
        except ConfigError as exc:
            print(f"Live demo needs full configuration: {exc}", file=sys.stderr)
            return 2
        client = LLMClient.from_config(config)
        from .analysis import analyze

        try:
            analysis = analyze(client, prepared, "synthetic demo period")
        except AnalysisError as exc:
            print(f"Live analysis failed: {exc}", file=sys.stderr)
            return 1
        print("Live analysis summary", file=sys.stderr)
        for line in analysis.summary_lines():
            print(f"  {line}", file=sys.stderr)
        print(file=sys.stderr)
        history = {}
    else:
        recorded = json.loads((FIXTURES / "synthetic_analysis.json").read_text())
        accepted, rejected = validate_signals(recorded, prepared)
        analysis = AnalysisRun(
            signals=accepted,
            rejections=rejected,
            messages=len(prepared),
            batches=1,
            signals_before_merge=len(accepted),
        )
        history = {
            (key.lower(), category): count
            for key, category, count in recorded.get("previous_message_counts", [])
        }

    for rejection in analysis.rejections:
        print(f"Rejected candidate: {rejection}", file=sys.stderr)

    signals = [
        replace(signal, previous_message_count=history.get((signal.title.lower(), signal.category)))
        for signal in analysis.signals
    ]
    signals = rank(signals, period_days=DEMO_PERIOD_DAYS)

    try:
        report = build_report(signals, prepared, "August 3–9 (synthetic)")
    except InsufficientEvidence as exc:
        print(f"No report: {exc}", file=sys.stderr)
        return 1

    print(render_markdown(report, DEMO_GUILD_ID, prepared, synthetic=True))
    return 0


def cmd_period(_: argparse.Namespace) -> int:
    config = load_config()
    start, end = analysis_period(datetime.now(UTC), config.report_timezone, config.analysis_days)
    print(f"{period_label(start, end, config.report_timezone)}  [{start} .. {end})")
    return 0


def cmd_investigate_demo(_: argparse.Namespace) -> int:
    from .case_demo import run

    print(run(), end="")
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    from .research import research_public

    config = load_config()
    try:
        result = research_public(
            config, args.query, tuple(args.domains.split(",")), approved=args.approved_public_query
        )
    except AnalysisError as exc:
        print(f"Research stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    from .evaluation import register as register_evaluation

    parser = argparse.ArgumentParser(prog="seismograph", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_evaluation(subparsers)

    def changes_demo(_args):
        from .case_history_demo import run

        print(run(), end="")
        return 0

    subparsers.add_parser(
        "changes-demo", help="Replay fictional case changes without credentials or a model."
    ).set_defaults(handler=changes_demo)

    def review_demo(_args):
        from .case_review_demo import run

        print(run(), end="")
        return 0

    subparsers.add_parser(
        "review-demo", help="Replay staff corrections and withdrawals without any connection."
    ).set_defaults(handler=review_demo)

    def exposure_demo(_args):
        from .exposure_demo import run

        print(run(), end="")
        return 0

    subparsers.add_parser(
        "exposure-demo", help="Replay quote-backed patch exposure without credentials or a model."
    ).set_defaults(handler=exposure_demo)

    subparsers.add_parser("run", help="Run the Discord bot.").set_defaults(handler=cmd_run)

    demo = subparsers.add_parser("demo", help="Render a report from synthetic fixtures.")
    demo.add_argument(
        "--live",
        action="store_true",
        help="Call the configured LLM instead of the recorded fixture analysis.",
    )
    demo.set_defaults(handler=cmd_demo)
    subparsers.add_parser(
        "investigate-demo", help="Run a fictional investigation and fix-verification scenario."
    ).set_defaults(handler=cmd_investigate_demo)
    research = subparsers.add_parser(
        "research-public", help="Explicitly research a public topic through Sonar; paid API call."
    )
    research.add_argument("query")
    research.add_argument(
        "--domains", required=True, help="Comma-separated public domain allowlist."
    )
    research.add_argument("--approved-public-query", action="store_true")
    research.set_defaults(handler=cmd_research)

    prune = subparsers.add_parser("prune", help="Delete messages past the retention window.")
    prune.add_argument("--days", type=int, help="Override RETENTION_DAYS.")
    prune.set_defaults(handler=cmd_prune)

    runs = subparsers.add_parser("runs", help="Inspect runs; retry only failed scheduled runs.")
    runs.add_argument("--retry", type=int, metavar="ID")
    runs.set_defaults(handler=cmd_runs)

    subparsers.add_parser("period", help="Print the current analysis period.").set_defaults(
        handler=cmd_period
    )

    args = parser.parse_args(argv)
    configure_logging(args.verbose or os.environ.get("SEISMOGRAPH_DEBUG") == "1")
    try:
        return args.handler(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
