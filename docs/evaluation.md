# Evaluating investigations without Discord

The evaluation command exercises the same investigation prompt, provider
adapter, evidence validator, and outcome verifier used by the bot. It needs no
Discord connection, server permission, database, or additional dependency.

This is a public, author-defined synthetic challenge set, not an independent
human benchmark. The answer key is separate from the model request; that does
not make these published fixtures a secret holdout. No real-model results are
included in this release.

## Start offline

```sh
python -m seismograph evaluate --output evaluation-smoke.json
```

The default is **reference replay, not model-quality evaluation**. It constructs
known predictions from the answer key, then checks the scorer against them.
An all-pass result proves only that these fixtures and scoring rules agree.
Regression tests also mutate predictions to verify that omissions, invented
quotes, wrong labels, irrelevant evidence, and false reassurance are detected.
It does not test a model's ability to resist the injection fixture.

Score a saved run without calling a provider:

```sh
python -m seismograph evaluate \
  --predictions evaluation-smoke.json --output rescored.json
```

Saved predictions have unverified provenance. Rescoring a reference replay does
not turn it into a model result. Reports include accepted prediction payloads, per-case
errors, UTC run time, software and suite versions, input and answer-key hashes,
the system-prompt hash, and the provider/model identifiers for live collection.
Rejected raw provider responses are not retained. Hashes identify content;
they do not authenticate who generated a prediction.
If an input hash is present in an imported report, it must match the input set.

Output paths must be new files in existing directories. Exit code `0` means
every fixture's deterministic requirements passed; `1` means at least one
failed; `2` means configuration or input validation stopped the run.

## Optional real-model run

Only run this when authorized to send the selected inputs to your model
provider. The distributed inputs are fictional. Do not substitute private
server exports without appropriate consent and your own privacy review.

Set these variables in your shell or deployment secret manager, not in Git:

```sh
export LLM_BASE_URL=https://api.perplexity.ai
export LLM_MODEL=sonar
export LLM_PROVIDER=sonar
# Set LLM_API_KEY securely in your environment.
python -m seismograph evaluate --live --allow-api-calls \
  --max-requests 24 --output sonar-run.json
```

No Discord environment variables are required. For another OpenAI-compatible
provider, set `LLM_PROVIDER=openai` and its base URL and model. Local HTTP
endpoints are allowed only for localhost; remote endpoints require HTTPS.
Provider compatibility and semantic accuracy still need to be measured.

Both `--live` and `--allow-api-calls` are required. The global HTTP-attempt
budget defaults to 24, accepts 1 through 200, includes retries and repairs,
and does not reset between cases. Cases skipped after exhaustion remain in
the score denominator. Exhaustion can favor early cases, so do not compare
partially completed runs as if they used equal coverage.

The collector receives only input cases and the model client, not the answer
key. Each request uses the production investigation prompt and preprocessed
message IDs, timestamps, and content. It excludes author hashes, the scorer's
release marker, and gold labels. The Sonar adapter disables search for this
analysis; this is not a web-research benchmark. Provider responses remain
untrusted and must pass the normal exact-quote and reference checks.

## Challenge set

Version 1 separates inputs in `seismograph/fixtures/evaluation_inputs.json`
from expected labels in `seismograph/fixtures/evaluation_gold.json`.

| Case | Trap | Expected verification |
| --- | --- | --- |
| c01 | Normal answers work, but nobody retested PDF export | Insufficient follow-up |
| c02 | One person repeats a successful retest three times | Insufficient follow-up |
| c03 | Unrelated conversation after an update | Insufficient follow-up |
| c04 | Workarounds mistaken for repairs | Insufficient follow-up |
| c05 | Two distinct reporters retest the affected workflow successfully | Improvement corroborated, not proven resolved |
| c06 | A success alongside a continued failure | Continued failures |
| c07 | Sarcastic praise describing an ongoing failure | Continued failures |
| c08 | Hearsay and quoted claims without firsthand retesting | Insufficient follow-up |
| c09 | An instruction-like message tries to fabricate evidence | Continued failures |
| c10 | Spanish and Serbian failure reports | Continued failures |
| c11 | Similar terminology describes different product workflows | No intervention recorded |
| c12 | An update is installed, but its outcome is unknown | Insufficient follow-up |

Labels are authored expectations, not universal ground truth. Review disputed
labels before using these results for a provider decision. The mixed-topic and
language cases are small examples, not comprehensive coverage.

`success` means a reported success on the affected workflow. `counterexample`
means relevant contrasting evidence from a different workflow or condition,
not proof of a repair. `failure`, `workaround`, and `unclear` retain their
ordinary investigation meanings. A counterexample observation is distinct
from a hypothesis's `contradicting_ids`: even a same-workflow success can
contradict a hypothesis claiming universal failure.

## What the numbers mean

Scores are pooled across cases, not averaged from percentages. Undefined
ratios are JSON `null`, never a perfect score.

| Metric | Numerator / denominator |
| --- | --- |
| Observation precision | Accepted relevant observation IDs / all accepted observation IDs |
| Observation recall | Accepted relevant observation IDs / all expected observation IDs |
| Outcome accuracy on matched | Correct outcome labels / matched relevant observation IDs |
| End-to-end label recall | Correct outcome labels / all expected observation IDs |
| Counterexample coverage | Required IDs present in hypothesis contradiction lists / all required counterexample IDs |
| Verification status accuracy | Exactly matching verification statuses / all input cases |
| False reassurance cases | Count of unwarranted “improvement corroborated” statuses |

A missing or structurally invalid case contributes zero accepted observations,
zero correct labels, and an incorrect verification status. Its expected
observations remain in recall denominators. Inspect invalid-case counts and
recall alongside precision: invalid responses can leave precision undefined
or deceptively high among the few remaining valid responses.

The all-pass gate requires exact observation IDs and labels, the expected
status, and every required counterexample reference for every case. The report
also shows missing/irrelevant observations, incorrect-label IDs, a confusion
matrix including missing predictions, and per-case failures. This gate is
fixture conformance, not production approval.

Counterexample coverage checks references, not whether the prose makes a valid
argument. Exact quotes prove traceability, not that a model interpreted them
correctly. No automatic score here establishes root cause, causal impact,
general prompt-injection resistance, or question usefulness.

## Human review before comparing providers

Keep the prediction reports. Hide provider names, randomize the presentation order,
and have two reviewers independently examine the input messages and each
response. Do not show aggregate scores until their first review is complete.
Use a separate set of newly authored, consented, or synthetic cases not used
while tuning the prompt.

Score each dimension `0` (wrong or harmful), `1` (partly useful, material
omissions), or `2` (well supported and useful), with a short evidence-based
reason. Use “not applicable” when a dimension genuinely cannot be assessed.

- **Evidence interpretation:** Does the explanation preserve the meaning and
  scope of each quote, including sarcasm, hearsay, and workflow differences?
- **Hypotheses:** Are proposed explanations distinguishable, plausible, and
  explicitly uncertain rather than asserted as causes?
- **Counterexamples:** Do contradiction references actually challenge the
  stated hypothesis, rather than merely appear in a list?
- **Falsification:** Would the proposed test be feasible and capable of
  disproving the explanation?
- **Follow-up question:** Is the question specific, answerable, and likely to
  resolve the most important remaining uncertainty?
- **Causal restraint:** Does the response avoid claiming the intervention
  caused recovery or that the problem is resolved?

Record disagreements and adjudication separately rather than hiding them in
one average. Report model configuration, set hashes, incomplete cases, and
reviewer agreement alongside results. No human-review results or independent
model comparison have been performed for this release.
