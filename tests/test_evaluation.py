from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from seismograph import cases
from seismograph import evaluation as ev
from seismograph.__main__ import main
from seismograph.analysis import LLMClient
from seismograph.config import ConfigError, load_model_config


@pytest.fixture
def suite():
    inputs = ev.load_inputs(ev.read_json(ev.FIXTURES / "evaluation_inputs.json"))
    gold = ev.load_gold(ev.read_json(ev.FIXTURES / "evaluation_gold.json"), inputs)
    return inputs, gold, ev.reference_replay(inputs, gold)


def arguments(**changes):
    return SimpleNamespace(
        **{
            **{
                "live": False,
                "allow_api_calls": False,
                "predictions": None,
                "max_requests": 24,
                "inputs": ev.FIXTURES / "evaluation_inputs.json",
                "gold": ev.FIXTURES / "evaluation_gold.json",
                "output": None,
            },
            **changes,
        }
    )


def test_reference_replay_checks_scorer_not_model_quality(monkeypatch, suite):
    monkeypatch.setattr(httpx, "post", Mock(side_effect=AssertionError("No network")))
    report = ev.run(arguments())
    assert report["mode"] == "reference_replay_NOT_model_quality"
    assert report["model"] is None
    assert report["http_attempts"] == 0
    assert report["scores"]["passed"]
    assert report["scores"]["cases"] == report["scores"]["exact_cases"] == 12
    assert report["scores"]["false_reassurance_cases"] == 0
    assert all(
        len(report[k]) == 64 for k in ("inputs_sha256", "gold_sha256", "system_prompt_sha256")
    )


def test_different_workflow_success_is_not_fix_evidence(suite):
    inputs, gold, predictions = suite
    result = ev.score(inputs, gold, predictions)
    assert result["details"][0]["actual_status"].startswith("Insufficient")
    for observation in predictions["c01"]["payload"]["observations"]:
        if observation["outcome"] == "counterexample":
            observation["outcome"] = "success"
    result = ev.score(inputs, gold, predictions)
    assert not result["passed"]
    assert result["false_reassurance_cases"] == 1
    assert result["details"][0]["wrong_labels"] == ["103", "104"]


def test_missing_counterexample_is_detected(suite):
    inputs, gold, predictions = suite
    predictions["c01"]["payload"]["hypotheses"] = []
    result = ev.score(inputs, gold, predictions)
    assert result["counterexample_coverage"] == 0.5
    assert result["details"][0]["missing_counterexamples"] == ["103", "104"]
    assert not result["passed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote", "This quote was invented"),
        ("message_id", "999"),
        ("outcome", []),
    ],
)
def test_invalid_case_stays_in_denominators(suite, field, value):
    inputs, gold, predictions = suite
    predictions["c02"]["payload"]["observations"][0][field] = value
    result = ev.score(inputs, gold, predictions)
    assert result["invalid_cases"] == 1
    assert result["cases"] == 12
    assert result["counts"]["expected"] == sum(len(g["labels"]) for g in gold.values())
    assert result["observation_recall"] < 1
    assert result["end_to_end_label_recall"] < 1


def test_abstention_is_not_perfect_accuracy(suite):
    inputs, gold, _ = suite
    result = ev.score(inputs, gold, {})
    assert result["invalid_cases"] == 12
    assert result["observation_precision"] is None
    assert result["outcome_accuracy_on_matched"] is None
    assert result["end_to_end_label_recall"] == 0
    assert result["verification_status_accuracy"] == 0
    assert not result["passed"]


def test_irrelevant_but_real_message_reduces_precision(suite):
    inputs, gold, predictions = suite
    message = inputs[2]["messages"][1]
    predictions["c03"]["payload"]["observations"].append(
        {
            "message_id": message.message_id,
            "quote": message.content,
            "outcome": "unclear",
            "condition": "Unrelated",
        }
    )
    result = ev.score(inputs, gold, predictions)
    assert result["observation_precision"] < 1
    assert result["details"][2]["irrelevant_observations"] == ["302"]
    assert not result["passed"]


def test_missing_observation_reduces_recall(suite):
    inputs, gold, predictions = suite
    predictions["c02"]["payload"]["observations"].pop()
    result = ev.score(inputs, gold, predictions)
    assert result["observation_recall"] < 1
    assert result["details"][1]["missing_observations"] == ["203"]


@pytest.mark.parametrize("predictions", [[], {"not-a-case": {}}])
def test_unknown_predictions_are_rejected(suite, predictions):
    with pytest.raises(ev.EvaluationError, match="unknown"):
        ev.score(suite[0], suite[1], predictions)


def test_real_request_collector_never_sends_gold_or_author_hashes(suite, monkeypatch):
    inputs, gold, reference = suite
    gold["c01"]["challenge"] = "SECRET_ANSWER_KEY_CANARY"
    bodies = []

    def post(url, **kwargs):
        bodies.append(kwargs["json"])
        payload = reference[inputs[len(bodies) - 1]["id"]]["payload"]
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        )

    monkeypatch.setattr(httpx, "post", post)
    client = LLMClient(
        "https://example.test",
        "FAKE_KEY",
        "test-model",
        provider="sonar",
        response_schema=cases.SCHEMA,
        max_requests=24,
    )
    assert ev.score(inputs, gold, ev.collect(inputs, client))["passed"]
    assert client.requests_used == 12
    for body in bodies:
        assert body["disable_search"] is True
        prompts = json.dumps(body["messages"])
        assert "SECRET_ANSWER_KEY_CANARY" not in prompts
        assert "expected_status" not in prompts
        assert "author_hash" not in prompts
        assert not any(m.author_hash in prompts for item in inputs for m in item["messages"])


def test_global_http_budget_includes_retries_and_does_not_reset(suite, monkeypatch):
    inputs, gold, _ = suite
    post = Mock(return_value=httpx.Response(503))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr("seismograph.analysis.time.sleep", lambda _: None)
    client = LLMClient("https://example.test", "FAKE_KEY", "test-model", max_requests=2)
    predictions = ev.collect(inputs, client)
    assert client.requests_used == post.call_count == 2
    assert len(predictions) == 12
    assert "budget" in predictions["c12"]["error"].lower()
    assert ev.score(inputs, gold, predictions)["invalid_cases"] == 12


def test_live_requires_explicit_opt_in_before_configuration_or_network(monkeypatch):
    config = Mock(side_effect=AssertionError("Do not load credentials"))
    monkeypatch.setattr(ev, "load_model_config", config)
    with pytest.raises(ev.EvaluationError, match="allow-api-calls"):
        ev.run(arguments(live=True))
    config.assert_not_called()


def test_model_config_does_not_require_discord():
    cfg = load_model_config(
        {
            "LLM_API_KEY": "test",
            "LLM_BASE_URL": "https://example.test/",
            "LLM_MODEL": "test-model",
            "LLM_PROVIDER": "sonar",
        }
    )
    assert cfg.llm_base_url == "https://example.test"
    assert cfg.llm_provider == "sonar"


@pytest.mark.parametrize(
    "base",
    [
        "http://example.test",
        "https://user:pass@example.test",
        "https://example.test?secret=x",
        "https://example.test#x",
        "not-a-url",
    ],
)
def test_invalid_model_endpoints_fail_closed(base):
    with pytest.raises(ConfigError):
        load_model_config({"LLM_API_KEY": "test", "LLM_BASE_URL": base, "LLM_MODEL": "model"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_status", []),
        ("labels", {"999": "success"}),
        ("labels", {"101": []}),
        ("counterexample_ids", [{}]),
        ("counterexample_ids", ["103", "103"]),
    ],
)
def test_malformed_gold_is_rejected(suite, field, value):
    raw = ev.read_json(ev.FIXTURES / "evaluation_gold.json")
    raw["cases"]["c01"][field] = value
    with pytest.raises(ev.EvaluationError):
        ev.load_gold(raw, suite[0])


@pytest.mark.parametrize("change", ["duplicate_case", "duplicate_message", "timezone", "oversized"])
def test_invalid_inputs_fail_before_api(change):
    raw = ev.read_json(ev.FIXTURES / "evaluation_inputs.json")
    if change == "duplicate_case":
        raw["cases"].append(deepcopy(raw["cases"][0]))
    elif change == "duplicate_message":
        raw["cases"][0]["messages"].append(deepcopy(raw["cases"][0]["messages"][0]))
    elif change == "timezone":
        raw["cases"][0]["messages"][0]["created_at"] = "2026-08-01T12:00:00"
    else:
        raw["cases"][0]["messages"] = [
            {**raw["cases"][0]["messages"][0], "message_id": str(i), "content": "x" * 3000}
            for i in range(20)
        ]
    with pytest.raises((ev.EvaluationError, cases.AnalysisError)):
        ev.load_inputs(raw)


def test_report_roundtrip_and_no_overwrite(tmp_path, capsys):
    output = tmp_path / "result.json"
    assert main(["evaluate", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["scores"]["passed"]
    assert main(["evaluate", "--output", str(output)]) == 2
    assert "new file" in capsys.readouterr().err
    replay = ev.run(arguments(predictions=output))
    assert replay["scores"]["passed"]
    assert replay["mode"] == "offline_predictions_unverified_provenance"
    report["inputs_sha256"] = "mismatch"
    output.write_text(json.dumps(report))
    with pytest.raises(ev.EvaluationError, match="different input"):
        ev.run(arguments(predictions=output))


def test_exit_codes_and_missing_predictions(tmp_path, capsys):
    output = tmp_path / "empty.json"
    output.write_text("{}")
    assert main(["evaluate", "--predictions", str(output)]) == 1
    assert main(["evaluate", "--live"]) == 2
    assert main(["evaluate", "--max-requests", "0"]) == 2
    with pytest.raises(SystemExit) as exc:
        main(["evaluate", "--live", "--predictions", str(output)])
    assert exc.value.code == 2


def test_output_preflight_prevents_paid_request(tmp_path, monkeypatch):
    output = tmp_path / "existing.json"
    output.write_text("keep me")
    monkeypatch.setattr(ev, "load_model_config", Mock(side_effect=AssertionError("No API")))
    with pytest.raises(ev.EvaluationError, match="new file"):
        ev.run(arguments(live=True, allow_api_calls=True, output=output))
    assert output.read_text() == "keep me"
