"""Answer-key-blind request collection and deterministic synthetic scoring."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, cases
from .analysis import AnalysisError, LLMClient, prepare
from .config import load_model_config

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STATUSES = {
    "No intervention recorded",
    "Continued failures reported",
    "Improvement corroborated, not proven resolved",
    "Insufficient follow-up; silence is not success",
}


class EvaluationError(ValueError):
    pass


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read_json(path: Path) -> dict:
    if path.stat().st_size > 1_000_000:
        raise EvaluationError("Evaluation files must be at most 1 MB")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise EvaluationError("Evaluation files must contain JSON objects")
    return value


def timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC).isoformat(timespec="seconds")
    except (ValueError, TypeError):
        raise EvaluationError("Evaluation timestamps must include a timezone") from None


def load_inputs(raw: dict) -> list[dict]:
    records = raw.get("cases")
    if (
        raw.get("suite_version") != "1"
        or not isinstance(records, list)
        or not 1 <= len(records) <= 50
    ):
        raise EvaluationError("Expected suite version 1 with 1 to 50 cases")
    result, ids = [], set()
    for item in records:
        if not isinstance(item, dict):
            raise EvaluationError("Invalid case input")
        cid, issue, rows = item.get("id"), item.get("issue"), item.get("messages")
        if not isinstance(cid, str) or not cid or cid in ids:
            raise EvaluationError("Case IDs must be unique nonempty strings")
        if not isinstance(issue, str) or not 1 <= len(issue) <= 120:
            raise EvaluationError("Invalid issue title")
        if not isinstance(rows, list) or not 1 <= len(rows) <= cases.MAX_CONTEXT:
            raise EvaluationError("Each case needs 1 to 120 messages")
        normalized = []
        for row in rows:
            if not isinstance(row, dict) or any(
                not isinstance(row.get(k), str) or not row[k]
                for k in ("message_id", "author_hash", "created_at", "content")
            ):
                raise EvaluationError("Invalid message input")
            normalized.append(
                {
                    **row,
                    "channel_id": "200000000000000011",
                    "created_at": timestamp(row["created_at"]),
                }
            )
        messages = prepare(normalized)
        if len(messages) != len(rows):
            raise EvaluationError(
                "Inputs must survive preprocessing without duplicate IDs or noise"
            )
        # Refuse oversized evidence before any provider call.
        cases.context(messages, {m.message_id for m in messages})
        release = item.get("release_at")
        result.append(
            {
                "id": cid,
                "issue": issue,
                "messages": messages,
                "release_at": None if release is None else timestamp(release),
            }
        )
        ids.add(cid)
    return result


def load_gold(raw: dict, inputs: list[dict]) -> dict:
    gold = raw.get("cases")
    if raw.get("suite_version") != "1" or not isinstance(gold, dict):
        raise EvaluationError("Expected gold suite version 1")
    if set(gold) != {c["id"] for c in inputs}:
        raise EvaluationError("Gold and input case IDs must match exactly")
    for item in inputs:
        entry = gold[item["id"]]
        if not isinstance(entry, dict):
            raise EvaluationError("Invalid gold case")
        labels, counter = entry.get("labels"), entry.get("counterexample_ids")
        known = {m.message_id for m in item["messages"]}
        if not isinstance(labels, dict) or any(
            mid not in known or not isinstance(label, str) or label not in cases.OUTCOMES
            for mid, label in labels.items()
        ):
            raise EvaluationError("Gold labels must reference known messages and outcomes")
        if (
            not isinstance(counter, list)
            or any(not isinstance(mid, str) or mid not in labels for mid in counter)
            or len(set(counter)) != len(counter)
        ):
            raise EvaluationError("Invalid gold counterexample IDs")
        if (
            not isinstance(entry.get("expected_status"), str)
            or entry["expected_status"] not in STATUSES
        ):
            raise EvaluationError("Invalid gold verification status")
    return gold


def collect(inputs: list[dict], model: LLMClient) -> dict:
    """This function has no answer-key parameter or file access."""
    predictions = {}
    for item in inputs:
        if model.requests_used >= model.max_requests:
            predictions[item["id"]] = {"error": "Global HTTP request budget exhausted"}
            continue
        try:
            payload = cases.investigate(model, item["issue"], item["messages"])
            predictions[item["id"]] = {"payload": payload}
        except AnalysisError as exc:
            predictions[item["id"]] = {"error": str(exc)}
    return predictions


def reference_replay(inputs: list[dict], gold: dict) -> dict:
    """Deliberately uses the key to test the scorer, never a model evaluation."""
    predictions = {}
    for item in inputs:
        entry = gold[item["id"]]
        by_id = {m.message_id: m for m in item["messages"]}
        counter = entry["counterexample_ids"]
        support = [mid for mid in entry["labels"] if mid not in counter]
        hypotheses = []
        if counter and support:
            hypotheses = [
                {
                    "explanation": "Scorer fixture only, not a model-generated explanation.",
                    "supporting_ids": support,
                    "contradicting_ids": counter,
                    "falsification_test": "Inspect the controlled fixture counterexamples.",
                }
            ]
        predictions[item["id"]] = {
            "payload": {
                "title": item["issue"],
                "observations": [
                    {
                        "message_id": mid,
                        "quote": by_id[mid].content[:350],
                        "outcome": outcome,
                        "condition": "Synthetic scorer fixture",
                    }
                    for mid, outcome in entry["labels"].items()
                ],
                "hypotheses": hypotheses,
                "next_question": "Scorer fixture only; no question quality was evaluated.",
            }
        }
    return predictions


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score(inputs: list[dict], gold: dict, predictions: dict) -> dict:
    if not isinstance(predictions, dict) or set(predictions) - {c["id"] for c in inputs}:
        raise EvaluationError("Predictions contain unknown cases or are not an object")
    totals = {
        "expected": 0,
        "predicted": 0,
        "matched": 0,
        "correct_labels": 0,
        "expected_counter": 0,
        "found_counter": 0,
    }
    details, confusion = [], {}
    invalid = false_reassurance = status_matches = exact_cases = 0
    for item in inputs:
        key = gold[item["id"]]
        expected = key["labels"]
        totals["expected"] += len(expected)
        totals["expected_counter"] += len(key["counterexample_ids"])
        prediction = predictions.get(item["id"], {})
        error, labels, counter, status = None, {}, set(), None
        try:
            if not isinstance(prediction, dict) or "payload" not in prediction:
                raise AnalysisError("Missing prediction or failed provider call")
            payload = cases.validate(prediction["payload"], item["messages"])
            labels = {o["message_id"]: o["outcome"] for o in payload["observations"]}
            counter = {mid for h in payload["hypotheses"] for mid in h["contradicting_ids"]}
            status = cases.verification(payload, item["messages"], item["release_at"])["status"]
        except AnalysisError as exc:
            error = str(exc)
            invalid += 1
        matched = set(labels) & set(expected)
        correct = sum(labels[mid] == expected[mid] for mid in matched)
        found_counter = set(key["counterexample_ids"]) & counter
        totals["predicted"] += len(labels)
        totals["matched"] += len(matched)
        totals["correct_labels"] += correct
        totals["found_counter"] += len(found_counter)
        status_ok = status == key["expected_status"]
        reassuring = status == "Improvement corroborated, not proven resolved" and not status_ok
        exact = (
            not error
            and labels == expected
            and status_ok
            and (len(found_counter) == len(key["counterexample_ids"]))
        )
        status_matches += status_ok
        false_reassurance += reassuring
        exact_cases += exact
        for mid in set(expected) | set(labels):
            pair = f"{expected.get(mid, '<irrelevant>')} -> {labels.get(mid, '<missing>')}"
            confusion[pair] = confusion.get(pair, 0) + 1
        details.append(
            {
                "case_id": item["id"],
                "passed": exact,
                "error": error,
                "missing_observations": sorted(set(expected) - set(labels)),
                "irrelevant_observations": sorted(set(labels) - set(expected)),
                "wrong_labels": sorted(mid for mid in matched if labels[mid] != expected[mid]),
                "missing_counterexamples": sorted(set(key["counterexample_ids"]) - counter),
                "expected_status": key["expected_status"],
                "actual_status": status,
                "false_reassurance": reassuring,
            }
        )
    return {
        "cases": len(inputs),
        "exact_cases": exact_cases,
        "invalid_cases": invalid,
        "passed": exact_cases == len(inputs),
        "observation_precision": ratio(totals["matched"], totals["predicted"]),
        "observation_recall": ratio(totals["matched"], totals["expected"]),
        "outcome_accuracy_on_matched": ratio(totals["correct_labels"], totals["matched"]),
        "end_to_end_label_recall": ratio(totals["correct_labels"], totals["expected"]),
        "counterexample_coverage": ratio(totals["found_counter"], totals["expected_counter"]),
        "verification_status_accuracy": ratio(status_matches, len(inputs)),
        "false_reassurance_cases": false_reassurance,
        "counts": totals,
        "confusion": dict(sorted(confusion.items())),
        "details": details,
    }


def run(args) -> dict:
    if not 1 <= args.max_requests <= 200:
        raise EvaluationError("max-requests must be between 1 and 200")
    if args.live and not args.allow_api_calls:
        raise EvaluationError("Live evaluation needs explicit --allow-api-calls approval")
    if args.output and (args.output.exists() or not args.output.parent.is_dir()):
        raise EvaluationError("Output must be a new file in an existing directory")
    raw_inputs, raw_gold = read_json(args.inputs), read_json(args.gold)
    inputs = load_inputs(raw_inputs)
    gold = load_gold(raw_gold, inputs)
    metadata = {"model": None, "provider": None, "http_attempts": 0}
    if args.live:
        config = load_model_config()
        model = LLMClient(
            config.llm_base_url,
            config.llm_api_key,
            config.llm_model,
            provider=config.llm_provider,
            response_schema=cases.SCHEMA,
            max_requests=args.max_requests,
        )
        predictions = collect(inputs, model)
        mode = "live_model"
        metadata = {
            "model": config.llm_model,
            "provider": config.llm_provider,
            "http_attempts": model.requests_used,
        }
    elif args.predictions:
        saved = read_json(args.predictions)
        if "inputs_sha256" in saved and saved["inputs_sha256"] != fingerprint(raw_inputs):
            raise EvaluationError("Saved predictions refer to a different input suite")
        predictions = saved.get("predictions", saved)
        mode = "offline_predictions_unverified_provenance"
    else:
        predictions = reference_replay(inputs, gold)
        mode = "reference_replay_NOT_model_quality"
    return {
        "mode": mode,
        "notice": (
            "Public author-defined synthetic challenge set; request-time answer-key separation "
            "is not a secret holdout or independent human validation. Reference replay is "
            "a scorer check, never a model-quality result. Prose quality requires human review."
        ),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "software_version": __version__,
        "suite_version": raw_inputs["suite_version"],
        "inputs_sha256": fingerprint(raw_inputs),
        "gold_sha256": fingerprint(raw_gold),
        "system_prompt_sha256": hashlib.sha256(cases.SYSTEM.encode()).hexdigest(),
        **metadata,
        "scores": score(inputs, gold, predictions),
        "predictions": predictions,
    }


def cli(args) -> int:
    try:
        report = run(args)
        text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            with args.output.open("x") as output:
                output.write(text)
            print(f"Saved {report['mode']} results to {args.output}")
            print(json.dumps(report["scores"], indent=2))
        else:
            print(text, end="")
        return 0 if report["scores"]["passed"] else 1
    except (EvaluationError, AnalysisError, OSError, ValueError) as exc:
        print(f"Evaluation stopped: {exc}", file=sys.stderr)
        return 2


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "evaluate", help="Run synthetic case evaluation; offline by default."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live", action="store_true", help="Use the configured model; explicit opt-in."
    )
    mode.add_argument(
        "--predictions", type=Path, help="Score saved predictions without an API call."
    )
    parser.add_argument("--allow-api-calls", action="store_true")
    parser.add_argument("--inputs", type=Path, default=FIXTURES / "evaluation_inputs.json")
    parser.add_argument("--gold", type=Path, default=FIXTURES / "evaluation_gold.json")
    parser.add_argument("--max-requests", type=int, default=24, help="Global HTTP attempt budget.")
    parser.add_argument(
        "--output", type=Path, help="New JSON file for scores and auditable predictions."
    )
    parser.set_defaults(handler=cli)
