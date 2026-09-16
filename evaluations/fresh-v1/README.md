# Fresh challenge set and blinded review

Ten new synthetic scenarios are separate from the twelve development fixtures.
This set targets judgment under ambiguity rather than another set of easy
sentiment labels. It is AI-authored and public, not a secret or independent
holdout. No model calls or human reviews have been performed for this set.

The production investigation prompt and verification logic were not tuned
against these cases. `freeze.json` records the byte hashes at preparation.
If either prompt or cases change, record a new evaluation version; do not keep
calling a tuned-on set held out. A true independent assessment still needs
cases and adjudication from people not involved in development.

## Files and boundaries

- `inputs.json`: Fictional messages, issue titles, reporter aliases, and
  intervention markers. Compatible with the existing evaluator.
- `gold.json`: Coordinator-only proposed labels and case-specific review notes.
  Several notes explicitly identify contestable labels or current-rule limits.
- `freeze.json`: Preparation revision and file hashes, not a signature.
- `../../docs/blind-review.md`: Common human-review rubric and review procedure.
- `../../tools/blind_review.py`: Offline packet generator and opt-in summary
  collector. These source-repository tools are not installed in the wheel.

The ready-to-review packet intentionally omits the answer key, aggregate scores,
provider/model names, and mapping. Because the repository is public, reviewers
must agree not to consult its coordinator files until ratings are locked.

## Prepare without credentials

From a source checkout with `pip install -e '.[dev]'` completed:

```sh
python tools/blind_review.py prepare --output fresh-review
```

The new directory contains `reviewers/packet.md`, two empty reviewer CSVs,
`reviewers/protocol.md`, and a separate `coordinator.json`. Distribute only
`reviewers/`. It is marked **NOT STARTED** and contains no fabricated candidate
answers or prefilled human ratings.

Optionally check fixture/scorer consistency without a model:

```sh
python -m seismograph evaluate \
  --inputs evaluations/fresh-v1/inputs.json \
  --gold evaluations/fresh-v1/gold.json \
  --output fresh-scorer-check.json
```

That is a reference replay, not evidence of model quality. It checks consistency
with current rules, including known conservative limitations.

## Collect and compare when API access is available

Set `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_PROVIDER` securely as
described in the evaluation guide. No Discord configuration is needed.
The following commands contact the configured provider; do not run them
without approval to send the selected inputs.

First use the same model and provider for both methods:

```sh
python -m seismograph evaluate --live --allow-api-calls \
  --inputs evaluations/fresh-v1/inputs.json \
  --gold evaluations/fresh-v1/gold.json \
  --max-requests 24 --output investigation-run.json

python tools/blind_review.py baseline --allow-api-calls \
  --max-requests 24 --output summary-run.json

python tools/blind_review.py prepare \
  --runs investigation-run.json summary-run.json \
  --output paired-review
```

Each collector has its own global 24-attempt ceiling, including retries and
repairs. There is no combined cross-command budget. Both use the same prepared
messages and client defaults; Sonar search is disabled. Summary output is
bounded prose and one question, not an investigation-shaped response.

The investigator command returns `1` when any deterministic case requirement
fails but still writes its result; do not discard that report. The baseline
returns `1` on incomplete collection and also saves failures. Code `2` means
preflight or execution stopped. Both refuse existing output paths.

The packet generator checks matching input hashes, rejects reference-replay
reports, shuffles cases and candidate order, and preserves missing outputs as
missing. Provider mode fields are not authenticated. It does not import
automatic scores or gold labels into the reviewer packet.

Use a new output directory for each preparation, then keep its mapping.
Generating again randomizes labels again; never apply old ratings to a newly
generated packet. A/B positions are identical for the two reviewers within
one packet, but randomized independently across cases.

## Questions this set should expose

| Scenario | Product judgment under test |
| --- | --- |
| Corrected notification report | Does the explanation acknowledge a retraction even though failure-precedence rules retain it? |
| Partial transcription rollout | Does it distinguish old-build failures from evidence against the new build? |
| Viewer invitation permissions | Does it ask about entitlement instead of inventing a policy or declaring a bug? |
| Recirculated billing screenshot | Does it avoid treating repeated hearsay as independent events? |
| Large ZIP uploads | Does it distinguish size scope and avoid claiming causality from confounded tests? |
| Reminder timestamps | Does it abstain when receipt time and timezone are unknown? |
| Extension-related typing | Does it distinguish a workaround from success in the required setup? |
| Embedded HTML instructions | Does it preserve evidence boundaries without obeying file content? |
| Keyboard submission | Does it separate a corroborated shortcut improvement from a different open accessibility issue? |
| Missing notes list | Does it avoid equating visibility failure with data loss or reduced chat with recovery? |

The first two cases intentionally expose current verifier limitations. Do not
change the prompt or counting rules just to make this set pass before collecting
the first real-model run. Record software-rule failures separately from model
interpretation failures and disputed author labels.
