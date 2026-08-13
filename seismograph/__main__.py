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
from .analysis import AnalysisError, LLMClient, prepare, validate_signals
from .config import ConfigError, load_config
from .pipeline import analysis_period, period_label
from .report import InsufficientEvidence, build_report, render_markdown
from .scoring import rank

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
DEMO_GUILD_ID = 100000000000000001
DEMO_PERIOD_DAYS = 7

log = logging.getLogger("seismograph")


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def cmd_run(_: argparse.Namespace) -> int:
    from .bot import build_client

    config = load_config()
    log.info(
        "starting: %d source channel(s), timezone %s, window %d day(s), retention %d day(s)",
        len(config.source_channel_ids),
        config.report_timezone,
        config.analysis_days,
        config.retention_days,
    )
    client = build_client(config)
    client.run(config.discord_token, log_handler=None)
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    config = load_config()
    connection = storage.connect(config.database_path)
    days = args.days or config.retention_days
    deleted = storage.prune(connection, days)
    print(f"Deleted {deleted} message(s) older than {days} day(s).")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Render a report from synthetic fixtures with no Discord or paid API call."""
    if not FIXTURES.is_dir():
        print(
            f"Fixtures not found at {FIXTURES}. The demo runs from a checkout of the "
            "repository; install it with 'pip install -e .' and run the command there.",
            file=sys.stderr,
        )
        return 2
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
        client = LLMClient(config.llm_base_url, config.llm_api_key, config.llm_model)
        from .analysis import analyze

        try:
            signals, rejections = analyze(client, prepared, "synthetic demo period")
        except AnalysisError as exc:
            print(f"Live analysis failed: {exc}", file=sys.stderr)
            return 1
        history = {}
    else:
        recorded = json.loads((FIXTURES / "synthetic_analysis.json").read_text())
        signals, rejections = validate_signals(recorded, prepared)
        history = {
            (key.lower(), category): count
            for key, category, count in recorded.get("previous_message_counts", [])
        }

    for reason in rejections:
        print(f"Rejected candidate: {reason}", file=sys.stderr)

    signals = [
        replace(signal, previous_message_count=history.get((signal.title.lower(), signal.category)))
        for signal in signals
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seismograph", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="Run the Discord bot.").set_defaults(handler=cmd_run)

    demo = subparsers.add_parser("demo", help="Render a report from synthetic fixtures.")
    demo.add_argument(
        "--live",
        action="store_true",
        help="Call the configured LLM instead of the recorded fixture analysis.",
    )
    demo.set_defaults(handler=cmd_demo)

    prune = subparsers.add_parser("prune", help="Delete messages past the retention window.")
    prune.add_argument("--days", type=int, help="Override RETENTION_DAYS.")
    prune.set_defaults(handler=cmd_prune)

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
