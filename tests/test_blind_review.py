import csv
import hashlib
import io
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from seismograph import cases
from seismograph import evaluation as ev
from seismograph.analysis import LLMClient
from tools import blind_review as br


@pytest.fixture
def fresh():
    raw = ev.read_json(br.DEFAULT_INPUTS)
    inputs = ev.load_inputs(raw)
    gold = ev.load_gold(ev.read_json(br.DEFAULT_INPUTS.with_name("gold.json")), inputs)
    return raw, inputs, gold


def test_fresh_is_disjoint_and_checks_current_rules(fresh):
    _, inputs, gold = fresh
    old = ev.load_inputs(ev.read_json(ev.FIXTURES / "evaluation_inputs.json"))
    assert len(inputs) == 10
    assert not ({c["id"] for c in inputs} & {c["id"] for c in old})
    assert not (
        {m.content for c in inputs for m in c["messages"]}
        & {m.content for c in old for m in c["messages"]}
    )
    result = ev.score(inputs, gold, ev.reference_replay(inputs, gold))
    assert result["passed"]
    assert result["details"][0]["actual_status"] == "Continued failures reported"
    assert "limitation" in gold["h01"]["review_note"]


def test_empty_pack_does_not_fabricate_answers_or_ratings(tmp_path, monkeypatch, fresh):
    monkeypatch.setattr(httpx, "post", Mock(side_effect=AssertionError("No network")))
    dest = tmp_path / "review"
    assert br.main(["prepare", "--output", str(dest)]) == 0
    packet = (dest / "reviewers/packet.md").read_text()
    assert "NOT STARTED" in packet
    assert packet.count("NOT COLLECTED") == 20
    for filename in ("reviewer-1.csv", "reviewer-2.csv"):
        rows = list(csv.DictReader((dest / "reviewers" / filename).open()))
        assert len(rows) == 20
        assert all(
            all(v == "" for k, v in r.items() if k not in {"case_id", "candidate"}) for r in rows
        )
    assert (dest / "reviewers/protocol.md").exists()
    assert not (dest / "reviewers/coordinator.json").exists()
    assert br.main(["prepare", "--output", str(dest)]) == 2


def reports_for(raw, inputs):
    return [
        {
            "mode": "live_summary_baseline",
            "model": f"SECRET_MODEL_{i}",
            "provider": "SECRET_PROVIDER",
            "inputs_sha256": ev.fingerprint(raw),
            "scores": {"SECRET_SCORE": 999},
            "predictions": {
                item["id"]: {
                    "payload": {
                        "summary": f"Candidate content {i}",
                        "next_question": "What happened?",
                    }
                }
                for item in inputs
            },
        }
        for i in range(2)
    ]


def test_mapping_matches_blind_order_and_hides_metadata(fresh):
    raw, inputs, _ = fresh
    reports = reports_for(raw, inputs)
    text, ratings, coordinator = br.packet(raw, inputs, reports)
    for secret in ("SECRET_MODEL", "SECRET_PROVIDER", "SECRET_SCORE", "review_note"):
        assert secret not in text
    assert len(list(csv.DictReader(io.StringIO(ratings)))) == 20
    assert {m["case_id"] for m in coordinator["mapping"].values()} == {i["id"] for i in inputs}
    for rid, mapping in coordinator["mapping"].items():
        section = text.split(f"## {rid}\n")[1].split("\n## ")[0]
        assert {mapping["A"], mapping["B"]} == {0, 1}
        for label in ("A", "B"):
            candidate = section.split(f"### Candidate {label}\n")[1].split("\n### ")[0]
            assert f"Candidate content {mapping[label]}" in candidate


def test_missing_outputs_are_kept_visible(fresh):
    raw, inputs, _ = fresh
    reports = reports_for(raw, inputs)
    reports[0]["predictions"] = {}
    text, _, coordinator = br.packet(raw, inputs, reports)
    assert text.count("NO USABLE OUTPUT") == 10
    assert len(coordinator["mapping"]) == 10


@pytest.mark.parametrize("change", ["reference", "hash", "unknown", "bad_record", "bad_payload"])
def test_invalid_run_preflight(tmp_path, fresh, change):
    raw, inputs, _ = fresh
    reports = reports_for(raw, inputs)
    report = reports[0]
    if change == "reference":
        report["mode"] = "reference_replay_NOT_model_quality"
    elif change == "hash":
        report["inputs_sha256"] = "wrong"
    elif change == "unknown":
        report["predictions"]["unknown"] = {}
    elif change == "bad_record":
        report["predictions"]["h01"] = []
    else:
        report["predictions"]["h01"] = {"payload": []}
    paths = [tmp_path / f"{i}.json" for i in range(2)]
    for path, report in zip(paths, reports, strict=True):
        path.write_text(json.dumps(report))
    with pytest.raises(ev.EvaluationError):
        br.accepted_runs(paths, raw, inputs)
    assert (
        br.main(["prepare", "--runs", *map(str, paths), "--output", str(tmp_path / "packet")]) == 2
    )
    assert not (tmp_path / "packet").exists()


def test_baseline_opt_in_and_output_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "load_model_config", Mock(side_effect=AssertionError("No secrets")))
    target = tmp_path / "run.json"
    assert br.main(["baseline", "--output", str(target)]) == 2
    target.write_text("keep")
    assert br.main(["baseline", "--allow-api-calls", "--output", str(target)]) == 2
    assert target.read_text() == "keep"


def test_baseline_uses_same_evidence_and_no_gold_or_reporters(fresh, monkeypatch):
    _, inputs, _ = fresh
    bodies = []

    def post(url, **kwargs):
        bodies.append(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "Recorded test summary",
                                    "next_question": "Which build was tested?",
                                }
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", post)
    model = LLMClient(
        "https://example.test",
        "fake",
        "test-model",
        provider="sonar",
        response_schema=br.BASELINE_SCHEMA,
        max_requests=24,
    )
    predictions = br.collect_baseline(inputs, model)
    assert len(predictions) == model.requests_used == 10
    for body, item in zip(bodies, inputs, strict=True):
        assert body["disable_search"] is True
        user = json.loads(body["messages"][1]["content"])
        assert user["messages"] == [
            {"message_id": m.message_id, "content": m.content, "timestamp": m.created_at}
            for m in item["messages"]
        ]
        assert set(user) == {"issue_to_investigate", "messages"}
        assert "gold" not in body["messages"][0]["content"]


def test_baseline_repair_and_global_attempt_budget(fresh, monkeypatch):
    _, inputs, _ = fresh
    post = Mock(
        return_value=httpx.Response(
            200,
            request=httpx.Request("POST", "https://example.test"),
            json={"choices": [{"message": {"content": "{}"}}]},
        )
    )
    monkeypatch.setattr(httpx, "post", post)
    model = LLMClient("https://example.test", "fake", "test", max_requests=3)
    predictions = br.collect_baseline(inputs, model)
    assert post.call_count == model.requests_used == 3
    assert len(predictions) == 10
    assert all("error" in p for p in predictions.values())
    assert "one repair" in predictions["h01"]["error"]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"summary": "x", "next_question": ""},
        {"summary": "x" * 4001, "next_question": "Which version?"},
    ],
)
def test_invalid_baseline_payload(payload):
    with pytest.raises(cases.InvalidOutput):
        br.baseline_payload(payload)


def test_baseline_run_report_and_roundtrip(tmp_path, monkeypatch, fresh):
    raw, inputs, _ = fresh
    monkeypatch.setattr(
        br,
        "load_model_config",
        lambda: SimpleNamespace(
            llm_base_url="https://example.test",
            llm_api_key="SECRET_API_KEY",
            llm_model="test",
            llm_provider="sonar",
        ),
    )
    monkeypatch.setattr(
        br,
        "collect_baseline",
        lambda rows, model: deepcopy(reports_for(raw, inputs)[0]["predictions"]),
    )
    path = tmp_path / "baseline.json"
    assert br.main(["baseline", "--allow-api-calls", "--output", str(path)]) == 0
    assert "SECRET_API_KEY" not in path.read_text()
    reports = br.accepted_runs([path, path], raw, inputs)
    assert len(reports) == 2
    assert reports[0]["mode"] == "live_summary_baseline"


def test_untrusted_fences_are_rendered_as_indented_data():
    value = {"summary": "```\n<script>bad</script>\n# SECRET"}
    assert all(line.startswith("    ") for line in br.block(value).splitlines())


def test_frozen_files_and_prompts_have_not_changed():
    manifest = ev.read_json(br.DEFAULT_INPUTS.with_name("freeze.json"))
    for relative, digest in manifest["file_sha256"].items():
        data = (br.DEFAULT_INPUTS.parent / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
    assert (
        hashlib.sha256(cases.SYSTEM.encode()).hexdigest() == manifest["investigation_prompt_sha256"]
    )
    assert (
        hashlib.sha256(br.BASELINE_SYSTEM.encode()).hexdigest()
        == manifest["baseline_prompt_sha256"]
    )
